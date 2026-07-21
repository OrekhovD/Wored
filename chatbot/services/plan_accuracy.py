"""
Plan accuracy tracker — records predictions and evaluates them against actual market moves.
Called on every plan revision to record what was predicted, and 1h later to evaluate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def record_plan_prediction(session_id: str, plan_version: int, revision_id: Optional[str] = None):
    """Record all planned_entries of the given version as predictions in plan_accuracy."""
    from storage.postgres_client import get_pool
    from storage.redis_client import get_redis
    import uuid

    pool = await get_pool()
    if not pool:
        return

    # Get current price
    redis = get_redis()
    ticker_raw = await redis.get("ticker:btcusdt")
    current_price = 0.0
    if ticker_raw:
        t = json.loads(ticker_raw)
        current_price = float(t.get("price", 0))

    async with pool.acquire() as conn:
        entries = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id=$1 AND plan_version=$2 AND status='planned'",
            session_id, plan_version,
        )
        # Get plan regime from session_plans
        plan_row = await conn.fetchrow(
            "SELECT plan_json FROM session_plans WHERE session_id=$1 AND version=$2",
            session_id, plan_version,
        )
        regime = None
        if plan_row and plan_row["plan_json"]:
            pj = plan_row["plan_json"]
            if isinstance(pj, str):
                pj = json.loads(pj)
            regime = pj.get("regime") or pj.get("market_regime")

        for entry in entries:
            tp_raw = entry["take_profit_json"]
            if isinstance(tp_raw, str):
                tp_raw = json.loads(tp_raw)
            await conn.execute(
                """
                INSERT INTO plan_accuracy
                    (id, session_id, plan_version, revision_id,
                     predicted_direction, predicted_entry_from, predicted_entry_to,
                     predicted_stop_loss, predicted_take_profit_json, predicted_regime,
                     actual_price_at_creation, outcome)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'pending')
                """,
                str(uuid.uuid4()), session_id, plan_version, revision_id,
                entry["side"],
                float(entry["entry_zone_from"]),
                float(entry["entry_zone_to"]),
                float(entry["stop_loss"]),
                json.dumps(tp_raw),
                regime,
                current_price,
            )
        log.info("Recorded %d plan predictions for v%d (price=$%.2f)", len(entries), plan_version, current_price)


async def evaluate_pending_predictions():
    """Evaluate predictions that are >=1h old and still pending.
    Fetch actual 1h high/low from HTX REST and compare.
    """
    from storage.postgres_client import get_pool
    import httpx

    pool = await get_pool()
    if not pool:
        return

    async with pool.acquire() as conn:
        pending = await conn.fetch(
            """
            SELECT * FROM plan_accuracy
            WHERE outcome = 'pending'
            AND created_at <= NOW() - INTERVAL '1 hour'
            """,
        )

    if not pending:
        return

    log.info("Evaluating %d pending plan predictions", len(pending))

    # Fetch 1h candle range from HTX for BTCUSDT
    actual_high_1h = {}
    actual_low_1h = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as hc:
            resp = await hc.get(
                "https://api.huobi.pro/market/history/kline",
                params={"symbol": "btcusdt", "period": "60min", "size": 2},
            )
            resp.raise_for_status()
            klines = resp.json().get("data", [])
            if klines:
                # Use the candle from ~1h ago
                k = klines[-2] if len(klines) > 1 else klines[-1]
                actual_high_1h["btcusdt"] = float(k["high"])
                actual_low_1h["btcusdt"] = float(k["low"])
                # Current price
                k_now = klines[-1]
                actual_high_1h["current"] = float(k_now["close"])
    except Exception as exc:
        log.warning("Failed to fetch HTX klines for evaluation: %s", exc)
        return

    for pred in pending:
        d = dict(pred)
        entry_from = float(d["predicted_entry_from"])
        entry_to = float(d["predicted_entry_to"])
        sl = float(d["predicted_stop_loss"])
        direction = d["predicted_direction"]
        price_at_creation = float(d["actual_price_at_creation"] or 0)

        high_1h = actual_high_1h.get("btcusdt", 0)
        low_1h = actual_low_1h.get("btcusdt", 0)
        current_price = actual_high_1h.get("current", 0)

        # Evaluate
        entry_touched = low_1h <= entry_to and high_1h >= entry_from
        sl_hit = (direction == "long" and low_1h <= sl) or (direction == "short" and high_1h >= sl)

        # Direction correctness: did price move in predicted direction?
        if direction == "long":
            direction_correct = current_price > price_at_creation
        else:
            direction_correct = current_price < price_at_creation

        # TP hit
        tp_list = d.get("predicted_take_profit_json", [])
        if isinstance(tp_list, str):
            tp_list = json.loads(tp_list)
        tp_hit = False
        if tp_list:
            tp1 = float(tp_list[0])
            if direction == "long":
                tp_hit = high_1h >= tp1
            else:
                tp_hit = low_1h <= tp1

        # Accuracy score (0-100)
        score = 0
        if direction_correct:
            score += 30
        if entry_touched:
            score += 25
        if tp_hit:
            score += 30
        if not sl_hit:
            score += 15

        # Outcome
        if tp_hit:
            outcome = "profit"
        elif sl_hit:
            outcome = "loss"
        elif entry_touched:
            outcome = "expired"
        else:
            outcome = "expired"

        price_change_pct = 0
        if price_at_creation > 0:
            price_change_pct = ((current_price - price_at_creation) / price_at_creation) * 100

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE plan_accuracy SET
                    actual_price_1h_later = $2,
                    actual_high_1h = $3,
                    actual_low_1h = $4,
                    price_change_pct_1h = $5,
                    direction_correct = $6,
                    entry_zone_touched = $7,
                    stop_loss_hit = $8,
                    take_profit_hit = $9,
                    accuracy_score = $10,
                    outcome = $11,
                    evaluated_at = NOW()
                WHERE id = $1
                """,
                d["id"], current_price, high_1h, low_1h, price_change_pct,
                direction_correct, entry_touched, sl_hit, tp_hit,
                score, outcome,
            )

    log.info("Evaluated %d plan predictions", len(pending))


