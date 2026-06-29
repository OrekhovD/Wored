"""
Breakout No-Trade / Trade Signal Detector (ТЗ TX_breakout_no_trade_htx_btcusdt_v1).

Определяет, является ли текущая рыночная ситуация допустимой для входа в сделку
или должна быть помечена как no-trade.

Логика:
  - Long: цена закрылась выше breakout_high confirm_closes раз подряд
  - Short: цена закрылась ниже breakout_low confirm_closes раз подряд
  - Объём > vol_factor * avg_volume(vol_period)
  - ATR > atr_factor * avg_atr(atr_period * 3)
  - Спред <= spread_max_pct
  - Risk limits не нарушены
  - Нет news block

Расчёт entry / SL / TP / position size — по ATR.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─── Config (§4, §12) ────────────────────────────────────────────────

@dataclass
class BreakoutConfig:
    """Параметры breakout сигнала (§4.1 + §12 defaults)."""
    symbol: str = "BTCUSDT"
    direction: str = "both"          # long / short / both
    timeframe: str = "5m"            # HTX period: 5min
    breakout_high: float = 61000.0
    breakout_low: float = 59800.0
    confirm_closes: int = 2
    vol_period: int = 20
    vol_factor: float = 1.5
    atr_period: int = 14
    atr_factor: float = 1.3
    risk_pct: float = 1.0
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0
    max_leverage: int = 20
    # Дополнительные (§4.2)
    require_retest: bool = False
    retest_bars: int = 3
    spread_max_pct: float = 0.05     # 0.05%
    news_block_minutes: int = 0
    session_max_drawdown_pct: float = 5.0


@dataclass
class BreakoutSignal:
    """Результат детекции сигнала."""
    decision: str = "no_trade"       # trade / no_trade
    direction: str = ""              # long / short / ""
    reason: str = ""                 # reason code
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size: float = 0.0
    notional: float = 0.0
    leverage: int = 0
    # Диагностика
    breakout_level: float = 0.0
    confirm_count: int = 0
    volume_ratio: float = 0.0
    atr_ratio: float = 0.0
    current_price: float = 0.0
    current_volume: float = 0.0
    current_atr: float = 0.0
    avg_volume: float = 0.0
    avg_atr: float = 0.0
    spread_pct: float = 0.0
    checks: dict = field(default_factory=dict)
    timestamp: str = ""


# ─── Helpers ─────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Вычисляет ATR (Average True Range) для серии свечей."""
    if len(closes) < period + 1:
        return []
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Simple moving average of TR
    atrs = []
    for i in range(len(trs) - period + 1):
        atrs.append(sum(trs[i:i + period]) / period)
    return atrs


def compute_position_size(equity: float, entry: float, stop_loss: float, risk_pct: float) -> tuple[float, float, int]:
    """§6.4 — расчёт размера позиции.

    Returns (position_size, notional, leverage_needed).
    """
    risk_amount = equity * risk_pct / 100.0
    per_unit_risk = abs(entry - stop_loss)
    if per_unit_risk <= 0:
        return 0.0, 0.0, 0
    position_size = risk_amount / per_unit_risk
    notional = position_size * entry
    return position_size, notional, 0


# ─── No-Trade Reasons (§5.3) ─────────────────────────────────────────

def evaluate_no_trade_reasons(
    confirm_count: int,
    required_closes: int,
    volume_ratio: float,
    vol_factor: float,
    atr_ratio: float,
    atr_factor: float,
    spread_pct: float,
    spread_max: float,
    max_drawdown: float,
    max_drawdown_limit: float,
    direction: str,
    session_direction: str,
    news_block: bool = False,
) -> Optional[str]:
    """Возвращает reason code если no-trade, или None если все проверки пройдены."""
    if confirm_count < required_closes:
        return f"insufficient_confirmations: {confirm_count}/{required_closes}"
    if volume_ratio < vol_factor:
        return f"volume_below_threshold: {volume_ratio:.2f} < {vol_factor}"
    if atr_ratio < atr_factor:
        return f"atr_below_threshold: {atr_ratio:.2f} < {atr_factor}"
    if spread_pct > spread_max:
        return f"spread_too_wide: {spread_pct:.4f}% > {spread_max}%"
    if news_block:
        return "news_block_active"
    if max_drawdown >= max_drawdown_limit:
        return f"max_drawdown_exceeded: {max_drawdown:.2f}% >= {max_drawdown_limit}%"
    if session_direction != "auto" and session_direction != "both" and direction != session_direction:
        return f"direction_conflict: signal={direction} session={session_direction}"
    return None


# ─── Main Detector (§5) ──────────────────────────────────────────────

