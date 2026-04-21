import json
from storage.redis_client import get_redis
from storage.postgres_client import get_recent_alert_history

async def build_context(ticker: str) -> str:
    """Build a rich contextual prompt for the analyst AI using DB data."""
    r = get_redis()
    price_data = await r.get(f"ticker:{ticker}")
    
    if not price_data:
        return f"Пользователь спрашивает о {ticker.upper()}, но актуальных данных в системе сейчас нет."
        
    p = json.loads(price_data)
    
    # Get last 5 alerts from Postgres matching this ticker
    history = await get_recent_alert_history(limit=50) # fetch enough to filter locally
    valid_alerts = [a for a in history if a['symbol'].lower() == ticker][:3]
    
    alert_str = "Нет недавних всплесков."
    if valid_alerts:
        alert_lines = []
        for a in valid_alerts:
            ts = a['timestamp'].strftime("%d.%m %H:%M")
            alert_lines.append(f"{a['threshold']:+.2f}% в {ts}")
        alert_str = "; ".join(alert_lines)
    
    return f"""
====== РЫНОЧНЫЙ КОНТЕКСТ ======
Монета: {ticker.upper()}
Текущая цена: ${p['price']:.4f}
Изменение за сутки: {p['change_pct']:+.2f}%
Суточный объем: {p['volume']:.2f}
Недавние аномальные скачки: {alert_str}
==============================

"""

async def build_comparison_context(ticker_a: str, ticker_b: str) -> str:
    """Build a rich contextual prompt comparing two tickers."""
    ctx_a = await build_context(ticker_a)
    ctx_b = await build_context(ticker_b)
    return f"Запрос на сравнение:\n{ctx_a}\n\n--- vs ---\n\n{ctx_b}\nОсновываясь исключительно на этих свежих данных, проведи лаконичное сравнение активов."
