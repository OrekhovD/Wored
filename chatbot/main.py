import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Setup Handlers
from handlers.now import router as console_router
from handlers.simulation_v3 import router as simulation_v3_router
from handlers.deprecated import router as deprecated_router
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
from handlers.admin import router as admin_router
from handlers.pipeline import router as pipeline_router
from handlers.news import router as news_router
from handlers.plans import router as plans_router
from handlers.paper_deal import router as paper_deal_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("chatbot")

#: update fields inspected for the trace line — never message text or payloads
_UPDATE_CONTENT_FIELDS = (
    "message", "edited_message", "channel_post", "edited_channel_post",
    "callback_query", "inline_query", "chosen_inline_result", "my_chat_member",
    "chat_member", "join_request", "poll_answer", "pre_checkout_query",
)


def _update_kind(update) -> str:
    for name in _UPDATE_CONTENT_FIELDS:
        if getattr(update, name, None) is not None:
            return name
    return "other"


def _update_event(update):
    for name in _UPDATE_CONTENT_FIELDS:
        event = getattr(update, name, None)
        if event is not None:
            return event
    return None


def _update_chat_id(update):
    event = _update_event(update)
    chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
    return getattr(chat, "id", None)


def _update_actor_id(update):
    event = _update_event(update)
    user = getattr(event, "from_user", None)
    return getattr(user, "id", None)

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


async def _poll_watchdog(bot: Bot):
    """Detect a hung aiogram HTTP session and self-exit so Docker restarts the container.

    Failure mode we care about: long-poll `getUpdates` socket half-opens after a
    transient network/Docker-Desktop hiccup and never returns; `aiogram.event`
    logs stay silent, users see no reply. A fresh `get_webhook_info` request
    shares the same aiohttp ClientSession; if the session is broken it will
    time out too. Two consecutive misses (over ~90 s) are treated as wedged
    and the process exits with code 1 — Docker's `restart: unless-stopped`
    brings it back with a fresh connection pool.

    Tuning: interval=45 s, timeout=15 s, threshold=2. Worst-case recovery
    latency ≈ 90 s + image pull 0 s (rebuild not needed).
    """
    consecutive_failures = 0
    # Give startup a grace period — DB migrations and initial poll setup.
    await asyncio.sleep(30)
    log.info("Poll watchdog started (interval=45s timeout=15s threshold=2)")
    while True:
        try:
            await asyncio.wait_for(bot.get_webhook_info(), timeout=15)
            if consecutive_failures:
                log.info("Poll watchdog: session recovered")
            consecutive_failures = 0
        except asyncio.TimeoutError:
            consecutive_failures += 1
            log.warning(
                "Poll watchdog: get_webhook_info timeout (%d/2)", consecutive_failures
            )
        except Exception as exc:  # network / auth errors — still worth restarting on
            consecutive_failures += 1
            log.warning(
                "Poll watchdog: get_webhook_info failed: %s (%d/2)", exc, consecutive_failures
            )
        if consecutive_failures >= 2:
            log.error("Poll watchdog: Telegram session wedged, exiting for Docker restart")
            # Force immediate process termination; `os._exit` skips cleanup by
            # design so a hung shutdown handler cannot block the restart.
            os._exit(1)
        await asyncio.sleep(45)


async def _sim_ai_monitor(bot: Bot):
    """Periodic check of AI-managed sim positions. Runs every 3 minutes.

    FROZEN BY DEFAULT (ТЗ Telegram Bot V1 §0 + curator decision Q-02, 2026-10-08):
    this loop used to close legacy ``sim_positions`` rows on a bot-side ROI formula
    (<= -50% / >= +100%) before any user notification, i.e. a second, undocumented
    trading domain next to ``paper_trading``. The data stays readable
    (``sim_positions`` / ``sim_evaluations`` are legacy read-only); automatic
    closing requires an explicit ``BOT_SIM_MONITOR_ENABLED=true`` plus a domain
    idempotent command path, which lands in P4.
    """
    if os.getenv("BOT_SIM_MONITOR_ENABLED", "false").strip().lower() not in {"1", "true", "yes"}:
        log.info(
            "Sim AI monitor disabled (BOT_SIM_MONITOR_ENABLED=false): "
            "legacy sim positions are read-only, no automatic closing"
        )
        return

    import json
    from services.sim_engine import get_open_positions, close_position, calculate_unrealized_pnl
    from storage.redis_client import get_redis

    await asyncio.sleep(10)  # wait for bot to start
    log.warning('Sim AI monitor ENABLED (3min interval) — legacy auto-close is active')

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

    # Authorization guard before any router: deny-by-default, private chat only.
    # No handler — including /start and every callback — is reachable without a
    # server-derived Principal (ТЗ Telegram Bot V1 §3).
    from security import guards
    guards.install(dp)

    # Command surface rule (RFC Q-15, live P3c finding): aiogram reads the command
    # token up to the first space, so ``/plans,`` is the command ``plans,`` — it
    # matches no ``Command`` filter and used to fall through to the AI chat
    # fallback, which answered a mistyped command with a generated trade plan.
    # Normalising the token once, before every router, keeps the rule out of the
    # individual screens (``handlers/telegram_surface.py``).
    from handlers.telegram_surface import command_surface_middleware
    dp.message.outer_middleware(command_surface_middleware)
    log.info("SURFACE: command token rule installed before all routers")

    # New read console first: /start, /now, /market, /forecast, /session,
    # /positions, /report, /help, /cancel own these commands from here on.
    dp.include_router(console_router)
    # V3 simulation plans: /plans is the canonical command from here on, and the
    # draft/edit/approve use cases run through the shared trading_math pipeline.
    # Included before deprecated_router so the historical /plan redirect cannot
    # shadow it.
    dp.include_router(simulation_v3_router)
    # Historical command names: one explicit redirect to the same use case,
    # never a second implementation (ТЗ §2, TB-03).
    dp.include_router(deprecated_router)

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
    dp.include_router(admin_router)
    dp.include_router(pipeline_router)
    dp.include_router(news_router)
    dp.include_router(plans_router)
    # P4 manual deal (/deal): the ticket numbers come from paper_trading.ticket,
    # so this screen must be reached before the AI chat fallback can answer it.
    dp.include_router(paper_deal_router)
    if os.getenv("HERMES_CHATBOT_GATEWAY_ENABLED", "false").lower() in {"1", "true", "yes"}:
        from handlers.hermes_admin import router as hermes_admin_router
        dp.include_router(hermes_admin_router)
    # Observability: identifiers and update type only. The previous form logged
    # ``update.model_dump_json()`` — private message text, callback payloads and
    # anything a user typed (ТЗ §3 forbids it; RFC D-09).
    from aiogram.types import Update
    @dp.update()
    async def trace_updates(update: Update):
        log.info(
            "UPDATE id=%s type=%s chat=%s actor=%s",
            update.update_id,
            _update_kind(update),
            _update_chat_id(update),
            _update_actor_id(update),
        )

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
    asyncio.create_task(_poll_watchdog(bot))
    
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
