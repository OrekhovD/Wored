"""
Daily Pipeline WORED v2 — Execution Agent state machine.

ТЗ раздел 6: 7 состояний, 10 переходов, 5 команд.
ТЗ раздел 7: Deterministic execution rules (1m свеча, worst-case fill, slippage).

State machine:
  IDLE → ARMED → IN_POSITION → COOLDOWN → ARMED → ...
                    ↓              ↓
                STOPPED        PAUSED
                    ↓              ↓
                COMPLETED      ARMED

Команды: continue, tighten, reduce, pause, close_all
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)

# ─── Constants from ТЗ ─────────────────────────────────────────────────

TAKER_FEE_RATE = 0.0006      # 0.06% taker
MAKER_FEE_RATE = 0.0002      # 0.02% maker
DEFAULT_SLIPPAGE_BPS = 2     # 0.02% slippage
LIQUIDATION_MARGIN = 0.005   # 0.5% margin threshold

ALLOWED_LEVERAGE = [100, 125, 150, 200]
MAX_SIMULTANEOUS_POSITIONS = 1

# ─── §8 Межфайловые константы (ТЗ fast_modes_WORED v1.1) ──────────────

TRADE_DIRECTIONS = ('long', 'short', 'both', 'auto')
TRADE_HORIZONS = ('fast', 'medium', 'long')
SESSION_GOAL_PROFILES = ('fast_profit', 'balanced_intraday', 'session_swing')

DEFAULT_TRADE_DIRECTION = 'auto'
DEFAULT_TRADE_HORIZON = 'fast'
DEFAULT_TARGET_NET_PROFIT_USDT = 1.5
DEFAULT_MAX_TRADE_DURATION_MINUTES = 15
DEFAULT_COST_FILTER_ENABLED = True
DEFAULT_SESSION_GOAL_PROFILE = 'fast_profit'

TRADE_HORIZON_DEFAULTS = {
    "fast": {
        "target_net_profit_usdt": 1.5,
        "max_trade_duration_minutes": 15,
        "session_goal_profile": "fast_profit",
    },
    "medium": {
        "target_net_profit_usdt": 3.0,
        "max_trade_duration_minutes": 90,
        "session_goal_profile": "balanced_intraday",
    },
    "long": {
        "target_net_profit_usdt": 5.0,
        "max_trade_duration_minutes": 480,
        "session_goal_profile": "session_swing",
    },
}


class SessionState(str, Enum):
    """ТЗ 6.1 — 9 состояний (v3: +CREATED, +PLANNED, +FAILED, +BLOCKED)."""
    CREATED = "created"
    PLANNED = "planned"
    IDLE = "idle"
    ARMED = "armed"
    IN_POSITION = "in_position"
    COOLDOWN = "cooldown"
    PAUSED = "paused"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionCommand(str, Enum):
    """ТЗ 6.3 — 5 команд."""
    CONTINUE = "continue"
    TIGHTEN = "tighten"
    REDUCE = "reduce"
    PAUSE = "pause"
    CLOSE_ALL = "close_all"


# ─── Transition table (ТЗ 6.2) ─────────────────────────────────────────

TRANSITIONS = {
    # v3: new early states
    (SessionState.CREATED, "session_created"): SessionState.PLANNED,
    (SessionState.PLANNED, "plan_generated"): SessionState.IDLE,
    (SessionState.PLANNED, "plan_generation_failed"): SessionState.FAILED,
    (SessionState.IDLE, "has_planned_entries"): SessionState.ARMED,
    (SessionState.IDLE, "bootstrap_blocked"): SessionState.BLOCKED,
    (SessionState.BLOCKED, "bootstrap_retry_ok"): SessionState.ARMED,
    (SessionState.BLOCKED, "session_window_completed"): SessionState.COMPLETED,
    (SessionState.ARMED, "entry_trigger_confirmed"): SessionState.IN_POSITION,
    (SessionState.IN_POSITION, "closed_by_stop_loss"): SessionState.COOLDOWN,
    (SessionState.IN_POSITION, "closed_by_tp_or_invalidation"): SessionState.ARMED,
    (SessionState.IN_POSITION, "liquidation_or_drawdown"): SessionState.STOPPED,
    (SessionState.COOLDOWN, "cooldown_expired"): SessionState.ARMED,
    (SessionState.ARMED, "revision_pause"): SessionState.PAUSED,
    (SessionState.PAUSED, "revision_resume"): SessionState.ARMED,
    # Wildcard transitions checked separately
}

WILDCARD_TRANSITIONS = {
    "session_window_completed": SessionState.COMPLETED,
    "close_all_command": SessionState.STOPPED,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ─── State Machine Controller ──────────────────────────────────────────

class ExecutionStateMachine:
    """
    Управляет переходами состояний для одной торговой сессии.
    Все переходы логируются в execution_events.
    """

    def __init__(self, session_id: str, initial_state: SessionState = SessionState.IDLE):
        self.session_id = session_id
        self.state = initial_state
        self.last_transition_at = _now_utc()
        self._failed_entries = 0
        self._cooldown_until: Optional[datetime] = None

    def can_transition(self, event: str) -> bool:
        """Проверить, возможен ли переход по событию."""
        key = (self.state, event)
        if key in TRANSITIONS:
            return True
        if event in WILDCARD_TRANSITIONS:
            return True
        return False

    async def transition(self, event: str, payload: dict | None = None) -> SessionState | None:
        """
        Выполнить переход состояния.
        Логирует событие в execution_events.
        Возвращает новое состояние или None если переход невозможен.
        """
        key = (self.state, event)
        new_state = TRANSITIONS.get(key)
        if new_state is None and event in WILDCARD_TRANSITIONS:
            new_state = WILDCARD_TRANSITIONS[event]

        if new_state is None:
            log.warning(
                "SM: no transition from %s on event '%s' (session %s)",
                self.state.value, event, self.session_id,
            )
            return None

        old_state = self.state
        self.state = new_state
        self.last_transition_at = _now_utc()

        # Log to DB
        await self._log_event(old_state, new_state, event, payload or {})

        log.info(
            "SM: %s → %s (event=%s, session=%s)",
            old_state.value, new_state.value, event, self.session_id,
        )
        return new_state

    async def _log_event(
        self,
        old_state: SessionState,
        new_state: SessionState,
        event_type: str,
        payload: dict,
    ) -> None:
        """Записать событие в execution_events."""
        try:
            from storage.postgres_client import get_pool
            pool = await get_pool()
            if not pool:
                return
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO execution_events
                        (id, session_id, event_type, state_before, state_after, event_payload, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    """,
                    _uuid(),
                    self.session_id,
                    event_type,
                    old_state.value,
                    new_state.value,
                    json.dumps(payload),
                )
        except Exception as exc:
            log.error("SM: failed to log event: %s", exc)

    def update_session_status(self) -> str:
        """Возвращает строковый статус для trading_sessions.status."""
        return self.state.value


