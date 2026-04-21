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

async def get_cached_tickers(symbols: list[str]) -> list[dict]:
    r = get_redis()
    results = []
    for s in symbols:
        data = await r.get(f"ticker:{s.lower()}")
        if data:
            results.append(json.loads(data))
    return results
