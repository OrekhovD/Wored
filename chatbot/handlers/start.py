from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

router = Router()

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Рынок"), KeyboardButton(text="📈 Аналитика")],
            [KeyboardButton(text="🔔 Алерты"), KeyboardButton(text="⚙️ Система")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 <b>Добро пожаловать в HTX Bot!</b>\n\n"
        "Я ваш AI-ассистент по крипторынку. Выберите нужный раздел ниже "
        "или задайте любой вопрос текстом."
    )
    await message.answer(text, reply_markup=get_main_keyboard())
