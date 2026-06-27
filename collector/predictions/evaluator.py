"""
Performance Evaluator - evaluates sim position series quality.
Metrics: winrate, avg_pnl, max_drawdown, liquidation_rate.
"""
from __future__ import annotations

import asyncpg
import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def _get_pool():
    db_url = os.getenv("DATABASE_URL")
    if db_url and "postgresql+asyncpg://" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn=db_url)


async def evaluate_sim_series(user_id: int | None = None, min_positions: int = 5) -> dict | None:
    """
    Evaluate a series of closed sim positions.
    Returns: {"run_id", "total", "winrate", "avg_pnl", "max_drawdown", "liquidation_rate", "details"}
    """
    pool = await _get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE user_id = $1 AND status IN ('closed','liquidated') ORDER BY closed_at DESC LIMIT 100",
                user_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE status IN ('closed','liquidated') ORDER BY closed_at DESC LIMIT 100"
            )

    if len(rows) < min_positions:
        return None

    total = len(rows)
    wins = 0
    pnl_list = []
    liquidations = 0
    peak = 0.0
    max_dd = 0.0

    cumulative = 0.0
    for r in rows:
        pnl = float(r["realized_pnl"] or 0)
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

        pnl_list.append(pnl)
        if pnl > 0:
            wins += 1
        if r["status"] == "liquidated" or r["close_reason"] == "liquidation":
            liquidations += 1

    winrate = (wins / total * 100) if total > 0 else 0
    avg_pnl = sum(pnl_list) / total if total > 0 else 0
    liq_rate = (liquidations / total * 100) if total > 0 else 0

    run_id = uuid.uuid4().hex[:16]
    details = {
        "wins": wins, "losses": total - wins, "liquidations": liquidations,
        "best_pnl": max(pnl_list) if pnl_list else 0,
        "worst_pnl": min(pnl_list) if pnl_list else 0,
    }

    # Save evaluation to DB
    from storage.postgres_client import save_sim_evaluation
    try:
        await save_sim_evaluation(run_id, total, winrate, avg_pnl, max_dd, liq_rate, details)
    except Exception:
        pass  # collector may not have chatbot's postgres_client

    log.info("Sim evaluation %s: total=%d winrate=%.1f%% avg_pnl=%.4f max_dd=%.4f liq=%.1f%%",
             run_id, total, winrate, avg_pnl, max_dd, liq_rate)

    return {
        "run_id": run_id,
        "total": total,
        "winrate": round(winrate, 2),
        "avg_pnl": round(avg_pnl, 4),
        "max_drawdown": round(max_dd, 4),
        "liquidation_rate": round(liq_rate, 2),
        "details": details,
    }
