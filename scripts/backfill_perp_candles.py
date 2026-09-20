#!/usr/bin/env python3
"""Backfill HTX linear-swap 1m candle history into ``trader_v1_perp_candles``.

Block A (self-learning data layer): the perpetual store is the single source of
truth for price history.  This script pages the public HTX ``/linear-swap-ex``
kline endpoint backwards in time, validates every page through the same
``parse_closed_candles`` boundary the collector uses (closed-only, dedup, OHLC
consistency), and persists idempotently.  Higher timeframes are derived from the
stored 1m rows, never fetched independently.

Spot REST is deliberately not touched here: the perpetual contract price is the
reference for the learning loop.

Usage:
    python scripts/backfill_perp_candles.py --contract BTC-USDT --days 180
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from collector.htx.history_loader import (  # noqa: E402
    DEFAULT_BASE_URL,
    MAX_PAGE_SIZE,
    NETWORK_PERIOD,
    HtxHistoryLoader,
    parse_closed_candles,
)
from collector.storage import perp_candles  # noqa: E402

log = logging.getLogger("backfill_perp")

DEFAULT_DAYS = 180
# HTX returns at most ``size`` candles per request; align the time window to the
# page size so no candle inside a window is silently dropped.
PAGE_SECONDS_PER_CANDLE = 60  # 1m period


async def _fetch_page(
    client: httpx.AsyncClient,
    loader: HtxHistoryLoader,
    *,
    contract_code: str,
    start_ms: int,
    end_ms: int,
) -> list:
    response = await client.get(
        loader.base_url + "/linear-swap-ex/market/history/kline",
        params={
            "contract_code": contract_code,
            "period": NETWORK_PERIOD,
            "size": MAX_PAGE_SIZE,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
    )
    response.raise_for_status()
    payload = response.json()
    return parse_closed_candles(
        payload,
        contract_code=contract_code,
        period=NETWORK_PERIOD,
        received_at=datetime.now(timezone.utc),
    )


async def backfill(contract_code: str, days: int, base_url: str) -> int:
    pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
    loader = HtxHistoryLoader(base_url=base_url)
    try:
        await perp_candles.ensure_perp_schema(pool)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        window = timedelta(seconds=MAX_PAGE_SIZE * PAGE_SECONDS_PER_CANDLE)
        total_new = 0
        cursor = start
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            while cursor < end:
                page_end = min(cursor + window, end)
                start_ms = int(cursor.timestamp() * 1000)
                end_ms = int(page_end.timestamp() * 1000)
                try:
                    candles = await _fetch_page(
                        client,
                        loader,
                        contract_code=contract_code,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                except Exception as exc:  # noqa: BLE001 - keep paging on transient errors
                    log.warning("page %s..%s failed: %s", cursor.date(), page_end.date(), exc)
                    cursor = page_end
                    await asyncio.sleep(1.0)
                    continue
                inserted = await perp_candles.bulk_upsert_candles(pool, candles)
                total_new += inserted
                log.info(
                    "%s -> %s: %d candles (%d new)",
                    cursor.isoformat(),
                    page_end.isoformat(),
                    len(candles),
                    inserted,
                )
                cursor = page_end
                await asyncio.sleep(0.2)  # be polite to the public endpoint

        for timeframe in perp_candles.DERIVED_TIMEFRAMES:
            stored = await perp_candles.aggregate_timeframe(pool, contract_code, timeframe)
            log.info("derived %s: %d buckets", timeframe, stored)

        final_count = await perp_candles.count_rows(pool, contract_code, "1min")
        log.info("1m rows now stored for %s: %d", contract_code, final_count)
        return total_new
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill HTX linear-swap 1m history")
    parser.add_argument("--contract", default="BTC-USDT", help="Perpetual contract code (e.g. BTC-USDT)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Days of history to backfill")
    parser.add_argument("--base-url", default=os.getenv("HTX_LINEAR_SWAP_BASE_URL", DEFAULT_BASE_URL))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if "huobi.pro" in args.base_url:
        parser.error("backfill must target the linear-swap host (api.hbdm.com), not spot")
    asyncio.run(backfill(args.contract.upper(), args.days, args.base_url))


if __name__ == "__main__":
    main()
