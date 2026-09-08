"""Collector is the sole producer of the multi-timeframe indicator snapshot."""
import asyncio
import json
import logging
import os
import time

from indicators.calculator import calculate_indicators
from services.market_data import TIMEFRAMES
from storage.redis_client import get_redis

log = logging.getLogger(__name__)


async def publish_market_contexts():
    for symbol in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(","):
        symbol = symbol.strip().lower()
        if not symbol:
            continue
        values = await asyncio.gather(*(calculate_indicators(symbol, period) for period in TIMEFRAMES.values()),
                                      return_exceptions=True)
        frames = {alias: value if isinstance(value, dict) else {} for alias, value in zip(TIMEFRAMES, values)}
        await get_redis().set(f"market_context:{symbol}", json.dumps({
            "schema_version": 1, "published_at": time.time(), "timeframes": frames,
        }, allow_nan=False), ex=90)
        if not all(frames.values()):
            log.warning("Incomplete indicator snapshot for %s", symbol)
