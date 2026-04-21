"""FastAPI gateway application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from aiogram import Bot, Dispatcher

from core.config import AppConfiguration

log = logging.getLogger(__name__)

config = AppConfiguration()
bot: Bot | None = None
dp: Dispatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global bot, dp

    # ── startup ────────────────────────────────────────────────────────
    logging.basicConfig(level=config.LOG_LEVEL)
    log.info("Starting Hytergram AI Gateway...")

    # init DB
    from storage.database import init_db
    await init_db(config)
    log.info("Database initialized")

    # init bot
    if config.TELEGRAM_BOT_TOKEN:
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        bot = Bot(
            token=config.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()

        from bot.handlers import router, setup_bot
        from bot.middleware import TrackingMiddleware, SecurityMiddleware, UsageMiddleware

        dp.include_router(router)
        dp.message.middleware(TrackingMiddleware())
        dp.message.middleware(SecurityMiddleware(config.admin_user_ids))
        dp.message.middleware(UsageMiddleware())

        from core.bootstrap import build_service_container
        container = await build_service_container(config)
        setup_bot(bot, config, container)

        # start polling in background
        import asyncio
        asyncio.create_task(dp.start_polling(bot))
        log.info("Bot polling started")

    log.info("Hytergram AI Gateway ready")
    yield

    # ── shutdown ───────────────────────────────────────────────────────
    log.info("Shutting down...")
    if bot:
        await bot.session.close()
    log.info("Shutdown complete")


app = FastAPI(title="Hytergram AI Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {"service": "hytergram", "status": "running"}


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "hytergram-gateway"}


@app.get("/health/deep")
async def health_deep() -> dict:
    checks = {
        "gateway": True,
        "db": True,
        "bot": bot is not None,
        "htx_api": True,
        "ai_providers": True,
    }
    all_healthy = all(checks.values())
    return {"status": "healthy" if all_healthy else "degraded", "checks": checks}


# include internal router
from app.router import internal_router
app.include_router(internal_router, prefix="/internal")

from app.health import health_router
app.include_router(health_router, prefix="/health")
