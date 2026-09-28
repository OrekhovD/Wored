"""workspace_presenters — deterministic display model for /workspace.

Maps domain state into the 4-zone layout (status bar, route-of-day, context,
attention queue). Does NOT perform financial arithmetic; only presents values
already computed by the domain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ── Stage mapping (DayState → workspace stage) ────────────────────────────────

_STAGE_MAP: Dict[str, str] = {
    "idle": "prepare",
    "starting": "prepare",
    "running": "work",
    "closing": "finishing",
    "settlement_pending": "finishing",
    "reconciled": "results",
    "closed": "results",
    "recovery_required": "blocked",
}

_STAGE_ORDER = ["prepare", "work", "finishing", "results", "learning"]


def day_stage(state: Optional[str]) -> str:
    """Map domain DayState string to workspace stage label."""
    if state is None:
        return "prepare"
    return _STAGE_MAP.get(state, "prepare")


# ── Attention queue builder ───────────────────────────────────────────────────

# Priority levels (lower = more urgent)
_PRIORITY_CRITICAL = 1
_PRIORITY_WARNING = 2
_PRIORITY_ACTION = 3
_PRIORITY_INFO = 5


def _make_item(
    severity: str,
    priority: int,
    reason_code: str,
    message: str,
    source: str = "paper_trading",
    primary_action: Optional[str] = None,
    object_ref: Optional[Dict[str, Any]] = None,
    next_check_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "priority": priority,
        "reason_code": reason_code,
        "message": message,
        "source": source,
        "primary_action": primary_action,
        "object_ref": object_ref,
        "next_check_at": next_check_at,
    }


def build_attention(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate the attention queue from a domain workspace state payload.

    The input dict must have keys: ok, day, accounts, market, automation_state,
    capabilities, pending_commands. Deterministic: same input → same output.
    """
    items: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    # 1. Critical: domain failure
    if not state.get("ok", True):
        items.append(_make_item(
            "critical", _PRIORITY_CRITICAL, "domain_unavailable",
            "Источник данных дня недоступен; действия заблокированы",
            primary_action="refresh_market",
        ))
        return items

    day = state.get("day")
    market = state.get("market", {})
    capabilities = state.get("capabilities", {})
    pending_commands = state.get("pending_commands", [])
    automation = state.get("automation_state")

    # 2. Market staleness
    quality = market.get("quality", "unknown")
    if quality == "stale":
        age_s = market.get("age_seconds")
        msg = "Рыночный mark устарел"
        if age_s is not None:
            msg += f" {age_s} с"
        msg += "; новые входы заблокированы" if not capabilities.get("can_trade", True) else ""
        items.append(_make_item(
            "warning", _PRIORITY_WARNING, "market_stale",
            msg, primary_action="refresh_market",
        ))
    elif quality == "unavailable":
        items.append(_make_item(
            "critical", _PRIORITY_CRITICAL, "market_unavailable",
            "Рыночные данные недоступны; торговля заблокирована",
            primary_action="check_feed",
        ))

    # 3. Pending commands
    for cmd in pending_commands:
        items.append(_make_item(
            "action_required", _PRIORITY_ACTION, "command_pending",
            f"Команда {cmd.get('action_code', '?')} ожидает результат",
            primary_action="view_command",
            object_ref={"kind": "command", "id": cmd.get("command_id", "")},
        ))

    # 4. Day state context
    if day is None:
        if capabilities.get("can_start"):
            items.append(_make_item(
                "info", _PRIORITY_INFO, "ready_to_start",
                "День не начат. Готов к запуску.",
                primary_action="start_day",
            ))
        else:
            reason = capabilities.get("reason_code", "requirements not met")
            items.append(_make_item(
                "warning", _PRIORITY_WARNING, "cannot_start",
                f"Запуск дня заблокирован: {reason}",
            ))
    else:
        day_state = day.get("state", "running")
        if day_state in ("closing", "settlement_pending"):
            items.append(_make_item(
                "action_required", _PRIORITY_ACTION, "closing_in_progress",
                "Завершение дня в процессе; ожидается сверка",
            ))
        elif day_state == "recovery_required":
            items.append(_make_item(
                "critical", _PRIORITY_CRITICAL, "recovery_required",
                "День требует восстановления. Свяжитесь с операционной службой.",
            ))

    # 5. Automation state context
    if automation == "data_stale":
        items.append(_make_item(
            "warning", _PRIORITY_WARNING, "auto_data_stale",
            "Автомат: рынок устарел, торговля приостановлена",
            primary_action="refresh_market",
        ))
    elif automation == "risk_blocked":
        items.append(_make_item(
            "warning", _PRIORITY_WARNING, "auto_risk_blocked",
            "Автомат: риск-лимит достигнут; входы заблокированы",
        ))
    elif automation in ("waiting_signal", "armed", "cooldown"):
        reason_map = {
            "waiting_signal": "ожидает сигнала",
            "armed": "готов к сигналу",
            "cooldown": "период охлаждения",
        }
        next_check = None
        if automation == "waiting_signal":
            next_check = "next candle close"
        items.append(_make_item(
            "info", _PRIORITY_INFO, f"auto_{automation}",
            f"Автомат: {reason_map.get(automation, automation)}",
            next_check_at=next_check,
        ))

    # 6. Empty state — no problems
    if not items:
        items.append(_make_item(
            "info", _PRIORITY_INFO, "all_clear",
            "Активных проблем нет. Наблюдение.",
        ))

    # Sort by priority (stable)
    items.sort(key=lambda x: x["priority"])
    return items


