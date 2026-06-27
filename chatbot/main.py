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
from handlers.predictions import router as predictions_router
from handlers.settings import router as settings_router
from handlers.trader import router as trader_router
from handlers.models import router as models_router
from handlers.admin import router as admin_router
from handlers.pipeline import router as pipeline_router

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


async def _sim_ai_monitor(bot: Bot):
    """Periodic check of AI-managed sim positions. Runs every 3 minutes."""
    import json
    from services.sim_engine import get_open_positions, close_position, calculate_unrealized_pnl
    from storage.redis_client import get_redis

    await asyncio.sleep(10)  # wait for bot to start
    log.info('Sim AI monitor started (3min interval)')

    while True:
        try:
            positions = await get_open_positions()
            ai_positions = [p for p in positions if p.get('ai_managed')]
            if not ai_positions:
                await asyncio.sleep(180)
                continue

            redis = get_redis()
            for pos in ai_positions:
                symbol = pos['symbol']
                ticker_data = await redis.get(f'ticker:{symbol}')
                if not ticker_data:
                    continue

                price = json.loads(ticker_data)['price']
                pnl = calculate_unrealized_pnl(pos, price)

                # Simple AI decision: close if ROI < -50% (stop loss) or ROI > 100% (take profit)
                # Full AI eval would call premium model here
                should_close = pnl['roi_pct'] <= -50.0 or pnl['roi_pct'] >= 100.0
                if pnl['is_liquidated']:
                    should_close = True

                if should_close:
                    reason = 'ai_stop_loss' if pnl['roi_pct'] < 0 else 'ai_take_profit'
                    if pnl['is_liquidated']:
                        reason = 'liquidation'
                    result = await close_position(pos['id'], price, reason=reason)
                    log.info('AI auto-closed sim #%d: %s %s pnl=%.4f reason=%s',
                             pos['id'], pos['direction'], symbol, result.get('realized_pnl', 0), reason)

                    # Notify user via Telegram
                    admin_id = int(os.getenv('TELEGRAM_ADMIN_ID', '0'))
                    if admin_id:
                        from services.sim_engine import format_position_card
                        card = format_position_card(result)
                        try:
                            await bot.send_message(admin_id, f"🤖 AI закрыла позицию\n\n{card}")
                        except Exception as e:
                            log.error('Notify failed: %s', e)

        except Exception as exc:
            log.error('Sim AI monitor error: %s', exc)

        await asyncio.sleep(180)


async def main():
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or "replace_with_bot_token" in token:
        log.error("TELEGRAM_TOKEN is missing or invalid. Bot cannot start.")
        return

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(callbacks_router)
    dp.include_router(market_router)
    dp.include_router(alerts_router)
    dp.include_router(analytics_router)
    dp.include_router(portfolio_router)
    dp.include_router(predictions_router)
    dp.include_router(settings_router)
    dp.include_router(trader_router)
    dp.include_router(models_router)
    dp.include_router(admin_router)
    dp.include_router(pipeline_router)
    if os.getenv("HERMES_CHATBOT_GATEWAY_ENABLED", "false").lower() in {"1", "true", "yes"}:
        from handlers.hermes_admin import router as hermes_admin_router
        dp.include_router(hermes_admin_router)
    # DEBUG: log all incoming updates
    from aiogram.types import Update
    @dp.update()
    async def debug_all_updates(update: Update):
        log.info('DEBUG UPDATE: %s', update.model_dump_json()[:200])

    dp.include_router(chat_router) # should be last since it catches messages

    from storage.postgres_client import ensure_ext_tables
    try:
        await ensure_ext_tables()
        log.info('TZ tables ensured')
    except Exception as e:
        log.error('ensure_ext_tables failed: %s', e)

    # Daily Pipeline v2 — ensure tables
    try:
        from services.pipeline_schema import ensure_pipeline_tables
        await ensure_pipeline_tables()
        log.info('Pipeline tables ensured (8 tables)')
    except Exception as e:
        log.error('ensure_pipeline_tables failed: %s', e)

    log.info("Chatbot started polling")
    asyncio.create_task(alert_listener(bot))
    asyncio.create_task(_sim_ai_monitor(bot))
    
    # Configure the system Menu Button to open WORED WebApp Dashboard
    from aiogram.types import MenuButtonWebApp, WebAppInfo
    webui_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8081/dashboard"
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Command Deck",
                web_app=WebAppInfo(url=webui_url)
            )
        )
        log.info("System Menu Button configured to open WORED WebApp Dashboard")
    except Exception as e:
        log.error(f"Failed to set WORED chat menu button: {e}")

    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == '__main__':
    asyncio.run(main())
