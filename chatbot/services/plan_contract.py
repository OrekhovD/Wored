"""Validated, versioned session plans. Economics describe isolated-v2 simulation only."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.execution_engine import (
    ALLOWED_LEVERAGE, TAKER_FEE_RATE, apply_slippage, calc_liquidation_price,
    validate_budget_share,
)
from services.sim_math import settlement

RULES = {
    "long": {"close_above_zone_on_1m_and_rsi_gt_50", "rsi_gt_50"},
    "short": {"close_below_zone_on_1m_and_rsi_lt_50", "rsi_lt_50"},
}
TERMINAL = {"completed", "stopped", "failed"}


def utc(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def session_error(session: dict, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if session.get("status") in TERMINAL:
        return "session_ended"
    try:
        if utc(session["session_end"]) <= now:
            return "session_expired"
        if utc(session["session_start"]) > now:
            return "session_not_started"
    except (ValueError, TypeError, KeyError):
        return "invalid_session_window"
    return None


def number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("finite_positive_number_required")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("finite_positive_number_required")
    return result


def entry_risk(entry: dict, session: dict, fill_price: float | None = None) -> dict:
    """Check worst zone edge or actual slipped fill; never silently repair model prices."""
    errors: list[str] = []
    try:
        side = entry["side"]
        if side not in RULES:
            raise ValueError("invalid_side")
        direction = session.get("trade_direction", "auto")
        if direction in ("long", "short") and side != direction:
            errors.append("direction_mismatch")
        lo, hi, sl, inv = (number(entry[k]) for k in (
            "entry_zone_from", "entry_zone_to", "stop_loss", "invalidation_price"))
        leverage = entry["recommended_leverage"]
        if type(leverage) is not int or leverage not in ALLOWED_LEVERAGE:
            raise ValueError("invalid_leverage")
        share = number(entry["budget_share_pct"])
        if not validate_budget_share(share, session.get("risk_mode", "balanced")):
            errors.append("budget_share_out_of_policy")
        if entry.get("margin_mode") != "isolated":
            errors.append("isolated_margin_required")
        if entry.get("confirmation_rule") not in RULES[side]:
            errors.append("unsupported_confirmation_rule")
        targets = entry.get("take_profit", entry.get("take_profit_json"))
        if isinstance(targets, str):
            targets = json.loads(targets)
        if not isinstance(targets, list) or not 1 <= len(targets) <= 3:
            raise ValueError("invalid_take_profit")
        tp = [number(v) for v in targets]
        if lo > hi:
            errors.append("reversed_entry_zone")
        if side == "long":
            geometry = sl < inv < lo <= hi < tp[0] and tp == sorted(set(tp))
        else:
            geometry = tp[0] < lo <= hi < inv < sl and tp == sorted(set(tp), reverse=True)
        if not geometry:
            errors.append("invalid_price_geometry")
        reference = hi if side == "long" else lo
        fill = number(fill_price) if fill_price is not None else apply_slippage(reference, side, True)
        if (side == "long" and not sl < fill < tp[0]) or (side == "short" and not tp[0] < fill < sl):
            errors.append("fill_outside_protection")
        budget = number(session["initial_budget_usdt"])
        margin = budget * share / 100
        notional = margin * leverage
        qty = notional / fill
        if margin > 1_000_000 or not all(math.isfinite(v) for v in (margin, notional, qty)):
            raise ValueError("simulation_size_out_of_bounds")
        liq = calc_liquidation_price(fill, leverage, side)
        stop_fill = apply_slippage(sl, side, False)
        if (side == "long" and stop_fill <= liq) or (side == "short" and stop_fill >= liq):
            errors.append("stop_beyond_liquidation")
        opening_fee = notional * TAKER_FEE_RATE
        stop_pnl, stop_fee = settlement(side, fill, stop_fill, qty, opening_fee)
        target_fill = apply_slippage(tp[0], side, False)
        tp_pnl, tp_fee = settlement(side, fill, target_fill, qty, opening_fee)
        loss = -stop_pnl
        rr = tp_pnl / loss if loss > 0 else 0.0
        if not all(math.isfinite(v) for v in (liq, stop_pnl, tp_pnl, rr)):
            raise ValueError("nonfinite_economics")
        if rr < 1:
            errors.append("net_reward_risk_below_one")
        target = number(session.get("target_net_profit_usdt", 1.5))
        if session.get("cost_filter_enabled", True) and tp_pnl < target:
            errors.append("target_net_profit_not_met")
        return {"ok": not errors, "errors": errors, "reference_entry": reference,
                "assumed_fill": fill, "margin_usdt": margin, "notional_usdt": notional,
                "quantity": qty, "liquidation_price": liq, "net_loss_sl_usdt": loss,
                "risk_pct": loss / budget * 100, "net_profit_tp1_usdt": tp_pnl,
                "net_reward_risk": rr, "fees_sl_usdt": opening_fee + stop_fee,
                "fees_tp1_usdt": opening_fee + tp_fee, "funding_included": False,
                "calculation_version": "isolated-v2", "slippage_bps": 2}
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        return {"ok": False, "errors": errors + [str(exc)]}


def validate_plan(raw: dict, session: dict, context: dict, now: datetime | None = None) -> dict:
    """Accept explanatory no-trade; quarantine rejected candidates without executable rows."""
    now = now or datetime.now(timezone.utc)
    error = session_error(session, now)
    if error:
        raise ValueError(error)
    if context.get("quality") != "ready":
        raise ValueError("market_data_unavailable")
    try:
        age = (now - utc(context["timestamp"])).total_seconds()
        if not 0 <= age <= 90:
            raise ValueError("market_snapshot_expired")
    except (KeyError, TypeError):
        raise ValueError("invalid_snapshot_timestamp") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise ValueError("invalid_plan_schema")
    if len(raw["entries"]) > 3:
        raise ValueError("too_many_entries")
    # Normalize primary_scenario: if entries empty and scenario text contains no_trade, normalize
    ps = raw.get("primary_scenario", "")
    if not raw.get("entries") and isinstance(ps, str) and "no_trade" in ps.lower():
        raw["primary_scenario"] = "no_trade"
    for key in ("thesis", "primary_scenario", "alternative_scenario", "no_trade_condition"):
        if not isinstance(raw.get(key), str) or not raw[key].strip() or len(raw[key]) > 1600:
            raise ValueError("invalid_" + key)
    if raw.get("market_regime") not in {"trend_up", "trend_down", "range", "volatile", "unknown"}:
        raise ValueError("invalid_market_regime")
    valid_until = min(utc(session["session_end"]), now + timedelta(hours=1))
    entries, rejected = [], []
    for candidate in raw["entries"]:
        if not isinstance(candidate, dict):
            raise ValueError("invalid_entry_schema")
        risk = entry_risk(candidate, session)
        if raw["primary_scenario"] == "no_trade":
            risk["errors"].append("no_trade_has_entries")
            risk["ok"] = False
        fields = {"side", "entry_zone_from", "entry_zone_to", "stop_loss", "invalidation_price",
                  "take_profit", "recommended_leverage", "budget_share_pct", "margin_mode",
                  "confirmation_rule", "reason_code"}
        # Preserve rejected evidence as JSON without NaN/Infinity or model metadata.
        clean = json.loads(json.dumps({k: v for k, v in candidate.items() if k in fields}),
                           parse_constant=lambda _: None)
        checked = {**clean, "risk": risk}
        if risk["ok"]:
            entries.append(checked)
        else:
            rejected.append(checked)
    if not raw["entries"] and raw["primary_scenario"] != "no_trade":
        raise ValueError("empty_plan_requires_no_trade")
    status = "accepted" if entries else "rejected" if rejected else "no_trade"
    # Only copy allowed explanatory fields. Model cannot override provenance or policy.
    result = {k: raw[k] for k in ("market_regime", "thesis", "primary_scenario",
                                 "alternative_scenario", "no_trade_condition")}
    result.update(schema_version=2, entries=entries, rejected_entries=rejected,
                  validation_status=status, valid_until=valid_until.isoformat(),
                  market_snapshot=context,
                  management={"exit": "full_position_at_tp1", "tp2": "reference_only",
                              "trailing_stop": False, "partial_exits": False,
                              "max_duration_minutes": int(session.get("max_trade_duration_minutes", 15))},
                  limitations=["Funding и стакан не включены в расчёт риска.",
                               "Ликвидация рассчитана по isolated-v2 симулятора, не по спецификации HTX.",
                               "Условие no_trade — аналитический текст; исполняются только формальные проверки заявок."])
    return result


def plan_error(plan: dict | None, session: dict, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    error = session_error(session, now)
    if error:
        return error
    if not plan or plan.get("version") != session.get("active_plan_version"):
        return "active_plan_version_missing"
    if plan.get("schema_version") != 2 or plan.get("validation_status") != "accepted":
        return "plan_not_validated"
    try:
        if utc(plan["valid_until"]) <= now:
            return "plan_expired"
    except (KeyError, ValueError, TypeError):
        return "invalid_plan_expiry"
    return None
