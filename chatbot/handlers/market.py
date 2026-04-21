from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from storage.redis_client import get_redis
import json
import os

router = Router()

def get_market_keyboard():
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
    buttons = []
    
    # Analyze buttons
    for sym in watchlist:
        buttons.append(InlineKeyboardButton(text=f"📈 {sym.upper()}", callback_data=f"analytics:{sym}"))
    
    # We will put analyze buttons in a row (if more than 2, split, but since it's 2, one row is fine)
    keyboard = [buttons]
    # Add refresh button
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_market")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def build_market_text() -> str:
    r = get_redis()
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
    lines = ["📊 <b>Обзор рынка:</b>\n"]
    for sym in watchlist:
        data = await r.get(f"ticker:{sym}")
        if data:
            ticker = json.loads(data)
            emoji = "🟢" if ticker['change_pct'] >= 0 else "🔴"
            lines.append(f"{emoji} <b>{sym.upper()}</b>: ${ticker['price']:.2f} ({ticker['change_pct']:+.2f}%)")
        else:
            lines.append(f"⚪ <b>{sym.upper()}</b>: <i>данные не загружены</i>")
    return "\n".join(lines)

async def send_market_data(message: Message):
    text = await build_market_text()
    await message.answer(text, reply_markup=get_market_keyboard())

@router.message(Command("market"))
async def cmd_market(message: Message):
    await send_market_data(message)
