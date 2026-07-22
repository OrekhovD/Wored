import asyncio
import json
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from htx.websocket import ws_listen
from indicators.calculator import calculate_indicators
from journal.writer import write_entry
from predictions.evaluator import evaluate_due_forecasts, refresh_historical_forecast_scores
from scheduler.alert_checker import check_alerts
from scheduler.sim_monitor import check_sim_positions
from scheduler.pipeline_jobs import register_pipeline_jobs
from storage.postgres_client import get_pool
from storage.redis_client import get_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("collector")


async def record_ai_journal():
    """Calculate indicators and write to ai_journal."""
    try:
        watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
        redis = get_redis()

        snapshot = {}
        all_indicators = {}

        for symbol in watchlist:
            data = await redis.get(f"ticker:{symbol}")
            if data:
                snapshot[symbol] = json.loads(data)

            indicators = await calculate_indicators(symbol)
            if indicators:
                all_indicators[symbol] = indicators

        if snapshot:
            await write_entry(snapshot, all_indicators, "Scheduled 15m snapshot")
            log.info("Recorded 15m AI journal entry.")
    except Exception as exc:
        log.error("Journal recording error: %s", exc)


async def main():
    import signal

    log.info("Starting Collector with WebSocket...")
    pool = await get_pool()
    get_redis()
    await refresh_historical_forecast_scores()

    # Ensure webui forecast tables exist on collector side
    try:
        from webui.app import PREDICTION_TABLES_SQL
        async with pool.acquire() as conn:
            await conn.execute(PREDICTION_TABLES_SQL)
        log.info("Forecast tables ensured from collector.")
    except Exception as exc:
        log.warning("Forecast tables init (collector): %s", exc)

    # Daily Pipeline v2 — ensure tables exist
    try:
        import importlib
        schema_mod = importlib.import_module("services.pipeline_schema")
        await schema_mod.ensure_pipeline_tables()
    except Exception as exc:
        log.warning("Pipeline tables init (collector): %s", exc)

    asyncio.create_task(ws_listen())

    scheduler = AsyncIOScheduler()
    scheduler.add_job(record_ai_journal, "interval", minutes=15, id="record_ai_journal")
    scheduler.add_job(check_alerts, "interval", minutes=5, id="check_alerts")
    scheduler.add_job(evaluate_due_forecasts, "interval", minutes=5, id="evaluate_forecasts")
    scheduler.add_job(refresh_historical_forecast_scores, "interval", hours=1, id="refresh_historical_scores")
    scheduler.add_job(check_sim_positions, "interval", minutes=2, id="check_sim_positions")
    scheduler.add_job(regenerate_hourly_correction, "interval", hours=1, id="hourly_correction")

    # Daily Pipeline v2 jobs (ТЗ раздел 11)
    register_pipeline_jobs(scheduler)

    from storage.postgres_client import cleanup_old_tickers

    scheduler.add_job(cleanup_old_tickers, "interval", hours=6, id="cleanup_tickers", kwargs={"hours": 24})
    scheduler.start()

    async def shutdown():
        scheduler.shutdown(wait=False)
        if pool:
            await pool.close()
        log.info("Graceful shutdown complete.")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    log.info("Collector running in background.")
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        log.info("Shutting down Collector.")


if __name__ == "__main__":
    asyncio.run(main())
