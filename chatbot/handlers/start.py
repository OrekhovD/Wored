from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo

router = Router()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    import os
    webui_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8081/dashboard"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎛️ Command Deck", web_app=WebAppInfo(url=webui_url))],
            [KeyboardButton(text="📊 Рынок"), KeyboardButton(text="🧠 Аналитика")],
            [KeyboardButton(text="🔮 Прогнозы"), KeyboardButton(text="🗂 Портфель")],
            [KeyboardButton(text="🔔 Алерты"), KeyboardButton(text="⚙️ Система")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 <b>Добро пожаловать в WORED Bot</b>\n\n"
        "Это Telegram-контур для рынка, AI-аналитики и почасовых прогнозов.\n"
        "Используйте кнопки ниже или задайте вопрос текстом.\n\n"
        "Быстрые сценарии:\n"
        "• <b>Рынок</b> — live snapshot по watchlist\n"
        "• <b>Аналитика</b> — reasoning analysis по монете\n"
        "• <b>Прогнозы</b> — запуск hourly forecast и live scorecard по моделям"
    )
    await message.answer(text, reply_markup=get_main_keyboard())
