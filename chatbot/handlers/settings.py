from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from storage.redis_client import get_redis
import os

router = Router()

async def send_settings(message: Message):
    model = os.getenv("GLM_MODEL", "glm-5.1")
    wl = os.getenv("WATCHLIST", "btcusdt,ethusdt").upper()
    alert_spike = os.getenv("ALERT_SPIKE_THRESHOLD", "3.0")
    
    # Check collector status via Redis ping for keys
    r = get_redis()
    keys = await r.keys("ticker:*")
    if keys:
        collector_status = f"✅ Online ({len(keys)} тикеров)"
    else:
        collector_status = "❌ Offline (нет данных)"
    
    text = (
        "⚙️ <b>Система:</b>\n\n"
        f"🤖 <b>Модель:</b> <code>{model} (ZhipuAI)</code>\n"
        f"📡 <b>Collector:</b> <code>{collector_status}</code>\n"
        f"📋 <b>Watchlist:</b> <code>{wl}</code>\n"
        f"⚡ <b>Порог алерта:</b> <code>{alert_spike}%</code>"
    )
    await message.answer(text)

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await send_settings(message)
