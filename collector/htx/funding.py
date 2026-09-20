"""Record HTX linear-swap funding observations for holding-cost accounting.

The fast execution path reads the *live* funding rate from the Redis market
snapshot published by :mod:`collector.htx.perpetual_market`.  The self-learning
loop needs the funding *history* to compute a holding-cost-adjusted PnL: without
it the simulated return of a position held across a settlement is overstated.

This module is a scheduled snapshotter.  It reads the current market snapshot,
extracts the funding rate HTX has published for the next settlement, and stores
one idempotent row per ``(venue, contract_code, funding_time)``.  Repeated polls
inside the same funding cycle collapse onto the same primary key, so a slower
scheduler interval only reduces observation granularity, never creates duplicates.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

MARKET_KEY_PREFIX = "market:perpetual:htx"


def _contracts() -> list[str]:
    values = os.getenv("PAPER_CONTRACTS", "BTC-USDT").split(",")
    contracts = [value.strip().upper() for value in values if value.strip()]
    return contracts or ["BTC-USDT"]


def _parse_snapshot(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def extract_funding(snapshot: dict[str, Any]) -> tuple[datetime, Decimal] | None:
    """Return ``(funding_time, funding_rate)`` from a market snapshot.

    Malformed or missing fields yield ``None`` instead of a fabricated rate: an
    absent observation is safer than a zero that would silently distort PnL.
    """
    rate_raw = snapshot.get("funding_rate")
    when_raw = snapshot.get("next_funding_at")
    if rate_raw is None or when_raw is None:
        return None
    try:
        rate = Decimal(str(rate_raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not rate.is_finite():
        return None
    try:
        funding_time = datetime.fromisoformat(str(when_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if funding_time.tzinfo is None:
        funding_time = funding_time.replace(tzinfo=timezone.utc)
    return funding_time.astimezone(timezone.utc), rate


async def record_funding_rate() -> None:
    """Snapshot the live funding rate for every configured contract to Postgres."""
    try:  # container PYTHONPATH=/app
        from storage.redis_client import get_redis
        from storage.perp_candles import upsert_funding_rate
        from storage.postgres_client import get_pool
    except ImportError:
        try:  # repository imports
            from collector.storage.redis_client import get_redis
            from collector.storage.perp_candles import upsert_funding_rate
            from collector.storage.postgres_client import get_pool
        except ImportError:
            log.warning("funding persistence unavailable; skipping funding snapshot")
            return

    redis = get_redis()
    for contract_code in _contracts():
        try:
            raw = await redis.get(f"{MARKET_KEY_PREFIX}:{contract_code}")
            snapshot = _parse_snapshot(raw)
            if snapshot is None:
                continue
            funding = extract_funding(snapshot)
            if funding is None:
                log.debug("funding fields missing in snapshot for %s", contract_code)
                continue
            funding_time, funding_rate = funding
            pool = await get_pool()
            await upsert_funding_rate(
                pool,
                contract_code=contract_code,
                funding_time=funding_time,
                funding_rate=funding_rate,
                predicted_rate=funding_rate,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort snapshotter
            log.warning("funding snapshot failed for %s: %s", contract_code, exc)
