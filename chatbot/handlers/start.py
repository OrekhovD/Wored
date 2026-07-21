from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
import os
import logging

log = logging.getLogger(__name__)
router = Router()


async def _has_active_session(user_id: int) -> bool:
    """Check if user has an active trading session."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        return session is not None
    except Exception:
        return False


def get_main_keyboard(has_session: bool = True) -> ReplyKeyboardMarkup:
    """ТЗ §5.1 — 6 разделов + главный CTA.
    ТЗ §5.2 — CTA dynamic: 'Mini App' если сессия активна, 'Старт сессии' если нет.
    """
    miniapp_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080/daily-session"

    if has_session:
        # ТЗ §5.2 — Mini App когда сессия активна
        cta_row = [KeyboardButton(text="📱 Сессия", web_app=WebAppInfo(url=miniapp_url))]
    else:
        # ТЗ §5.2 — Старт сессии когда сессии нет
        cta_row = [KeyboardButton(text="🚀 Старт сессии")]

    return ReplyKeyboardMarkup(
        keyboard=[
            cta_row,
            # Row 1-2: 6 разделов (ТЗ §5.1)
            [KeyboardButton(text="📊 Рынок"), KeyboardButton(text="🧠 Аналитика")],
            [KeyboardButton(text="🔮 Прогнозы"), KeyboardButton(text="📦 Портфель")],
            [KeyboardButton(text="🔔 Алерты"), KeyboardButton(text="🎯 Сессия")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    has_session = await _has_active_session(message.from_user.id)

    if has_session:
        cta_text = "📱 <b>Сессия</b> — Mini App: live runtime, revision-команды, метрики"
    else:
        cta_text = "🚀 <b>Старт сессии</b> — запустить новую дневную торговую сессию"

    text = (
        f"🏴‍☠️ <b>WORED Trading Bot</b>\n\n"
        f"Daily Pipeline · BTCUSDT · HTX · 8h\n\n"
        f"{cta_text}\n\n"
        "Или пиши: «статус», «цена btc», «анализ eth»…"
    )
    await message.answer(text, reply_markup=get_main_keyboard(has_session))