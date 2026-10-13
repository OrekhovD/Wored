"""
Portfolio handler — legacy name, one screen with the console (TB-03).

``/portfolio`` is redirected by ``handlers/deprecated.py`` and the «Портфель»
button calls the same function, so the old sim_engine-based digest can never show
a second balance for one owner.  Positions are read from the ``paper_trading``
domain through :mod:`integrations.wored_api`.
"""
from aiogram import Router
from aiogram.types import Message

from handlers import now as console
from security.principal import Principal

router = Router()


async def cmd_portfolio(message: Message, principal: Principal) -> None:
    """Delegate to the console positions screen — no own implementation."""
    await console.show_positions(message, principal)