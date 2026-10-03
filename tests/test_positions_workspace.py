"""P5.3a tests for the V3 positions read-model (ТЗ V3 §7, MC-17 backend half).

Two layers, no database required:

  * pure builder tests — the liquidation level shown on a card is byte-identical
    to the shared ``trading_math`` formula the engine acts on (never a stored
    guess), unrealized is derived only from a live mark, and a missing/stale
    mark leaves unrealized ``None`` instead of fabricating a price;
  * ASGI endpoint tests over a fake pool — owner scoping, filters, server
    pagination, the separate ``pending`` bucket, and the fail-closed 503 / distinct
    ``empty`` vs ``error`` source states demanded by ТЗ §7.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from instrument_registry import load_registry
from paper_trading.risk import calculate_liquidation_price
from positions_workspace import (
    _count_sql,
    _pending_card,
    _pending_sql,
    _position_card,
    _positions_sql,
    _validate_filters,
    _liq_and_unrealized,
    router,
)

SPEC = None


def _spec():
    global SPEC
    if SPEC is None:
        SPEC = load_registry(force_reload=True).get("htx:linear-swap:BTC-USDT")
    return SPEC


def _position_row(**over) -> dict:
    row = {
        "position_id": uuid4(),
        "account_id": uuid4(),
        "account_kind": "manual",
        "day_id": uuid4(),
        "instrument": "BTC-USDT",
        "side": "long",
        "qty": Decimal("10"),
        "avg_entry_price": Decimal("10000"),
        "isolated_margin": Decimal("100"),
        "stop_loss": Decimal("9000"),
        "take_profit": Decimal("12000"),
        "status": "open",
        "opened_at": datetime.now(timezone.utc),
        "closed_at": None,
        "close_price": None,
        "realized_gross_pnl": Decimal("0"),
        "realized_net_pnl": Decimal("0"),
        "entry_fee": Decimal("6"),
        "exit_fee": Decimal("0"),
        "funding_cashflow": Decimal("0"),
    }
    row.update(over)
    return row


def _card(row, *, mark=None, mark_status="live"):
    return _position_card(
        row, specs={"BTC-USDT": _spec()}, mark=mark, mark_status=mark_status,
        fills=[], posting_events=[],
    )


# ---------------------------------------------------------------------------
# liquidation level is the shared formula, not an invented number
# ---------------------------------------------------------------------------


def test_liquidation_price_equals_shared_formula():
    row = _position_row()
    spec = _spec()
    derived = _liq_and_unrealized(row, spec=spec, mark=None)
    expected = calculate_liquidation_price(
        Decimal("10000"), 10, "long",
        isolated_margin=Decimal("100"),
        notional=Decimal("10") * Decimal("10000") * spec.contract_size,
    )
    assert derived["liquidation_price"] == format(expected, "f")
    assert derived["liquidation_basis"]["method"].startswith("shared trading_math")
    assert derived["liquidation_basis"]["contract_size"] == format(spec.contract_size, "f")


def test_terminal_position_has_no_live_liquidation_level():
    """A closed position's fact is the recorded close, not a live level."""
    closed = _position_row(status="closed", closed_at=datetime.now(timezone.utc),
                           close_price=Decimal("10100"),
                           realized_gross_pnl=Decimal("100"),
                           realized_net_pnl=Decimal("87.94"), exit_fee=Decimal("6.06"))
    card = _card(closed)
    assert card["liquidation_price"] is None
    assert card["exit_reason"] is None
    assert card["realized_net_pnl"] == format(Decimal("87.94"), "f")


def test_liquidated_position_marks_exit_reason():
    liq = _position_row(status="liquidated", closed_at=datetime.now(timezone.utc),
                        close_price=Decimal("1"), realized_net_pnl=Decimal("-100"))
    card = _card(liq)
    assert card["status"] == "liquidated"
    assert card["exit_reason"] == "liquidation"
    assert card["liquidation_price"] is None


# ---------------------------------------------------------------------------
# unrealized is a projection from a live mark; missing mark stays null
# ---------------------------------------------------------------------------


def test_unrealized_long_positive_above_entry():
    card = _card(_position_row(), mark=Decimal("10500"))
    assert card["mark"] == "10500"
    assert card["unrealized_gross"] is not None
    gross = Decimal(card["unrealized_gross"])
    assert gross == Decimal("10500") * Decimal("0") + Decimal("500") * Decimal("10") * _spec().contract_size
    assert gross > 0
    # net is gross minus a projected exit fee, both exposed for audit
    net = Decimal(card["unrealized_net"])
    assert net < gross
    assert card["unrealized_basis"]["method"].startswith("mark-entry")


def test_unrealized_short_sign_flips():
    card = _card(_position_row(side="short"), mark=Decimal("9500"))
    gross = Decimal(card["unrealized_gross"])
    assert gross > 0  # short profits as mark falls below entry


def test_missing_mark_leaves_unrealized_null_but_keeps_liq():
    card = _card(_position_row(), mark=None, mark_status="stale")
    assert card["mark"] is None
    assert card["unrealized_gross"] is None
    assert card["unrealized_net"] is None
    # the liquidation level does not depend on the mark and is still shown
    assert card["liquidation_price"] is not None
    assert card["mark_status"] == "stale"


