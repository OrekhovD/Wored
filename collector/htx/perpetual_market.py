"""Publish validated HTX USDT-M perpetual snapshots to Redis.

This module consumes public HTX endpoints only. It never authenticates and
never sends an order. A complete, internally consistent snapshot replaces the
previous Redis value atomically; partial responses leave the last good value
untouched until its TTL expires.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
MARKET_KEY_PREFIX = "market:perpetual:htx"
DEFAULT_BASE_URL = "https://api.hbdm.com"


class PerpetualFeedError(RuntimeError):
    """A public response cannot be published as an execution snapshot."""


def _decimal(value: Any, field: str, *, allow_zero: bool = False, allow_negative: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PerpetualFeedError(f"{field}: invalid decimal") from exc
    if not result.is_finite():
        raise PerpetualFeedError(f"{field}: invalid decimal")
    if not allow_negative and result < 0:
        raise PerpetualFeedError(f"{field}: expected a positive decimal")
    if not allow_zero and result == 0:
        raise PerpetualFeedError(f"{field}: expected a positive decimal")
    return result


def _iso_from_ms(value: Any, field: str) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise PerpetualFeedError(f"{field}: invalid millisecond timestamp") from exc
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def _first_data(payload: dict[str, Any], field: str) -> dict[str, Any]:
    if payload.get("status") != "ok":
        raise PerpetualFeedError(f"{field}: HTX status is not ok")
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise PerpetualFeedError(f"{field}: data object is missing")
    return data


def _book_price(value: Any, field: str) -> Decimal:
    if not isinstance(value, list) or not value:
        raise PerpetualFeedError(f"{field}: expected [price, size]")
    return _decimal(value[0], field)


def build_snapshot(
    *,
    contract_code: str,
    ticker: dict[str, Any],
    index: dict[str, Any],
    funding: dict[str, Any],
    contract: dict[str, Any],
    mark: dict[str, Any],
    received_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize public HTX payloads into the WebUI execution contract."""
    if ticker.get("status") != "ok" or not isinstance(ticker.get("tick"), dict):
        raise PerpetualFeedError("ticker: tick object is missing")
    tick = ticker["tick"]
    index_data = _first_data(index, "index")
    funding_data = _first_data(funding, "funding")
    contract_data = _first_data(contract, "contract")
    mark_rows = mark.get("data")
    if mark.get("status") != "ok" or not isinstance(mark_rows, list) or not mark_rows:
        raise PerpetualFeedError("mark: kline is missing")
    mark_row = mark_rows[-1]
    if not isinstance(mark_row, dict):
        raise PerpetualFeedError("mark: invalid kline row")

    identities = {
        str(index_data.get("contract_code", "")),
        str(funding_data.get("contract_code", "")),
        str(contract_data.get("contract_code", "")),
    }
    if identities != {contract_code}:
        raise PerpetualFeedError("contract identity mismatch")

    bid = _book_price(tick.get("bid"), "bid")
    ask = _book_price(tick.get("ask"), "ask")
    if bid > ask:
        raise PerpetualFeedError("book: bid exceeds ask")

    source_ms = tick.get("ts") or ticker.get("ts")
    source_at = _iso_from_ms(source_ms, "ticker.ts")
    mark_id = int(mark_row.get("id", 0))
    mark_source_at = datetime.fromtimestamp(mark_id, tz=timezone.utc).isoformat()
    now = (received_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contract_size = _decimal(contract_data.get("contract_size"), "contract_size")

    return {
        "schema_version": SCHEMA_VERSION,
        "venue": "htx",
        "market_type": "linear-swap",
        "contract_code": contract_code,
        "bid": format(bid, "f"),
        "ask": format(ask, "f"),
        "last": format(_decimal(tick.get("close"), "last"), "f"),
        "mark": format(_decimal(mark_row.get("close"), "mark"), "f"),
        "index": format(_decimal(index_data.get("index_price"), "index"), "f"),
        "funding_rate": format(
            _decimal(funding_data.get("funding_rate"), "funding_rate", allow_zero=True, allow_negative=True),
            "f",
        ),
        "next_funding_at": _iso_from_ms(
            funding_data.get("next_funding_time") or funding_data.get("funding_time"),
            "next_funding_time",
        ),
        "contract_size": format(contract_size, "f"),
        "price_tick": format(_decimal(contract_data.get("price_tick"), "price_tick"), "f"),
        "quantity_step": format(contract_size, "f"),
        "source_at": source_at,
        "received_at": now.isoformat(),
        "component_times": {
            "ticker": source_at,
            "index": _iso_from_ms(index_data.get("index_ts"), "index_ts"),
            "mark": mark_source_at,
            "funding": _iso_from_ms(funding.get("ts"), "funding.ts"),
        },
        "source": "htx-public-rest",
        "quality": "live",
    }


@dataclass
class _ReferenceCache:
    index: dict[str, Any] | None = None
    funding: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    mark: dict[str, Any] | None = None
    refreshed_at: float = 0.0


class HtxPerpetualPublisher:
    """Poll public HTX endpoints and publish only complete snapshots."""

    def __init__(self, contract_code: str, *, base_url: str, poll_seconds: float) -> None:
        self.contract_code = contract_code
        self.base_url = base_url.rstrip("/")
        self.poll_seconds = poll_seconds
        self.reference = _ReferenceCache()

    async def _get(self, client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
        response = await client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PerpetualFeedError(f"{path}: expected JSON object")
        return payload

    async def _refresh_reference(self, client: httpx.AsyncClient, monotonic: float) -> None:
        if self.reference.contract is not None and monotonic - self.reference.refreshed_at < 30:
            return
        index, funding, contract, mark = await asyncio.gather(
            self._get(
                client,
                "/linear-swap-api/v1/swap_index",
                contract_code=self.contract_code,
            ),
            self._get(
                client,
                "/linear-swap-api/v1/swap_funding_rate",
                contract_code=self.contract_code,
            ),
            self._get(
                client,
                "/linear-swap-api/v1/swap_contract_info",
                contract_code=self.contract_code,
            ),
            self._get(
                client,
                "/index/market/history/linear_swap_mark_price_kline",
                contract_code=self.contract_code,
                period="1min",
                size=1,
            ),
        )
        self.reference = _ReferenceCache(index, funding, contract, mark, monotonic)

    async def run(self) -> None:
        try:  # container PYTHONPATH=/app
            from storage.redis_client import get_redis
        except ModuleNotFoundError:  # repository imports
            from collector.storage.redis_client import get_redis
        redis = get_redis()
        timeout = httpx.Timeout(8.0, connect=5.0)
        ttl = max(15, int(self.poll_seconds * 5))
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                try:
                    monotonic = asyncio.get_running_loop().time()
                    await self._refresh_reference(client, monotonic)
                    ticker = await self._get(
                        client,
                        "/linear-swap-ex/market/detail/merged",
                        contract_code=self.contract_code,
                    )
                    snapshot = build_snapshot(
                        contract_code=self.contract_code,
                        ticker=ticker,
                        index=self.reference.index or {},
                        funding=self.reference.funding or {},
                        contract=self.reference.contract or {},
                        mark=self.reference.mark or {},
                    )
                    key = f"{MARKET_KEY_PREFIX}:{self.contract_code}"
                    await redis.set(key, json.dumps(snapshot, separators=(",", ":")), ex=ttl)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("HTX perpetual snapshot %s rejected: %s", self.contract_code, exc)
                await asyncio.sleep(self.poll_seconds)


def _contracts() -> list[str]:
    values = os.getenv("PAPER_CONTRACTS", "BTC-USDT").split(",")
    contracts = [value.strip().upper() for value in values if value.strip()]
    return contracts or ["BTC-USDT"]


async def publish_perpetual_markets() -> None:
    """Run one independent publisher per configured simulated contract."""
    base_url = os.getenv("HTX_LINEAR_SWAP_BASE_URL", DEFAULT_BASE_URL).strip()
    try:
        poll_seconds = float(os.getenv("HTX_PERPETUAL_POLL_SECONDS", "1"))
        if not 0.5 <= poll_seconds <= 30:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("HTX_PERPETUAL_POLL_SECONDS must be between 0.5 and 30") from exc

    publishers = [
        HtxPerpetualPublisher(code, base_url=base_url, poll_seconds=poll_seconds).run()
        for code in _contracts()
    ]
    await asyncio.gather(*publishers)
