"""HTX (Huobi) market-data adapter — READ ONLY, no trading."""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from base64 import b64encode

import httpx

from core.exceptions import HTXError, HTXRateLimitError
from core.schemas import HTXCandle, HTXOrderBook, HTXRecentTrade, HTXTicker


class HTXMarketDataAdapter:
    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = "https://api.huobi.pro") -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._last_ts: float = 0.0  # rate-limit tracker

    # ── public endpoints ────────────────────────────────────────────────
    async def get_symbols(self) -> list[dict]:
        return await self._get("/api/v3/market/list", params={})

    async def get_tickers(self) -> list[HTXTicker]:
        raw = await self._get("/market/tickers", params={})
        return [self._to_ticker(x) for x in raw.get("data", [])]

    async def get_ticker(self, symbol: str) -> HTXTicker:
        raw = await self._get("/market/detail/merged", params={"symbol": symbol.lower()})
        d = raw.get("tick", {})
        return HTXTicker(
            symbol=symbol,
            last=d.get("close", 0.0),
            volume=d.get("amount", 0.0),
            change_pct=round(((d.get("close", 0) - d.get("open", 0)) / d.get("open", 1)) * 100, 2) if d.get("open") else 0.0,
            high=d.get("high", 0.0),
            low=d.get("low", 0.0),
            timestamp=int(time.time()),
        )

    async def get_klines(self, symbol: str, interval: str = "1day", limit: int = 100) -> list[HTXCandle]:
        """interval: 1min, 5min, 15min, 30min, 60min, 4hour, 1day, 1week, 1mon."""
        raw = await self._get("/market/history/kline", params={
            "symbol": symbol.lower(), "period": interval, "size": limit,
        })
        return [self._to_candle(x) for x in raw.get("data", [])]

    async def get_order_book(self, symbol: str, depth: int = 20) -> HTXOrderBook:
        raw = await self._get("/market/depth", params={"symbol": symbol.lower(), "type": "step0", "depth": depth})
        d = raw.get("tick", {})
        return HTXOrderBook(
            bid=[(float(b[0]), float(b[1])) for b in d.get("bids", [])[:depth]],
            ask=[(float(a[0]), float(a[1])) for a in d.get("asks", [])[:depth]],
            timestamp=d.get("ts", int(time.time() * 1000)),
        )

    async def get_recent_trades(self, symbol: str, limit: int = 20) -> list[HTXRecentTrade]:
        raw = await self._get("/market/history/trade", params={"symbol": symbol.lower(), "size": limit})
        result: list[HTXRecentTrade] = []
        for chunk in raw.get("data", []):
            for t in chunk.get("data", []):
                result.append(HTXRecentTrade(
                    trade_id=t.get("id", 0),
                    price=float(t.get("price", 0)),
                    quantity=float(t.get("amount", 0)),
                    direction=t.get("direction", ""),
                    timestamp=t.get("ts", 0),
                ))
        return result

    async def get_server_time(self) -> int:
        raw = await self._get("/v1/common/timestamp", params={})
        return int(raw.get("data", 0))

    # ── internal helpers ────────────────────────────────────────────────
    async def _get(self, path: str, params: dict[str, str | int]) -> dict:
        await self._wait_ratelimit()
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
        if r.status_code == 429:
            raise HTXRateLimitError("HTX API rate limit reached")
        if r.status_code != 200:
            raise HTXError(f"HTX GET {url} returned {r.status_code}")
        body = r.json()
        if body.get("status") == "error":
            raise HTXError(f"HTX error: {body.get('err-msg', body.get('err-code', ''))}")
        return body

    async def _wait_ratelimit(self) -> None:
        import asyncio
        now = time.monotonic()
        elapsed = now - self._last_ts
        if elapsed < 0.25:  # ~4 req/s safety
            await asyncio.sleep(0.25 - elapsed)
        self._last_ts = time.monotonic()

    @staticmethod
    def _to_ticker(d: dict) -> HTXTicker:
        return HTXTicker(
            symbol=d.get("symbol", ""),
            last=d.get("close", 0.0),
            volume=d.get("vol", 0.0),
            change_pct=0.0,
            high=d.get("high", 0.0),
            low=d.get("low", 0.0),
            timestamp=d.get("ts", 0),
        )

    @staticmethod
    def _to_candle(d: dict) -> HTXCandle:
        return HTXCandle(
            timestamp=d.get("id", 0),
            open=d.get("open", 0.0),
            high=d.get("high", 0.0),
            low=d.get("low", 0.0),
            close=d.get("close", 0.0),
            volume=d.get("amount", 0.0),
        )

    @staticmethod
    def _sign_request(method: str, path: str, params: str, secret: str) -> str:
        ts = "2024-01-01T00:00:00"  # placeholder; real impl generates UTC ISO 8601
        payload = f"{method.upper()}\napi.huobi.pro\n{path}\n{params}"
        h = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        return b64encode(h).decode()
