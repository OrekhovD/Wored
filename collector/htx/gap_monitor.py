"""Detect and heal gaps in the persisted perpetual 1m candle series.

The collector polls HTX every minute, but a restart, a rate-limit, or a network
blip can leave holes in ``trader_v1_perp_candles``.  Downstream the learning
loop assumes a contiguous 1m base series, so gaps are both a data-integrity
signal (surfaced as ``candle_gap_count`` in ``/healthz``) and an actionable
condition: this job re-reads the most recent bounded history and re-persists it,
which transparently backfills any missing recent bucket thanks to the idempotent
``ON CONFLICT DO NOTHING`` writer.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

GAP_GAUGE_KEY = "market:perpetual:htx:candle_gap_count"
# Only the recent window is treated as "must be contiguous"; older history is the
# responsibility of the explicit backfill script, not this watchdog.
GAP_WINDOW_MINUTES = 360


def _contracts() -> list[str]:
    values = os.getenv("PAPER_CONTRACTS", "BTC-USDT").split(",")
    contracts = [value.strip().upper() for value in values if value.strip()]
    return contracts or ["BTC-USDT"]


def _load_deps():
    try:  # container PYTHONPATH=/app
        from storage.redis_client import get_redis
        from storage.postgres_client import get_pool
        from storage.perp_candles import bulk_upsert_candles, count_gaps
    except ImportError:
        try:  # repository imports
            from collector.storage.redis_client import get_redis
            from collector.storage.postgres_client import get_pool
            from collector.storage.perp_candles import bulk_upsert_candles, count_gaps
        except ImportError:
            return None
    try:
        from htx.history_loader import HtxHistoryLoader, NETWORK_PERIOD, DEFAULT_BASE_URL
    except ImportError:
        from collector.htx.history_loader import (
            HtxHistoryLoader,
            NETWORK_PERIOD,
            DEFAULT_BASE_URL,
        )
    return {
        "get_redis": get_redis,
        "get_pool": get_pool,
        "upsert": bulk_upsert_candles,
        "count_gaps": count_gaps,
        "loader": HtxHistoryLoader(base_url=os.getenv("HTX_LINEAR_SWAP_BASE_URL", DEFAULT_BASE_URL)),
        "period": NETWORK_PERIOD,
    }


async def check_candle_gaps() -> int:
    """Return the total 1m gap count and trigger a best-effort heal.

    The count is written to Redis so ``/healthz`` can surface it without the WebUI
    holding a database connection.  A non-zero result also re-reads recent HTX
    history to backfill missing buckets; healing is idempotent and never deletes.
    """
    deps = _load_deps()
    if deps is None:
        log.debug("candle gap monitor unavailable; skipping")
        return 0
    import httpx

    redis = deps["get_redis"]()
    pool = await deps["get_pool"]()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=GAP_WINDOW_MINUTES)
    total_gaps = 0
    for contract_code in _contracts():
        try:
            gaps = await deps["count_gaps"](pool, contract_code, "1min", window_start, now)
            total_gaps += int(gaps or 0)
            if gaps:
                healed = await _heal(deps, contract_code)
                log.info("candle gaps %s: %d detected, %d rows re-persisted", contract_code, gaps, healed)
        except Exception as exc:  # noqa: BLE001 - watchdog must never crash the collector
            log.warning("candle gap check failed for %s: %s", contract_code, exc)
    try:
        await redis.set(GAP_GAUGE_KEY, str(total_gaps))
    except Exception as exc:  # noqa: BLE001
        log.warning("candle gap gauge write failed: %s", exc)
    return total_gaps


async def _heal(deps, contract_code: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
        candles = await deps["loader"].fetch_recent(client, contract_code=contract_code, period=deps["period"])
    if not candles:
        return 0
    return await deps["upsert"](await deps["get_pool"](), candles)
