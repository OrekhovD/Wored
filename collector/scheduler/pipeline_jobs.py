"""
Daily Pipeline WORED v2 — Scheduler jobs for collector.

ТЗ раздел 11 — 10 джобов:
  prepare_daily_context (T-30 мин)
  close_previous_day_stats (T-20 мин)
  generate_initial_8h_plan (T-10 мин)
  session_bootstrap (T)
  execution_watch_loop (каждые 10 сек)
  stats_snapshot (каждые 60 сек)
  hourly_recalibration (каждый час)
  stale_data_guard (каждые 30 сек)
  session_closeout (по окончании окна)
  post_session_review (после closeout)

Эти джобы встраиваются в существующий APScheduler контур collector/main.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─── Stale Data Guard (ТЗ 11: каждые 30 сек) ───────────────────────────

STALE_THRESHOLD_SECONDS = 60  # SLA: WebSocket snapshot must be < 60s old


async def stale_data_guard():
    """
    ТЗ 11, 10 — проверка свежести WebSocket/Redis snapshot.
    Если stale → ставит execution в PAUSED для всех активных сессий.
    """
    try:
        from storage.redis_client import get_redis
        from storage.postgres_client import get_pool

        redis = get_redis()
        pool = await get_pool()
        if not pool:
            return

        # Check BTCUSDT ticker freshness
        ticker_raw = await redis.get("ticker:btcusdt")
        is_stale = True
        if ticker_raw:
            data = json.loads(ticker_raw)
            # Check if timestamp exists and is fresh
            ts_str = data.get("timestamp") or data.get("ts")
            if ts_str:
                try:
                    if isinstance(ts_str, (int, float)):
                        ts = datetime.fromtimestamp(float(ts_str) / 1000 if float(ts_str) > 1e12 else float(ts_str), tz=timezone.utc)
                    else:
                        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    age = (_now_utc() - ts).total_seconds()
                    is_stale = age > STALE_THRESHOLD_SECONDS
                except Exception:
                    is_stale = True  # can't parse → treat as stale
            else:
                # No timestamp — check if we got data at all
                is_stale = False  # assume fresh if data exists

        if is_stale:
            log.warning("Stale data detected — pausing active sessions")
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE trading_sessions
                    SET status = 'paused', updated_at = NOW()
                    WHERE status IN ('armed', 'in_position')
                    """,
                )
                # Log event for each paused session
                sessions = await conn.fetch(
                    "SELECT id FROM trading_sessions WHERE status = 'paused' AND updated_at > NOW() - INTERVAL '1 minute'"
                )
                for s in sessions:
                    import uuid
                    await conn.execute(
                        """
                        INSERT INTO execution_events (id, session_id, event_type, event_payload)
                        VALUES ($1, $2, 'stale_data_pause', $3)
                        """,
                        str(uuid.uuid4()), str(s["id"]),
                        json.dumps({"reason": "websocket_stale", "threshold_sec": STALE_THRESHOLD_SECONDS}),
                    )

    except Exception as exc:
        log.error("stale_data_guard error: %s", exc)


# ─── Execution Watch Loop (ТЗ 11: каждые 10 сек) ───────────────────────

async def execution_watch_loop_job():
    """
    ТЗ 11 — проверка entry/exit/invalidation для всех активных сессий.
    """
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return

        async with pool.acquire() as conn:
            sessions = await conn.fetch(
                """
                SELECT id FROM trading_sessions
                WHERE status IN ('armed', 'in_position', 'cooldown', 'idle')
                AND session_end > NOW()
                """,
            )

        for s in sessions:
            try:
                # Import defensively — session_manager lives in chatbot package
                import importlib
                sm_mod = importlib.import_module("services.session_manager")
                result = await sm_mod.execution_watch_loop(str(s["id"]))
                if result.get("actions"):
                    log.info("Watch loop session %s: %d actions", s["id"], len(result["actions"]))
            except Exception as exc:
                log.warning("Watch loop failed for session %s: %s", s["id"], exc)

    except Exception as exc:
        log.error("execution_watch_loop_job error: %s", exc)


# ─── Stats Snapshot (ТЗ 11: каждые 60 сек) ─────────────────────────────

async def stats_snapshot_job():
    """
    ТЗ 11 — обновить equity, PnL, drawdown для всех активных сессий.
    """
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return

        async with pool.acquire() as conn:
            sessions = await conn.fetch(
                "SELECT id FROM trading_sessions WHERE status NOT IN ('completed', 'stopped')"
            )

        for s in sessions:
            try:
                import importlib
                sa_mod = importlib.import_module("services.stats_audit")
                await sa_mod.stats_snapshot(str(s["id"]))
            except Exception as exc:
                log.warning("Stats snapshot failed for session %s: %s", s["id"], exc)

    except Exception as exc:
        log.error("stats_snapshot_job error: %s", exc)


# ─── Hourly Recalibration (ТЗ 11: каждый час) ──────────────────────────

