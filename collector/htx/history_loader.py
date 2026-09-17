"""Validated public HTX linear-swap candle history boundary.

The loader deliberately returns only closed candles.  Persistence is kept
outside this transport module so database migration/rehearsal remains an
explicit operation and no collector retry can create a duplicate financial or
market record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import asyncio
import json
import logging
import os
from typing import Any, Sequence

import httpx

DEFAULT_BASE_URL = "https://api.hbdm.com"
MAX_PAGE_SIZE = 2000
PERIODS = {"1min": timedelta(minutes=1), "60min": timedelta(hours=1)}
CANDLE_KEY_PREFIX = "market:perpetual:htx:candles"
log = logging.getLogger(__name__)


class HistoryLoadError(RuntimeError):
    """HTX history cannot safely be used as a closed-candle data source."""


@dataclass(frozen=True)
class PerpetualCandle:
    contract_code: str
    period: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str = "htx-linear-swap-rest"


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HistoryLoadError(f"{field}: invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise HistoryLoadError(f"{field}: expected a positive decimal")
    return parsed


def _open_time(value: Any) -> datetime:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoryLoadError("candle.id: invalid timestamp") from exc
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def parse_closed_candles(
    payload: dict[str, Any],
    *,
    contract_code: str,
    period: str,
    received_at: datetime,
) -> list[PerpetualCandle]:
    """Parse one HTX page, reject malformed rows, and discard open candles."""
    interval = PERIODS.get(period)
    if interval is None:
        raise HistoryLoadError("period: expected 1min or 60min")
    if payload.get("status") != "ok" or not isinstance(payload.get("data"), list):
        raise HistoryLoadError("history: HTX status is not ok")
    now = received_at.astimezone(timezone.utc)
    parsed: list[PerpetualCandle] = []
    seen: set[datetime] = set()
    for row in payload["data"]:
        if not isinstance(row, dict):
            raise HistoryLoadError("history: candle row is not an object")
        opened = _open_time(row.get("id"))
        if opened + interval > now:
            continue
        if opened in seen:
            raise HistoryLoadError("history: duplicate candle open time")
        seen.add(opened)
        candle = PerpetualCandle(
            contract_code=contract_code,
            period=period,
            open_time=opened,
            close_time=opened + interval,
            open=_decimal(row.get("open"), "candle.open"),
            high=_decimal(row.get("high"), "candle.high"),
            low=_decimal(row.get("low"), "candle.low"),
            close=_decimal(row.get("close"), "candle.close"),
            volume=_decimal(row.get("vol"), "candle.vol", allow_zero=True),
        )
        if candle.high < candle.low:
            raise HistoryLoadError("history: high below low")
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            raise HistoryLoadError("history: OHLC range is inconsistent")
        parsed.append(candle)
    return sorted(parsed, key=lambda item: item.open_time)


def find_gaps(candles: Sequence[PerpetualCandle]) -> list[tuple[datetime, datetime]]:
    """Return missing closed-candle spans; inputs must share one period."""
    if not candles:
        return []
    period = candles[0].period
    interval = PERIODS.get(period)
    if interval is None or any(item.period != period for item in candles):
        raise ValueError("candles: one supported period is required")
    ordered = sorted(candles, key=lambda item: item.open_time)
    gaps: list[tuple[datetime, datetime]] = []
    for prior, current in zip(ordered, ordered[1:]):
        expected = prior.open_time + interval
        if current.open_time > expected:
            gaps.append((expected, current.open_time))
    return gaps


class HtxHistoryLoader:
    """REST page reader with a bounded request size and no persistence side effect."""

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, page_size: int = MAX_PAGE_SIZE) -> None:
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size: expected value from 1 to {MAX_PAGE_SIZE}")
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size

    async def fetch_recent(
        self,
        client: httpx.AsyncClient,
        *,
        contract_code: str,
        period: str,
        received_at: datetime | None = None,
    ) -> list[PerpetualCandle]:
        if period not in PERIODS:
            raise ValueError("period: expected 1min or 60min")
        response = await client.get(
            self.base_url + "/linear-swap-ex/market/history/kline",
            params={"contract_code": contract_code, "period": period, "size": self.page_size},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise HistoryLoadError("history: expected JSON object")
        return parse_closed_candles(
            payload,
            contract_code=contract_code,
            period=period,
            received_at=received_at or datetime.now(timezone.utc),
        )


def candle_key(contract_code: str, period: str) -> str:
    return f"{CANDLE_KEY_PREFIX}:{contract_code}:{period}"


def candle_payload(candles: Sequence[PerpetualCandle]) -> str:
    return json.dumps(
        [
            {
                "open_time": item.open_time.isoformat(),
                "close_time": item.close_time.isoformat(),
                "open": format(item.open, "f"),
                "high": format(item.high, "f"),
                "low": format(item.low, "f"),
                "close": format(item.close, "f"),
                "volume": format(item.volume, "f"),
                "source": item.source,
            }
            for item in candles
        ],
        separators=(",", ":"),
    )


async def publish_closed_candles() -> None:
    """Refresh bounded 1m/60m closed history for the deterministic runner."""
    try:
        from storage.redis_client import get_redis
    except ModuleNotFoundError:
        from collector.storage.redis_client import get_redis
    contracts = [item.strip().upper() for item in os.getenv("PAPER_CONTRACTS", "BTC-USDT").split(",") if item.strip()]
    try:
        poll_seconds = float(os.getenv("HTX_CANDLE_POLL_SECONDS", "60"))
        if poll_seconds < 15 or poll_seconds > 300:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("HTX_CANDLE_POLL_SECONDS must be between 15 and 300") from exc
    loader = HtxHistoryLoader(base_url=os.getenv("HTX_LINEAR_SWAP_BASE_URL", DEFAULT_BASE_URL))
    redis = get_redis()
    ttl = max(300, int(poll_seconds * 5))
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
        while True:
            try:
                for contract_code in contracts or ["BTC-USDT"]:
                    for period in PERIODS:
                        candles = await loader.fetch_recent(
                            client,
                            contract_code=contract_code,
                            period=period,
                        )
                        if candles:
                            await redis.set(candle_key(contract_code, period), candle_payload(candles), ex=ttl)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A previous complete key stays valid until its own TTL; the
                # runner will fail closed once the key ages out.
                log.warning("HTX closed-candle refresh rejected: %s", exc)
            await asyncio.sleep(poll_seconds)
