"""
Portfolio handler — delegates to pipeline._build_positions_response.
Shows real session positions (PnL, margin, side) instead of plain tickers.
"""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    from handlers.pipeline import _build_positions_response
    text, kb = await _build_positions_response(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")