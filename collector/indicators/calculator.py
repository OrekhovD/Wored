import logging
import os
import httpx
from services.market_data import TIMEFRAMES, PERIOD_SECONDS, calculate_closed_indicators

log = logging.getLogger(__name__)
HTX_REST_URL = os.getenv("HTX_REST_URL", "https://api.huobi.pro")


async def calculate_indicators(symbol: str, period: str = "15min") -> dict:
    period = TIMEFRAMES.get(period, period)
    if period not in PERIOD_SECONDS:
        raise ValueError("Unsupported candle period")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{HTX_REST_URL}/market/history/kline",
                params={"symbol": symbol, "period": period, "size": 100})
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok":
                return calculate_closed_indicators(data.get("data", []), period)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        log.warning("Indicators unavailable for %s/%s", symbol, period)
    return {}
