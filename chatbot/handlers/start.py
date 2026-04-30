from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

router = Router()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
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