# ---------------------------------------------------------------------------
# pending bucket is an order intent, never a fake position
# ---------------------------------------------------------------------------


def test_pending_card_maps_side_and_state():
    order = {
        "order_id": uuid4(), "account_id": uuid4(), "account_kind": "auto",
        "day_id": uuid4(), "instrument": "BTC-USDT", "side": "buy",
        "qty": Decimal("5"), "price": Decimal("10000"), "stop_loss": Decimal("9500"),
        "take_profit": None, "state": "submitted", "filled_qty": Decimal("0"),
        "origin": "auto", "actor": "auto", "created_at": datetime.now(timezone.utc),
    }
    card = _pending_card(order)
    assert card["kind"] == "pending"
    assert card["status"] == "pending"
    assert card["side"] == "long"
    assert card["position_id"] is None
    assert card["order_state"] == "submitted"


def test_pending_sell_maps_short():
    order = {
        "order_id": uuid4(), "account_id": uuid4(), "account_kind": "manual",
        "day_id": uuid4(), "instrument": "BTC-USDT", "side": "sell", "qty": Decimal("1"),
        "price": Decimal("9"), "stop_loss": None, "take_profit": None,
        "state": "pending", "filled_qty": Decimal("0"), "origin": "user",
        "actor": "user", "created_at": datetime.now(timezone.utc),
    }
    assert _pending_card(order)["side"] == "short"


# ---------------------------------------------------------------------------
# SQL builders keep owner as $1 and place params in the right slots
# ---------------------------------------------------------------------------


def test_positions_sql_owner_is_first_and_status_predicate_bound():
    sql, args = _positions_sql("open", "manual", None, limit=10, offset=0)
    assert "a.owner_id = $1" in sql
    assert "p.status = $2" in sql
    assert "a.kind = $3" in sql
    assert "LIMIT $4 OFFSET $5" in sql
    assert args[1:] == ["open", "manual", 10, 0]


def test_positions_sql_all_has_no_status_predicate():
    sql, _ = _positions_sql("all", None, None, limit=5, offset=10)
    assert "p.status" not in sql
    assert "LIMIT $2 OFFSET $3" in sql


def test_pending_sql_filters_pending_order_states():
    sql, args = _pending_sql(None, None, limit=20)
    assert "o.state = ANY($2::text[])" in sql
    assert set(args[1]) == {"pending", "submitted", "partially_filled"}


def test_count_sql_matches_positions_shape():
    sql, args = _count_sql("closed", "auto", None)
    assert "count(*)" in sql and "a.owner_id = $1" in sql
    assert args[1:] == ["closed", "auto"]


# ---------------------------------------------------------------------------
# filter validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["bogus", ""])
def test_validate_filters_rejects_bad_status(status):
    with pytest.raises(HTTPException) as exc:
        _validate_filters(status, None, None)
    assert exc.value.status_code == 400
    assert exc.value.detail["reason_code"] == "bad_status_filter"


def test_validate_filters_rejects_bad_account_and_day():
    with pytest.raises(HTTPException):
        _validate_filters("all", "retail", None)
    with pytest.raises(HTTPException) as exc:
        _validate_filters("all", None, "not-a-uuid")
    assert exc.value.detail["reason_code"] == "bad_day_filter"


def test_validate_filters_accepts_account_all():
    # the UI's "Оба" option sends account=all, which must mean "no account
    # predicate" rather than a 400 (MC-17 default load path).
    _validate_filters("all", "all", None)  # does not raise


# ---------------------------------------------------------------------------
# ASGI endpoint harness (fake pool + fake redis), no session → admin owner
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, key):  # noqa: ARG002
        return json.dumps(self._payload) if self._payload is not None else None


