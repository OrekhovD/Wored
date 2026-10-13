"""Two rules about the Telegram input surface, implemented once for every router.

Both were found in the live P3c run (13.10.2026, RFC Q-15/Q-16, report §6.1) and
neither belongs to a single screen, which is why they live here instead of being
patched into whichever handler happened to notice them (ТЗ §2, TB-03).

**Q-15 — a mistyped command must never become a model answer.**  aiogram reads
the command token up to the first whitespace, so ``/plans,`` is the command
``plans,`` — not ``plans`` — and no ``Command`` filter matches it.  The update
then reaches the last router, the AI chat fallback, which answered it with a
generated *trade plan*: entry, stop-loss, take-profit, a confidence figure.  It
looked exactly like the plan surface and had no ``plan_hash``, no violation list
and no contract behind it, which is what "no invented numbers" forbids.  Two
complementary rules follow: the command token is normalised before routers see
it, and text that still looks like a command after every filter has passed is
refused instead of generated.

**Q-16 — "message is not modified" is not an outage.**  Editing a card to the
content it already has is Telegram's normal no-op, and re-opening an unchanged
screen does exactly that.  The console screen wrapper caught it as a generic
failure and displayed «🔴 Сервис данных недоступен / BACKEND_UNAVAILABLE», a
false alarm about the data service.  :func:`is_no_op_edit` names the condition so
every reply helper can treat it as success and keep the error taxonomy honest
(ТЗ §6).

**Q-20 — a command payload typed without its command is not a question.**  The
questionnaire answer (``goal=… duration=…``) and the deal-ticket arguments
(``side=buy risk=10 stop=…``) are the two inputs this bot accepts as a string of
``key=value`` tokens.  Sent as a bare message they used to fall through to the AI
chat, which answered a *payload* as if it were a prompt — with generated trade
numbers.  :func:`looks_like_key_values` names the shape; the owning service
decides whether the keys are its, and the fallback refuses with the exact command
instead of calling a model.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject

log = logging.getLogger("chatbot.surface")

#: punctuation a user leaves attached to a command while still typing
_TRAILING_PUNCTUATION = ".,;:!?،"

#: Telegram's wording for an edit that changes nothing (exact text is stable and
#: carries no numbers, so a substring test is safe here)
_NOT_MODIFIED = "message is not modified"

#: shown instead of a model answer when the input is still shaped like a command
SLASH_HINT = (
    "🤔 Команда не распознана: похоже, в ней опечатка.\n"
    "Полный список — <code>/help</code>, планы — <code>/plans</code>, "
    "текущий день — <code>/session</code>.\n"
    "<i>Текст, похожий на команду, не отправляется в модель: торговые числа в этом "
    "боте берутся только из контракта плана и доменного pipeline, и карточка без "
    "<code>plan_hash</code> планом не является.</i>"
)


def normalize_command_text(text: Any) -> Any:
    """Drop trailing punctuation glued to the command token, keep arguments as is.

    Only the first whitespace-delimited token is touched, and only when it starts
    with ``/``.  ``/plans, new`` → ``/plans new``; ``/plans show <id>`` is already
    clean and comes back byte-identical, because the angle brackets are an
    argument, not the command.
    """
    if not isinstance(text, str):
        return text
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return text
    indent = len(text) - len(stripped)
    head, sep, tail = stripped.partition(" ")
    cleaned = head.rstrip(_TRAILING_PUNCTUATION)
    if cleaned == head or not cleaned.startswith("/"):
        return text
    return text[:indent] + cleaned + sep + tail


def looks_like_command(text: Any) -> bool:
    """True for text a human meant as a slash command, however malformed."""
    return isinstance(text, str) and text.lstrip().startswith("/")


def looks_like_key_values(text: Any) -> bool:
    """True when every whitespace token is ``key=value`` — a payload, not prose.

    Only the shape is judged here; which command the payload belongs to is known
    exclusively by the service that owns those keys (ТЗ TB-03), so this rule can
    be shared by the questionnaire and the deal ticket without either re-declaring
    the other's vocabulary.
    """
    if not isinstance(text, str):
        return False
    tokens = text.split()
    return bool(tokens) and all("=" in token and token.split("=", 1)[0].strip() for token in tokens)


def is_no_op_edit(exc: BaseException) -> bool:
    """Telegram's «nothing changed» answer, as opposed to a real bad request."""
    return isinstance(exc, TelegramBadRequest) and _NOT_MODIFIED in str(exc).lower()


def set_event_text(event: Message, text: str) -> None:
    """Write a normalised command back onto the incoming message.

    aiogram message models are pydantic instances created with ``frozen=True``,
    so ``event.text = ...`` raises ``ValidationError`` (verified against the
    container's aiogram and asserted in ``tests/telegram/test_slash_surface.py``).
    ``object.__setattr__`` is the same bypass the test doubles use
    (:class:`conftest.FakeMessage`).  Only ``text`` is replaced: chat, sender,
    ``message_id`` and entities are untouched, so authorisation and every derived
    read of the event behave exactly as before.
    """
    object.__setattr__(event, "text", text)


async def command_surface_middleware(
    handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
    event: TelegramObject,
    data: Dict[str, Any],
) -> Any:
    """Outer message middleware: fix the token before any router can miss it.

    Installed once in ``main`` rather than per-router, so a mistyped command
    behaves the same for the console, the plan screens and every legacy name.
    """
    if isinstance(event, Message):
        original = event.text
        normalized = normalize_command_text(original)
        if normalized != original:
            log.info("command token normalized: %r -> %r", original, normalized)
            set_event_text(event, normalized)
    return await handler(event, data)
