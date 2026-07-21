import json
import logging
import os
from storage.redis_client import get_redis
from storage.postgres_client import get_recent_alert_history
from storage.journal_reader import get_recent_journal, format_journal_for_ai

log = logging.getLogger(__name__)


async def _fetch_htx_klines(symbol: str, interval: str, limit: int = 50) -> list[dict]:
    """Fetch klines from HTX REST API."""
    import httpx
    url = f"https://api.huobi.pro/market/history/kline?period={interval}&size={limit}&symbol={symbol}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            data = resp.json()
            klines = data.get("data", [])
            klines.reverse()
            return klines
    except Exception as exc:
        log.warning("HTX kline fetch failed (%s/%s): %s", symbol, interval, exc)
        return []


def _compute_rsi(candles: list[dict], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    closes = [c["close"] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _compute_macd(candles: list[dict], fast=12, slow=26, signal=9) -> dict | None:
    if len(candles) < slow + signal:
        return None
    closes = [c["close"] for c in candles]

    def ema(data, period):
        k = 2 / (period + 1)
        ema_val = data[0]
        for i in range(1, len(data)):
            ema_val = data[i] * k + ema_val * (1 - k)
        return ema_val

    ema_fast = closes[-fast-1:] if len(closes) >= fast + 1 else closes
    ema_slow = closes[-slow-1:] if len(closes) >= slow + 1 else closes

    # Simple EMA calculation
    def ema_series(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ema_f = ema_series(closes, fast)
    ema_s = ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_f, ema_s)]
    signal_line = ema_series(macd_line, signal)
    hist = macd_line[-1] - signal_line[-1] if len(signal_line) > 0 else 0

    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1] if signal_line else 0,
        "hist": hist,
    }


async def build_dual_timeframe_context(ticker: str) -> str:
    """Build context with indicators from multiple timeframes (15m, 1h, 4h)."""
    # Current price
    r = get_redis()
    price_data = await r.get(f"ticker:{ticker}")
    if price_data:
        p = json.loads(price_data)
        price_block = f"Монета: {ticker.upper()}\nЦена: ${p['price']}\nИзменение 24h: {p['change_pct']:+.2f}%"
    else:
        price_block = f"Монета: {ticker.upper()}\nЦена: НЕТ ДАННЫХ"

    timeframes = [
        ("15min", "15min", "⚡ 15min (скальп)"),
        ("60min", "60min", "📈 1h (интрадей)"),
        ("4hour", "4hour", "📊 4h (свинг)"),
    ]

    tf_blocks = []
    for interval, key, label in timeframes:
        candles = await _fetch_htx_klines(ticker, interval)
        if not candles:
            tf_blocks.append(f"{label}: данные недоступны")
            continue

        rsi = _compute_rsi(candles)
        macd = _compute_macd(candles)

        parts = []
        if rsi is not None:
            zone = "ПЕРЕКУПЛЕН" if rsi > 70 else ("ПЕРЕПРОДАН" if rsi < 30 else "нейтральный")
            parts.append(f"RSI={rsi:.1f} ({zone})")
        if macd:
            direction = "бычий" if macd["hist"] > 0 else "медвежий"
            parts.append(f"MACD hist={macd['hist']:+.4f} ({direction})")

        tf_blocks.append(f"{label}: {' | '.join(parts) if parts else 'недостаточно данных'}")

    # Also include latest journal entry
    journal_entries = await get_recent_journal(limit=1)
    journal_block = format_journal_for_ai(journal_entries) if journal_entries else ""

    sections = [
        "====== DUAL TIMEFRAME ANALYSIS ======",
        price_block,
        "",
        "\n".join(tf_blocks),
        "",
        journal_block,
        "=====================================",
    ]

    return "\n".join(s for s in sections if s)


async def build_analysis_knowledge(ticker: str, depth: str = "short") -> str:
    """
    Build a comprehensive AI knowledge context for analysis.
    
    depth="short" → 2 journal entries (30 min) — for regular analysis
    depth="deep"  → 8 journal entries (2 hours) — for deep analysis
    """
    limit = 2 if depth == "short" else 8
    
    # 1. Current price from Redis
    r = get_redis()
    price_data = await r.get(f"ticker:{ticker}")
    
    if not price_data:
        price_block = f"Текущие данные по {ticker.upper()}: НЕТ В КЭШЕ."
    else:
        p = json.loads(price_data)
        price_block = (
            f"Монета: {ticker.upper()}\n"
            f"Текущая цена: ${p['price']}\n"
            f"Изменение за сутки: {p['change_pct']:+.2f}%\n"
            f"Суточный объем: {p['volume']:.2f}"
        )
    
    # 2. Recent alerts
    try:
        history = await get_recent_alert_history(limit=50)
        valid_alerts = [a for a in history if a['symbol'].lower() == ticker][:3]
    except Exception:
        valid_alerts = []
    
    if valid_alerts:
        alert_lines = []
        for a in valid_alerts:
            ts = a['timestamp'].strftime("%d.%m %H:%M")
            alert_lines.append(f"  ⚠️ {a['threshold']:+.2f}% в {ts}")
        alert_block = "Недавние аномальные скачки:\n" + "\n".join(alert_lines)
    else:
        alert_block = "Недавние аномальные скачки: нет."
    
    # 3. Journal history with indicators
    journal_entries = await get_recent_journal(limit=limit)
    journal_block = format_journal_for_ai(journal_entries)
    
    # 4. Latest indicators summary for the specific ticker
    indicator_block = ""
    if journal_entries:
        latest = journal_entries[0]
        ind = latest.get("indicators", {}).get(ticker, {})
        if ind:
            parts = []
            if "rsi_14" in ind:
                rsi = ind["rsi_14"]
                zone = "ПЕРЕКУПЛЕН" if rsi > 70 else ("ПЕРЕПРОДАН" if rsi < 30 else "нейтральный")
                parts.append(f"RSI(14) = {rsi:.1f} ({zone})")
            if "macd" in ind:
                parts.append(f"MACD = {ind['macd']:.4f}")
            if "macd_signal" in ind:
                parts.append(f"Signal = {ind['macd_signal']:.4f}")
            if "macd_hist" in ind:
                hist = ind["macd_hist"]
                direction = "бычий" if hist > 0 else "медвежий"
                parts.append(f"Histogram = {hist:.4f} ({direction})")
            if parts:
                indicator_block = "Текущие индикаторы:\n  " + "\n  ".join(parts)
    
    # Assemble
    sections = [
        "====== РЫНОЧНЫЙ КОНТЕКСТ ======",
        price_block,
        alert_block,
        indicator_block,
        "",
        journal_block,
        "==============================",
    ]
    
    return "\n".join(s for s in sections if s)
