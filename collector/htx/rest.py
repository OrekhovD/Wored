import asyncio
import httpx
import logging
from typing import Optional

log = logging.getLogger(__name__)

HTX_REST_URL = "https://api.huobi.pro"
MAX_RETRIES = 3


async def get_all_tickers() -> list[dict]:
    """Fetch 24h market stats for all pairs from HTX."""
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{HTX_REST_URL}/market/tickers")
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "ok":
                    results = []
                    for ticker in data.get("data", []):
                        if str(ticker.get("symbol", "")).endswith("usdt"):
                            close = float(ticker.get("close", 0))
                            open_price = float(ticker.get("open", 0))
                            change = ((close - open_price) / open_price * 100) if open_price else 0
                            results.append(
                                {
                                    "symbol": ticker["symbol"],
                                    "price": close,
                                    "volume": float(ticker.get("vol", 0)),
                                    "change_pct": change,
                                }
                            )
                    return results
        except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
            wait = 2 ** attempt
            log.warning("HTX retry %s/%s in %ss: %s", attempt + 1, MAX_RETRIES, wait, exc)
            await asyncio.sleep(wait)
        except Exception as exc:
            log.error("Error fetching HTX tickers: %s", exc)
            return []

    log.error("HTX: all retries exhausted")
    return []


async def get_symbol_ticker(symbol: str) -> Optional[dict]:
    """Fetch current ticker snapshot for one symbol from HTX."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{HTX_REST_URL}/market/detail/merged",
                params={"symbol": symbol.lower()},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                return None

            tick = data.get("tick", {})
            close = float(tick.get("close", 0.0))
            open_price = float(tick.get("open", 0.0))
            change_pct = ((close - open_price) / open_price * 100.0) if open_price else 0.0
            return {
                "symbol": symbol.lower(),
                "price": close,
                "volume": float(tick.get("vol", 0.0)),
                "change_pct": change_pct,
            }
    except Exception as exc:
        log.warning("Failed to fetch current ticker for %s: %s", symbol, exc)
        return None
