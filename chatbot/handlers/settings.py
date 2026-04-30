import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ai.models import MODELS, expand_fallback_tiers
from ai.resilience import _resilience_handlers
from storage.postgres_client import get_latest_prediction_request
from storage.redis_client import get_redis

router = Router()


def _format_chain(preferred: str) -> str:
    tiers = expand_fallback_tiers(preferred)
    parts = [MODELS[tier].model_id for tier in tiers if tier in MODELS and MODELS[tier].model_id.strip()]
    return " -> ".join(parts)


async def send_settings(message: Message):
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").upper()
    alert_spike = os.getenv("ALERT_SPIKE_THRESHOLD", "3.0")

    redis_client = get_redis()
    keys = await redis_client.keys("ticker:*")
    collector_status = f"✅ Online ({len(keys)} тикеров)" if keys else "❌ Offline (нет данных)"

    cb_lines = []
    for tier_name, handler in _resilience_handlers.items():
        stats = handler.get_circuit_stats()
        state_emoji = {"closed": "🟢", "open": "🔴", "half_open": "🟡"}.get(stats["state"], "⚪")
        cb_lines.append(f"{state_emoji} <b>{tier_name}</b>: {stats['state']} · fails {stats['failure_count']}")
    cb_text = "\n".join(cb_lines) if cb_lines else "⚪ Нет runtime-статистики по circuit breaker."

    latest_prediction = await get_latest_prediction_request()
    if latest_prediction is None:
        forecast_line = "🔮 <b>Forecast Lab:</b> пока нет сохранённых запросов"
    else:
        score = (
            f" · hit {latest_prediction['avg_accuracy']:.1f}% / miss {latest_prediction['avg_failure']:.1f}%"
            if latest_prediction.get("avg_accuracy") is not None
            else ""
        )
        forecast_line = (
            f"🔮 <b>Forecast Lab:</b> "
            f"#{latest_prediction['id']} {latest_prediction['symbol'].upper()} {latest_prediction['horizon_hours']}h "
            f"· {latest_prediction['status']}{score}"
        )

    text = (
        "⚙️ <b>Система</b>\n\n"
        f"📡 <b>Collector:</b> <code>{collector_status}</code>\n"
        f"📋 <b>Watchlist:</b> <code>{watchlist}</code>\n"
        f"⚡ <b>Порог алерта:</b> <code>{alert_spike}%</code>\n"
        f"{forecast_line}\n\n"
        "<b>AI chains:</b>\n"
        f"• worker: <code>{_format_chain('worker')}</code>\n"
        f"• analyst: <code>{_format_chain('analyst')}</code>\n"
        f"• strategist: <code>{_format_chain('premium')}</code>\n"
        f"• oracle: <code>{MODELS['minimax'].model_id}</code>\n\n"
        f"🛡️ <b>Circuit Breakers:</b>\n{cb_text}"
    )
    await message.answer(text)


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await send_settings(message)