def _mark_payload(mark: str = "10100", *, live: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    age = 0 if live else 400
    ts = (now - timedelta(seconds=age)).isoformat()
    return {
        "venue": "htx", "market_type": "linear-swap", "contract_code": "BTC-USDT",
        "bid": mark, "ask": mark, "last": mark, "mark": mark, "index": mark,
        "funding_rate": "0.0001", "next_funding_at": (now + timedelta(hours=2)).isoformat(),
        "source_at": ts, "received_at": now.isoformat(),
        "component_times": {"ticker": ts, "mark": ts, "index": ts, "funding": ts},
        "source": "fixture",
    }


class FakePool:
    def __init__(self, *, positions, total, orders=None, fills=None, postings=None, error=None):
        self.positions = positions
        self.total = total
        self.orders = orders or []
        self.fills = fills or []
        self.postings = postings or []
        self.error = error

    async def fetch(self, sql, *args):  # noqa: ARG002
        if self.error is not None:
            raise self.error
        low = sql.lower()
        if "paper_v2_fills" in low:
            return self.fills
        if "paper_v2_postings" in low:
            return self.postings
        if "paper_v2_orders" in low:
            return self.orders
        return self.positions

    async def fetchrow(self, sql, *args):  # noqa: ARG002
        if self.error is not None:
            raise self.error
        if "count(*)" in sql.lower():
            return {"n": self.total}
        # single-position lookup: honour the requested id (owner-scoped 404 path)
        if args:
            wanted = str(args[0])
            for row in self.positions:
                if str(row["position_id"]) == wanted:
                    return row
            return None
        return self.positions[0] if self.positions else None


def _app(*, pool, redis_payload="10100") -> FastAPI:
    app = FastAPI()
    app.state.pg_pool = pool
    app.state.redis_client = FakeRedis(_mark_payload(redis_payload) if redis_payload else None)
    # The real webui installs SessionMiddleware; the read-model's owner resolver
    # relies on it, so the harness mirrors that wiring (no session → admin owner).
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(SessionMiddleware, secret_key="test-only-secret")
    app.include_router(router)
    return app


async def _get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_list_positions_ok():
    pos = _position_row()
    fills = [{"fill_id": uuid4(), "order_id": pos["position_id"]}]
    pool = FakePool(positions=[pos], total=1, fills=fills,
                    postings=[{"event_id": uuid4(), "source_ref": str(fills[0]["fill_id"])}],
                    orders=[])
    body = (await _get(_app(pool=pool), "/api/v3/positions?status=open")).json()
    assert body["source_status"] == "ok"
    assert body["page"]["total"] == 1 and body["page"]["returned"] == 1
    card = body["positions"][0]
    assert card["position_id"] == str(pos["position_id"])
    assert card["order_id"] == str(pos["position_id"])  # same IDs as the ledger
    assert card["mark"] == "10100"
    assert card["liquidation_price"] is not None
    assert card["unrealized_gross"] is not None
    assert card["links"]["fills"] == [str(fills[0]["fill_id"])]
    assert card["links"]["posting_events"]  # resolved via fill source_ref


async def test_list_positions_includes_pending_bucket():
    order = {"order_id": uuid4(), "account_id": uuid4(), "account_kind": "auto",
             "day_id": uuid4(), "instrument": "BTC-USDT", "side": "buy", "qty": Decimal("2"),
             "price": Decimal("10000"), "stop_loss": None, "take_profit": None,
             "state": "pending", "filled_qty": Decimal("0"), "origin": "auto",
             "actor": "auto", "created_at": datetime.now(timezone.utc)}
    pool = FakePool(positions=[], total=0, orders=[order])
    body = (await _get(_app(pool=pool), "/api/v3/positions?status=all")).json()
    assert body["pending"][0]["kind"] == "pending"
    assert body["positions"] == []


async def test_empty_history_is_source_status_empty_not_error():
    pool = FakePool(positions=[], total=0, orders=[])
    body = (await _get(_app(pool=pool), "/api/v3/positions")).json()
    assert body["source_status"] == "empty"
    assert body["positions"] == [] and body["pending"] == []


async def test_pool_unavailable_is_503():
    app = _app(pool=FakePool(positions=[], total=0))
    app.state.pg_pool = None
    resp = await _get(app, "/api/v3/positions")
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason_code"] == "positions_source_unavailable"


async def test_query_failure_is_503_error_state():
    pool = FakePool(positions=[], total=0, error=RuntimeError("db down"))
    resp = await _get(_app(pool=pool), "/api/v3/positions")
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason_code"] == "positions_query_failed"


async def test_pagination_has_more_flag():
    pos = _position_row()
    pool = FakePool(positions=[pos], total=42)
    body = (await _get(_app(pool=pool), "/api/v3/positions?limit=1&offset=0")).json()
    assert body["page"]["has_more"] is True
    assert body["page"]["total"] == 42


@pytest.mark.parametrize("q,code", [("limit=0", "limit_out_of_range"),
                                   ("limit=9999", "limit_out_of_range"),
                                   ("status=nope", "bad_status_filter"),
                                   ("account=retail", "bad_account_filter")])
async def test_bad_query_params_400(q, code):
    resp = await _get(_app(pool=FakePool(positions=[], total=0)), f"/api/v3/positions?{q}")
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason_code"] == code


async def test_get_single_position_and_404_for_others():
    pos = _position_row()
    pool = FakePool(positions=[pos], total=1)
    app = _app(pool=pool)
    ok = await _get(app, f"/api/v3/positions/{pos['position_id']}")
    assert ok.status_code == 200
    assert ok.json()["position"]["position_id"] == str(pos["position_id"])
    missing = await _get(app, f"/api/v3/positions/{uuid4()}")
    assert missing.status_code == 404


async def test_mark_unavailable_is_fail_visible_not_fabricated():
    pos = _position_row()
    pool = FakePool(positions=[pos], total=1)
    app = _app(pool=pool, redis_payload=None)  # no snapshot
    app.state.redis_client = None
    body = (await _get(app, "/api/v3/positions?status=open")).json()
    card = body["positions"][0]
    assert card["mark_status"] == "source_unavailable"
    assert card["mark"] is None
    assert card["unrealized_gross"] is None
    assert card["liquidation_price"] is not None  # mark-independent
