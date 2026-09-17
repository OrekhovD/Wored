import redis.asyncio as redis
import os
import json

_redis = None

def get_redis():
    global _redis
    if not _redis:
        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        _redis = redis.from_url(url, decode_responses=True)
    return _redis

async def cache_tickers(tickers: list[dict]):
    r = get_redis()
    pipeline = r.pipeline()
    for t in tickers:
        pipeline.set(f"ticker:{t['symbol']}", json.dumps(t), ex=60) # cache 60s
    await pipeline.execute()

async def publish_alert(symbol: str, msg: str):
    r = get_redis()
    payload = json.dumps({"symbol": symbol, "message": msg})
    await r.publish("market_alerts", payload)
