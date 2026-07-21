import json
import os
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage.redis_client import get_redis
from storage.journal_reader import get_recent_journal

log = logging.getLogger(__name__)
router = Router()

WATCHLIST = [s.strip().lower() for s in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if s.strip()]


def get_market_keyboard() -> InlineKeyboardMarkup:
    """ТЗ §8.6/§9 — 4 кнопки: символы + Анализ + Прогнозы (max 5)."""
    keyboard = []
    # Row 1: symbol buttons (ТЗ §8.6 — BTCUSDT, ETHUSDT)
    sym_buttons = [InlineKeyboardButton(text=sym.upper(), callback_data=f"analytics:{sym}") for sym in WATCHLIST[:2]]
    if sym_buttons:
        keyboard.append(sym_buttons)
    # Row 2: Анализ + Прогнозы (ТЗ §8.6)
    keyboard.append([
        InlineKeyboardButton(text="🧠 Анализ", callback_data="analytics:btcusdt"),
        InlineKeyboardButton(text="🔮 Прогнозы", callback_data="prediction_menu"),
    ])
    # Row 3: Обновить
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_market")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_market_text() -> str:
    """ТЗ §8.6 — live snapshot watchlist с RSI, MACD, volatility mode."""
    r = get_redis()
    lines = ["📊 <b>Рынок</b>", ""]

    # Get latest indicators from journal
    journal = await get_recent_journal(1)
    indicators_all = {}
    if journal:
        indicators_all = journal[0].get("indicators", {})

    for sym in WATCHLIST:
        data = await r.get(f"ticker:{sym}")
        if data:
            ticker = json.loads(data)
            emoji = "🟢" if ticker["change_pct"] >= 0 else "🔴"
            vol = ticker.get("volume", 0)
            vol_str = f" · Vol: {vol:,.0f} USDT" if vol else ""
            lines.append(f"{emoji} <b>{sym.upper()}</b>: ${ticker['price']:,.2f} ({ticker['change_pct']:+.2f}%){vol_str}")
        else:
            lines.append(f"⚪ <b>{sym.upper()}</b>: <i>данные не загружены</i>")

    # Add indicators (ТЗ §8.6)
    ind_lines = []
    for sym in WATCHLIST:
        ind = indicators_all.get(sym, {})
        rsi = ind.get("rsi_14")
        macd_hist = ind.get("macd_hist")
        if rsi is not None:
            ind_lines.append(f"RSI {sym.upper()} 1h: {rsi:.0f}")
        if macd_hist is not None:
            macd_signal = "positive" if macd_hist > 0 else "negative"
            ind_lines.append(f"MACD {sym.upper()} 1h: {macd_signal}")

    if ind_lines:
        lines.append("")
        lines.extend(ind_lines)

    # Volatility mode
    vol_parts = []
    for sym in WATCHLIST:
        data = await r.get(f"ticker:{sym}")
        if data:
            ticker = json.loads(data)
            vol = abs(ticker.get("change_pct", 0))
            if vol > 3:
                vol_parts.append(f"{sym.upper()}: high")
            elif vol > 1:
                vol_parts.append(f"{sym.upper()}: normal")
            else:
                vol_parts.append(f"{sym.upper()}: low")

    if vol_parts:
        lines.append(f"Режим волатильности: {vol_parts[0].split(': ')[1]}" if len(vol_parts) == 1 else "Режим волатильности: mixed")

    return "\n".join(lines)


async def send_market_data(message: Message):
    text = await build_market_text()
    await message.answer(text, reply_markup=get_market_keyboard())


@router.message(Command("market"))
async def cmd_market(message: Message):
    await send_market_data(message)