from aiogram import Router, F
from aiogram.types import Message
from handlers.market import send_market_data
from handlers.analytics import show_analytics_menu
from handlers.alerts import send_alerts
from handlers.settings import send_settings

router = Router()

@router.message(F.text == "📊 Рынок")
async def menu_market(message: Message):
    await send_market_data(message)

@router.message(F.text == "📈 Аналитика")
async def menu_analytics(message: Message):
    await show_analytics_menu(message)

@router.message(F.text == "🔔 Алерты")
async def menu_alerts(message: Message):
    await send_alerts(message)

@router.message(F.text == "⚙️ Система")
async def menu_settings(message: Message):
    await send_settings(message)
