import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Setup Handlers
from handlers.start import router as start_router
from handlers.menu import router as menu_router
from handlers.callbacks import router as callbacks_router
from handlers.market import router as market_router
from handlers.chat import router as chat_router
from handlers.alerts import router as alerts_router
from handlers.analytics import router as analytics_router
from handlers.portfolio import router as portfolio_router
from handlers.settings import router as settings_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("chatbot")

async def alert_listener(bot: Bot):
    """Subscribes to Redis pub/sub to push alerts to Admin."""
    import json
    from storage.redis_client import get_redis
    
    admin_id = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
    if not admin_id:
        return
        
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("market_alerts")
    
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = json.loads(message["data"])
            try:
                await bot.send_message(admin_id, data["message"])
            except Exception as e:
                log.error(f"Push alert failed: {e}")

async def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token or token.startswith("8686265741"):
        log.warning("Please update TELEGRAM_TOKEN in .env. Bot may fail to start.")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(callbacks_router)
    dp.include_router(market_router)
    dp.include_router(alerts_router)
    dp.include_router(analytics_router)
    dp.include_router(portfolio_router)
    dp.include_router(settings_router)
    dp.include_router(chat_router) # should be last since it catches messages

    log.info("Chatbot started polling")
    asyncio.create_task(alert_listener(bot))
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
