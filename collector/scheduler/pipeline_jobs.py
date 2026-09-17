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
    """Pause armed sessions per symbol; keep protective exit monitoring enabled."""
    from storage.redis_client import get_redis
    from storage.postgres_client import get_pool
    from services.market_data import fresh_ticker
    import uuid
    pool = await get_pool()
    if pool is None:
        return
    redis = get_redis()
    sessions = await pool.fetch("SELECT id,symbol FROM trading_sessions WHERE status='armed'")
    for session in sessions:
        try:
            ticker = json.loads(await redis.get(f"ticker:{session['symbol'].lower()}") or "{}")
            fresh = fresh_ticker(ticker, max_age=STALE_THRESHOLD_SECONDS)
        except Exception:
            fresh = False
        if fresh:
            continue
        async with pool.acquire() as conn, conn.transaction():
            changed = await conn.fetchval(
                "UPDATE trading_sessions SET status='paused',updated_at=NOW() "
                "WHERE id=$1 AND status='armed' RETURNING id", session["id"])
            if changed is not None:
                await conn.execute(
                    "INSERT INTO execution_events(id,session_id,event_type,event_payload) "
                    "VALUES($1,$2,'stale_data_pause',$3)", str(uuid.uuid4()), str(changed),
                    json.dumps({"symbol":session["symbol"],"reason":"ticker_stale_or_missing"}))


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
                WHERE status IN ('armed', 'in_position', 'cooldown', 'idle', 'paused')
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