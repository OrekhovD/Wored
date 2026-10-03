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
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from instrument_registry import InstrumentNotFound, load_registry
from market_workspace import MARKET_KEY_PREFIX, build_market_state

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/simulation", tags=["simulation-v3"])

SCHEMA_VERSION = 1
PLAN_TYPE = "SimulationPlanV1"

# ── implemented catalogs (grounded in runtime code, ТЗ §5) ──────────────────
# strategy: paper_trading/strategy.py BaselineV1Strategy.VERSION
STRATEGY_CATALOG: dict[str, tuple[str, ...]] = {"baseline_v1": ("default",)}
# fee: paper_trading/execution.py FEE_RATE = 0.0006 taker
FEE_SCHEDULE_VERSIONS = ("taker_6bps_v1",)
# slippage: paper_trading/market.py DEFAULT_SLIPPAGE_BPS = 2, adverse direction
SLIPPAGE_MODEL_VERSIONS = ("adverse_2bps_v1",)
# funding: HTX mark-price funding stream (collector/htx/perpetual_market.py)
FUNDING_MODEL_VERSIONS = ("htx_mark_funding_v1",)
# risk/liquidation triggers evaluate on mark (paper_trading/risk.py)
STOP_POLICIES = ("mark",)
TAKE_PROFIT_POLICIES = ("mark", "none")
MARKET_DATA_POLICIES = ("closed_candles_only",)
SESSION_MODES = ("live_paper", "historical_replay")  # scenario: separate P-scope
ALLOWED_SIDES = ("long", "short")

# window bounds per mode, hours (ТЗ §5: 1–168 live, 1–720 replay)
WINDOW_LIMITS_HOURS = {"live_paper": (1, 168), "historical_replay": (1, 720)}
# budgets/limits are USDT Decimals; caps are sanity rails, not product policy
BUDGET_MAX = Decimal("100000")
RISK_PER_ORDER_MAX = Decimal("1000")
LOSS_MAX = Decimal("100000")
LEVERAGE_MAX = 10
POSITIONS_MAX = 10

# live start skew: exchange/commissioning clock may differ by minutes at most
START_SKEW_SECONDS = 300

_HHMM_RANGE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")

# ordered for canonical serialization; hash stability depends on this list
PLAN_FIELDS = (
    "instrument_key", "mode", "start_at", "end_at", "timezone",
    "manual_budget", "auto_budget",
    "strategy_id", "strategy_version", "style", "allowed_sides",
    "max_leverage", "max_risk_per_order", "max_daily_loss", "max_session_loss",
    "max_total_exposure", "max_open_positions", "cooldown_minutes",
    "trading_hours", "stop_policy", "take_profit_policy", "close_at_end",
    "fee_schedule_version", "slippage_model_version", "funding_model_version",
    "market_data_policy", "seed",
)

PLAN_DEFAULTS: dict[str, Any] = {
    "timezone": "Etc/UTC",
    "style": "default",
    "cooldown_minutes": 5,
    "trading_hours": "any",
    "stop_policy": "mark",
    "take_profit_policy": "mark",
    "close_at_end": True,
    "fee_schedule_version": FEE_SCHEDULE_VERSIONS[0],
    "slippage_model_version": SLIPPAGE_MODEL_VERSIONS[0],
    "funding_model_version": FUNDING_MODEL_VERSIONS[0],
    "market_data_policy": MARKET_DATA_POLICIES[0],
    "seed": None,
}

# session state machine (ТЗ §5); plan states handled separately
SESSION_TRANSITIONS: dict[str, set[str]] = {
    "starting": {"running", "rejected", "expired"},
    "running": {"closing", "expired"},
    "closing": {"settlement_pending"},
    "settlement_pending": {"closed"},
    "closed": set(),
    "rejected": set(),
    "expired": set(),
}
PLAN_STATES = ("draft", "approved", "superseded", "rejected")


# ── small strict parsers ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


