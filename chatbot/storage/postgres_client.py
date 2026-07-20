from __future__ import annotations

import asyncpg
import asyncio
import os
import logging
import json
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_pool = None

async def get_pool():
    global _pool
    if not _pool:
        db_url = os.getenv("DATABASE_URL")
        # Ensure correct driver format for asyncpg
        if db_url and "postgresql+asyncpg://" in db_url:
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        max_retries = 5
        backoff = 2
        for attempt in range(1, max_retries + 1):
            try:
                _pool = await asyncpg.create_pool(dsn=db_url)
                if _pool:
                    return _pool
            except Exception as e:
                log.error(f"Postgres connect error (attempt {attempt}/{max_retries}): {e}")
                _pool = None
            if attempt == max_retries:
                log.error("Postgres connect: all retries exhausted")
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
    return _pool

# ─── Table schemas for TЗ requirements ───────────────────────────────

EXT_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_model_prefs (
    user_id BIGINT PRIMARY KEY,
    model_alias VARCHAR(50) NOT NULL DEFAULT 'auto',
    auto_mode BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_log (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(64),
    user_id BIGINT,
    task_type VARCHAR(30),
    routing_mode VARCHAR(20),
    requested_model VARCHAR(80),
    final_model VARCHAR(80),
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,
    latency_ms INT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'ok',
    error_type VARCHAR(40),
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_usage_log_user ON usage_log (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_log_model ON usage_log (final_model, created_at);

CREATE TABLE IF NOT EXISTS strategy_rules (
    id SERIAL PRIMARY KEY,
    version INT NOT NULL,
    rules JSONB NOT NULL,
    source VARCHAR(30) DEFAULT 'strategy_learner',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sim_evaluations (
    id SERIAL PRIMARY KEY,
    evaluation_run_id VARCHAR(64) NOT NULL,
    total_positions INT DEFAULT 0,
    winrate DECIMAL(5,2) DEFAULT 0,
    avg_pnl DECIMAL(20,8) DEFAULT 0,
    max_drawdown DECIMAL(5,2) DEFAULT 0,
    liquidation_rate DECIMAL(5,2) DEFAULT 0,
    details JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def ensure_ext_tables():
    """Create TЗ-related tables if they don't exist."""
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        for stmt in [s.strip() for s in EXT_TABLES_SQL.split(";") if s.strip()]:
            await conn.execute(stmt)


# ─── user_model_prefs CRUD ────────────────────────────────────────────

async def save_user_model(user_id: int, model_alias: str, auto_mode: bool = True) -> bool:
    """Save user's model preference."""
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_model_prefs (user_id, model_alias, auto_mode, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                model_alias = EXCLUDED.model_alias,
                auto_mode = EXCLUDED.auto_mode,
                updated_at = NOW()
            """,
            user_id, model_alias, auto_mode,
        )
    return True


async def get_user_model(user_id: int) -> dict | None:
    """Get user's model preference."""
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, model_alias, auto_mode FROM user_model_prefs WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row else None


# ─── usage_log CRUD ───────────────────────────────────────────────────

async def record_usage(
    request_id: str, user_id: int, task_type: str, routing_mode: str,
    requested_model: str, final_model: str,
    prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0,
    latency_ms: int = 0, status: str = "ok", error_type: str = None, error_msg: str = None,
) -> bool:
    """Log an AI request for token accounting."""
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO usage_log
                (request_id, user_id, task_type, routing_mode, requested_model, final_model,
                 prompt_tokens, completion_tokens, total_tokens, latency_ms, status, error_type, error_msg)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            request_id, user_id, task_type, routing_mode, requested_model, final_model,
            prompt_tokens, completion_tokens, total_tokens, latency_ms, status, error_type, error_msg,
        )
    return True


async def get_usage_summary(user_id: int, period: str = "day") -> dict:
    """Get usage summary for a user. period: day/week/month."""
    pool = await get_pool()
    if not pool:
        return {"requests": 0, "total_tokens": 0}
    interval = {"day": "1 day", "week": "7 days", "month": "30 days"}.get(period, "1 day")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) as requests, COALESCE(SUM(total_tokens), 0) as total_tokens
            FROM usage_log
            WHERE user_id = $1 AND created_at >= NOW() - INTERVAL '{interval}'
            """,
            user_id,
        )
    return {"requests": row["requests"], "total_tokens": row["total_tokens"]} if row else {"requests": 0, "total_tokens": 0}


# ─── strategy_rules CRUD ──────────────────────────────────────────────

async def save_strategy_rules(rules: dict, version: int = 1, source: str = "strategy_learner") -> bool:
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategy_rules (version, rules, source) VALUES ($1, $2, $3)",
            version, json.dumps(rules), source,
        )
    return True


async def get_latest_strategy_rules() -> dict | None:
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT version, rules, created_at FROM strategy_rules ORDER BY id DESC LIMIT 1"
        )
    if not row:
        return None
    return {"version": row["version"], "rules": json.loads(row["rules"]) if isinstance(row["rules"], str) else row["rules"], "created_at": _serialize_dt(row["created_at"])}


# ─── sim_evaluations CRUD ─────────────────────────────────────────────

async def save_sim_evaluation(run_id: str, total: int, winrate: float, avg_pnl: float, max_dd: float, liq_rate: float, details: dict = None) -> bool:
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sim_evaluations (evaluation_run_id, total_positions, winrate, avg_pnl, max_drawdown, liquidation_rate, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            run_id, total, winrate, avg_pnl, max_dd, liq_rate, json.dumps(details) if details else None,
        )
    return True



async def get_recent_alert_history(limit: int = 5):
    pool = await get_pool()
    if not pool: return []
    query = """
    SELECT symbol, threshold, timestamp 
    FROM alerts 
    ORDER BY timestamp DESC 
    LIMIT $1
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, limit)
        return [dict(r) for r in records]


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _format_ui_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _build_prediction_request_status(row: asyncpg.Record) -> dict:
    avg_accuracy = float(row["avg_accuracy"]) if row["avg_accuracy"] is not None else None
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "horizon_hours": int(row["horizon_hours"]),
        "base_price": float(row["base_price"]),
        "status": row["status"],
        "source": row["source"] or "webui",
        "requested_by": row["requested_by"] or "unknown",
        "created_at": _serialize_dt(row["created_at"]),
        "created_at_display": _format_ui_timestamp(row["created_at"]),
        "updated_at": _serialize_dt(row["updated_at"]),
        "updated_at_display": _format_ui_timestamp(row["updated_at"]),
        "completed_models": int(row["completed_models"] or 0),
        "failed_models": int(row["failed_models"] or 0),
        "total_points": int(row["total_points"] or 0),
        "evaluated_points": int(row["evaluated_points"] or 0),
        "avg_accuracy": avg_accuracy,
        "avg_failure": round(100.0 - avg_accuracy, 2) if avg_accuracy is not None else None,
    }


def _split_model_display_name(model_name: str) -> tuple[str, str]:
    if " / " not in model_name:
        return "", model_name.strip()
    role_name, short_name = model_name.split(" / ", 1)
    return role_name.strip(), short_name.strip()


async def get_recent_prediction_requests(limit: int = 5, symbol: str | None = None) -> list[dict]:
    pool = await get_pool()
    if not pool:
        return []

    query = """
    SELECT
        fr.id,
        fr.symbol,
        fr.horizon_hours,
        fr.base_price,
        fr.status,
        fr.source,
        fr.requested_by,
        fr.created_at,
        fr.updated_at,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status = 'completed'
        ), 0) AS completed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status <> 'completed'
        ), 0) AS failed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ), 0) AS total_points,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
              AND fp.evaluated_at IS NOT NULL
        ), 0) AS evaluated_points,
        (
            SELECT ROUND(AVG(fp.accuracy_score)::numeric, 2)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ) AS avg_accuracy
    FROM forecast_requests fr
    WHERE ($2::text IS NULL OR fr.symbol = $2)
    ORDER BY fr.created_at DESC
    LIMIT $1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, limit, symbol)
    return [_build_prediction_request_status(row) for row in rows]


async def get_prediction_request_detail(request_id: int) -> dict | None:
    pool = await get_pool()
    if not pool:
        return None

    summary_query = """
    SELECT
        fr.id,
        fr.symbol,
        fr.horizon_hours,
        fr.base_price,
        fr.status,
        fr.source,
        fr.requested_by,
        fr.created_at,
        fr.updated_at,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status = 'completed'
        ), 0) AS completed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status <> 'completed'
        ), 0) AS failed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ), 0) AS total_points,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
              AND fp.evaluated_at IS NOT NULL
        ), 0) AS evaluated_points,
        (
            SELECT ROUND(AVG(fp.accuracy_score)::numeric, 2)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ) AS avg_accuracy
    FROM forecast_requests fr
    WHERE fr.id = $1
    """
    points_query = """
    SELECT
        fmr.id AS model_run_id,
        fmr.model_key,
        fmr.model_name,
        fmr.model_id,
        fmr.status AS model_status,
        fmr.summary,
        fmr.error_message,
        fp.id AS point_id,
        fp.forecast_hour,
        fp.target_time,
        fp.predicted_price,
        fp.predicted_change_pct,
        fp.confidence,
        fp.rationale,
        fp.actual_price,
        fp.actual_change_pct,
        fp.price_error_pct,
        fp.change_error_pct,
        fp.accuracy_score,
        fp.failure_score,
        fp.direction_match,
        fp.verdict,
        fp.evaluated_at
    FROM forecast_model_runs fmr
    LEFT JOIN forecast_points fp ON fp.model_run_id = fmr.id
    WHERE fmr.request_id = $1
    ORDER BY fmr.created_at ASC, fp.forecast_hour ASC
    """

    async with pool.acquire() as conn:
        summary_row = await conn.fetchrow(summary_query, request_id)
        if summary_row is None:
            return None
        point_rows = await conn.fetch(points_query, request_id)

    detail = _build_prediction_request_status(summary_row)
    model_runs: dict[int, dict] = {}

    for row in point_rows:
        model_run_id = row["model_run_id"]
        if model_run_id not in model_runs:
            role_name, short_name = _split_model_display_name(row["model_name"])
            model_runs[model_run_id] = {
                "id": model_run_id,
                "model_key": row["model_key"],
                "model_name": row["model_name"],
                "model_id": row["model_id"],
                "status": row["model_status"],
                "summary": row["summary"] or "",
                "error_message": row["error_message"] or "",
                "role_name": role_name,
                "short_name": short_name,
                "points": [],
                "avg_accuracy": None,
                "avg_failure": None,
                "evaluated_points": 0,
            }

        if row["point_id"] is None:
            continue

        accuracy_score = float(row["accuracy_score"]) if row["accuracy_score"] is not None else None
        failure_score = (
            float(row["failure_score"])
            if row["failure_score"] is not None
            else round(100.0 - accuracy_score, 2) if accuracy_score is not None else None
        )
        model_runs[model_run_id]["points"].append(
            {
                "id": row["point_id"],
                "forecast_hour": int(row["forecast_hour"]),
                "target_time": _serialize_dt(row["target_time"]),
                "target_time_display": _format_ui_timestamp(row["target_time"]),
                "predicted_price": float(row["predicted_price"]),
                "predicted_change_pct": float(row["predicted_change_pct"]),
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "rationale": row["rationale"] or "",
                "actual_price": float(row["actual_price"]) if row["actual_price"] is not None else None,
                "actual_change_pct": float(row["actual_change_pct"]) if row["actual_change_pct"] is not None else None,
                "price_error_pct": float(row["price_error_pct"]) if row["price_error_pct"] is not None else None,
                "change_error_pct": float(row["change_error_pct"]) if row["change_error_pct"] is not None else None,
                "accuracy_score": accuracy_score,
                "failure_score": failure_score,
                "direction_match": row["direction_match"],
                "verdict": row["verdict"] or "",
                "evaluated_at": _serialize_dt(row["evaluated_at"]),
                "evaluated_at_display": _format_ui_timestamp(row["evaluated_at"]),
            }
        )

    detail["models"] = list(model_runs.values())
    for model in detail["models"]:
        scores = [point["accuracy_score"] for point in model["points"] if point["accuracy_score"] is not None]
        model["evaluated_points"] = len(scores)
        model["avg_accuracy"] = round(sum(scores) / len(scores), 2) if scores else None
        model["avg_failure"] = round(100.0 - model["avg_accuracy"], 2) if model["avg_accuracy"] is not None else None

    ranked_models = [model for model in detail["models"] if model["avg_accuracy"] is not None]
    detail["top_model"] = max(ranked_models, key=lambda item: item["avg_accuracy"]) if ranked_models else None
    return detail


async def get_latest_prediction_request(symbol: str | None = None) -> dict | None:
    items = await get_recent_prediction_requests(limit=1, symbol=symbol)
    if not items:
        return None
    return await get_prediction_request_detail(items[0]["id"])

