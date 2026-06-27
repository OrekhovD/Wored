"""
Sim position monitor — периодическая проверка открытых позиций:
1. Ликвидация при достижении цены ликвидации
2. AI-управление: агент решает hold/close для ai_managed позиций
3. Funding rate каждые 8 часов
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

TAKER_FEE_RATE = 0.0006
LIQUIDATION_MARGIN = 0.005
FUNDING_RATE = 0.0001
FUNDING_INTERVAL_HOURS = 8


async def _get_pool():
    from storage.postgres_client import get_pool
    return await get_pool()


async def check_sim_positions():
    """Проверить все открытые симмулированные позиции."""
    try:
        from storage.redis_client import get_redis
        pool = await _get_pool()
        if not pool:
            return

        # Get all open positions
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE status = 'open' ORDER BY opened_at DESC"
            )

        if not rows:
            return

        # Get current prices from Redis
        redis = get_redis()
        current_prices = {}
        for row in rows:
            symbol = row["symbol"]
            if symbol not in current_prices:
                ticker_data = await redis.get(f"ticker:{symbol}")
                if ticker_data:
                    current_prices[symbol] = json.loads(ticker_data)["price"]

        if not current_prices:
            log.warning("No current prices for sim position check")
            return

        # 1. Check liquidations
        for row in rows:
            symbol = row["symbol"]
            if symbol not in current_prices:
                continue

            price = current_prices[symbol]
            entry = float(row["entry_price"])
            leverage = int(row["leverage"])
            direction = row["direction"]
            size = float(row["size"])
            margin = float(row["margin"])

            if direction == "long":
                liq_price = entry * (1 - 1 / leverage + LIQUIDATION_MARGIN)
                is_liq = price <= liq_price
            else:
                liq_price = entry * (1 + 1 / leverage - LIQUIDATION_MARGIN)
                is_liq = price >= liq_price

            if is_liq:
                # Close at liquidation price
                close_fee = float(row["notional"]) * TAKER_FEE_RATE
                if direction == "long":
                    raw_pnl = (liq_price - entry) * size
                else:
                    raw_pnl = (entry - liq_price) * size
                realized_pnl = raw_pnl - float(row["entry_fee"]) - close_fee - float(row["funding_paid"] or 0)

                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE sim_positions SET status='liquidated', close_price=$2, close_fee=$3, realized_pnl=$4, closed_at=NOW(), close_reason='liquidation' WHERE id=$1",
                        row["id"], liq_price, close_fee, realized_pnl,
                    )
                log.warning("Sim #%d LIQUIDATED: %s %s pnl=%.4f", row["id"], direction, symbol, realized_pnl)

        # 2. Apply funding (simplified — just log, real funding in chatbot)
        for row in rows:
            from datetime import datetime, timezone
            opened = row["opened_at"]
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            periods = int(age_h // FUNDING_INTERVAL_HOURS)
            if periods > 0:
                funding_cost = float(row["notional"]) * FUNDING_RATE * periods
                current_funding = float(row["funding_paid"] or 0)
                if abs(current_funding - funding_cost) > 0.0001:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE sim_positions SET funding_paid=$2 WHERE id=$1",
                            row["id"], funding_cost,
                        )

        # 3. AI-managed: skip in collector (AI eval runs in chatbot context)

    except Exception as exc:
        log.error("Sim monitor error: %s", exc)