def _dec_str(value: Any) -> str | None:
    """Canonical Decimal string: fixed-point, no exponent, no trailing noise."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite():
        return None
    text = format(d.normalize(), "f")
    # normalize(Decimal("1000")) yields "1E+3"; render plain fixed-point
    if "E" in text or "e" in text:
        text = format(d, "f")
    return text


def _parse_decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(str(value))
    except (TypeError, ValueError):
        return None
    if f != int(f):
        return None
    return int(f)


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # naive times are refused outright: no silent UTC guess
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ── normalize ───────────────────────────────────────────────────────────────


def normalize_plan(raw: Any) -> tuple[dict[str, Any] | None, list[Violation]]:
    """Type-and-range check every SimulationPlanV1 field; return a canonical
    plan (Decimal strings, ISO-UTC times) or per-field violations.  Unknown
    keys are a violation — silent drops would let a plan "look" complete while
    carrying fields the runner will never honour."""
    violations: list[Violation] = []
    if not isinstance(raw, dict):
        return None, [Violation("_plan", "bad_payload", "plan must be a JSON object")]

    for key in raw:
        if key not in PLAN_FIELDS:
            violations.append(Violation(str(key), "unknown_field", "field is not part of SimulationPlanV1"))

    plan: dict[str, Any] = {}

    def bad(field: str, code: str, msg: str) -> None:
        violations.append(Violation(field, code, msg))

    # instrument — registry only (spot keys are not registered → unknown_instrument)
    instrument_key = raw.get("instrument_key")
    if not isinstance(instrument_key, str) or not instrument_key.strip():
        bad("instrument_key", "field_required", "instrument_key is required")
    else:
        try:
            load_registry().get(instrument_key.strip())
            plan["instrument_key"] = instrument_key.strip()
        except InstrumentNotFound:
            bad("instrument_key", "unknown_instrument", "not in the perpetual registry")

    def enum_field(field: str, allowed: tuple[str, ...], required: bool = True) -> None:
        value = raw.get(field, PLAN_DEFAULTS.get(field))
        if value is None:
            if required:
                bad(field, "field_required", f"{field} is required")
            return
        if not isinstance(value, str) or value not in allowed:
            bad(field, "field_enum", f"{field} must be one of {list(allowed)}")
            return
        plan[field] = value

    def decimal_field(field: str, lo: Decimal, hi: Decimal, inclusive_lo: bool = False) -> None:
        value = raw.get(field)
        if value is None or value == "":
            bad(field, "field_required", f"{field} is required (Decimal USDT)")
            return
        d = _parse_decimal(value)
        if d is None:
            bad(field, "field_type", f"{field} must be a Decimal")
            return
        if (d < lo or (not inclusive_lo and d == lo) or d > hi):
            bad(field, "field_range", f"{field} must be in ({lo}; {hi}] USDT" if not inclusive_lo else f"{field} must be in [{lo}; {hi}] USDT")
            return
        plan[field] = _dec_str(d)

    def int_field(field: str, lo: int, hi: int, required: bool = True) -> None:
        value = raw.get(field)
        if value is None:
            if required:
                bad(field, "field_required", f"{field} is required")
            elif field in PLAN_DEFAULTS:
                plan[field] = PLAN_DEFAULTS[field]
            return
        parsed = _parse_int(value)
        if parsed is None:
            bad(field, "field_type", f"{field} must be an integer")
            return
        if parsed < lo or parsed > hi:
            bad(field, "field_range", f"{field} must be between {lo} and {hi}")
            return
        plan[field] = parsed

    enum_field("mode", SESSION_MODES)
    if raw.get("mode") == "scenario":
        bad("mode", "mode_not_supported", "scenario mode is introduced separately; only live_paper and historical_replay are selectable now")

    # timezone: IANA name, validated — display timezone, storage stays UTC
    tz_name = raw.get("timezone", PLAN_DEFAULTS["timezone"])
    if not isinstance(tz_name, str):
        bad("timezone", "field_type", "timezone must be an IANA name")
    else:
        try:
            ZoneInfo(tz_name)
            plan["timezone"] = tz_name
        except (ZoneInfoNotFoundError, ValueError):
            bad("timezone", "field_enum", f"unknown IANA timezone {tz_name!r}")

    # window
    start_dt = _parse_dt(raw.get("start_at"))
    if start_dt is None:
        bad("start_at", "field_type", "start_at must be an ISO-8601 datetime with timezone")
    end_dt = _parse_dt(raw.get("end_at"))
    if end_dt is None:
        bad("end_at", "field_type", "end_at must be an ISO-8601 datetime with timezone")
    if start_dt is not None:
        plan["start_at"] = _iso(start_dt)
    if end_dt is not None:
        plan["end_at"] = _iso(end_dt)

    decimal_field("manual_budget", Decimal(0), BUDGET_MAX)
    decimal_field("auto_budget", Decimal(0), BUDGET_MAX)

    # strategy catalog: version must match the implemented strategy id
    strategy_id = raw.get("strategy_id")
    if not isinstance(strategy_id, str) or strategy_id not in STRATEGY_CATALOG:
        bad("strategy_id", "field_enum", f"strategy_id must be one of {list(STRATEGY_CATALOG)}")
        enum_field("style", ("default",))  # keep field order stable for messages
    else:
        plan["strategy_id"] = strategy_id
        enum_field("style", STRATEGY_CATALOG[strategy_id])
    version = raw.get("strategy_version")
    if not isinstance(version, str) or not version:
        bad("strategy_version", "field_required", "strategy_version is required")
    elif strategy_id in STRATEGY_CATALOG and version != strategy_id:
        bad("strategy_version", "field_enum", f"strategy_version must equal strategy_id for the catalog ({strategy_id})")
    else:
        plan["strategy_version"] = version

    sides = raw.get("allowed_sides")
    if not isinstance(sides, list) or not sides:
        bad("allowed_sides", "field_required", "allowed_sides must be a non-empty list")
    else:
        norm = sorted({str(s).lower() for s in sides})
        if any(s not in ALLOWED_SIDES for s in norm):
            bad("allowed_sides", "field_enum", f"allowed_sides entries must be within {list(ALLOWED_SIDES)}")
        else:
            plan["allowed_sides"] = norm

    int_field("max_leverage", 1, LEVERAGE_MAX)
    decimal_field("max_risk_per_order", Decimal(0), RISK_PER_ORDER_MAX)
    decimal_field("max_daily_loss", Decimal(0), LOSS_MAX)
    decimal_field("max_session_loss", Decimal(0), LOSS_MAX)
    decimal_field("max_total_exposure", Decimal(0), LOSS_MAX)
    int_field("max_open_positions", 1, POSITIONS_MAX)
    int_field("cooldown_minutes", 0, 1440, required=False)

    hours = raw.get("trading_hours", PLAN_DEFAULTS["trading_hours"])
    if not isinstance(hours, str) or (hours != "any" and not _HHMM_RANGE.match(hours)):
        bad("trading_hours", "field_enum", "trading_hours must be 'any' or 'HH:MM-HH:MM'")
    else:
        plan["trading_hours"] = hours

    enum_field("stop_policy", STOP_POLICIES)
    enum_field("take_profit_policy", TAKE_PROFIT_POLICIES)

    close_at_end = _parse_bool(raw.get("close_at_end", True))
    if close_at_end is None:
        bad("close_at_end", "field_type", "close_at_end must be a boolean")
    elif close_at_end is not True:
        # forced close at session end is the only implemented rule (ТЗ §5)
        bad("close_at_end", "field_enum", "close_at_end=false is not an implemented rule; the choice is disabled")
    else:
        plan["close_at_end"] = True

    enum_field("fee_schedule_version", FEE_SCHEDULE_VERSIONS)
    enum_field("slippage_model_version", SLIPPAGE_MODEL_VERSIONS)
    enum_field("funding_model_version", FUNDING_MODEL_VERSIONS)
    enum_field("market_data_policy", MARKET_DATA_POLICIES)

    seed = raw.get("seed")
    if seed is None:
        if raw.get("mode") == "historical_replay":
            bad("seed", "field_required", "seed is required for historical_replay (fixed data version)")
        else:
            plan["seed"] = None
    else:
        parsed = _parse_int(seed)
        if parsed is None or parsed < 0 or parsed > 2**31 - 1:
            bad("seed", "field_range", "seed must be an integer in [0, 2^31-1]")
        else:
            plan["seed"] = parsed

    if violations:
        return None, violations
    return plan, []


# ── cross-field + window validation ─────────────────────────────────────────


def check_plan_semantics(plan: dict[str, Any], *, now: datetime) -> list[Violation]:
    """Relation checks that individual field ranges cannot express."""
    v: list[Violation] = []
    start = _parse_dt(plan.get("start_at"))
    end = _parse_dt(plan.get("end_at"))
    mode = plan.get("mode")
    if start and end:
        if end <= start:
            v.append(Violation("end_at", "window_invalid", "end_at must be after start_at"))
        else:
            hours = (end - start).total_seconds() / 3600
            lo, hi = WINDOW_LIMITS_HOURS.get(str(mode), (1, 168))
            if hours < lo or hours > hi:
                v.append(Violation(
                    "end_at", "window_range",
                    f"{mode} window must be {lo}-{hi} hours, got {round(hours, 2)}",
                ))
        if mode == "historical_replay" and end > now:
            v.append(Violation("end_at", "replay_window_open", "historical_replay requires a fully closed interval (end_at <= now)"))
        if mode == "live_paper" and start < now - timedelta(seconds=START_SKEW_SECONDS):
            v.append(Violation("start_at", "live_window_past", "live_paper cannot start more than 5 minutes in the past"))

    manual = _parse_decimal(plan.get("manual_budget"))
    auto = _parse_decimal(plan.get("auto_budget"))
    risk = _parse_decimal(plan.get("max_risk_per_order"))
    daily = _parse_decimal(plan.get("max_daily_loss"))
    session_loss = _parse_decimal(plan.get("max_session_loss"))
    exposure = _parse_decimal(plan.get("max_total_exposure"))

    if risk is not None and manual is not None and auto is not None:
        if risk > min(manual, auto):
            v.append(Violation("max_risk_per_order", "risk_exceeds_budget", "per-order risk must not exceed the smaller account budget"))
    if daily is not None and session_loss is not None and session_loss < daily:
        v.append(Violation("max_session_loss", "loss_hierarchy", "max_session_loss must be at least max_daily_loss"))
    if session_loss is not None and manual is not None and auto is not None:
        if session_loss > manual + auto:
            v.append(Violation("max_session_loss", "loss_exceeds_budgets", "session loss cap exceeds combined manual+auto budgets"))
    if exposure is not None and risk is not None and exposure < risk:
        v.append(Violation("max_total_exposure", "exposure_below_risk", "total exposure cap must be at least the per-order risk"))
    return v


def estimate_worst_case_risk(plan: dict[str, Any]) -> dict[str, Any]:
    """Deterministic 'оценка худшего допустимого убытка' shown before approval:
    the binding of the loss caps against the positions/risk multiplication."""
    risk = _parse_decimal(plan.get("max_risk_per_order")) or Decimal(0)
    positions = _parse_int(plan.get("max_open_positions")) or 0
    daily = _parse_decimal(plan.get("max_daily_loss")) or Decimal(0)
    session_loss = _parse_decimal(plan.get("max_session_loss")) or Decimal(0)
    per_wave = risk * positions
    worst = min(session_loss, per_wave, daily * positions) if positions else min(session_loss, per_wave)
    return {
        "worst_allowed_loss_usdt": _dec_str(worst),
        "drivers": {
            "max_session_loss_usdt": _dec_str(session_loss),
            "positions_x_risk_usdt": _dec_str(per_wave),
            "daily_x_positions_usdt": _dec_str(daily * positions),
        },
        "method": "min(session_loss, open_positions*risk_per_order, daily_loss*open_positions)",
    }


def plan_hash(plan: dict[str, Any]) -> str:
    """sha256 over canonical JSON of the normalized plan (key-sorted, fixed
    field order, Decimal as canonical string).  Approve pins it; the session
    stores it; any byte change in the plan changes the hash (MC-11)."""
    canonical = {k: plan.get(k) for k in PLAN_FIELDS}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── stores ──────────────────────────────────────────────────────────────────


class PlanImmutable(Exception):
    """Raised when an edit/approve transition violates immutability."""


@dataclass
class PlanRecord:
    plan_id: str
    owner_id: str
    status: str  # draft|approved|superseded|rejected
    plan: dict[str, Any] | None
    plan_hash: str | None
    violations: list[dict[str, str]]
    version: int
    parent_id: str | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    consumed_by: list[str] | None = None


@dataclass
class SessionRecord:
    session_id: str
    owner_id: str
    plan_id: str
    plan_hash: str
    mode: str
    instrument_key: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    idempotency_key: str
    request_fingerprint: str
    state_log: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


def _plan_to_dict(rec: PlanRecord) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_type": PLAN_TYPE,
        "plan_id": rec.plan_id,
        "owner_id": rec.owner_id,
        "status": rec.status,
        "version": rec.version,
        "parent_id": rec.parent_id,
        "plan": rec.plan,
        "plan_hash": rec.plan_hash,
        "violations": rec.violations,
        "approved_at": _iso(rec.approved_at) if rec.approved_at else None,
        "consumed_by_sessions": rec.consumed_by or [],
        "created_at": _iso(rec.created_at),
        "updated_at": _iso(rec.updated_at),
    }


def _session_to_dict(rec: SessionRecord) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": rec.session_id,
        "owner_id": rec.owner_id,
        "plan_id": rec.plan_id,
        "plan_hash": rec.plan_hash,
        "mode": rec.mode,
        "instrument_key": rec.instrument_key,
        "start_at": _iso(rec.start_at),
        "end_at": _iso(rec.end_at),
        "timezone": rec.timezone,
        "status": rec.status,
        "state_log": rec.state_log,
        "created_at": _iso(rec.created_at),
        "updated_at": _iso(rec.updated_at),
    }


class MemorySimulationStore:
    """QA/dev store — same semantics as the Postgres one, lost on restart."""

    def __init__(self) -> None:
        self.plans: dict[str, PlanRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self._by_owner_key: dict[tuple[str, str], str] = {}  # (owner, idem) -> session_id

    async def create_plan(self, *, owner_id: str, plan: dict[str, Any] | None,
                          plan_hash: str | None, violations: list[dict[str, str]],
                          parent_id: str | None = None) -> PlanRecord:
        now = datetime.now(timezone.utc)
        version = 1
        if parent_id and parent_id in self.plans:
            version = self.plans[parent_id].version + 1
        rec = PlanRecord(str(uuid4()), owner_id, "draft", plan, plan_hash,
                         violations, version, parent_id, now, now)
        self.plans[rec.plan_id] = rec
        return rec

    async def get_plan(self, plan_id: str, owner_id: str) -> PlanRecord | None:
        rec = self.plans.get(plan_id)
        return rec if rec and rec.owner_id == owner_id else None

    async def list_plans(self, owner_id: str, status: str | None, limit: int) -> list[PlanRecord]:
        rows = [p for p in self.plans.values() if p.owner_id == owner_id
                and (status is None or p.status == status)]
        rows.sort(key=lambda p: p.created_at, reverse=True)
        return rows[:limit]

    async def replace_draft(self, *, plan_id: str, owner_id: str, plan: dict[str, Any] | None,
                            plan_hash: str | None, violations: list[dict[str, str]]) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status != "draft":
            raise PlanImmutable(f"plan {plan_id} is {rec.status}; drafts only")
        rec.plan, rec.plan_hash, rec.violations = plan, plan_hash, violations
        rec.updated_at = datetime.now(timezone.utc)
        return rec

    async def approve_plan(self, *, plan_id: str, owner_id: str) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status == "approved":
            raise PlanImmutable("already approved")
        if rec.status != "draft":
            raise PlanImmutable(f"plan is {rec.status}; drafts only")
        rec.status = "approved"
        rec.approved_at = datetime.now(timezone.utc)
        rec.updated_at = rec.approved_at
        return rec

    async def attach_session(self, plan_id: str, session_id: str) -> None:
        rec = self.plans.get(plan_id)
        if rec is not None:
            rec.consumed_by = (rec.consumed_by or []) + [session_id]

    async def create_session(self, rec: SessionRecord) -> SessionRecord:
        key = (rec.owner_id, rec.idempotency_key)
        existing = self._by_owner_key.get(key)
        if existing:
            prior = self.sessions[existing]
            if prior.request_fingerprint != rec.request_fingerprint:
                raise PlanImmutable("idempotency_conflict")  # reused as 409 signal
            return prior
        self.sessions[rec.session_id] = rec
        self._by_owner_key[key] = rec.session_id
        return rec

    async def get_session(self, session_id: str, owner_id: str) -> SessionRecord | None:
        rec = self.sessions.get(session_id)
        return rec if rec and rec.owner_id == owner_id else None

    async def transition_session(self, *, session_id: str, owner_id: str, to: str,
                                 reason: str) -> SessionRecord:
        rec = await self.get_session(session_id, owner_id)
        if rec is None:
            raise KeyError(session_id)
        if to not in SESSION_TRANSITIONS.get(rec.status, set()):
            raise PlanImmutable(f"illegal transition {rec.status} → {to}")
        now = datetime.now(timezone.utc)
        rec.state_log = rec.state_log + [{
            "from": rec.status, "to": to, "reason": reason, "at": _iso(now),
        }]
        rec.status = to
        rec.updated_at = now
        return rec


class PostgresSimulationStore:
    """PG-backed store over the additive simulation_v3 migration.  JSONB keeps
    the normalized plan byte-exact; plan_hash pins what approve froze so old
    versions can never be quietly rewritten (MC-11)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @staticmethod
    def _row_plan(row: Any) -> PlanRecord:
        plan = row["plan"] if isinstance(row["plan"], dict) else json.loads(row["plan"] or "null")
        return PlanRecord(
            plan_id=str(row["id"]), owner_id=row["owner_id"], status=row["status"],
            plan=plan, plan_hash=row["plan_hash"],
            violations=row["violations"] if isinstance(row["violations"], list) else json.loads(row["violations"] or "[]"),
            version=row["version"], parent_id=str(row["parent_id"]) if row["parent_id"] else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            consumed_by=[str(s) for s in (row["consumed_by_sessions"] or [])],
        )

    async def create_plan(self, *, owner_id: str, plan: dict[str, Any] | None,
                          plan_hash: str | None, violations: list[dict[str, str]],
                          parent_id: str | None = None) -> PlanRecord:
        pid = uuid4()
        version = 1
        if parent_id:
            row = await self.pool.fetchrow(
                "SELECT version FROM simulation_plan_versions WHERE id=$1 AND owner_id=$2",
                UUID(parent_id), owner_id)
            if row:
                version = int(row["version"]) + 1
        await self.pool.execute(
            """
            INSERT INTO simulation_plan_versions
              (id, owner_id, status, plan, plan_hash, violations, version, parent_id)
            VALUES ($1, $2, 'draft', $3::jsonb, $4, $5::jsonb, $6, $7)
            """,
            pid, owner_id,
            json.dumps(plan) if plan is not None else None,
            plan_hash, json.dumps(violations), version,
            UUID(parent_id) if parent_id else None,
        )
        rec = await self.get_plan(str(pid), owner_id)
        assert rec is not None
        return rec

    async def get_plan(self, plan_id: str, owner_id: str) -> PlanRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_plan_versions WHERE id=$1 AND owner_id=$2",
            UUID(plan_id), owner_id)
        return self._row_plan(row) if row else None

    async def list_plans(self, owner_id: str, status: str | None, limit: int) -> list[PlanRecord]:
        if status:
            rows = await self.pool.fetch(
                "SELECT * FROM simulation_plan_versions WHERE owner_id=$1 AND status=$2 "
                "ORDER BY created_at DESC LIMIT $3", owner_id, status, limit)
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM simulation_plan_versions WHERE owner_id=$1 "
                "ORDER BY created_at DESC LIMIT $2", owner_id, limit)
        return [self._row_plan(r) for r in rows]

    async def replace_draft(self, *, plan_id: str, owner_id: str, plan: dict[str, Any] | None,
                            plan_hash: str | None, violations: list[dict[str, str]]) -> PlanRecord:
        result = await self.pool.execute(
            "UPDATE simulation_plan_versions SET plan=$3::jsonb, plan_hash=$4, "
            "violations=$5::jsonb, updated_at=now() "
            "WHERE id=$1 AND owner_id=$2 AND status='draft'",
            UUID(plan_id), owner_id,
            json.dumps(plan) if plan is not None else None, plan_hash,
            json.dumps(violations))
        if result.endswith("0"):
            rec = await self.get_plan(plan_id, owner_id)
            if rec is None:
                raise KeyError(plan_id)
            raise PlanImmutable(f"plan {plan_id} is {rec.status}; drafts only")
        rec = await self.get_plan(plan_id, owner_id)
        assert rec is not None
        return rec

    async def approve_plan(self, *, plan_id: str, owner_id: str) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status != "draft":
            raise PlanImmutable("already approved" if rec.status == "approved" else f"plan is {rec.status}; drafts only")
        await self.pool.execute(
            "UPDATE simulation_plan_versions SET status='approved', approved_at=now(), "
            "updated_at=now() WHERE id=$1 AND owner_id=$2 AND status='draft'",
            UUID(plan_id), owner_id)
        rec = await self.get_plan(plan_id, owner_id)
        assert rec is not None
        return rec

    async def attach_session(self, plan_id: str, session_id: str) -> None:
        await self.pool.execute(
            "UPDATE simulation_plan_versions SET consumed_by_sessions = "
            "consumed_by_sessions || array[$2::uuid] WHERE id=$1",
            UUID(plan_id), UUID(session_id))

    async def create_session(self, rec: SessionRecord) -> SessionRecord:
        try:
            await self.pool.execute(
                """
                INSERT INTO simulation_sessions
                  (id, owner_id, plan_id, plan_hash, mode, instrument_key,
                   start_at, end_at, timezone, status, idempotency_key,
                   request_fingerprint, state_log)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
                """,
                UUID(rec.session_id), rec.owner_id, UUID(rec.plan_id), rec.plan_hash,
                rec.mode, rec.instrument_key, rec.start_at, rec.end_at, rec.timezone,
                rec.status, rec.idempotency_key, rec.request_fingerprint,
                json.dumps(rec.state_log))
        except Exception as exc:  # unique (owner_id, idempotency_key) collision
            if "uq_simulation_idempotency" not in str(exc):
                raise
            row = await self.pool.fetchrow(
                "SELECT request_fingerprint FROM simulation_sessions WHERE owner_id=$1 AND idempotency_key=$2",
                rec.owner_id, rec.idempotency_key)
            if row and row["request_fingerprint"] == rec.request_fingerprint:
                existing = await self.get_session_by_key(rec.owner_id, rec.idempotency_key)
                assert existing is not None
                return existing
            raise PlanImmutable("idempotency_conflict") from exc
        return rec

    async def get_session_by_key(self, owner_id: str, idem: str) -> SessionRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_sessions WHERE owner_id=$1 AND idempotency_key=$2",
            owner_id, idem)
        return self._row_session(row) if row else None

    @staticmethod
    def _row_session(row: Any) -> SessionRecord:
        log_raw = row["state_log"]
        return SessionRecord(
            session_id=str(row["id"]), owner_id=row["owner_id"],
            plan_id=str(row["plan_id"]), plan_hash=row["plan_hash"],
            mode=row["mode"], instrument_key=row["instrument_key"],
            start_at=row["start_at"], end_at=row["end_at"], timezone=row["timezone"],
            status=row["status"], idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            state_log=log_raw if isinstance(log_raw, list) else json.loads(log_raw or "[]"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    async def get_session(self, session_id: str, owner_id: str) -> SessionRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_sessions WHERE id=$1 AND owner_id=$2",
            UUID(session_id), owner_id)
        return self._row_session(row) if row else None

    async def transition_session(self, *, session_id: str, owner_id: str, to: str,
                                 reason: str) -> SessionRecord:
        rec = await self.get_session(session_id, owner_id)
        if rec is None:
            raise KeyError(session_id)
        if to not in SESSION_TRANSITIONS.get(rec.status, set()):
            raise PlanImmutable(f"illegal transition {rec.status} → {to}")
        entry = {"from": rec.status, "to": to, "reason": reason,
                 "at": _iso(datetime.now(timezone.utc))}
        await self.pool.execute(
            "UPDATE simulation_sessions SET status=$3, updated_at=now(), "
            "state_log = state_log || $4::jsonb WHERE id=$1 AND owner_id=$2 AND status=$5",
            UUID(session_id), owner_id, to, json.dumps([entry]), rec.status)
        rec = await self.get_session(session_id, owner_id)
        assert rec is not None
        return rec


# ── request plumbing ────────────────────────────────────────────────────────


def _owner_id(request: Request) -> str:
    session = getattr(request, "session", {}) or {}
    if session.get("auth_type") == "telegram":
        user = session.get("telegram_user") or {}
        if isinstance(user.get("user_id"), int):
            return f"telegram:{user['user_id']}"
    if session.get("authenticated") and session.get("username"):
        return f"password:{session['username']}"
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
    body = await request.json()
    _owner_id(request)  # auth scope even for dry-run validation
    raw_plan = body.get("plan") if isinstance(body, dict) else None
    plan, violations = normalize_plan(raw_plan)
    market: dict[str, Any] | None = None
    if plan is not None:
        for v in check_plan_semantics(plan, now=datetime.now(timezone.utc)):
            violations.append(v)
        # market gate for live_paper: a stale quote blocks approval/start (MC-10)
        if plan.get("mode") == "live_paper":
            state = await _market_state(request, str(plan["instrument_key"]))
            if state is None:
                violations.append(Violation("instrument_key", "quote_source_unavailable",
                                            "redis market source is not reachable; live_paper cannot be validated"))
            else:
                market = {"snapshot_id": state["snapshot_id"], "as_of": state["as_of"],
                          "quality_worst": state["quality"]["worst"],
                          "can_enter": state["capabilities"]["can_enter"]}
                if not state["capabilities"]["can_enter"]:
                    violations.append(Violation("instrument_key", "stale_quote",
                                                str(state["capabilities"].get("reason_code") or "market feed degraded")))
        elif plan.get("mode") == "historical_replay":
            cov = await _coverage(request, str(plan["instrument_key"]),
                                  _parse_dt(plan["start_at"]) or datetime.now(timezone.utc),
                                  _parse_dt(plan["end_at"]) or datetime.now(timezone.utc))
            market = {"coverage": cov}
            if cov.get("reason_code"):
                violations.append(Violation("start_at", str(cov["reason_code"]),
                                            "replay history is not fully covered for the requested window"))
    codes = [v.as_dict() for v in violations]
    ok = plan is not None and not codes
    return _json({
        "schema_version": SCHEMA_VERSION,
        "plan_type": PLAN_TYPE,
        "normalized": plan,
        "violations": codes,
        "worst_case_risk": estimate_worst_case_risk(plan) if plan is not None else None,
        "market": market,
        "plan_hash": plan_hash(plan) if ok else None,
        "can_approve": ok,
    })


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
