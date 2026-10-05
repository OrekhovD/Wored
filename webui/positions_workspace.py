"""V3 positions read-model: ``/api/v3/positions`` and ``/api/v3/positions/{id}``.

Scope (ТЗ V3 §7 / §34, P5.3a — backend only): give the workspace a single,
owner-scoped, paginated view over the *recorded* trading truth so the operator
can follow ``order → fill → position → close/liquidation`` for the manual and
auto accounts on one instrument, and so the chart can draw the same IDs and
levels the table shows.

Design rules this module honours:

  * The financial truth stays in ``paper_v2_*`` (positions / orders / fills /
    postings).  Nothing here writes; it is a pure projection, so a card can
    never disagree with the ledger the runner committed (ТЗ §7: identical IDs
    and amounts in chart and table).
  * **No invented numbers.**  Entry, qty, margin, stop/target, fees, funding and
    realized P&L are read verbatim from the row.  The *liquidation level* is not
    stored, so it is recomputed through :func:`paper_trading.risk.calculate_liquidation_price`
    — the exact same shared formula the liquidation handler acts on (ADR-01) —
    and the card exposes the basis (leverage, notional, source) so the number is
    auditable, not magic.
  * Unrealized P&L needs a live mark.  When the Redis quote is missing or stale
    the card reports ``mark_status`` and leaves unrealized ``None`` instead of
    freezing a fabricated price (fail-visible, the same contract the market
    read-model uses).
  * ``pending`` is a distinct bucket: entry intents that have not produced a
    position yet (``paper_v2_orders`` still ``pending``/``submitted``/
    ``partially_filled``).  It is never a fake position row.
  * Storage is the app's ``pg_pool``; with no pool the endpoint is 503
    fail-closed, never a silent demo list.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from instrument_registry import InstrumentRegistry, InstrumentSpec, load_registry
from market_workspace import MARKET_KEY_PREFIX, build_market_state

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["positions-v3"])

SCHEMA_VERSION = 1

#: The status filters ТЗ §79 names.  ``all`` maps to "no status predicate";
#: ``pending`` is served from the orders table, the rest from positions.
POSITION_STATUSES = ("open", "closed", "liquidated")
FILTER_STATUSES = ("open", "pending", "closed", "liquidated", "all")
ACCOUNT_KINDS = ("manual", "auto")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Order states that count as a still-pending entry intent (no position yet).
_PENDING_ORDER_STATES = ("pending", "submitted", "partially_filled")


# ── optional domain imports (webui runs with /app as import root in container) ─
try:  # pragma: no cover - exercised through both branches in tests
    from paper_trading.risk import calculate_liquidation_price as _calculate_liq
    from paper_trading.execution import FEE_RATE as _TAKER_FEE_RATE
    from paper_trading.liquidation_store import DEFAULT_LEVERAGE as _DEFAULT_LEVERAGE
    from paper_trading.adapter import owner_id_from_webui as _owner_id_from_webui

    _DOMAIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _calculate_liq = None
    _TAKER_FEE_RATE = Decimal("0.0005")  # HTX USDT-M Prime 0 taker (matches paper_trading.execution.FEE_RATE)
    _DEFAULT_LEVERAGE = 10
    _owner_id_from_webui = None
    _DOMAIN_AVAILABLE = False


# ---------------------------------------------------------------------------
# small strict helpers
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _fmt(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# owner resolution — the UUID identity paper_v2_accounts is keyed on
# ---------------------------------------------------------------------------


def _owner_uuid(request: Request) -> str:
    """Resolve the stable ``paper_v2_owners.owner_id`` UUID for the session.

    Mirrors ``paper_api._owner_from_request`` exactly (Telegram and password
    logins share the chatbot's uuid5 namespace), so the WebUI, the chatbot and
    this read-model all see one owner.  Identity comes only from server-side
    session state — never from a body or query argument.
    """
    if _owner_id_from_webui is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "domain_unavailable",
                "message": "paper_trading identity resolver is not importable",
            },
        )
    session = getattr(request, "session", {}) or {}
    if session.get("auth_type") == "telegram":
        user = session.get("telegram_user") or {}
        if isinstance(user.get("user_id"), int):
            return _owner_id_from_webui("admin", telegram_user_id=user["user_id"])
    if session.get("auth_type") == "password" and session.get("username"):
        return _owner_id_from_webui(str(session["username"]))
    return _owner_id_from_webui("admin")


# ---------------------------------------------------------------------------
# card builders (pure — no DB, no clock — so they unit-test in isolation)
# ---------------------------------------------------------------------------


def _liq_and_unrealized(
    row: dict[str, Any], *, spec: InstrumentSpec | None, mark: Decimal | None
) -> dict[str, Any]:
    """Recompute the shared-formula liquidation level and, when a fresh mark is
    present, the open-position unrealized projection.  Every number is either a
    recorded fact or an auditable derivation from recorded inputs — never a
    stored-away guess."""
    direction = str(row["side"])
    entry = _dec(row["avg_entry_price"])
    qty = _dec(row["qty"])
    margin = _dec(row["isolated_margin"])
    is_open = str(row["status"]) == "open"

    out: dict[str, Any] = {
        "liquidation_price": None,
        "liquidation_basis": None,
        "mark": _fmt(mark),
        "unrealized_gross": None,
        "unrealized_net": None,
        "unrealized_basis": None,
    }

    contract_size = spec.contract_size if spec is not None else None
    if qty is not None and entry is not None and contract_size is not None:
        notional = qty * entry * contract_size
        out["notional"] = _fmt(notional)
        # The *live* liquidation level is only meaningful for an open position;
        # a closed/liquidated one carries the recorded close as its fact.
        if _calculate_liq is not None and is_open:
            liq = _calculate_liq(
                entry, _DEFAULT_LEVERAGE, direction,
                isolated_margin=margin, notional=notional,
            )
            out["liquidation_price"] = _fmt(liq)
            out["liquidation_basis"] = {
                "method": "shared trading_math liquidation_price (ADR-01)",
                "leverage": _DEFAULT_LEVERAGE,
                "entry_price": _fmt(entry),
                "isolated_margin": _fmt(margin),
                "notional": _fmt(notional),
                "contract_size": _fmt(contract_size),
            }
        # Unrealized only for still-open positions with a live mark.
        if mark is not None and is_open and contract_size is not None:
            gross = (mark - entry) * qty * contract_size if direction == "long" \
                else (entry - mark) * qty * contract_size
            exit_notional = abs(mark) * qty * contract_size
            exit_fee = (_TAKER_FEE_RATE * exit_notional).quantize(Decimal("0.00000001"))
            net = gross - exit_fee
            out["unrealized_gross"] = _fmt(gross)
            out["unrealized_net"] = _fmt(net)
            out["unrealized_basis"] = {
                "method": "mark-entry × qty × contract_size, minus projected exit fee",
                "taker_fee_rate": _fmt(_TAKER_FEE_RATE),
                "projected_exit_fee": _fmt(exit_fee),
            }
    else:
        out["notional"] = None
    return out


def _position_card(
    row: dict[str, Any],
    *,
    specs: dict[str, InstrumentSpec],
    mark: Decimal | None,
    mark_status: str,
    fills: list[dict[str, Any]],
    posting_events: list[str],
) -> dict[str, Any]:
    status = str(row["status"])
    spec = specs.get(str(row["instrument"]))
    derived = _liq_and_unrealized(row, spec=spec, mark=mark if status == "open" else None)
    entry_fee = _dec(row.get("entry_fee"))
    exit_fee = _dec(row.get("exit_fee"))
    funding = _dec(row.get("funding_cashflow"))
    return {
        "kind": "position",
        "position_id": str(row["position_id"]),
        "order_id": str(row["position_id"]),  # open commit pins position_id == order_id
        "account_id": str(row["account_id"]),
        "account_kind": str(row["account_kind"]),
        "day_id": str(row["day_id"]),
        "instrument": str(row["instrument"]),
        "status": status,
        "side": str(row["side"]),
        "qty": _fmt(_dec(row["qty"])),
        "entry_price": _fmt(_dec(row["avg_entry_price"])),
        "isolated_margin": _fmt(_dec(row["isolated_margin"])),
        "stop_loss": _fmt(_dec(row["stop_loss"])),
        "take_profit": _fmt(_dec(row["take_profit"])),
        "liquidation_price": derived["liquidation_price"],
        "liquidation_basis": derived["liquidation_basis"],
        "notional": derived["notional"],
        "mark": derived["mark"],
        "mark_status": mark_status,
        "close_price": _fmt(_dec(row["close_price"])),
        "realized_gross_pnl": _fmt(_dec(row["realized_gross_pnl"])),
        "realized_net_pnl": _fmt(_dec(row["realized_net_pnl"])),
        "unrealized_gross": derived["unrealized_gross"],
        "unrealized_net": derived["unrealized_net"],
        "unrealized_basis": derived["unrealized_basis"],
        "entry_fee": _fmt(entry_fee),
        "exit_fee": _fmt(exit_fee),
        "total_fees": _fmt((entry_fee or Decimal(0)) + (exit_fee or Decimal(0))),
        "funding_cashflow": _fmt(funding),
        "exit_reason": "liquidation" if status == "liquidated" else None,
        "opened_at": _iso(row["opened_at"]),
        "closed_at": _iso(row["closed_at"]),
        "links": {
            "fills": [str(f["fill_id"]) for f in fills],
            "posting_events": posting_events,
        },
    }


def _pending_card(row: dict[str, Any]) -> dict[str, Any]:
    """An entry intent that has not produced a position.  Rendered with the same
    IDs the orders table holds, but ``kind='pending'`` so the UI never confuses
    it with a filled position."""
    return {
        "kind": "pending",
        "order_id": str(row["order_id"]),
        "position_id": None,
        "account_id": str(row["account_id"]),
        "account_kind": str(row["account_kind"]),
        "day_id": str(row["day_id"]),
        "instrument": str(row["instrument"]),
        "status": "pending",
        "side": "long" if str(row["side"]) == "buy" else "short",
        "qty": _fmt(_dec(row["qty"])),
        "entry_price": _fmt(_dec(row["price"])),
        "stop_loss": _fmt(_dec(row["stop_loss"])),
        "take_profit": _fmt(_dec(row["take_profit"])),
        "order_state": str(row["state"]),
        "filled_qty": _fmt(_dec(row["filled_qty"])),
        "origin": str(row["origin"]),
        "actor": str(row["actor"]),
        "created_at": _iso(row["created_at"]),
        "links": {"fills": [], "posting_events": []},
    }


# ---------------------------------------------------------------------------
# query building
# ---------------------------------------------------------------------------


def _positions_sql(
    status: str, account: str | None, day: str | None, *, limit: int, offset: int
) -> tuple[str, list[Any]]:
    clauses = ["a.owner_id = $1"]
    args: list[Any] = [None]  # owner filled by caller positionally
    idx = 2
    if status in POSITION_STATUSES:
        clauses.append(f"p.status = ${idx}")
        args.append(status)
        idx += 1
    if account:
        clauses.append(f"a.kind = ${idx}")
        args.append(account)
        idx += 1
    if day:
        clauses.append(f"p.day_id = ${idx}")
        args.append(UUID(day))
        idx += 1
    where = " AND ".join(clauses)
    sql = (
        "SELECT p.*, a.kind AS account_kind FROM paper_v2_positions p "
        "JOIN paper_v2_accounts a ON a.account_id = p.account_id "
        f"WHERE {where} ORDER BY p.opened_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
    )
    args.extend([limit, offset])
    return sql, args


def _count_sql(status: str, account: str | None, day: str | None) -> tuple[str, list[Any]]:
    clauses = ["a.owner_id = $1"]
    args: list[Any] = [None]
    idx = 2
    if status in POSITION_STATUSES:
        clauses.append(f"p.status = ${idx}")
        args.append(status)
        idx += 1
    if account:
        clauses.append(f"a.kind = ${idx}")
        args.append(account)
        idx += 1
    if day:
        clauses.append(f"p.day_id = ${idx}")
        args.append(UUID(day))
        idx += 1
    return (
        "SELECT count(*) AS n FROM paper_v2_positions p "
        "JOIN paper_v2_accounts a ON a.account_id = p.account_id "
        "WHERE " + " AND ".join(clauses),
        args,
    )


def _pending_sql(account: str | None, day: str | None, *, limit: int) -> tuple[str, list[Any]]:
    clauses = ["a.owner_id = $1", f"o.state = ANY(${2}::text[])"]
    args: list[Any] = [None, list(_PENDING_ORDER_STATES)]
    idx = 3
    if account:
        clauses.append(f"a.kind = ${idx}")
        args.append(account)
        idx += 1
    if day:
        clauses.append(f"o.day_id = ${idx}")
        args.append(UUID(day))
        idx += 1
    sql = (
        "SELECT o.*, a.kind AS account_kind FROM paper_v2_orders o "
        "JOIN paper_v2_accounts a ON a.account_id = o.account_id "
        "WHERE " + " AND ".join(clauses) + f" ORDER BY o.created_at DESC LIMIT ${idx}"
    )
    args.append(limit)
    return sql, args


async def _attach_children(pool: Any, position_ids: list[str]) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    """Batch-fetch fills (by order_id) and posting events (by fill / liquidation
    source_ref) so a card carries the same IDs the ledger used — one query each,
    no per-row N+1."""
    fills_by_pos: dict[str, list[dict]] = {}
    events_by_pos: dict[str, list[str]] = {}
    if not position_ids:
        return fills_by_pos, events_by_pos
    rows = await pool.fetch(
        "SELECT fill_id, order_id FROM paper_v2_fills WHERE order_id = ANY($1::uuid[])",
        [UUID(pid) for pid in position_ids],
    )
    fill_ids: list[str] = []
    for r in rows:
        oid = str(r["order_id"])
        fills_by_pos.setdefault(oid, []).append({"fill_id": r["fill_id"]})
        fill_ids.append(str(r["fill_id"]))
    # postings link by source_ref == fill_id (fills) or == 'liq:{position_id}'
    source_refs = list(fill_ids) + [f"liq:{pid}" for pid in position_ids]
    if source_refs:
        prows = await pool.fetch(
            "SELECT DISTINCT event_id, source_ref FROM paper_v2_postings "
            "WHERE source_ref = ANY($1::text[])",
            source_refs,
        )
        ref_to_event: dict[str, list[str]] = {}
        for r in prows:
            ref_to_event.setdefault(str(r["source_ref"]), []).append(str(r["event_id"]))
        for pid in position_ids:
            evs: list[str] = []
            for f in fills_by_pos.get(pid, []):
                evs += ref_to_event.get(str(f["fill_id"]), [])
            evs += ref_to_event.get(f"liq:{pid}", [])
            events_by_pos[pid] = sorted(set(evs))
    return fills_by_pos, events_by_pos


# ---------------------------------------------------------------------------
# live mark (single registered instrument today)
# ---------------------------------------------------------------------------


async def _current_mark(request: Request, spec: InstrumentSpec | None) -> tuple[Decimal | None, str]:
    """Return (mark, quality) for the instrument, or (None, status) when the
    quote is unavailable/degraded.  Reuses the market read-model so freshness
    semantics are identical to ``/market/{key}/state``."""
    if spec is None:
        return None, "unknown_instrument"
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None, "source_unavailable"
    key = f"{MARKET_KEY_PREFIX}:{spec.contract_code}"
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 — surface as outage, never fabricate
        log.warning("v3 positions mark read failed for %s: %s", spec.instrument_key, exc)
        return None, "source_error"
    payload: dict[str, Any] | None = None
    if raw is not None:
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            payload = None
    state = build_market_state(spec, payload, now=datetime.now(timezone.utc))
    if state.get("identity_ok") is False or not payload:
        return None, "snapshot_missing" if payload is None else "identity_mismatch"
    quote = state.get("quote") or {}
    mark = _dec(quote.get("mark"))
    if mark is None:
        return None, "mark_missing"
    return mark, state["quality"]["worst"]


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


def _validate_filters(status: str, account: str | None, day: str | None) -> None:
    if status not in FILTER_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "bad_status_filter", "message": f"status must be one of {list(FILTER_STATUSES)}"},
        )
    if account is not None and account not in ACCOUNT_KINDS and account != "all":
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "bad_account_filter", "message": f"account must be one of {list(ACCOUNT_KINDS) + ['all']}"},
        )
    if day is not None:
        try:
            UUID(day)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400, detail={"reason_code": "bad_day_filter", "message": "day must be a UUID"}
            )


@router.get("/positions")
async def list_positions(
    request: Request, status: str = "all", account: str | None = None,
    day: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0,
) -> JSONResponse:
    owner = _owner_uuid(request)
    _validate_filters(status, account, day)
    # ``all`` (and an omitted account) mean "no account predicate" downstream.
    if account == "all":
        account = None
    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(status_code=400, detail={"reason_code": "limit_out_of_range", "max": MAX_LIMIT})
    if offset < 0:
        raise HTTPException(status_code=400, detail={"reason_code": "offset_negative"})

    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "positions_source_unavailable", "message": "PostgreSQL pool is not connected"},
        )

    registry: InstrumentRegistry = load_registry()
    specs = {spec.contract_code: spec for spec in registry.list()}
    # The single registered perpetual drives the mark for all cards today.
    primary_spec = next(iter(specs.values()), None)

    try:
        pos_sql, pos_args = _positions_sql(status, account, day, limit=limit, offset=offset)
        rows = await pool.fetch(pos_sql, owner, *pos_args[1:])
        count_sql, count_args = _count_sql(status, account, day)
        total_row = await pool.fetchrow(count_sql, owner, *count_args[1:])
        pending_rows: list[dict] = []
        if status in ("all", "pending"):
            pend_sql, pend_args = _pending_sql(account, day, limit=limit)
            pending_rows = await pool.fetch(pend_sql, owner, *pend_args[1:])
    except Exception as exc:  # noqa: BLE001 — source outage is an error state, not empty
        log.warning("v3 positions query failed for %s: %s", owner, exc)
        raise HTTPException(
            status_code=503, detail={"reason_code": "positions_query_failed", "message": str(exc)}
        ) from exc

    position_ids = [str(r["position_id"]) for r in rows]
    fills_by_pos, events_by_pos = await _attach_children(pool, position_ids)

    mark, mark_status = await _current_mark(request, primary_spec)

    cards = [
        _position_card(
            dict(r), specs=specs, mark=mark, mark_status=mark_status,
            fills=fills_by_pos.get(str(r["position_id"]), []),
            posting_events=events_by_pos.get(str(r["position_id"]), []),
        )
        for r in rows
    ]
    pending_cards = [_pending_card(dict(r)) for r in pending_rows]

    total = int(total_row["n"]) if total_row else 0
    if not cards and not pending_cards:
        source_status = "empty"
    else:
        source_status = "ok"
    return JSONResponse(
        {
            "schema_version": SCHEMA_VERSION,
            "owner_id": owner,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "filters": {"status": status, "account": account, "day": day},
            "page": {"limit": limit, "offset": offset, "total": total,
                     "returned": len(cards), "has_more": offset + len(cards) < total},
            "positions": cards,
            "pending": pending_cards,
            "mark": _fmt(mark),
            "mark_status": mark_status,
            "source_status": source_status,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/positions/{position_id}")
async def get_position(request: Request, position_id: str) -> JSONResponse:
    owner = _owner_uuid(request)
    try:
        UUID(position_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail={"reason_code": "position_not_found", "message": "no such position"})
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503, detail={"reason_code": "positions_source_unavailable", "message": "PostgreSQL pool is not connected"}
        )
    row = await pool.fetchrow(
        "SELECT p.*, a.kind AS account_kind FROM paper_v2_positions p "
        "JOIN paper_v2_accounts a ON a.account_id = p.account_id "
        "WHERE p.position_id = $1 AND a.owner_id = $2",
        UUID(position_id), owner,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"reason_code": "position_not_found", "message": "no such position"})
    registry = load_registry()
    specs = {spec.contract_code: spec for spec in registry.list()}
    fills_by_pos, events_by_pos = await _attach_children(pool, [position_id])
    mark, mark_status = await _current_mark(request, specs.get(str(row["instrument"])))
    card = _position_card(
        dict(row), specs=specs, mark=mark, mark_status=mark_status,
        fills=fills_by_pos.get(position_id, []), posting_events=events_by_pos.get(position_id, []),
    )
    return JSONResponse({"schema_version": SCHEMA_VERSION, "position": card}, headers={"Cache-Control": "no-store"})
