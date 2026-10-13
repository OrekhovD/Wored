"""One deterministic plan-validation pipeline: contract -> gates -> hash.

Extracted from the body of ``webui/simulation_api.validate_plan_endpoint`` in
P3c so that the dashboard and the Telegram bot answer the question *«is this
plan approvable, and why not?»* with literally the same code path.  The ТЗ P3
exit gate is *«из UI виден тот же* ``plan_hash`` *и violations, что в WebUI»* —
that is only true by construction if there is one pipeline, not two copies that
can drift apart field by field.

Order and semantics are fixed (ТЗ V3 §5, MC-10):

  1. ``normalize_plan`` — types, ranges, enums, registry membership; any
     violation here means there is no canonical plan at all;
  2. ``check_plan_semantics`` — cross-field relations and the window limits that
     individual ranges cannot express;
  3. the market gate for ``live_paper`` — ``can_enter`` comes from the shared
     per-component freshness verdict in :mod:`trading_math.market_state`, so a
     degraded feed blocks the bot exactly as it blocks the dashboard;
  4. the coverage gate for ``historical_replay`` — incomplete 1-minute history
     blocks, and an *unknown* coverage blocks too (fail-closed);
  5. ``estimate_worst_case_risk`` — the money the plan could lose at worst;
  6. ``plan_hash`` — only when nothing blocks, so a hash never pins a plan the
     domain would refuse.

Dependency rule: stdlib plus :mod:`trading_math.plan_contract` only.  The market
and coverage reads are **injected providers** — the web layer supplies them from
FastAPI state, the bot supplies them from its own Redis/Postgres clients, and
this module never imports either transport.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from trading_math.plan_contract import (
    PLAN_TYPE,
    SCHEMA_VERSION,
    Violation,
    _parse_dt,
    check_plan_semantics,
    estimate_worst_case_risk,
    normalize_plan,
    plan_hash,
)

#: ``async (instrument_key) -> market state dict | None`` — None means the quote
#: source is unreachable, which the caller must treat as a blocker, never as an
#: absence of a problem.
MarketProvider = Callable[[str], Awaitable[Any]]
#: ``async (instrument_key, start, end) -> coverage dict`` — may carry a
#: ``reason_code`` (e.g. ``missing_intervals``).
CoverageProvider = Callable[[str, datetime, datetime], Awaitable[dict]]


async def validate_plan(
    raw: Any,
    *,
    market: MarketProvider | None = None,
    coverage: CoverageProvider | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize, gate and hash a raw plan body.

    Returns the response payload the dashboard endpoint has always returned:
    ``schema_version``, ``plan_type``, ``normalized``, ``violations``,
    ``worst_case_risk``, ``market``, ``plan_hash``, ``can_approve``.  The bot
    renders the same dict, so a card and the dashboard cannot disagree.

    Without a provider the corresponding gate is *not* skipped silently: a
    ``live_paper`` plan with no market provider gets a ``quote_source_unavailable``
    violation and a replay plan gets ``history_source_unavailable``, because an
    unchecked plan is not a checked one.
    """
    moment = now or datetime.now(timezone.utc)
    plan, violations = normalize_plan(raw)
    market_state: dict[str, Any] | None = None

    if plan is not None:
        for v in check_plan_semantics(plan, now=moment):
            violations.append(v)

        if plan.get("mode") == "live_paper":
            state = await market(str(plan["instrument_key"])) if market else None
            if state is None:
                violations.append(Violation(
                    "instrument_key", "quote_source_unavailable",
                    "market source is not reachable or not wired; live_paper cannot be validated"))
            elif state.get("identity_ok") is False:
                # The payload describes another venue/market/contract than the
                # registry entry.  Fail closed as a violation: substituting a
                # foreign quote would validate a plan against wrong data (and
                # the previous webui copy raised KeyError on the missing keys).
                violations.append(Violation(
                    "instrument_key", "identity_mismatch",
                    f"market payload identity differs from the registry: {state.get('observed')}"))
            else:
                market_state = {"snapshot_id": state.get("snapshot_id") or str(uuid4()),
                               "as_of": state.get("as_of"),
                               "quality_worst": state["quality"]["worst"],
                               "can_enter": state["capabilities"]["can_enter"]}
                if not state["capabilities"]["can_enter"]:
                    violations.append(Violation(
                        "instrument_key", "stale_quote",
                        str(state["capabilities"].get("reason_code") or "market feed degraded")))
        elif plan.get("mode") == "historical_replay":
            start = _parse_dt(plan["start_at"]) or moment
            end = _parse_dt(plan["end_at"]) or moment
            if coverage is None:
                cov: dict[str, Any] = {"reason_code": "history_source_unavailable"}
            else:
                cov = await coverage(str(plan["instrument_key"]), start, end) or {}
            market_state = {"coverage": cov}
            if cov.get("reason_code"):
                violations.append(Violation(
                    "start_at", str(cov["reason_code"]),
                    "replay history is not fully covered for the requested window"))

    codes = [v.as_dict() if isinstance(v, Violation) else v for v in violations]
    ok = plan is not None and not codes
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_type": PLAN_TYPE,
        "normalized": plan,
        "violations": codes,
        "worst_case_risk": estimate_worst_case_risk(plan) if plan is not None else None,
        "market": market_state,
        "plan_hash": plan_hash(plan) if ok else None,
        "can_approve": ok,
    }