async def hourly_recalibration_job():
    """
    ТЗ 11 — выпустить patch нового плана для всех активных сессий.
    """
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return

        async with pool.acquire() as conn:
            sessions = await conn.fetch(
                "SELECT id FROM trading_sessions WHERE status IN ('armed', 'in_position', 'cooldown', 'paused', 'idle')"
            )

        for s in sessions:
            try:
                import importlib
                sm_mod = importlib.import_module("services.session_manager")
                result = await sm_mod.hourly_revision(str(s["id"]))
                if "error" not in result:
                    log.info("Hourly revision for session %s: v%d cmd=%s",
                             s["id"], result.get("new_version"), result.get("execution_command"))
            except Exception as exc:
                log.warning("Hourly revision failed for session %s: %s", s["id"], exc)

    except Exception as exc:
        log.error("hourly_recalibration_job error: %s", exc)


# ─── Session Closeout (ТЗ 11: по окончании окна) ───────────────────────

async def session_closeout_job():
    """
    ТЗ 11 — закрыть сессии, у которых истекло окно.
    """
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return

        async with pool.acquire() as conn:
            expired = await conn.fetch(
                """
                SELECT id FROM trading_sessions
                WHERE session_end <= NOW()
                AND status NOT IN ('completed', 'stopped')
                """,
            )

        for s in expired:
            try:
                import importlib
                sa_mod = importlib.import_module("services.stats_audit")
                result = await sa_mod.session_closeout(str(s["id"]))
                log.info("Session %s closed: pnl=%.4f trades=%d",
                         s["id"], result.get("total_pnl_usdt", 0), result.get("trade_count", 0))
            except Exception as exc:
                log.warning("Closeout failed for session %s: %s", s["id"], exc)

    except Exception as exc:
        log.error("session_closeout_job error: %s", exc)


# ─── Post-Session Review (ТЗ 11: после closeout) ───────────────────────

async def post_session_review_job():
    """
    ТЗ 11 — Premium review для завершённых сессий без review.
    """
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return

        async with pool.acquire() as conn:
            # Find completed sessions without review
            sessions_needing_review = await conn.fetch(
                """
                SELECT ts.id FROM trading_sessions ts
                WHERE ts.status = 'completed'
                AND NOT EXISTS (
                    SELECT 1 FROM daily_reviews dr WHERE dr.session_id = ts.id AND dr.status = 'completed'
                )
                """,
            )

        for s in sessions_needing_review:
            try:
                import importlib
                sa_mod = importlib.import_module("services.stats_audit")
                result = await sa_mod.post_session_review(str(s["id"]))
                log.info("Review for session %s: model=%s status=%s",
                         s["id"], result.get("model_used"), result.get("status"))
            except Exception as exc:
                log.warning("Review failed for session %s: %s", s["id"], exc)

    except Exception as exc:
        log.error("post_session_review_job error: %s", exc)


# ─── Plan Accuracy Evaluation (каждые 15 мин) ─────────────────────────

async def evaluate_plan_accuracy_job():
    """
    Evaluate pending plan predictions that are >=1h old.
    """
    try:
        import importlib
        pa_mod = importlib.import_module("services.plan_accuracy")
        await pa_mod.evaluate_pending_predictions()
    except Exception as exc:
        log.error("evaluate_plan_accuracy_job error: %s", exc)


# ─── Registration function for collector/main.py ───────────────────────

def register_pipeline_jobs(scheduler):
    """
    Регистрация всех pipeline джобов в существующий APScheduler.
    Вызывается из collector/main.py после создания scheduler.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    # execution_watch_loop — каждые 10 секунд
    scheduler.add_job(
        execution_watch_loop_job,
        "interval",
        seconds=10,
        id="pipeline_execution_watch",
        replace_existing=True,
    )

    # stats_snapshot — каждые 60 секунд
    scheduler.add_job(
        stats_snapshot_job,
        "interval",
        seconds=60,
        id="pipeline_stats_snapshot",
        replace_existing=True,
    )

    # stale_data_guard — каждые 30 секунд
    scheduler.add_job(
        stale_data_guard,
        "interval",
        seconds=30,
        id="pipeline_stale_data_guard",
        replace_existing=True,
    )

    # hourly_recalibration — каждый час
    scheduler.add_job(
        hourly_recalibration_job,
        "interval",
        hours=1,
        id="pipeline_hourly_recalibration",
        replace_existing=True,
    )

    # session_closeout — каждые 5 минут (проверяет истёкшие окна)
    scheduler.add_job(
        session_closeout_job,
        "interval",
        minutes=5,
        id="pipeline_session_closeout",
        replace_existing=True,
    )

    # post_session_review — каждые 10 минут (проверяет завершённые без review)
    scheduler.add_job(
        post_session_review_job,
        "interval",
        minutes=10,
        id="pipeline_post_session_review",
        replace_existing=True,
    )

    # plan_accuracy evaluation — каждые 15 минут (проверяет pending predictions >=1h old)
    scheduler.add_job(
        evaluate_plan_accuracy_job,
        "interval",
        minutes=15,
        id="pipeline_plan_accuracy",
        replace_existing=True,
    )

    log.info("Pipeline scheduler jobs registered: 7 recurring jobs")