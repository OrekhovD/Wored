"""Presentation formatters for the paper-trading layer.

All functions return plain dicts or strings — no FastAPI/Starlette/Jinja
dependencies.  Designed to be consumed by API handlers, CLI tools, or tests.

Uses the domain contracts from ``paper_trading.contracts`` for Position,
StatusDTO, and ReasonCode.

Python 3.9 compatible.  All money Decimal → string.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from paper_trading.contracts import (
    Position,
    PositionSide,
    ReasonCode,
    StatusDTO,
)
from paper_trading.runner import RunnerStatus
from paper_trading.strategy import Signal as StrategySignal

# ─── Helpers ───────────────────────────────────────────────────────────

def _money(value: Decimal | None) -> str:
    """Format a Decimal as a clean money string."""
    if value is None:
        return "0"
    quantized = value.quantize(Decimal("0.000001"))
    s = format(quantized, "f").rstrip("0").rstrip(".")
    return s or "0"


def _dec_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return _money(value)
    return str(value)


# ─── format_status_dto ─────────────────────────────────────────────────

def format_status_dto(status: RunnerStatus) -> dict[str, Any]:
    """Format a :class:`RunnerStatus` into a JSON-serializable DTO dict."""
    return {
        "running": status.running,
        "recovered": status.recovered,
        "fence_token": status.fence_token,
        "entries_blocked": status.entries_blocked,
        "instance_id": status.instance_id,
        "run_id": status.run_id,
        "position_count": len(status.active_positions),
        "pending_command_count": len(status.pending_commands),
        "open_order_count": len(status.open_orders),
        "last_signal": _signal_dto(status.last_signal) if status.last_signal else None,
        "last_heartbeat_epoch": status.last_heartbeat_epoch,
        "last_poll_epoch": status.last_poll_epoch,
        "last_error": status.last_error,
        "last_decision": _decision_dto(status.last_decision) if status.last_decision else None,
        "positions": [_position_dto(p) for p in status.active_positions],
    }


def format_status_dto_from_contract(dto: StatusDTO) -> dict[str, Any]:
    """Format a :class:`paper_trading.contracts.StatusDTO` into a dict."""
    return {
        "owner_id": str(dto.owner_id) if dto.owner_id else None,
        "day_id": str(dto.day_id) if dto.day_id else None,
        "account_kind": dto.account_kind.value if dto.account_kind else None,
        "mode": dto.mode,
        "day_state": dto.day_state.value,
        "automation_state": dto.automation_state.value,
        "engine_status": dto.engine_status,
        "engine_age_seconds": dto.engine_age_seconds,
        "feed_status": dto.feed_status,
        "feed_age_seconds": dto.feed_age_seconds,
        "strategy_version": dto.strategy_version,
        "open_positions": dto.open_positions,
        "pending_orders": dto.pending_orders,
        "closed_trades": dto.closed_trades,
        "reason_code": dto.reason_code.value if dto.reason_code else None,
        "last_decision_code": dto.last_decision_code.value if dto.last_decision_code else None,
        "last_decision_detail": dto.last_decision_detail,
        "equity": _dec_str(dto.equity),
        "realized_pnl": _dec_str(dto.realized_pnl),
        "unrealized_pnl": _dec_str(dto.unrealized_pnl),
        "total_fees": _dec_str(dto.total_fees),
    }


def _signal_dto(signal: StrategySignal) -> dict[str, Any]:
    return {
        "side": signal.side,
        "close_price": _dec_str(signal.close_price),
        "bar_timestamp": signal.bar_timestamp,
        "ema20": _dec_str(signal.ema20_at_trigger),
        "atr": _dec_str(signal.atr_at_trigger),
        "stop_loss": _dec_str(signal.stop_loss),
        "take_profit": _dec_str(signal.take_profit),
        "entry_zone_low": _dec_str(signal.entry_zone_low),
        "entry_zone_high": _dec_str(signal.entry_zone_high),
        "net_rr": _dec_str(signal.net_rr),
        "strategy_version": signal.strategy_version,
        "created_at_epoch": signal.created_at_epoch,
    }


def _decision_dto(decision: Any) -> dict[str, Any]:
    """Format a Decision dataclass into a dict."""
    return {
        "reason_code": decision.reason_code.value if hasattr(decision.reason_code, "value") else str(decision.reason_code),
        "reason_detail": decision.reason_detail,
        "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
    }


def _position_dto(pos: Position) -> dict[str, Any]:
    return {
        "position_id": str(pos.position_id),
        "account_id": str(pos.account_id),
        "day_id": str(pos.day_id),
        "instrument": pos.instrument,
        "side": pos.side.value,
        "qty": _dec_str(pos.qty),
        "avg_entry_price": _dec_str(pos.avg_entry_price),
        "isolated_margin": _dec_str(pos.isolated_margin),
        "stop_loss": _dec_str(pos.stop_loss),
        "take_profit": _dec_str(pos.take_profit),
        "status": pos.status.value,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "strategy_version": getattr(pos, "owner_engine_version", "1"),
    }


# ─── format_zero_positions_reason ──────────────────────────────────────

def format_zero_positions_reason(
    status: RunnerStatus,
    *,
    now_epoch: float,
    day_end_epoch: float | None = None,
) -> str:
    """Return a specific, actionable reason string when there are 0 positions.

    The text includes concrete numbers and the next action to take.
    """
    reasons: list[str] = []

    if not status.recovered:
        reasons.append(
            "Восстановление состояния не завершено — новые входы заблокированы. "
            "Дождитесь завершения recovery из PostgreSQL."
        )
    elif status.entries_blocked:
        reasons.append(
            "Входы заблокированы (entries_blocked=true). "
            f"Проверьте fence_token {status.fence_token} и завершите recovery."
        )

    if status.last_error:
        reasons.append(
            f"Последняя ошибка: {status.last_error}. "
            "Исправьте причину перед новыми входами."
        )

    if status.last_signal is not None:
        signal_age = now_epoch - status.last_signal.created_at_epoch
        if signal_age > 60.0:
            reasons.append(
                f"Последний сигнал истёк {signal_age:.0f} сек назад "
                f"(TTL 60 сек, бар {status.last_signal.bar_timestamp}). "
                "Ожидайте новый триггер на следующей 1m свече."
            )

    # End-of-day block
    if day_end_epoch is not None:
        minutes_left = (day_end_epoch - now_epoch) / 60.0
        if minutes_left <= 5:
            reasons.append(
                f"До конца торгового дня {minutes_left:.0f} мин — входы запрещены "
                "(правило: нет входов в последние 5 минут). "
                "Завершите день и сформируйте отчёт."
            )

    # Check the last decision reason code
    if status.last_decision is not None:
        reason_code = status.last_decision.reason_code
        if hasattr(reason_code, "value"):
            rc = reason_code.value
        else:
            rc = str(reason_code)

        if rc == ReasonCode.cooldown.value:
            reasons.append(
                "Стратегия в cooldown (10 мин после SL). "
                "Ожидайте завершения cooldown перед новым входом."
            )
        elif rc == ReasonCode.waiting_regime.value:
            reasons.append(
                "1h regime не подтверждён: EMA20 ≤ EMA50 или close ≤ EMA20. "
                "Ожидайте подтверждения тренда на 1h таймфрейме."
            )
        elif rc == ReasonCode.waiting_trigger.value:
            reasons.append(
                "1m триггер не сработал: prev low > prev EMA20 или close ≤ EMA20 "
                "или close ≤ prev high. "
                "Ожидайте pullback- breakout на 1m свече."
            )
        elif rc == ReasonCode.signal_expired.value:
            reasons.append(
                "Последний сигнал истёк (TTL 60 сек). "
                "Ожидайте новый сигнал на следующей 1m свече."
            )

    # No signal at all and no specific reason
    if not reasons and status.last_signal is None and status.last_decision is None:
        reasons.append(
            "Активных сигналов нет: условия BaselineV1 не выполнены "
            "(1h regime EMA20>EMA50 + close>EMA20, 15m confirm, 1m trigger). "
            "Ожидайте пробоя: prev low ≤ prev EMA20, close > EMA20, close > prev high."
        )

    # Fallback
    if not reasons:
        reasons.append(
            f"Позиций нет. Стратегия активна (fence_token {status.fence_token}). "
            "Ожидайте новый сигнал на следующей 1m свече."
        )

    return " | ".join(reasons)


# ─── format_plan_summary ───────────────────────────────────────────────

def format_plan_summary(
    *,
    strategy_version: str,
    account_id: str,
    opening_capital: Decimal,
    max_risk_per_order: Decimal,
    max_leverage: int,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    tp_rr: Decimal,
    min_net_rr: Decimal,
    cooldown_minutes: int,
) -> dict[str, Any]:
    """Format a strategy plan summary as a DTO dict."""
    return {
        "strategy_version": strategy_version,
        "account_id": account_id,
        "opening_capital": _dec_str(opening_capital),
        "max_risk_per_order": _dec_str(max_risk_per_order),
        "max_leverage": max_leverage,
        "timeframes": {
            "regime": "1h",
            "confirmation": "15m",
            "trigger": "1m",
        },
        "indicators": {
            "ema_fast_period": ema_fast,
            "ema_slow_period": ema_slow,
            "atr_period": atr_period,
        },
        "risk_rules": {
            "tp_rr": _dec_str(tp_rr),
            "min_net_rr": _dec_str(min_net_rr),
            "cooldown_minutes": cooldown_minutes,
            "sl_atr_fraction": "0.25",
            "entry_atr_fraction": "0.25",
        },
        "entry_conditions": [
            "1h: EMA20 > EMA50 and close > EMA20",
            "15m: close > EMA20",
            "1m: prev low <= prev EMA20, last close > last EMA20, last close > prev high",
        ],
        "exit_conditions": [
            "SL = min(5 lows) - 0.25*ATR",
            "TP = 2R",
            "net RR < 1.2 -> cancel",
            "cooldown 10 min after SL",
            "no entries in last 5 min of day",
        ],
    }


# ─── format_position_card ──────────────────────────────────────────────

def format_position_card(
    pos: Position,
    *,
    current_price: Decimal | None = None,
    account_label: str | None = None,
) -> dict[str, Any]:
    """Format a single position as a card DTO."""
    card: dict[str, Any] = {
        "position_id": str(pos.position_id),
        "account_id": str(pos.account_id),
        "account_label": account_label or str(pos.account_id),
        "side": pos.side.value,
        "entry_price": _dec_str(pos.avg_entry_price),
        "quantity": _dec_str(pos.qty),
        "stop_loss": _dec_str(pos.stop_loss),
        "take_profit": _dec_str(pos.take_profit),
        "status": pos.status.value,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "strategy_version": getattr(pos, "owner_engine_version", "1"),
    }

    if current_price is not None and pos.avg_entry_price > 0 and pos.qty > 0:
        unrealized: Decimal
        if pos.side == PositionSide.long:
            unrealized = (current_price - pos.avg_entry_price) * pos.qty
        else:
            unrealized = (pos.avg_entry_price - current_price) * pos.qty
        card["current_price"] = _dec_str(current_price)
        card["unrealized_pnl"] = _dec_str(unrealized)

        # Distance to SL/TP in %
        sl_dist = abs(current_price - pos.stop_loss) / pos.avg_entry_price * Decimal(100)
        tp_dist = abs(pos.take_profit - current_price) / pos.avg_entry_price * Decimal(100)
        card["distance_to_sl_pct"] = _dec_str(sl_dist)
        card["distance_to_tp_pct"] = _dec_str(tp_dist)

    return card


# ─── format_report ─────────────────────────────────────────────────────

def format_report(
    *,
    day_id: str,
    account_id: str,
    account_label: str | None,
    opening_capital: Decimal,
    realized_pnl: Decimal,
    total_fees: Decimal,
    trades_count: int,
    wins: int,
    losses: int,
    positions: Sequence[Position],
    strategy_version: str,
    current_prices: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Format an end-of-day or live report as a DTO dict.

    ``current_prices`` maps position_id (str) → current price Decimal for
    unrealized PnL calculation.
    """
    net_pnl = realized_pnl - total_fees
    win_rate = (Decimal(wins) / Decimal(trades_count) * Decimal(100)) if trades_count > 0 else Decimal(0)
    ending_equity = opening_capital + net_pnl

    # Unrealized PnL from open positions
    unrealized_total = Decimal(0)
    open_positions: list[dict[str, Any]] = []
    for pos in positions:
        card = format_position_card(pos, account_label=account_label)
        if current_prices and str(pos.position_id) in current_prices:
            card = format_position_card(
                pos,
                current_price=current_prices[str(pos.position_id)],
                account_label=account_label,
            )
            if "unrealized_pnl" in card:
                unrealized_total += Decimal(str(card["unrealized_pnl"]) or "0")
        open_positions.append(card)

    return {
        "day_id": day_id,
        "account_id": account_id,
        "account_label": account_label or account_id,
        "strategy_version": strategy_version,
        "summary": {
            "opening_capital": _dec_str(opening_capital),
            "ending_equity": _dec_str(ending_equity),
            "realized_pnl": _dec_str(realized_pnl),
            "total_fees": _dec_str(total_fees),
            "net_pnl": _dec_str(net_pnl),
            "unrealized_pnl": _dec_str(unrealized_total),
            "return_pct": _dec_str(
                (net_pnl / opening_capital * Decimal(100)) if opening_capital > 0 else Decimal(0)
            ),
        },
        "stats": {
            "trades_count": trades_count,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": _dec_str(win_rate),
        },
        "open_positions": open_positions,
        "open_position_count": len(open_positions),
    }