from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo

router = Router()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """ТЗ §5.1 — 6 разделов + главный CTA (Mini App / Старт сессии)."""
    import os
    miniapp_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080/daily-session"
    return ReplyKeyboardMarkup(
        keyboard=[
            # Row 0: Главный CTA — Mini App (ТЗ §5.2)
            [KeyboardButton(text="📱 Сессия", web_app=WebAppInfo(url=miniapp_url))],
            # Row 1: 6 разделов (ТЗ §5.1)
            [KeyboardButton(text="📊 Рынок"), KeyboardButton(text="🧠 Аналитика")],
            [KeyboardButton(text="🔮 Прогнозы"), KeyboardButton(text="📦 Портфель")],
            [KeyboardButton(text="🔔 Алерты"), KeyboardButton(text="🎯 Старт сессии")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "🏴‍☠️ <b>WORED Trading Bot</b>\n\n"
        "Daily Pipeline · BTCUSDT · HTX · 8h\n\n"
        "📱 <b>Сессия</b> — Mini App: live runtime, revision-команды, метрики\n"
        "📊 <b>Рынок</b> — live snapshot по watchlist\n"
        "🧠 <b>Аналитика</b> — AI анализ по монете\n"
        "🔮 <b>Прогнозы</b> — Forecast Lab\n"
        "📦 <b>Портфель</b> — позиции и PnL\n"
        "🔔 <b>Алерты</b> — последние движения\n"
        "🎯 <b>Старт сессии</b> — запустить дневную торговую сессию\n\n"
        "Или пиши текстом: «статус сессии», «цена btc», «анализ eth»…"
    )
    await message.answer(text, reply_markup=get_main_keyboard())