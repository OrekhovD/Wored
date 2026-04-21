import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from storage.postgres_client import save_tickers, get_pool
from storage.redis_client import cache_tickers, get_redis
from htx.rest import get_all_tickers
from scheduler.alert_checker import check_alerts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("collector")

async def sync_market_data():
    """Fetch tickers from HTX, save to cache and DB."""
    try:
        tickers = await get_all_tickers()
        if tickers:
            # Save to redis cache
            await cache_tickers(tickers)
            # Save to pg
            await save_tickers(tickers)
            log.info(f"Processed {len(tickers)} tickers.")
        else:
            log.warning("No tickers found.")
    except Exception as e:
        log.error(f"Sync error: {e}")

async def main():
    import signal
    log.info("Starting Collector...")
    # Init DBs
    pool = await get_pool()
    get_redis()
    
    scheduler = AsyncIOScheduler()
    
    # 1. Fetch market data every 1 minute
    scheduler.add_job(sync_market_data, 'interval', minutes=1, id='sync_market_data')
    
    # 2. Check for alerts every 5 minutes
    scheduler.add_job(check_alerts, 'interval', minutes=5, id='check_alerts')
    
    # 3. Cleanup old tickers every 6 hours
    from storage.postgres_client import cleanup_old_tickers
    scheduler.add_job(cleanup_old_tickers, 'interval', hours=6, id='cleanup_tickers', kwargs={'hours': 24})
    
    scheduler.start()
    
    async def shutdown():
        scheduler.shutdown(wait=False)
        if pool:
            await pool.close()
        log.info("Graceful shutdown complete.")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
    
    # Keep the loop alive
    log.info("Collector running in background.")
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        log.info("Shutting down Collector.")

if __name__ == '__main__':
    asyncio.run(main())
