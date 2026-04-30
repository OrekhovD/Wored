import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router()


def get_analytics_keyboard() -> InlineKeyboardMarkup:
    watchlist = [item.strip().lower() for item in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if item.strip()]
    buttons = [InlineKeyboardButton(text=sym.upper(), callback_data=f"analytics:{sym}") for sym in watchlist]
    extra_row = [
        InlineKeyboardButton(text="🔮 Forecast Lab", callback_data="prediction_menu"),
        InlineKeyboardButton(text="📊 Рынок", callback_data="back_to_market"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons, extra_row] if buttons else [extra_row])


async def show_analytics_menu(message: Message):
    await message.answer(
        "🧠 <b>AI-аналитика</b>\n\nВыберите монету для reasoning-анализа или откройте прогнозный контур.",
        reply_markup=get_analytics_keyboard(),
    )


@router.message(Command("analytics"))
async def cmd_analytics(message: Message):
    await show_analytics_menu(message)
