"""V3 session constructor: ``/api/v3/simulation/*`` — SimulationPlanV1 commands.

Scope (ТЗ V3 §5, P3): the plan an operator builds *before* any money-motive
event exists.  This module owns four things and nothing else:

  * a typed field catalog for ``SimulationPlanV1`` — every value comes from the
    *implemented* strategy/fee/slippage catalog (``paper_trading/strategy.py``,
    ``execution.py``, ``market.py``, ``risk.py``), never from a free string or
    an "as-promise" option the runner cannot honour (ТЗ §5: неподдерживаемое
    правило запрещено выбирать);
  * deterministic ``normalize → validate → plan_hash`` — Decimal strings,
    canonical JSON, sha256; identical input yields an identical hash;
  * the plan state machine ``draft → approved`` (approved versions are
    immutable; editing creates a new linked version) and the session state
    machine ``starting → running → closing → settlement_pending → closed``
    with ``rejected/expired`` kept separate, every transition stamped with a
    reason and a UTC timestamp;
  * the *start gates* demanded by MC-10: unexecutable strategy, wrong limits,
    incomplete replay history (``missing_intervals``) and a stale quote each
    block approve/start with an exact machine-readable reason code.

The financial truth stays in ``paper_v2_*`` (orders/fills/positions/postings);
``simulation_sessions`` is only the umbrella that binds days to one session_id
(P5 wires the runner; here sessions are created and frozen against a plan hash).

Storage is injectable: the real app builds a Postgres-backed store from
``app.state.pg_pool`` (tables from ``db/migrations/20260929_simulation_v3.sql``
— additive only); QA fixtures set ``app.state.simulation_store`` to an
in-memory implementation.  No pool and no store → 503 fail-closed, never a
silent demo plan.

P3 extraction: the field catalogs and ``normalize → check_plan_semantics →
estimate_worst_case_risk → plan_hash`` moved verbatim to
:mod:`trading_math.plan_contract`, which is mounted into the chatbot container
as well — one validator, therefore one ``plan_hash`` for dashboard and bot.
The names are re-exported below: import from either module, never re-implement.

P3b extraction: the plan/session *stores* moved verbatim to
:mod:`trading_math.simulation_store` for the same reason — one immutability
rule, one session state machine, one idempotency contract regardless of runtime.
The asyncpg pool is injected there, so this module stays the only place that
knows about FastAPI.

Owner identity: ``_owner_id`` returns the canonical
``uuid5(NAMESPACE_URL, "wored:owner:{...}")`` key shared with ``paper_api``,
``positions_workspace`` and the Telegram bot.  The former ``password:<name>`` /
``telegram:<id>`` strings were a second namespace for the same human; existing
rows were re-keyed by ``db/migrations/20261012_simulation_owner_unification.sql``.

P3c extraction: the ``normalize → semantics → market gate → coverage → hash``
sequence moved to :func:`trading_math.plan_validation.validate_plan`, and the
per-component freshness verdict behind ``_market_state`` lives in
:mod:`trading_math.market_state`.  Both files are mounted into the chatbot
container, so the violations and the ``plan_hash`` a Telegram card shows are the
output of this same pipeline (ТЗ P3 exit gate), with FastAPI-specific reads
still injected from here as providers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from instrument_registry import load_registry
from trading_math.market_state import MARKET_KEY_PREFIX, build_market_state
from trading_math.plan_contract import (  # noqa: F401  (re-exported contract)
    ALLOWED_SIDES,
    BUDGET_MAX,
    FEE_SCHEDULE_VERSIONS,
    FUNDING_MODEL_VERSIONS,
    LEVERAGE_MAX,
    LOSS_MAX,
    MARKET_DATA_POLICIES,
    PLAN_DEFAULTS,
    PLAN_FIELDS,
    PLAN_STATES,
    PLAN_TYPE,
    POSITIONS_MAX,
    RISK_PER_ORDER_MAX,
    SCHEMA_VERSION,
    SESSION_MODES,
    SESSION_TRANSITIONS,
    SLIPPAGE_MODEL_VERSIONS,
    START_SKEW_SECONDS,
    STOP_POLICIES,
    STRATEGY_CATALOG,
    TAKE_PROFIT_POLICIES,
    WINDOW_LIMITS_HOURS,
    Violation,
    _dec_str,
    _iso,
    _parse_bool,
    _parse_decimal,
    _parse_dt,
    _parse_int,
    check_plan_semantics,
    estimate_worst_case_risk,
    normalize_plan,
    plan_hash,
)
from trading_math.plan_validation import validate_plan
from trading_math.simulation_store import (  # noqa: F401  (re-exported stores)
    MemorySimulationStore,
    PlanImmutable,
    PlanRecord,
    PostgresSimulationStore,
    SessionRecord,
    _plan_to_dict,
    _session_to_dict,
)

# Everything between here and the endpoints is a re-export, not a definition.
# ``_dec_str``/``_parse_*`` are private-by-name but part of the contract the QA
# suites import from this module (tests/test_simulation_api.py), so they are
# listed explicitly rather than hidden behind ``import *``.
__all__ = [
    "ALLOWED_SIDES",
    "BUDGET_MAX",
    "FEE_SCHEDULE_VERSIONS",
    "FUNDING_MODEL_VERSIONS",
    "LEVERAGE_MAX",
    "LOSS_MAX",
    "MARKET_DATA_POLICIES",
    "MemorySimulationStore",
    "PLAN_DEFAULTS",
    "PLAN_FIELDS",
    "PLAN_STATES",
    "PLAN_TYPE",
    "POSITIONS_MAX",
    "PlanImmutable",
    "PlanRecord",
    "PostgresSimulationStore",
    "RISK_PER_ORDER_MAX",
    "SCHEMA_VERSION",
    "SESSION_MODES",
    "SESSION_TRANSITIONS",
    "SLIPPAGE_MODEL_VERSIONS",
    "START_SKEW_SECONDS",
    "STOP_POLICIES",
    "STRATEGY_CATALOG",
    "SessionRecord",
    "TAKE_PROFIT_POLICIES",
    "WINDOW_LIMITS_HOURS",
    "Violation",
    "_dec_str",
    "_iso",
    "_parse_bool",
    "_parse_decimal",
    "_parse_dt",
    "_parse_int",
    "_plan_to_dict",
    "_session_to_dict",
    "check_plan_semantics",
    "estimate_worst_case_risk",
    "normalize_plan",
    "plan_hash",
    "router",
]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/simulation", tags=["simulation-v3"])

# ── SimulationPlanV1 contract: imported above, deliberately not defined here ─
# Catalogs, PLAN_FIELDS/PLAN_DEFAULTS, the strict parsers, normalize_plan,
# check_plan_semantics, estimate_worst_case_risk and plan_hash live in
# trading_math/plan_contract.py — the same file the Telegram bot loads, so a
# plan cannot validate one way in the dashboard and another way in the chat.


# ── plan/session stores: imported above, deliberately not defined here ──
# PlanImmutable / PlanRecord / SessionRecord, the two dict presenters and both
# store implementations live in trading_math/simulation_store.py — the same file
# the Telegram bot loads, so a draft cannot be immutable in one runtime and
# mutable in the other.  The names are re-exported; import from either module.


# ── request plumbing ────────────────────────────────────────────────────────

try:  # the canonical expression is owned by the paper_trading adapter
    from paper_trading.adapter import owner_id_from_webui as _canonical_owner_id
except ImportError:  # pragma: no cover - only when /opt/paper_trading is absent
    import uuid as _uuid

    def _canonical_owner_id(session_username: str,
                            telegram_user_id: int | None = None) -> str:
        """Identical deterministic value to ``paper_trading.adapter``."""
        seed = telegram_user_id if telegram_user_id is not None else session_username
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"wored:owner:{seed}"))


def _owner_id(request: Request) -> str:
    """Server-side owner key for the plan domain.

    Returns the same ``uuid5(NAMESPACE_URL, "wored:owner:{...}")`` the paper
    domain, ``positions_workspace`` and the Telegram bot compute for the same
    human, so a plan drafted in chat and a plan approved in the dashboard
    belong to one owner instead of two namespaces.  Identity still comes only
    from the session — never from a body or query argument — and an
    unauthenticated request stays 401 (no implicit admin fallback here).
    """
    session = getattr(request, "session", {}) or {}
    if session.get("auth_type") == "telegram":
        user = session.get("telegram_user") or {}
        if isinstance(user.get("user_id"), int):
            return _canonical_owner_id("", telegram_user_id=user["user_id"])
    if session.get("authenticated") and session.get("username"):
        return _canonical_owner_id(session["username"])
    raise HTTPException(status_code=401, detail={"reason_code": "unauthenticated",
                                                 "message": "login required for simulation commands"})


def _require_csrf(request: Request, body: dict[str, Any]) -> None:
    """Session-cookie principals must echo the CSRF token (header or body);
    a Telegram init-data principal is not CSRF-reachable."""
    session = getattr(request, "session", {}) or {}
    if session.get("auth_type") == "telegram":
        return
    expected = session.get("csrf_token")
    given = request.headers.get("x-csrf-token") or body.get("csrf_token")
    if not expected or not given or not hmac.compare_digest(str(expected), str(given)):
        raise HTTPException(status_code=403, detail={"reason_code": "csrf_invalid",
                                                     "message": "missing or invalid CSRF token"})


async def _get_store(request: Request):
    store = getattr(request.app.state, "simulation_store", None)
    if store is not None:
        return store
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail={"reason_code": "simulation_store_unavailable",
                                                     "message": "no simulation store configured"})
    return PostgresSimulationStore(pool)


async def _market_state(request: Request, instrument_key: str) -> dict[str, Any] | None:
    """Same snapshot source as the V3 market read-model; None when redis is
    unavailable (the caller must fail-closed for live_paper gates)."""
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None
    spec = load_registry().get(instrument_key)
    key = f"{MARKET_KEY_PREFIX}:{spec.contract_code}"
    raw = await redis_client.get(key)
    payload = None
    if raw is not None:
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            payload = None
    return build_market_state(spec, payload, now=datetime.now(timezone.utc))


async def _coverage(request: Request, instrument_key: str, start: datetime, end: datetime) -> dict[str, Any]:
    """1-minute closed-candle coverage over [start, end) for replay plans.
    Requires the PG pool; without it the check is *unknown*, and replay start
    is blocked (fail-closed) — an unchecked history is not a complete one."""
    pool = getattr(request.app.state, "pg_pool", None)
    required = int((end - start).total_seconds() // 60)
    if pool is None:
        return {"required_intervals": required, "available_intervals": None, "reason_code": "history_source_unavailable"}
    spec = load_registry().get(instrument_key)
    rows = await pool.fetch(
        """
        SELECT count(*) AS n FROM trader_v1_perp_candles
        WHERE venue = $1 AND contract_code = $2 AND timeframe = '1min'
          AND open_time >= $3 AND close_time <= $4
        """,
        spec.venue, spec.contract_code, start, end,
    )
    available = int(rows[0]["n"]) if rows else 0
    out = {"required_intervals": required, "available_intervals": available}
    if available < required:
        out["reason_code"] = "missing_intervals"
    return out


def _json(obj: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


# ── endpoints ───────────────────────────────────────────────────────────────


@router.post("/plans/validate")
async def validate_plan_endpoint(request: Request) -> JSONResponse:
    """Dry-run a plan body through the shared pipeline.

    The sequence itself lives in :mod:`trading_math.plan_validation`; this
    handler only supplies the two reads that need FastAPI state — the live
    market snapshot and the candle coverage — so the answer cannot diverge from
    what the Telegram bot computes for the same body.
    """
    body = await request.json()
    _owner_id(request)  # auth scope even for dry-run validation
    raw_plan = body.get("plan") if isinstance(body, dict) else None
    result = await validate_plan(
        raw_plan,
        market=lambda key: _market_state(request, key),
        coverage=lambda key, start, end: _coverage(request, key, start, end),
        now=datetime.now(timezone.utc),
    )
    return _json(result)


@router.post("/plans")
async def create_plan(request: Request) -> JSONResponse:
    body = await request.json()
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    plan, violations = normalize_plan(body.get("plan") if isinstance(body, dict) else None)
    store = await _get_store(request)
    rec = await store.create_plan(
        owner_id=owner, plan=plan, plan_hash=plan_hash(plan) if plan else None,
        violations=[v.as_dict() for v in violations])
    return _json({"plan": _plan_to_dict(rec)}, status=201)


@router.get("/plans")
async def list_plans(request: Request, status: str | None = None, limit: int = 20) -> JSONResponse:
    owner = _owner_id(request)
    if status is not None and status not in PLAN_STATES:
        raise HTTPException(status_code=400, detail={"reason_code": "bad_status_filter",
                                                     "message": f"status must be one of {list(PLAN_STATES)}"})
    store = await _get_store(request)
    rows = await store.list_plans(owner, status, max(1, min(limit, 100)))
    return _json({"schema_version": SCHEMA_VERSION, "plans": [_plan_to_dict(r) for r in rows]})


@router.get("/plans/{plan_id}")
async def get_plan(request: Request, plan_id: str) -> JSONResponse:
    owner = _owner_id(request)
    store = await _get_store(request)
    try:
        UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    rec = await store.get_plan(plan_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    return _json({"schema_version": SCHEMA_VERSION, "plan": _plan_to_dict(rec)})


@router.post("/plans/{plan_id}")
async def edit_plan(request: Request, plan_id: str) -> JSONResponse:
    """Update a draft in place.  An approved plan can never be edited — the
    only path forward is a NEW version linked to the parent (MC-11)."""
    body = await request.json()
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    plan, violations = normalize_plan(body.get("plan") if isinstance(body, dict) else None)
    store = await _get_store(request)
    try:
        rec = await store.replace_draft(
            plan_id=plan_id, owner_id=owner, plan=plan,
            plan_hash=plan_hash(plan) if plan else None,
            violations=[v.as_dict() for v in violations])
    except KeyError:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    except PlanImmutable as exc:
        raise HTTPException(status_code=409, detail={"reason_code": "plan_immutable", "message": str(exc)})
    return _json({"plan": _plan_to_dict(rec)})


@router.post("/plans/{plan_id}/approve")
async def approve_plan(request: Request, plan_id: str) -> JSONResponse:
    """Re-validate server-side, then pin the hash.  A draft carrying any
    violation — unexecuted field, bad limit, stale quote — is refused with the
    exact reason codes (MC-10)."""
    body = await request.json() if (await request.body()) else {}
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    store = await _get_store(request)
    try:
        UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    rec = await store.get_plan(plan_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    if rec.status == "approved":
        raise HTTPException(status_code=409, detail={"reason_code": "plan_immutable", "message": "already approved"})

    violations = list(rec.violations)
    if rec.plan is None:
        violations = violations or [Violation("_plan", "plan_unparseable",
                                              "draft never passed normalization; fix fields before approval").as_dict()]
    else:
        # full re-validation at approve time: market and clock may have moved
        now = datetime.now(timezone.utc)
        for v in check_plan_semantics(rec.plan, now=now):
            violations.append(v.as_dict())
        if rec.plan.get("mode") == "live_paper":
            state = await _market_state(request, str(rec.plan["instrument_key"]))
            if state is None:
                violations.append(Violation("instrument_key", "quote_source_unavailable",
                                            "redis market source is not reachable").as_dict())
            elif not state["capabilities"]["can_enter"]:
                violations.append(Violation("instrument_key", "stale_quote",
                                            str(state["capabilities"].get("reason_code") or "market feed degraded")).as_dict())
        elif rec.plan.get("mode") == "historical_replay":
            cov = await _coverage(request, str(rec.plan["instrument_key"]),
                                  _parse_dt(rec.plan["start_at"]) or now,
                                  _parse_dt(rec.plan["end_at"]) or now)
            if cov.get("reason_code"):
                violations.append(Violation("start_at", str(cov["reason_code"]),
                                            "replay history is not fully covered").as_dict())
    if violations:
        return _json({"reason_code": "plan_not_approvable", "violations": violations}, status=422)

    try:
        rec = await store.approve_plan(plan_id=plan_id, owner_id=owner)
    except PlanImmutable as exc:
        raise HTTPException(status_code=409, detail={"reason_code": "plan_immutable", "message": str(exc)})
    return _json({"plan": _plan_to_dict(rec)})


@router.post("/sessions")
async def create_session(request: Request) -> JSONResponse:
    """Start a session from an *approved* plan only, with an idempotency key:
    a repeat with the same key+payload returns the same session; the same key
    with a different payload is a 409 (ТЗ §6)."""
    idem = request.headers.get("idempotency-key")
    body = await request.json()
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    plan_id = body.get("plan_id") if isinstance(body, dict) else None
    if not isinstance(idem, str) or not (8 <= len(idem) <= 128):
        raise HTTPException(status_code=400, detail={"reason_code": "idempotency_key_required",
                                                     "message": "Idempotency-Key header is required (8-128 chars)"})
    if not isinstance(plan_id, str):
        raise HTTPException(status_code=400, detail={"reason_code": "plan_id_required", "message": "plan_id is required"})
    store = await _get_store(request)
    try:
        UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    rec = await store.get_plan(plan_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail={"reason_code": "plan_not_found", "message": "no such plan"})
    if rec.status != "approved" or rec.plan is None or not rec.plan_hash:
        raise HTTPException(status_code=409, detail={"reason_code": "plan_not_approved",
                                                     "message": f"plan is {rec.status}; only approved plans can start a session"})
    plan = rec.plan
    # final start gate: quote freshness for live_paper at the moment of start
    if plan.get("mode") == "live_paper":
        state = await _market_state(request, str(plan["instrument_key"]))
        if state is None:
            raise HTTPException(status_code=503, detail={"reason_code": "quote_source_unavailable",
                                                         "message": "redis market source is not reachable"})
        if not state["capabilities"]["can_enter"]:
            raise HTTPException(status_code=409, detail={"reason_code": "stale_quote",
                                                         "message": str(state["capabilities"].get("reason_code") or "market feed degraded"),
                                                         "as_of": state["as_of"]})
    start = _parse_dt(plan["start_at"]) or datetime.now(timezone.utc)
    end = _parse_dt(plan["end_at"]) or datetime.now(timezone.utc)
    fingerprint = hashlib.sha256(json.dumps(
        {"plan_id": plan_id, "plan_hash": rec.plan_hash}, sort_keys=True).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    session = SessionRecord(
        session_id=str(uuid4()), owner_id=owner, plan_id=plan_id, plan_hash=rec.plan_hash,
        mode=str(plan["mode"]), instrument_key=str(plan["instrument_key"]),
        start_at=start, end_at=end, timezone=str(plan.get("timezone") or "Etc/UTC"),
        status="starting", idempotency_key=idem, request_fingerprint=fingerprint,
        state_log=[{"from": "approved", "to": "starting", "reason": "session_created", "at": _iso(now)}],
        created_at=now, updated_at=now,
    )
    try:
        created = await store.create_session(session)
    except PlanImmutable:
        raise HTTPException(status_code=409, detail={"reason_code": "idempotency_conflict",
                                                     "message": "same Idempotency-Key with a different payload"})
    if created.session_id != session.session_id:
        return _json({"session": _session_to_dict(created), "replayed": True})
    await store.attach_session(plan_id, created.session_id)
    return _json({"session": _session_to_dict(created), "replayed": False}, status=201)


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> JSONResponse:
    owner = _owner_id(request)
    store = await _get_store(request)
    try:
        UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"reason_code": "session_not_found", "message": "no such session"})
    rec = await store.get_session(session_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail={"reason_code": "session_not_found", "message": "no such session"})
    return _json({"schema_version": SCHEMA_VERSION, "session": _session_to_dict(rec)})


@router.post("/sessions/{session_id}/transitions")
async def transition_session(request: Request, session_id: str) -> JSONResponse:
    """Owner-scoped state-machine advance with a mandatory reason — the
    runner (P5) is the intended driver; illegal edges are refused (ТЗ §5)."""
    body = await request.json()
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    to = body.get("to") if isinstance(body, dict) else None
    reason = body.get("reason") if isinstance(body, dict) else None
    if not isinstance(to, str) or not isinstance(reason, str) or not reason.strip():
        raise HTTPException(status_code=400, detail={"reason_code": "transition_fields_required",
                                                     "message": "to and a non-empty reason are required"})
    store = await _get_store(request)
    try:
        rec = await store.transition_session(session_id=session_id, owner_id=owner, to=to, reason=reason.strip())
    except KeyError:
        raise HTTPException(status_code=404, detail={"reason_code": "session_not_found", "message": "no such session"})
    except PlanImmutable as exc:
        raise HTTPException(status_code=409, detail={"reason_code": "illegal_transition",
                                                     "message": str(exc),
                                                     "allowed": sorted(SESSION_TRANSITIONS.get(to, set()))})
    return _json({"session": _session_to_dict(rec)})
