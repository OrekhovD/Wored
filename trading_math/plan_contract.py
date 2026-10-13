"""Deterministic ``SimulationPlanV1`` contract — the single validator (P3 extraction).

Moved verbatim out of ``webui/simulation_api.py`` so that the dashboard and the
Telegram bot normalise, validate and hash a plan with *one* implementation: the
hash an operator approves in the WebUI must be the hash the bot shows, or the
"same numbers as the WebUI" promise (ТЗ §0, MC-11) is false by construction.

Scope and rules of this module:

  * a typed field catalog — every value comes from the *implemented*
    strategy/fee/slippage catalog (``paper_trading/strategy.py``,
    ``execution.py``, ``market.py``, ``risk.py``), never from a free string or
    an "as-promise" option the runner cannot honour (ТЗ §5: неподдерживаемое
    правило запрещено выбирать);
  * deterministic ``normalize → validate → plan_hash`` — Decimal strings,
    canonical JSON, sha256; identical input yields an identical hash;
  * the session/plan state-machine tables the callers gate on.

What deliberately stays OUT (webui-side): storage (``PlanRecord``/``SessionRecord``
and both stores), the FastAPI router, ``_owner_id``/CSRF, the market-state and
coverage start gates.  Those need a request, a pool or a Redis key and would
drag web-only dependencies into the bot container.

Dependency rule: stdlib plus :mod:`trading_math.instrument_registry` only.  This
package is mounted at ``/opt/trading_math`` and ``/opt`` is on ``PYTHONPATH`` in
the chatbot, collector and webui images, so both consumers import the same file
without any Compose change.

Hash-stability warning: ``PLAN_FIELDS`` order is part of the canonical
serialization.  Reordering it, or changing any Decimal/ISO normalisation here,
silently changes every ``plan_hash`` and breaks MC-11 immutability acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trading_math.instrument_registry import InstrumentNotFound, load_registry

SCHEMA_VERSION = 1
PLAN_TYPE = "SimulationPlanV1"

# ── implemented catalogs (grounded in runtime code, ТЗ §5) ──────────────────
# strategy: paper_trading/strategy.py BaselineV1Strategy.VERSION
STRATEGY_CATALOG: dict[str, tuple[str, ...]] = {"baseline_v1": ("default",)}
# fee: paper_trading/execution.py FEE_RATE = 0.0005 taker (HTX USDT-M Prime 0)
# NOTE: the schedule version TAG is still "taker_6bps_v1" (opaque id); it should be
# renamed to reflect 5bps in a coordinated server+JS+HTML+tests pass (separate task).
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
    raw_start = raw.get("start_at")
    raw_end = raw.get("end_at")
    start_dt = _parse_dt(raw_start)
    if start_dt is None:
        if not isinstance(raw_start, str) or not raw_start.strip():
            bad("start_at", "field_required", "start_at is required (UTC window start)")
        else:
            bad("start_at", "field_type", "start_at must be an ISO-8601 datetime with timezone")
    end_dt = _parse_dt(raw_end)
    if end_dt is None:
        if not isinstance(raw_end, str) or not raw_end.strip():
            bad("end_at", "field_required", "end_at is required (UTC window end)")
        else:
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