# ─── Execution Rules (ТЗ 7) ─────────────────────────────────────────────

def check_entry_trigger(
    candle: dict,
    entry_zone_from: float,
    entry_zone_to: float,
    confirmation_rule: str,
    indicators: dict | None = None,
) -> bool:
    """
    ТЗ 7.2 — проверка касания уровня входа и confirmation_rule.

    candle: {open, high, low, close, volume, time}
    indicators: {rsi, macd_hist, atr, trend} for the candle's timeframe
    """
    high = float(candle.get("high", 0))
    low = float(candle.get("low", 0))

    # Zone touch check
    if not (low <= entry_zone_to and high >= entry_zone_from):
        return False

    # Confirmation rule evaluation
    # Supported rules:
    #   close_above_zone_on_1m_and_rsi_gt_50
    #   close_above_zone_on_5m
    #   any (no confirmation needed)
    rule = (confirmation_rule or "any").lower().strip()

    if rule == "any":
        return True

    close = float(candle.get("close", 0))
    rsi = float(indicators.get("rsi", 50)) if indicators else 50.0

    if "close_above_zone" in rule:
        if close < entry_zone_to:
            return False
        if "rsi_gt_50" in rule and rsi <= 50:
            return False
        return True

    if "rsi_gt" in rule:
        threshold = 50
        parts = rule.split("rsi_gt_")
        if len(parts) > 1:
            try:
                threshold = int(parts[1])
            except ValueError:
                pass
        return rsi > threshold

    # Unknown rule — conservative: reject
    log.warning("Unknown confirmation_rule: %s — rejecting entry", confirmation_rule)
    return False


def check_stop_loss_hit(candle: dict, stop_loss: float, side: str) -> bool:
    """ТЗ 7.3 — worst-case fill для stop-loss."""
    high = float(candle.get("high", 0))
    low = float(candle.get("low", 0))

    if side.lower() == "long":
        return low <= stop_loss
    else:  # short
        return high >= stop_loss


