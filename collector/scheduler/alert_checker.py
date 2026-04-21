import logging
from storage.redis_client import get_redis
from storage.postgres_client import save_alert, get_pool
from storage.redis_client import publish_alert
import json
import os

log = logging.getLogger(__name__)

THRESHOLD = 3.0 # Default spike %

async def check_alerts():
    """Look for price spikes."""
    r = get_redis()
    keys = await r.keys("ticker:*")
    if not keys:
        return
        
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
        
    for k in keys:
        data = await r.get(k)
        if data:
            ticker = json.loads(data)
            sym = ticker['symbol'].lower()
            if sym not in watchlist:
                continue
                
            change = ticker.get('change_pct', 0)
            if abs(change) >= THRESHOLD:
                msg = f"🚀 SPIKE ALERT: {sym.upper()} changed {change:.2f}% (Price: {ticker['price']})"
                if change < 0:
                    msg = f"🩸 DROP ALERT: {sym.upper()} changed {change:.2f}% (Price: {ticker['price']})"
                
                # Check ratelimit in redis so we don't spam
                rl_key = f"alert_sent:{sym}"
                if not await r.exists(rl_key):
                    await publish_alert(ticker['symbol'], msg)
                    await save_alert(ticker['symbol'], change)
                    await r.set(rl_key, "1", ex=3600) # spam protect 1h
                    log.info(msg)
