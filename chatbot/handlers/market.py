import json
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage.redis_client import get_redis

router = Router()


def get_market_keyboard() -> InlineKeyboardMarkup:
    watchlist = [item.strip().lower() for item in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if item.strip()]
    analytics_buttons = [InlineKeyboardButton(text=f"🧠 {sym.upper()}", callback_data=f"analytics:{sym}") for sym in watchlist]
    prediction_buttons = [InlineKeyboardButton(text=f"🔮 {sym.upper()}", callback_data=f"prediction_symbol:{sym}") for sym in watchlist]

    keyboard = []
    if analytics_buttons:
        keyboard.append(analytics_buttons)
    if prediction_buttons:
        keyboard.append(prediction_buttons)
    keyboard.append(
        [
            InlineKeyboardButton(text="📋 Forecast Lab", callback_data="prediction_menu"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_market"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_market_text() -> str:
    r = get_redis()
    watchlist = [item.strip().lower() for item in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if item.strip()]
    lines = ["📊 <b>Обзор рынка</b>", ""]

    for sym in watchlist:
        data = await r.get(f"ticker:{sym}")
        if data:
            ticker = json.loads(data)
            emoji = "🟢" if ticker["change_pct"] >= 0 else "🔴"
            lines.append(f"{emoji} <b>{sym.upper()}</b>: ${ticker['price']:.2f} ({ticker['change_pct']:+.2f}%)")
        else:
            lines.append(f"⚪ <b>{sym.upper()}</b>: <i>данные не загружены</i>")
    lines.extend(["", "Ниже можно сразу перейти в анализ или hourly forecast по монете."])
    return "\n".join(lines)


async def send_market_data(message: Message):
    text = await build_market_text()
    await message.answer(text, reply_markup=get_market_keyboard())


@router.message(Command("market"))
async def cmd_market(message: Message):
    await send_market_data(message)
