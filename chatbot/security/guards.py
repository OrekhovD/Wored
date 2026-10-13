"""Outer authorization middleware for every Telegram update (ТЗ §3).

Placement matters: this runs *before* any router, so no handler — including
``/start`` and any callback — can be reached without a Principal.  Deny is
silent about contents: the audit line carries actor ID, chat type and the reason
code, never the message text (ТЗ §3 forbids logging private content, and the
previous global ``update.model_dump_json()`` logger did exactly that).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Awaitable, Callable

from aiogram.types import CallbackQuery, Message

from security import principal as P

log = logging.getLogger("chatbot.security")

DENIED_TEXT = "⛔ Доступ запрещён."
GROUP_TEXT = "⛔ Бот работает только в личных сообщениях."
RATE_TEXT = "⏳ Слишком часто. Повторите позже."

_reason_counts: dict[str, int] = {}


def _audit(event_name: str, reason: str, actor: int | None, chat_id: int | None) -> None:
    """Denial audit without any message content."""
    _reason_counts[reason] = _reason_counts.get(reason, 0) + 1
    log.warning(
        "AUTH_DENIED reason=%s event=%s actor=%s chat=%s total=%d",
        reason, event_name, actor, chat_id, _reason_counts[reason],
    )


class RateLimiter:
    """Per (actor, action) minimum interval, in-process.

    Acceptable for V1 because there is exactly one polling consumer; a restart
    clears the window, which only relaxes a UX guard, never a financial one.
    """

    def __init__(self, min_interval_seconds: float = 2.0) -> None:
        self._min = max(0.0, float(min_interval_seconds))
        self._last: dict[tuple[int, str], float] = {}

    def allow(self, actor: int, action: str, *, now: float | None = None) -> bool:
        if self._min <= 0:
            return True
        moment = time.monotonic() if now is None else now
        key = (actor, action)
        previous = self._last.get(key)
        if previous is not None and (moment - previous) < self._min:
            return False
        self._last[key] = moment
        return True


_refresh_limiter: RateLimiter | None = None


def refresh_limiter() -> RateLimiter:
    """Refresh throttle for reads (ТЗ §U02: not more than once per 2 s/actor)."""
    global _refresh_limiter
    if _refresh_limiter is None:
        raw = 2.0
        try:
            raw = float(os.getenv("BOT_MARKET_REFRESH_MIN_SECONDS", "2") or 2)
        except ValueError:
            raw = 2.0
        _refresh_limiter = RateLimiter(raw)
    return _refresh_limiter


def _actor_and_chat(event: Any) -> tuple[int | None, int | None, str]:
    if isinstance(event, CallbackQuery):
        actor = event.from_user.id if event.from_user else None
        chat = event.message.chat.id if event.message and event.message.chat else None
        chat_type = (
            event.message.chat.type if event.message and event.message.chat else "unknown"
        )
        return actor, chat, chat_type
    if isinstance(event, Message):
        actor = event.from_user.id if event.from_user else None
        chat = event.chat.id if event.chat else None
        return actor, chat, (event.chat.type if event.chat else "unknown")
    return None, None, "unknown"


async def auth_middleware(
    handler: Callable[[Any, dict], Awaitable[Any]],
    event: Any,
    data: dict,
) -> Any:
    """Resolve a Principal or refuse; inject ``data["principal"]``."""
    actor, chat, chat_type = _actor_and_chat(event)
    name = type(event).__name__

    if P.private_chat_only() and chat_type not in {"private"}:
        _audit(name, "NOT_PRIVATE", actor, chat)
        return await _refuse(event, GROUP_TEXT)

    resolved = P.resolve(actor)
    if resolved is None:
        _audit(name, "NOT_ALLOWLISTED", actor, chat)
        return await _refuse(event, DENIED_TEXT)

    if not resolved.is_reader:
        _audit(name, "ROLE_DENIED", actor, chat)
        return await _refuse(event, DENIED_TEXT)

    data["principal"] = resolved
    return await handler(event, data)


async def _refuse(event: Any, text: str) -> Any:
    """Answer in the shape Telegram expects, without leaking any state."""
    if isinstance(event, CallbackQuery):
        return await event.answer(text=text, show_alert=False)
    if isinstance(event, Message):
        return await event.answer(text)
    return None


def install(dp: Any) -> None:
    """Attach the guard to every message and callback before routers run."""
    dp.message.outer_middleware(auth_middleware)
    dp.callback_query.outer_middleware(auth_middleware)
    warning = P.configuration_warning()
    if warning:
        log.warning("AUTHZ CONFIG: %s", warning)
    else:
        log.info("AUTHZ: deny-by-default guard installed (allowlist configured)")
