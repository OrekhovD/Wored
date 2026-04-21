import httpx
import logging
import asyncio

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
                if data.get('status') == 'ok':
                    results = []
                    for t in data.get('data', []):
                        if str(t.get('symbol', '')).endswith('usdt'):
                            close = float(t.get('close', 0))
                            open_price = float(t.get('open', 0))
                            change = ((close - open_price) / open_price * 100) if open_price else 0
                            results.append({
                                'symbol': t['symbol'],
                                'price': close,
                                'volume': float(t.get('vol', 0)),
                                'change_pct': change
                            })
                    return results
        except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
            wait = 2 ** attempt
            log.warning(f"HTX retry {attempt+1}/{MAX_RETRIES} in {wait}s: {e}")
            await asyncio.sleep(wait)
        except Exception as e:
            log.error(f"Error fetching HTX tickers: {e}")
            return []
    
    log.error("HTX: all retries exhausted")
    return []
