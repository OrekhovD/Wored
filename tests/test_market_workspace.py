"""P1.1 tests for /api/v3 market read-model (ТЗ V3 §4, MC-01…MC-03).

Covers:
  * per-component freshness verdict (bid/ask, mark, index, funding) — not a
    single "data fresh" flag (MC-03);
  * identity checks that refuse to substitute a spot feed for a perpetual
    snapshot (MC-01);
  * candle invariants: OHLC containment, monotonic times, gap/duplicate
    detection, derived 5m/1d quality labelling (MC-02);
  * stale feed blocks ``can_enter`` while protective actions remain available.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI

from market_workspace import (
    CandleRow,
    aggregate_candles,
    build_forming,
    build_market_state,
    detect_gaps,
    router,
    validate_candle_rows,
)
from instrument_registry import load_registry


REPO_REGISTRY_PATH = None  # env override set per-test


BTC_SPEC = None  # populated lazily below


def _spec():
    global BTC_SPEC
    if BTC_SPEC is None:
        BTC_SPEC = load_registry(force_reload=True).get("htx:linear-swap:BTC-USDT")
    return BTC_SPEC


def _payload(
    *,
    now: datetime,
    ticker_age: float = 0.0,
    mark_age: float = 0.0,
    index_age: float = 0.0,
    funding_age: float = 0.0,
    bid: str = "100",
    ask: str = "100.2",
    funding_rate: str = "0.0001",
    venue: str = "htx",
    market_type: str = "linear-swap",
    contract: str = "BTC-USDT",
    source: str = "htx-linear-swap-ws",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "venue": venue,
        "market_type": market_type,
        "contract_code": contract,
        "bid": bid,
        "ask": ask,
        "last": "100.1",
        "mark": "100.08",
        "index": "100.05",
        "funding_rate": funding_rate,
        "next_funding_at": (now + timedelta(hours=4)).isoformat(),
        "contract_size": "0.0001",
        "price_tick": "0.1",
        "quantity_step": "0.001",
        "source_at": (now - timedelta(seconds=ticker_age)).isoformat(),
        "received_at": now.isoformat(),
        "component_times": {
            "ticker": (now - timedelta(seconds=ticker_age)).isoformat(),
            "mark": (now - timedelta(seconds=mark_age)).isoformat(),
            "index": (now - timedelta(seconds=index_age)).isoformat(),
            "funding": (now - timedelta(seconds=funding_age)).isoformat(),
        },
        "source": source,
    }


# ---------------------------------------------------------------------------
# build_market_state — pure unit
# ---------------------------------------------------------------------------


def test_state_fresh_all_components_live_and_can_enter() -> None:
    now = datetime.now(timezone.utc)
    state = build_market_state(_spec(), _payload(now=now), now=now)
    q = state["quality"]
    assert q["ticker"] == "live"
    assert q["mark"] == "live"
    assert q["index"] == "live"
    assert q["funding"] == "live"
    assert q["worst"] == "live"
    assert state["capabilities"]["can_enter"] is True
    assert state["capabilities"]["can_close"] is True
    assert state["capabilities"]["reason_code"] is None


def test_state_stale_ticker_blocks_entry_but_keeps_values() -> None:
    now = datetime.now(timezone.utc)
    payload = _payload(now=now, ticker_age=20)
    state = build_market_state(_spec(), payload, now=now)
    assert state["quality"]["ticker"] == "stale"
    assert state["quality"]["worst"] == "stale"
    assert state["capabilities"]["can_enter"] is False
    assert "ticker" in state["capabilities"]["reason_code"]
    # TZ §3: stale must still SHOW the last known values with their age.
    assert state["quote"]["bid"] == "100"
    assert state["quote"]["ask"] == "100.2"
    assert state["quote"]["component_times"]["ticker"] is not None


def test_state_stale_mark_only_marks_worst_stale() -> None:
    now = datetime.now(timezone.utc)
    payload = _payload(now=now, mark_age=200)
    state = build_market_state(_spec(), payload, now=now)
    assert state["quality"]["mark"] == "stale"
    assert state["quality"]["ticker"] == "live"
    assert state["quality"]["worst"] == "stale"
    assert state["capabilities"]["can_enter"] is False


def test_state_crossed_book_downgrades_ticker() -> None:
    now = datetime.now(timezone.utc)
    payload = _payload(now=now, bid="101", ask="100")
    state = build_market_state(_spec(), payload, now=now)
    # crossed book poisons the ticker signal regardless of timestamps
    assert state["quality"]["ticker"] != "live"


def test_state_payload_none_marks_all_missing() -> None:
    now = datetime.now(timezone.utc)
    state = build_market_state(_spec(), None, now=now)
    assert state["quote"] is None
    assert state["quality"]["worst"] == "missing"
    assert state["capabilities"]["can_enter"] is False
    assert state["capabilities"]["reason_code"] == "snapshot_missing"
    # protective close stays available even with no data
    assert state["capabilities"]["can_close"] is True


def test_state_identity_mismatch_refuses_substitution() -> None:
    """A spot-shaped payload on the perpetual key must not be trusted (MC-01)."""
    now = datetime.now(timezone.utc)
    state = build_market_state(
        _spec(),
        _payload(now=now, venue="htx", market_type="spot"),
        now=now,
    )
    assert state.get("identity_ok") is False
    assert state["observed"]["market_type"] == "spot"


def test_state_negative_funding_rate_is_accepted() -> None:
    now = datetime.now(timezone.utc)
    payload = _payload(now=now, funding_rate="-0.00015")
    state = build_market_state(_spec(), payload, now=now)
    assert state["quote"]["funding_rate"] == "-0.00015"
    assert state["quality"]["funding"] == "live"


def test_state_source_field_passes_through_verbatim() -> None:
    """Evidence-level invariant: a synthetic staging tag never becomes "live HTX"."""
    now = datetime.now(timezone.utc)
    payload = _payload(now=now, source="staging-synthetic")
    state = build_market_state(_spec(), payload, now=now)
    assert state["source"] == "staging-synthetic"


def test_state_spread_and_sequence_present() -> None:
    now = datetime.now(timezone.utc)
    state = build_market_state(_spec(), _payload(now=now), now=now)
    assert Decimal(state["spread"]) == Decimal("0.2")
    assert state["sequence"] > 0


# ---------------------------------------------------------------------------
# validate_candle_rows / detect_gaps / aggregate_candles — pure unit
# ---------------------------------------------------------------------------


def _row(idx: int, *, ohlc: tuple[str, str, str, str] = ("100", "101", "99", "100.5")) -> CandleRow:
    start = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx)
    return CandleRow(
        start_at=start,
        end_at=start + timedelta(minutes=1),
        open=Decimal(ohlc[0]),
        high=Decimal(ohlc[1]),
        low=Decimal(ohlc[2]),
        close=Decimal(ohlc[3]),
        volume=Decimal("10"),
        source="htx-linear-swap-rest",
    )


def test_validate_rows_accepts_valid_ohlc() -> None:
    rows = [_row(i) for i in range(5)]
    out = validate_candle_rows(rows, stored_seconds=60)
    assert len(out) == 5
    assert out[0]["closed"] is True
    assert out[0]["open"] == "100"
    assert out[0]["source"] == "htx-linear-swap-rest"


def test_validate_rows_rejects_high_below_body() -> None:
    rows = [_row(0, ohlc=("100", "99", "95", "100.5"))]  # high < max(open,close)
    from market_workspace import CandleSourceError
    with pytest.raises(CandleSourceError) as exc:
        validate_candle_rows(rows, stored_seconds=60)
    assert exc.value.reason_code == "invalid_candle_ohlc"


def test_validate_rows_rejects_duplicate_time() -> None:
    rows = [_row(0), _row(0)]  # same start_at
    from market_workspace import CandleSourceError
    with pytest.raises(CandleSourceError) as exc:
        validate_candle_rows(rows, stored_seconds=60)
    assert exc.value.reason_code == "duplicate_candle"


def test_validate_rows_rejects_wrong_span() -> None:
    rows = [_row(0)]
    # Tell validator it's a 1-hour series but rows carry 1-minute span.
    from market_workspace import CandleSourceError
    with pytest.raises(CandleSourceError) as exc:
        validate_candle_rows(rows, stored_seconds=3600)
    assert exc.value.reason_code == "invalid_candle_span"


def test_detect_gaps_finds_missing_span() -> None:
    rows = [_row(i) for i in [0, 1, 2, 5, 6]]
    validated = validate_candle_rows(rows, stored_seconds=60)
    gaps = detect_gaps(validated, period_seconds=60)
    assert len(gaps) == 1
    assert gaps[0]["found_start_at"].startswith("2026-09-28T00:05")


# ---------------------------------------------------------------------------
# build_forming — P1.3 forming-period facts (no synthesized body)
# ---------------------------------------------------------------------------


def test_build_forming_empty_history_is_none() -> None:
    """No candles → no forming period to describe, not a fabricated one."""
    now = datetime(2026, 9, 28, 0, 30, tzinfo=timezone.utc)
    assert build_forming([], period_seconds=60, now=now) is None


def test_build_forming_bounds_from_last_closed_candle() -> None:
    validated = validate_candle_rows([_row(i) for i in range(3)], stored_seconds=60)
    # last candle end_at == 00:03:00 → forming period starts there
    now = datetime(2026, 9, 28, 0, 3, 20, tzinfo=timezone.utc)
    forming = build_forming(validated, period_seconds=60, now=now)
    assert forming is not None
    assert forming["state"] == "forming"
    assert forming["start_at"] == validated[-1]["end_at"]
    assert forming["ends_at"].startswith("2026-09-28T00:04")
    assert forming["candle_body_available"] is False


def test_build_forming_elapsed_and_remaining_are_consistent() -> None:
    validated = validate_candle_rows([_row(i) for i in range(2)], stored_seconds=60)
    now = datetime(2026, 9, 28, 0, 2, 15, tzinfo=timezone.utc)
    forming = build_forming(validated, period_seconds=60, now=now)
    assert forming is not None
    assert abs(forming["elapsed_seconds"] - 15.0) < 1e-6
    assert abs(forming["remaining_seconds"] - 45.0) < 1e-6
    # invariant: elapsed + remaining == the full period
    assert abs(forming["elapsed_seconds"] + forming["remaining_seconds"] - 60.0) < 1e-6


def test_build_forming_clamps_negative_when_clock_skews() -> None:
    """If ``now`` is before the last close (clock skew), clamp to >= 0."""
    validated = validate_candle_rows([_row(0)], stored_seconds=60)
    now = datetime(2026, 9, 28, 0, 0, 30, tzinfo=timezone.utc)  # before 00:01 close
    forming = build_forming(validated, period_seconds=60, now=now)
    assert forming is not None
    assert forming["elapsed_seconds"] >= 0.0
    assert forming["remaining_seconds"] >= 0.0


def test_aggregate_5m_from_1m_full_buckets_only() -> None:
    rows = [_row(i) for i in range(11)]  # 0..10 → two complete buckets of 5, one partial
    validated = validate_candle_rows(rows, stored_seconds=60)
    out = aggregate_candles(validated, factor=5, period_seconds=300)
    # 11 rows → bucket_ts grouping by 300s: 11//5=2 full buckets + 1 partial (dropped)
    assert len(out) == 2
    for item in out:
        assert Decimal(item["high"]) >= max(Decimal(item["open"]), Decimal(item["close"]))
        assert Decimal(item["low"]) <= min(Decimal(item["open"]), Decimal(item["close"]))
        assert Decimal(item["volume"]) == Decimal("50")


# ---------------------------------------------------------------------------
# HTTP endpoints — httpx ASGITransport
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload

    async def get(self, key: str) -> str | None:
        assert key == "market:perpetual:htx:BTC-USDT"
        return json.dumps(self.payload) if self.payload is not None else None


class FakePool:
    def __init__(self, rows: list[dict] | None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.last_query: tuple | None = None

    async def fetch(self, sql: str, *args):
        self.last_query = args
        if self.error is not None:
            raise self.error
        return self.rows


def _make_app(
    *,
    redis_payload: dict | None = None,
    pool_rows: list[dict] | None = None,
    pool_error: Exception | None = None,
    pool_available: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.state.redis_client = FakeRedis(redis_payload)
    app.state.pg_pool = FakePool(pool_rows, pool_error) if pool_available else None
    app.include_router(router)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.get(path)


async def test_instruments_endpoint_lists_btc() -> None:
    app = _make_app()
    response = await _get(app, "/api/v3/instruments")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    keys = [item["instrument_key"] for item in payload["instruments"]]
    assert "htx:linear-swap:BTC-USDT" in keys
    entry = payload["instruments"][0]
    # Decimal serialised as string, not float
    assert isinstance(entry["price_tick"], str)
    assert isinstance(entry["contract_size"], str)


async def test_state_endpoint_returns_per_component_quality() -> None:
    now = datetime.now(timezone.utc)
    app = _make_app(redis_payload=_payload(now=now, ticker_age=30))
    response = await _get(app, "/api/v3/market/htx:linear-swap:BTC-USDT/state")
    assert response.status_code == 200
    body = response.json()
    assert body["quality"]["ticker"] == "stale"
    assert body["quality"]["worst"] == "stale"
    assert body["capabilities"]["can_enter"] is False
    assert body["quote"]["bid"] == "100"


async def test_state_endpoint_unknown_instrument_404() -> None:
    """MC-01: a spot key must not silently fall back to perpetual data."""
    app = _make_app()
    response = await _get(app, "/api/v3/market/htx:spot:BTC-USDT/state")
    assert response.status_code == 404
    assert response.json()["detail"]["reason_code"] == "instrument_not_registered"


async def test_state_endpoint_redis_unavailable_503() -> None:
    app = _make_app()
    app.state.redis_client = None
    response = await _get(app, "/api/v3/market/htx:linear-swap:BTC-USDT/state")
    assert response.status_code == 503
    assert response.json()["detail"]["reason_code"] == "redis_unavailable"


async def test_state_endpoint_missing_key_returns_missing_quality() -> None:
    app = _make_app(redis_payload=None)
    response = await _get(app, "/api/v3/market/htx:linear-swap:BTC-USDT/state")
    assert response.status_code == 200  # data outage is a valid state, not HTTP error
    body = response.json()
    assert body["quote"] is None
    assert body["quality"]["worst"] == "missing"
    assert body["capabilities"]["can_enter"] is False


async def test_state_endpoint_identity_mismatch_503() -> None:
    """MC-01: snapshot payload claims to be spot → hard error, no substitution."""
    now = datetime.now(timezone.utc)
    app = _make_app(redis_payload=_payload(now=now, market_type="spot"))
    response = await _get(app, "/api/v3/market/htx:linear-swap:BTC-USDT/state")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason_code"] == "identity_mismatch"
    assert detail["observed"]["market_type"] == "spot"


def _candle_row(idx: int) -> dict:
    start = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx)
    return {
        "open_time": start,
        "close_time": start + timedelta(minutes=1),
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("10"),
        "source": "htx-linear-swap-rest",
        "venue": "htx",
    }


async def test_candles_endpoint_returns_validated_rows() -> None:
    rows = [_candle_row(i) for i in range(5)]
    app = _make_app(pool_rows=rows)
    response = await _get(app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=1m&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "ok"
    assert len(body["candles"]) == 5
    assert body["candles"][0]["closed"] is True
    assert body["candles"][0]["open"] == "100"  # serialised as Decimal-string
    assert body["gaps"] == []
    # P1.3: forming-period facts published alongside closed candles, never a body
    assert body["period_seconds"] == 60
    assert body["forming"] is not None
    assert body["forming"]["state"] == "forming"
    assert body["forming"]["candle_body_available"] is False
    assert body["forming"]["start_at"] == body["candles"][-1]["end_at"]
    assert body["forming"]["elapsed_seconds"] >= 0.0
    assert body["forming"]["remaining_seconds"] >= 0.0


async def test_candles_endpoint_derives_5m() -> None:
    rows = [_candle_row(i) for i in range(10)]
    app = _make_app(pool_rows=rows)
    response = await _get(
        app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=5m&limit=10"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["quality"] == "derived"
    assert body["derived_from"] == "1m"
    assert len(body["candles"]) == 2
    for candle in body["candles"]:
        assert candle["volume"] == "50"  # 5 rows × 10 vol each
    # P1.3: forming period tracks the derived timeframe, not the stored 1m
    assert body["period_seconds"] == 300
    assert body["forming"]["ends_at"] > body["forming"]["start_at"]


async def test_candles_endpoint_unknown_period_400() -> None:
    app = _make_app()
    response = await _get(
        app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=1w"
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason_code"] == "unsupported_period"


async def test_candles_endpoint_db_unavailable_503() -> None:
    app = _make_app(pool_available=False)
    response = await _get(
        app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=1m"
    )
    assert response.status_code == 503
    assert response.json()["detail"]["reason_code"] == "history_unavailable"


async def test_candles_endpoint_empty_history_is_not_error() -> None:
    """TZ §7: "пусто" is a distinct state from "ошибка"."""
    app = _make_app(pool_rows=[])
    response = await _get(
        app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=1m"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "empty"
    assert body["candles"] == []
    # P1.3: empty history has no forming period to describe
    assert body["forming"] is None


async def test_candles_endpoint_query_failure_503() -> None:
    app = _make_app(pool_error=RuntimeError("db died"))
    response = await _get(
        app, "/api/v3/market/htx:linear-swap:BTC-USDT/candles?period=1m"
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason_code"] == "history_query_failed"
