"""Standalone paper-trading runner for staging acceptance tests.

Runs inside the webui container image (asyncpg + redis already installed).
Seeds a synthetic PerpetualSnapshot to Redis every 2 s, then polls
paper_v2_commands and executes them via PaperTradingRunner.

Usage (inside container):
    python /scripts/staging_runner.py

Environment:
    DATABASE_URL   postgresql://user:pass@host:5432/db
    REDIS_URL      redis://host:6379/0
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [runner] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("staging_runner")

# ── Demo snapshot seeding ──────────────────────────────────────────────

INSTRUMENT = "BTC-USDT"
REDIS_SNAPSHOT_KEY = f"market:perpetual:htx:{INSTRUMENT}"
DEMO_PRICE = "64250"


def _make_demo_payload() -> str:
    """Build a JSON snapshot dict that passes BOTH:
    - paper_trading/market.py:snapshot_from_dict (runner)
    - webui/paper_market.py:parse_live_snapshot (webui in live mode)
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": 1,
        "mode": "live",
        "venue": "htx",
        "market_type": "linear-swap",
        "contract_code": INSTRUMENT,
        "bid": DEMO_PRICE,
        "ask": DEMO_PRICE,
        "last": DEMO_PRICE,
        "mark": DEMO_PRICE,
        "index": DEMO_PRICE,
        "funding_rate": "0.0001",
        "next_funding_at": ts,
        "component_times": {
            "ticker": ts,
            "index": ts,
            "mark": ts,
            "funding": ts,
        },
        "contract_size": "0.001",
        "price_tick": "0.1",
        "quantity_step": "0.001",
        "source_at": ts,
        "received_at": ts,
        "source": "staging-synthetic",
        "quality": "live",
    }
    return json.dumps(payload)


async def seed_snapshot(redis_client) -> None:
    """Write a fresh demo snapshot to Redis (called every poll cycle)."""
    payload = _make_demo_payload()
    await redis_client.set(REDIS_SNAPSHOT_KEY, payload, ex=30)


# ── Main ───────────────────────────────────────────────────────────────

async def main() -> None:
    import asyncpg
    import redis.asyncio as aioredis

    from paper_trading.runner import (
        PaperTradingRunner,
        PgCommandSource,
        PgRecoveryStore,
        RedisMarketDataSource,
    )
    from paper_trading.repository import PaperRepository
    from paper_trading.strategy import BaselineV1Strategy
    from paper_trading.risk import RiskSettings

    db_url = os.getenv("DATABASE_URL", "postgresql://woredstg:woredstg-disposable-only@postgres:5432/wored_staging")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

    log.info("Connecting PostgreSQL: %s", db_url.split("@")[0] + "@***")
    pool = await asyncpg.create_pool(dsn=db_url, min_size=2, max_size=5)

    log.info("Connecting Redis: %s", redis_url)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    # Verify connections
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    await redis_client.ping()
    log.info("DB + Redis connections OK")

    # Create runner
    repo = PaperRepository(pool)
    market_data = RedisMarketDataSource(redis_client, contract_code=INSTRUMENT)
    command_source = PgCommandSource(repo)
    recovery_store = PgRecoveryStore(repo)

    runner = PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        repository=repo,
        market_data=market_data,
        command_source=command_source,
        recovery_store=recovery_store,
        risk_settings=RiskSettings(require_risk_tier=False),
        poll_interval=2.0,
        heartbeat_interval=5.0,
    )

    # Initial seed + recovery
    await seed_snapshot(redis_client)
    if runner.recovery_store:
        report = await runner.recover()
        log.info("Recovery: %s", report)

    log.info("Staging runner started (poll=2s, demo_price=%s)", DEMO_PRICE)

    # Main loop: seed snapshot + run cycle
    try:
        while True:
            await seed_snapshot(redis_client)
            await runner.run_cycle()
            await asyncio.sleep(2.0)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        await pool.close()
        await redis_client.close()
        log.info("Connections closed")


if __name__ == "__main__":
    asyncio.run(main())
