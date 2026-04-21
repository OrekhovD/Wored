from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from storage.redis_client import get_redis
import json
import os

router = Router()

def get_analytics_keyboard():
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
    buttons = []
    for sym in watchlist:
        buttons.append(InlineKeyboardButton(text=f"{sym.upper()}", callback_data=f"analytics:{sym}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

async def show_analytics_menu(message: Message):
    await message.answer(
        "🧠 <b>Выберите монету для AI-анализа:</b>", 
        reply_markup=get_analytics_keyboard()
    )

@router.message(Command("analytics"))
async def cmd_analytics(message: Message):
    # Backward compatibility if user typed /analytics btcusdt by hand
    args = message.text.split()
    if len(args) > 1:
        # User passed arg - we could route to callback logic, 
        # but to keep it clean, let's just ask them to use the buttons
        pass
        
    await show_analytics_menu(message)
