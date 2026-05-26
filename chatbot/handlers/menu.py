from aiogram import F, Router
from aiogram.types import Message

from handlers.alerts import send_alerts
from handlers.analytics import show_analytics_menu
from handlers.market import send_market_data
from handlers.portfolio import cmd_portfolio
from handlers.predictions import send_prediction_menu
from handlers.settings import send_settings

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


@router.message(F.text == "🗂 Портфель")
async def menu_portfolio(message: Message):
    await cmd_portfolio(message)


@router.message(F.text == "🔔 Алерты")
async def menu_alerts(message: Message):
    await send_alerts(message)


@router.message(F.text == "⚙️ Система")
async def menu_settings(message: Message):
    await send_settings(message)


@router.message(F.text == "🎛️ Command Deck")
async def menu_command_deck(message: Message):
    import os
    webui_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8081/dashboard"
    await message.answer(
        "🎛️ <b>WORED Command Deck</b>\n\n"
        "Вы можете открыть интерактивную панель управления по ссылке ниже:\n"
        f"🔗 <a href='{webui_url}'>Открыть Command Deck</a>\n\n"
        "<i>Или воспользуйтесь кнопкой «🎛️ Command Deck» на клавиатуре бота!</i>"
    )


@router.message(F.text == "🔑 HER Console")
async def menu_her_console(message: Message):
    import os
    her_url = os.getenv("HER_MINIAPP_URL") or "http://localhost:8081/dashboard"
    await message.answer(
        "🔑 <b>HER Key Console</b>\n\n"
        "Панель управления моделями и ключами AI Gateway:\n"
        f"🔗 <a href='{her_url}'>Открыть HER Console</a>\n\n"
        "<i>Или воспользуйтесь кнопкой «🔑 HER Console» на клавиатуре бота!</i>"
    )