async def detect_breakout_signal(
    snapshot: dict,
    config: BreakoutConfig,
    klines: list[dict] | None = None,
    equity: float = 100.0,
    session_direction: str = "auto",
    max_drawdown: float = 0.0,
    news_block: bool = False,
) -> BreakoutSignal:
    """§5 — основной детектор breakout сигнала.

    Args:
        snapshot: Redis ticker snapshot {price, volume, ...}
        config: BreakoutConfig
        klines: список свечей [{open, high, low, close, vol, ...}, ...]
        equity: текущий капитал для расчёта position size
        session_direction: направление сессии (auto/long/short/both)
        max_drawdown: текущий drawdown сессии в %
        news_block: активен ли news block

    Returns:
        BreakoutSignal с decision=trade/no_trade и всеми параметрами
    """
    sig = BreakoutSignal(timestamp=_now_iso())
    sig.current_price = float(snapshot.get("price", 0))

    checks: dict[str, Any] = {}

    # 1. Проверка данных
    if not klines or len(klines) < max(config.vol_period, config.atr_period * 3 + 1):
        sig.decision = "no_trade"
        sig.reason = "insufficient_data"
        checks["data"] = {"ok": False, "klines": len(klines) if klines else 0}
        sig.checks = checks
        return sig

    checks["data"] = {"ok": True, "klines": len(klines)}

    # 2. Извлекаем closes, highs, lows, volumes
    closes = [float(k.get("close", 0)) for k in klines]
    highs = [float(k.get("high", 0)) for k in klines]
    lows = [float(k.get("low", 0)) for k in klines]
    volumes = [float(k.get("vol", k.get("volume", 0))) for k in klines]

    # 3. Подсчёт confirm_closes подряд выше breakout_high / ниже breakout_low
    confirm_long = 0
    confirm_short = 0
    for c in reversed(closes):
        if c > config.breakout_high:
            confirm_long += 1
        else:
            break
    for c in reversed(closes):
        if c < config.breakout_low:
            confirm_short += 1
        else:
            break

    sig.confirm_count = max(confirm_long, confirm_short)
    checks["confirmations"] = {
        "long": confirm_long,
        "short": confirm_short,
        "required": config.confirm_closes,
    }

    # 4. Volume ratio
    if len(volumes) >= config.vol_period:
        avg_vol = sum(volumes[-config.vol_period - 1:-1]) / config.vol_period
        sig.avg_volume = avg_vol
        sig.current_volume = volumes[-1]
        sig.volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0.0
    checks["volume"] = {
        "current": sig.current_volume,
        "avg": sig.avg_volume,
        "ratio": round(sig.volume_ratio, 3),
        "threshold": config.vol_factor,
    }

    # 5. ATR ratio
    atrs = compute_atr(highs, lows, closes, config.atr_period)
    if len(atrs) >= config.atr_period * 2:
        # avg ATR за период atr_period*3 (§5.1 п.4)
        avg_atr_window = config.atr_period * 3
        avg_atr = sum(atrs[-avg_atr_window:]) / min(len(atrs), avg_atr_window)
        sig.current_atr = atrs[-1]
        sig.avg_atr = avg_atr
        sig.atr_ratio = atrs[-1] / avg_atr if avg_atr > 0 else 0.0
    checks["atr"] = {
        "current": round(sig.current_atr, 2),
        "avg": round(sig.avg_atr, 2),
        "ratio": round(sig.atr_ratio, 3),
        "threshold": config.atr_factor,
    }

    # 6. Spread (из snapshot, если есть bid/ask)
    bid = float(snapshot.get("bid", snapshot.get("price", 0)))
    ask = float(snapshot.get("ask", snapshot.get("price", 0)))
    if bid > 0 and ask > 0:
        sig.spread_pct = ((ask - bid) / bid) * 100
    checks["spread"] = {
        "pct": round(sig.spread_pct, 4),
        "max": config.spread_max_pct,
    }

    # 7. Определение направления сигнала
    signal_direction = ""
    signal_breakout_level = 0.0

    if config.direction in ("long", "both") and confirm_long >= config.confirm_closes:
        signal_direction = "long"
        signal_breakout_level = config.breakout_high
        sig.breakout_level = config.breakout_high
    elif config.direction in ("short", "both") and confirm_short >= config.confirm_closes:
        signal_direction = "short"
        signal_breakout_level = config.breakout_low
        sig.breakout_level = config.breakout_low

    if not signal_direction:
        # Нет пробоя ни вверх, ни вниз
        sig.decision = "no_trade"
        sig.reason = "no_breakout_confirmed"
        checks["signal"] = {"direction": "none", "reason": "no breakout above/below levels"}
        sig.checks = checks
        return sig

    # 8. No-trade checks (§5.3)
    reason = evaluate_no_trade_reasons(
        confirm_count=confirm_long if signal_direction == "long" else confirm_short,
        required_closes=config.confirm_closes,
        volume_ratio=sig.volume_ratio,
        vol_factor=config.vol_factor,
        atr_ratio=sig.atr_ratio,
        atr_factor=config.atr_factor,
        spread_pct=sig.spread_pct,
        spread_max=config.spread_max_pct,
        max_drawdown=max_drawdown,
        max_drawdown_limit=config.session_max_drawdown_pct,
        direction=signal_direction,
        session_direction=session_direction,
        news_block=news_block,
    )

    if reason:
        sig.decision = "no_trade"
        sig.direction = signal_direction
        sig.reason = reason
        checks["signal"] = {"direction": signal_direction, "reason": reason}
        sig.checks = checks
        return sig

    # 9. Расчёт entry / SL / TP (§6)
    entry_price = sig.current_price
    if config.require_retest:
        # Limit entry на ретест уровня
        entry_price = signal_breakout_level

    atr_val = sig.current_atr if sig.current_atr > 0 else 1.0

    if signal_direction == "long":
        stop_loss = entry_price - (atr_val * config.sl_atr_mult)
        take_profit = entry_price + (entry_price - stop_loss) * config.tp_rr
    else:
        stop_loss = entry_price + (atr_val * config.sl_atr_mult)
        take_profit = entry_price - (stop_loss - entry_price) * config.tp_rr

    # 10. Position sizing (§6.4)
    pos_size, notional, _ = compute_position_size(equity, entry_price, stop_loss, config.risk_pct)
    if pos_size <= 0:
        sig.decision = "no_trade"
        sig.direction = signal_direction
        sig.reason = "position_size_zero"
        checks["signal"] = {"direction": signal_direction, "reason": "position_size_zero"}
        sig.checks = checks
        return sig

    # Leverage check
    margin_needed = notional / config.max_leverage
    leverage = int(config.max_leverage)
    if margin_needed > equity:
        leverage = int(notional / equity) + 1
        if leverage > config.max_leverage:
            sig.decision = "no_trade"
            sig.direction = signal_direction
            sig.reason = f"leverage_exceeded: {leverage}x > {config.max_leverage}x"
            checks["signal"] = {"direction": signal_direction, "reason": sig.reason}
            sig.checks = checks
            return sig

    # 11. Trade signal!
    sig.decision = "trade"
    sig.direction = signal_direction
    sig.reason = "all_checks_passed"
    sig.entry_price = round(entry_price, 2)
    sig.stop_loss = round(stop_loss, 2)
    sig.take_profit = round(take_profit, 2)
    sig.position_size = round(pos_size, 6)
    sig.notional = round(notional, 2)
    sig.leverage = leverage

    checks["signal"] = {
        "direction": signal_direction,
        "decision": "trade",
        "entry": sig.entry_price,
        "sl": sig.stop_loss,
        "tp": sig.take_profit,
        "size": sig.position_size,
        "notional": sig.notional,
        "leverage": sig.leverage,
    }
    sig.checks = checks

    log.info("Breakout signal: %s %s entry=%s sl=%s tp=%s size=%s lev=%dx",
             sig.decision, sig.direction, sig.entry_price, sig.stop_loss,
             sig.take_profit, sig.position_size, sig.leverage)

    return sig