def check_take_profit_hit(candle: dict, take_profit: float, side: str) -> bool:
    """ТЗ 7.3 — conservative fill для take-profit."""
    high = float(candle.get("high", 0))
    low = float(candle.get("low", 0))

    if side.lower() == "long":
        return high >= take_profit
    else:  # short
        return low <= take_profit


def check_invalidation(candle: dict, invalidation_price: float, side: str) -> bool:
    """Проверка invalidation level."""
    high = float(candle.get("high", 0))
    low = float(candle.get("low", 0))

    if side.lower() == "long":
        return low <= invalidation_price
    else:
        return high >= invalidation_price


def apply_slippage(price: float, side: str, is_entry: bool, bps: int = DEFAULT_SLIPPAGE_BPS) -> float:
    """ТЗ 7.3 — slippage применяется к цене исполнения."""
    slip = bps / 10000.0
    if is_entry:
        # Buy higher, sell lower (worst for trader)
        if side.lower() == "long":
            return price * (1 + slip)
        else:
            return price * (1 - slip)
    else:
        # Exit: sell lower, buy higher (worst for trader)
        if side.lower() == "long":
            return price * (1 - slip)
        else:
            return price * (1 + slip)


# ─── PnL Formulas (ТЗ 8) ───────────────────────────────────────────────

def calc_position_size(budget_usdt: float, budget_share_pct: float, leverage: int) -> dict:
    """ТЗ 8.1 — position_notional_usdt = budget * share% * leverage."""
    margin_used = budget_usdt * budget_share_pct / 100.0
    notional = margin_used * leverage
    return {
        "margin_used_usdt": round(margin_used, 8),
        "position_notional_usdt": round(notional, 8),
    }


def calc_fees(notional: float, fee_rate_open: float = TAKER_FEE_RATE, fee_rate_close: float = TAKER_FEE_RATE) -> dict:
    """ТЗ 8.2 — комиссии на открытие и закрытие."""
    open_fee = notional * fee_rate_open
    close_fee = notional * fee_rate_close
    return {
        "open_fee_usdt": round(open_fee, 8),
        "close_fee_usdt": round(close_fee, 8),
        "total_fee_usdt": round(open_fee + close_fee, 8),
    }


def calc_realised_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    position_qty: float,
    total_fee_usdt: float,
) -> float:
    """ТЗ 8.3 — gross_pnl - total_fee."""
    if side.lower() == "long":
        gross = position_qty * (exit_price - entry_price)
    else:
        gross = position_qty * (entry_price - exit_price)
    realised = gross - total_fee_usdt
    return round(realised, 8)


def calc_unrealised_pnl(
    side: str,
    entry_price: float,
    mark_price: float,
    position_qty: float,
    accrued_fees: float,
) -> float:
    """ТЗ 8.4."""
    if side.lower() == "long":
        raw = position_qty * (mark_price - entry_price)
    else:
        raw = position_qty * (entry_price - mark_price)
    return round(raw - accrued_fees, 8)


def calc_equity(cash_balance: float, realised_pnl: float, unrealised_pnl: float) -> float:
    """ТЗ 8.5."""
    return round(cash_balance + realised_pnl + unrealised_pnl, 8)


def calc_drawdown_pct(peak_equity: float, current_equity: float) -> float:
    """ТЗ 8.6."""
    if peak_equity <= 0:
        return 0.0
    return round((peak_equity - current_equity) / peak_equity * 100.0, 8)


def calc_profit_factor(gross_profit_sum: float, gross_loss_sum: float) -> float | None:
    """ТЗ 8.7 — null если gross_loss = 0."""
    if gross_loss_sum == 0:
        return None
    return round(gross_profit_sum / abs(gross_loss_sum), 8)


def calc_liquidation_price(entry_price: float, leverage: int, side: str) -> float:
    """Расчёт цены ликвидации."""
    if side.lower() == "long":
        return round(entry_price * (1 - 1 / leverage + LIQUIDATION_MARGIN), 8)
    else:
        return round(entry_price * (1 + 1 / leverage - LIQUIDATION_MARGIN), 8)


def is_liquidated(current_price: float, liq_price: float, side: str) -> bool:
    """Проверка факта ликвидации."""
    if side.lower() == "long":
        return current_price <= liq_price
    else:
        return current_price >= liq_price


