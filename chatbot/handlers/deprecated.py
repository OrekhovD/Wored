"""Deprecated Telegram commands → the new console use cases (ТЗ §2, TB-03).

Historical command names keep answering, but they never keep a second
implementation of the same action: each one resolves to the same screen function
its target command uses, so ``/portfolio`` and ``/positions`` cannot show two
different balances.  A redirect adds one explicit line about where the command
is going and what still has no new screen.

``/settings`` is intentionally not redirected in P2: it manages AI-routing
preferences, has no financial read-model to duplicate, and its replacement is
scheduled for P6 (U10/U11).  The decision is recorded in
``docs/TELEGRAM-BOT-COMMAND-MAP.md``.

P3c moves ``/plan`` onto the V3 plan screens: it now opens the same «Мои планы»
use case as the canonical ``/plans`` command (which is no longer a redirect at
all).  The old daily-pipeline plan screen is not reachable from the command
surface and writes nothing.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from handlers import now as console
from handlers import simulation_v3 as plans_v3
from security.principal import Principal

log = logging.getLogger("chatbot.deprecated")
router = Router(name="deprecated_commands")

#: command → (target command, screen)
_REDIRECTS = {
    "predictions": ("/forecast", console.show_forecast),
    "portfolio": ("/positions", console.show_positions),
    "trader": ("/session", console.show_session),
    "accuracy": ("/report", console.show_reports),
    "diff": ("/session", console.show_session),
}

_NOTE = (
    "<i>⚠️ Команда <code>/{old}</code> устарела и вызывает тот же use case, "
    "что <code>{new}</code>.</i>"
)

#: ``/plan`` has its own wording: it lands on a new canonical screen, not on an
#: existing nav card, so the note names the command to use from now on
_PLAN_NOTE = (
    "<i>↪️ Команда <code>/plan</code> устарела: откройте <code>/plans</code> — "
    "это тот же экран и тот же use case. Подкоманды (<code>/plans show &lt;id&gt;</code>, "
    "<code>/plans new</code>, <code>/plans set …</code>) доступны только в <code>/plans</code>.</i>"
)

#: the old Daily-Pipeline screens are not part of the new console at all
_LEGACY_NOTE = (
    "<i>Старые экраны daily-pipeline (trading_sessions/session_plans) в новую консоль "
    "не перенесены: команда открывает ближайший новый экран, и никаких записей в "
    "старые таблицы не появляется.</i>"
)

_LEGACY_SCREENS = frozenset({"trader", "diff", "accuracy"})


async def _redirect(message: Message, principal: Principal, old: str) -> None:
    target, screen = _REDIRECTS[old]
    note = _NOTE.replace("{old}", old).replace("{new}", target)
    if old in _LEGACY_SCREENS:
        note += "\n" + _LEGACY_NOTE
    await message.answer(note, parse_mode="HTML")
    await console.render_screen(screen, message, principal)


@router.message(Command("predictions"))
async def cmd_predictions(message: Message, principal: Principal) -> None:
    await _redirect(message, principal, "predictions")


@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message, principal: Principal) -> None:
    await _redirect(message, principal, "portfolio")


@router.message(Command("trader"))
async def cmd_trader(message: Message, principal: Principal) -> None:
    await _redirect(message, principal, "trader")


@router.message(Command("plan"))
async def cmd_plan(message: Message, principal: Principal) -> None:
    """``/plan`` redirects to the canonical V3 plan screen (TB-03)."""
    await message.answer(_PLAN_NOTE, parse_mode="HTML")
    await plans_v3.show_plans(message, principal)


@router.message(Command("accuracy"))
async def cmd_accuracy(message: Message, principal: Principal) -> None:
    await _redirect(message, principal, "accuracy")


@router.message(Command("diff"))
async def cmd_diff(message: Message, principal: Principal) -> None:
    await _redirect(message, principal, "diff")
