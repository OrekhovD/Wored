"""Reply-keyboard surface for the bot (legacy section shortcuts).

The ``/start`` command itself is owned by :mod:`handlers.now` (TB-03: one command,
one implementation).  What stays here is only the persistent reply keyboard, i.e.
the shortcut row Telegram shows under every chat; its buttons are routed to the
console screens in :mod:`handlers.menu`, so a button and the matching command
cannot render two different data sets.

``TG_MINIAPP_URL`` / ``WEBUI_URL`` fall back to ``localhost`` — on a phone that is
not the WORED machine, therefore the console offers the Mini App deep link only
when a public ``https`` origin is configured (ТЗ §3).
"""

import logging
import os

from aiogram import Router
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

log = logging.getLogger(__name__)
router = Router()


def get_main_keyboard(has_session: bool = True) -> ReplyKeyboardMarkup:
    """Command Deck keyboard — быстрый доступ к оперативному экрану + разделы."""
    miniapp_url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080/command-deck"

    # Главная кнопка — всегда Mini App с Command Deck
    cta_row = [KeyboardButton(text="⚡ Command Deck", web_app=WebAppInfo(url=miniapp_url))]

    return ReplyKeyboardMarkup(
        keyboard=[
            cta_row,
            # Row 1-2: 6 разделов
            [KeyboardButton(text="📊 Рынок"), KeyboardButton(text="🧠 Аналитика")],
            [KeyboardButton(text="🔮 Прогнозы"), KeyboardButton(text="📦 Портфель")],
            [KeyboardButton(text="🔔 Алерты"), KeyboardButton(text="🎯 Сессия")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
