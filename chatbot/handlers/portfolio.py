from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from storage.redis_client import get_cached_tickers
import os

router = Router()

@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
    tickers = await get_cached_tickers(watchlist)
    
    if not tickers:
        await message.answer("📁 Ваш портфель пуст или данные еще загружаются.")
        return
        
    lines = ["💼 <b>Ваш Портфель (Watchlist):</b>\n"]
    for ticker in tickers:
        sym = ticker['symbol'].upper()
        emoji = "🟢" if ticker['change_pct'] >= 0 else "🔴"
        lines.append(f"{emoji} <b>{sym}</b>: ${ticker['price']} ({ticker['change_pct']:+.2f}%)")
        
    await message.answer("\n".join(lines))
