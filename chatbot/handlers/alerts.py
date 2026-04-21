from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from storage.postgres_client import get_recent_alert_history
import os

router = Router()

async def send_alerts(message: Message):
    history = await get_recent_alert_history(20) # pull more to filter
    watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
    
    # Filter by watchlist
    valid_history = [alert for alert in history if alert['symbol'] in watchlist][:5]
    
    if not valid_history:
        await message.answer("✅ За последние 24ч резких движений BTC/ETH не зафиксировано.")
        return
        
    lines = ["🔔 <b>Последние алерты:</b>\n"]
    for alert in valid_history:
        sym = alert['symbol'].upper()
        # format timestamp
        ts = alert['timestamp'].strftime("%Y-%m-%d %H:%M")
        emoji = "🚀" if alert['threshold'] > 0 else "🩸"
        lines.append(f"{emoji} <b>{sym}</b> {alert['threshold']:+.2f}% в <code>{ts}</code>")
        
    await message.answer("\n".join(lines))

@router.message(Command("alerts"))
async def cmd_alerts(message: Message):
    await send_alerts(message)