async def get_accuracy_summary(session_id: str) -> dict:
    """Get accuracy summary for a session."""
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if not pool:
        return {"error": "No DB"}

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM plan_accuracy WHERE session_id=$1",
            session_id,
        )
        evaluated = await conn.fetchval(
            "SELECT count(*) FROM plan_accuracy WHERE session_id=$1 AND outcome != 'pending'",
            session_id,
        )
        avg_score = await conn.fetchval(
            "SELECT avg(accuracy_score) FROM plan_accuracy WHERE session_id=$1 AND outcome != 'pending'",
            session_id,
        )
        direction_accuracy = await conn.fetchval(
            "SELECT count(*)::float / NULLIF(count(*),0) * 100 FROM plan_accuracy WHERE session_id=$1 AND outcome != 'pending' AND direction_correct = true",
            session_id,
        )
        profit_count = await conn.fetchval(
            "SELECT count(*) FROM plan_accuracy WHERE session_id=$1 AND outcome='profit'",
            session_id,
        )
        loss_count = await conn.fetchval(
            "SELECT count(*) FROM plan_accuracy WHERE session_id=$1 AND outcome='loss'",
            session_id,
        )
        # Version-by-version breakdown
        versions = await conn.fetch(
            """
            SELECT plan_version, count(*) as total,
                   avg(accuracy_score) as avg_score,
                   count(*) FILTER (WHERE outcome='profit') as profit,
                   count(*) FILTER (WHERE outcome='loss') as loss,
                   count(*) FILTER (WHERE outcome='pending') as pending
            FROM plan_accuracy WHERE session_id=$1
            GROUP BY plan_version ORDER BY plan_version
            """,
            session_id,
        )

    return {
        "total_predictions": total or 0,
        "evaluated": evaluated or 0,
        "avg_accuracy_score": round(float(avg_score or 0), 1),
        "direction_accuracy_pct": round(float(direction_accuracy or 0), 1),
        "profit_count": profit_count or 0,
        "loss_count": loss_count or 0,
        "versions": [dict(v) for v in versions],
    }


async def get_plan_diff(session_id: str, version_a: int, version_b: int) -> dict:
    """Compare two plan versions — what changed between them."""
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if not pool:
        return {"error": "No DB"}

    async with pool.acquire() as conn:
        entries_a = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id=$1 AND plan_version=$2 ORDER BY side, entry_zone_from",
            session_id, version_a,
        )
        entries_b = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id=$1 AND plan_version=$2 ORDER BY side, entry_zone_from",
            session_id, version_b,
        )

        revisions = await conn.fetch(
            "SELECT * FROM session_revisions WHERE session_id=$1 AND base_version=$2 AND new_version=$3",
            session_id, version_a, version_b,
        )

    def _entry_summary(e):
        d = dict(e)
        return {
            "side": d["side"],
            "status": d["status"],
            "entry_from": float(d["entry_zone_from"]),
            "entry_to": float(d["entry_zone_to"]),
            "stop_loss": float(d["stop_loss"]),
            "leverage": d["recommended_leverage"],
        }

    return {
        "version_a": version_a,
        "version_b": version_b,
        "entries_a": [_entry_summary(e) for e in entries_a],
        "entries_b": [_entry_summary(e) for e in entries_b],
        "revision": dict(revisions[0]) if revisions else None,
    }