# ─── Risk Policy (ТЗ 9) ────────────────────────────────────────────────

RISK_MODES = {
    "defensive": {
        "budget_share_range": (5, 10),
        "max_failed_entries": 2,
        "cooldown_minutes": 30,
    },
    "balanced": {
        "budget_share_range": (10, 20),
        "max_failed_entries": 3,
        "cooldown_minutes": 20,
    },
    "aggressive": {
        "budget_share_range": (20, 30),
        "max_failed_entries": 4,
        "cooldown_minutes": 10,
    },
}


def get_risk_params(risk_mode: str) -> dict:
    """ТЗ 9.2 — параметры режима риска."""
    return RISK_MODES.get(risk_mode, RISK_MODES["balanced"])


def validate_leverage(leverage: int) -> bool:
    """ТЗ 9.1 — allowed_leverage_values."""
    return leverage in ALLOWED_LEVERAGE


def validate_budget_share(budget_share_pct: float, risk_mode: str) -> bool:
    """ТЗ 9.2 — budget share в пределах режима."""
    params = get_risk_params(risk_mode)
    lo, hi = params["budget_share_range"]
    return lo <= budget_share_pct <= hi


# ─── §4 Cost Filter & Trade Economics (ТЗ fast_modes_WORED v1.1) ──────

def estimate_expected_total_fees(notional: float) -> float:
    """Оценка общих комиссий за сделку (open + close)."""
    open_fee = notional * TAKER_FEE_RATE
    close_fee = notional * TAKER_FEE_RATE
    return round(open_fee + close_fee, 8)


def estimate_expected_slippage(notional: float, bps: int = DEFAULT_SLIPPAGE_BPS) -> float:
    """Оценка slippage в USDT для входа и выхода."""
    slip = bps / 10000.0
    # Entry + exit slippage
    return round(notional * slip * 2, 8)


def estimate_expected_gross_profit(
    side: str,
    entry_price: float,
    take_profit: float,
    position_qty: float,
) -> float:
    """Оценка валовой прибыли до комиссий."""
    if side.lower() == "long":
        gross = position_qty * (take_profit - entry_price)
    else:
        gross = position_qty * (entry_price - take_profit)
    return round(gross, 8)


def estimate_expected_net_profit(
    side: str,
    entry_price: float,
    take_profit: float,
    position_qty: float,
    notional: float,
) -> dict:
    """Полная оценка экономики сделки: gross - fees - slippage."""
    gross = estimate_expected_gross_profit(side, entry_price, take_profit, position_qty)
    fees = estimate_expected_total_fees(notional)
    slip = estimate_expected_slippage(notional)
    net = gross - fees - slip
    return {
        "expected_gross_profit_usdt": gross,
        "expected_total_fees_usdt": fees,
        "expected_slippage_usdt": slip,
        "expected_net_profit_usdt": round(net, 8),
    }


def should_reject_by_cost_filter(
    expected_net_profit: float,
    target_net_profit: float,
    cost_filter_enabled: bool = True,
) -> tuple[bool, str]:
    """Решение: блокировать ли вход по cost filter."""
    if not cost_filter_enabled:
        return False, "cost_filter_disabled"
    if expected_net_profit < target_net_profit:
        return True, "entry_rejected_cost_filter"
    return False, "approved"


def evaluate_entry_economics(
    side: str,
    entry_price: float,
    take_profit: float,
    position_qty: float,
    notional: float,
    target_net_profit: float,
    cost_filter_enabled: bool = True,
) -> dict:
    """Комплексная оценка: economics + decision."""
    econ = estimate_expected_net_profit(side, entry_price, take_profit, position_qty, notional)
    reject, reason = should_reject_by_cost_filter(
        econ["expected_net_profit_usdt"],
        target_net_profit,
        cost_filter_enabled,
    )
    return {
        **econ,
        "rejected": reject,
        "reject_reason": reason,
        "target_net_profit_usdt": target_net_profit,
    }


def enforce_trade_horizon_timeout(
    opened_at: datetime,
    max_duration_minutes: int,
) -> tuple[bool, int]:
    """Для trade_horizon='fast': проверить, не истекло ли max_trade_duration.

    Returns: (should_close, elapsed_minutes)
    """
    now = _now_utc()
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    elapsed = (now - opened_at).total_seconds() / 60.0
    elapsed_int = int(elapsed)
    if elapsed >= max_duration_minutes:
        return True, elapsed_int
    return False, elapsed_int