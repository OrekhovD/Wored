import json
import logging
from storage.redis_client import get_redis
from storage.postgres_client import get_recent_alert_history
from storage.journal_reader import get_recent_journal, format_journal_for_ai

log = logging.getLogger(__name__)


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