# ── Capabilities builder ─────────────────────────────────────────────────────

def build_capabilities(
    day: Optional[Dict[str, Any]],
    ok: bool,
    market_quality: str,
) -> Dict[str, Any]:
    """Derive user-facing capabilities from domain state (read-only, no commands)."""
    caps: Dict[str, Any] = {
        "can_start": False,
        "can_trade": False,
        "can_finish": False,
        "can_pause_auto": False,
        "can_resume_auto": False,
        "can_close_auto": False,
        "can_view_report": False,
        "commands_enabled": False,  # B3 will enable
        "reason_code": None,
    }

    if not ok:
        caps["reason_code"] = "domain_unavailable"
        return caps

    if day is None:
        caps["can_start"] = True  # B3 will gate on preview
        return caps

    state = day.get("state", "")
    automation_state = day.get("automation_state")
    if state == "running":
        caps["can_trade"] = market_quality == "live"
        caps["can_finish"] = True
        caps["can_pause_auto"] = True
        caps["can_close_auto"] = True
        # ``resume_auto`` is only valid when the runner has been paused by the
        # operator; other paused-adjacent states (risk_blocked, waiting_signal)
        # are managed by the runner itself and are not user-resumable.
        if automation_state == "paused":
            caps["can_pause_auto"] = False
            caps["can_resume_auto"] = True
    elif state in ("closing", "settlement_pending"):
        caps["reason_code"] = "closing_in_progress"
    elif state in ("closed", "reconciled"):
        caps["can_view_report"] = True

    return caps


# ── Status bar formatting ────────────────────────────────────────────────────

def format_status_bar(
    day: Optional[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
    market: Dict[str, Any],
    pending_count: int,
    stage: str,
) -> Dict[str, Any]:
    """Single source for the status bar zone data."""
    return {
        "stage": stage,
        "day_date": (day or {}).get("local_date", "—"),
        "day_state": (day or {}).get("state"),
        "market_quality": market.get("quality", "unknown"),
        "market_age_seconds": market.get("age_seconds"),
        "accounts": [
            {
                "kind": acc.get("kind", "?"),
                "equity": acc.get("equity", acc.get("cash", "—")),
                "open_positions": acc.get("open_positions", 0),
            }
            for acc in accounts
        ],
        "pending_commands": pending_count,
    }