# ─── Convenience: build signal from Redis + HTX REST ──────────────────

async def get_breakout_signal_for_session(
    session: dict,
    config: BreakoutConfig | None = None,
) -> BreakoutSignal:
    """Получить breakout сигнал для активной сессии.

    Собирает snapshot из Redis, klines из HTX REST, metrics из БД,
    и вызывает detect_breakout_signal().
    """
    if config is None:
        config = BreakoutConfig(
            symbol=session.get("symbol", "BTCUSDT"),
            direction=session.get("trade_direction", "auto"),
        )

    # 1. Redis snapshot
    from storage.redis_client import get_redis
    redis = get_redis()
    symbol_lower = config.symbol.lower()
    ticker_raw = await redis.get(f"ticker:{symbol_lower}")
    if not ticker_raw:
        return BreakoutSignal(decision="no_trade", reason="no_market_snapshot", timestamp=_now_iso())
    snapshot = json.loads(ticker_raw)

    # 2. Klines from HTX REST (inline to avoid cross-package import)
    import httpx
    HTX_REST_URL = "https://api.huobi.pro"
    period_map = {"5m": "5min", "15m": "15min", "1h": "60min", "4h": "4hour"}
    period = period_map.get(config.timeframe, "5min")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{HTX_REST_URL}/market/history/kline",
                params={"symbol": symbol_lower, "period": period, "size": 100}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                return BreakoutSignal(decision="no_trade", reason="htx_api_error", timestamp=_now_iso())
            klines = list(reversed(data.get("data", [])))
    except Exception as exc:
        log.warning("Failed to fetch klines: %s", exc)
        return BreakoutSignal(decision="no_trade", reason="htx_api_error", timestamp=_now_iso())

    # 3. Equity + drawdown from session
    equity = float(session.get("initial_budget_usdt", 100.0))
    max_drawdown = 0.0
    # Could fetch from session_metrics if available

    # 4. Direction from session
    session_direction = session.get("trade_direction", "auto")

    return await detect_breakout_signal(
        snapshot=snapshot,
        config=config,
        klines=klines,
        equity=equity,
        session_direction=session_direction,
        max_drawdown=max_drawdown,
    )