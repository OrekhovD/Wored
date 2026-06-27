from aiogram import F, Router
from aiogram.types import Message

from handlers.alerts import send_alerts
from handlers.analytics import show_analytics_menu
from handlers.market import send_market_data
from handlers.portfolio import cmd_portfolio
from handlers.predictions import send_prediction_menu

router = Router()


@router.message(F.text == "📊 Рынок")
async def menu_market(message: Message):
    await send_market_data(message)


@router.message(F.text == "🧠 Аналитика")
async def menu_analytics(message: Message):
    await show_analytics_menu(message)


@router.message(F.text == "🔮 Прогнозы")
async def menu_predictions(message: Message):
    await send_prediction_menu(message)


@router.message(F.text == "📦 Портфель")
async def menu_portfolio(message: Message):
    await cmd_portfolio(message)


@router.message(F.text == "🔔 Алерты")
async def menu_alerts(message: Message):
    await send_alerts(message)


@router.message(F.text == "🎯 Старт сессии")
async def menu_start_session(message: Message):
    """ТЗ §6.1 — старт сессии из главного меню."""
    from handlers.pipeline import handle_pipeline_intent
    result = await handle_pipeline_intent({"intent": "pipeline_start", "budget": 100.0, "risk_mode": "balanced"}, user_id=message.from_user.id)
    await message.answer(result, reply_markup=None)