"""Text-button shortcuts of the persistent reply keyboard.

TB-03 (ТЗ Telegram Bot V1 §2): a button and the command that covers the same
action must resolve to one use case, so the read buttons below call the console
screens directly instead of the legacy ticker/sim digest.  Buttons that open a
*creation* flow (Аналитика, Прогнозы-меню, Алерты) keep their own handlers.
"""
from aiogram import F, Router
from aiogram.types import Message

from handlers import now as console
from handlers.alerts import send_alerts
from handlers.analytics import show_analytics_menu
from handlers.predictions import send_prediction_menu
from security.principal import Principal

router = Router()


@router.message(F.text == "📊 Рынок")
async def menu_market(message: Message, principal: Principal):
    await console.render_screen(console.show_market_detail, message, principal)


@router.message(F.text == "🧠 Аналитика")
async def menu_analytics(message: Message):
    await show_analytics_menu(message)


@router.message(F.text == "🔮 Прогнозы")
async def menu_predictions(message: Message):
    await send_prediction_menu(message)


@router.message(F.text == "📦 Портфель")
async def menu_portfolio(message: Message, principal: Principal):
    await console.render_screen(console.show_positions, message, principal)


@router.message(F.text == "🔔 Алерты")
async def menu_alerts(message: Message):
    await send_alerts(message)


@router.message(F.text == "🎯 Сессия")
async def menu_session_status(message: Message, principal: Principal):
    """ТЗ §5.1 — кнопка Сессия вызывает тот же use case, что /session."""
    await console.render_screen(console.show_session, message, principal)
