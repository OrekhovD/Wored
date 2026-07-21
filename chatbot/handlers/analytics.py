from __future__ import annotations

import os
import re
import json
import logging
from typing import Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage.redis_client import get_redis
from storage.journal_reader import get_recent_journal
from ai.router import route_request
from handlers.chat import sanitize_html, strip_html
from aiogram.exceptions import TelegramBadRequest

log = logging.getLogger(__name__)
router = Router()

WATCHLIST = [s.strip().lower() for s in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if s.strip()]


def get_analytics_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=sym.upper(), callback_data=f"analytics:{sym}") for sym in WATCHLIST]
    extra_row = [
        InlineKeyboardButton(text="🔮 Forecast Lab", callback_data="prediction_menu"),
        InlineKeyboardButton(text="📊 Рынок", callback_data="back_to_market"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons, extra_row] if buttons else [extra_row])


def get_analytics_result_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """ТЗ §8.7 — кнопки: Mini App, Рынок, Прогноз, Полный обзор."""
    url = os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080"
    base = url.rstrip("/")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 Mini App", url=base),
                InlineKeyboardButton(text="📊 Рынок", callback_data="back_to_market"),
            ],
            [
                InlineKeyboardButton(text=f"🔮 Прогноз {symbol.upper()}", callback_data=f"prediction_symbol:{symbol}"),
                InlineKeyboardButton(text="📈 Полный обзор", url=f"{base}/predictions"),
            ],
        ]
    )


async def show_analytics_menu(message: Message):
    await message.answer(
        "🧠 <b>AI-аналитика</b>\n\nВыберите монету для reasoning-анализа или откройте прогнозный контур.",
        reply_markup=get_analytics_keyboard(),
    )


def _extract_symbol(text: str) -> Optional[str]:
    """Extract symbol from text like 'анализ btc', 'что по eth', 'сравни btc и eth'."""
    msg = text.lower().strip()
    for sym in WATCHLIST:
        if sym in msg:
            return sym
    # bare symbol
    for sym in WATCHLIST:
        bare = sym.replace("usdt", "")
        if bare in msg:
            return sym
    return None


async def handle_analytics_text(message: Message, text: str):
    """ТЗ §6.3/§8.7 — analytics через text command, structured template."""
    symbol = _extract_symbol(text)
    if not symbol:
        await show_analytics_menu(message)
        return
    await _send_analytics_response(message, symbol)


async def _send_analytics_response(message: Message, symbol: str):
    """ТЗ §8.7 — structured analytics response."""
    try:
        redis_client = get_redis()
        data = await redis_client.get(f"ticker:{symbol}")
        if not data:
            await message.answer(
                f"⚠️ Данные по {symbol.upper()} временно недоступны.",
                reply_markup=get_analytics_result_keyboard(symbol),
            )
            return

        ticker = json.loads(data)

        # Get indicators from latest journal entry
        journal = await get_recent_journal(1)
        indicators = {}
        if journal:
            indicators = journal[0].get("indicators", {}).get(symbol, {})

        rsi = indicators.get("rsi_14")
        macd_hist = indicators.get("macd_hist")
        macd_val = indicators.get("macd")

        # Determine trend
        change = ticker.get("change_pct", 0)
        if change > 0.5:
            trend = "вверх"
        elif change < -0.5:
            trend = "вниз"
        else:
            trend = "боковик"

        # MACD signal
        if macd_hist is not None:
            macd_signal = "бычий" if macd_hist > 0 else "медвежий"
        else:
            macd_signal = "—"

        # Volatility mode
        vol = abs(change)
        if vol > 3:
            vol_mode = "high"
        elif vol > 1:
            vol_mode = "normal"
        else:
            vol_mode = "low"

        # Build context for AI
        context = [
            {
                "role": "system",
                "content": (
                    f"Текущие данные рынка для {symbol.upper()}: "
                    f"цена ${ticker['price']}, объём {ticker.get('volume', 0)}, "
                    f"изменение {change:+.2f}%. "
                    f"RSI 1h: {rsi:.0f}" if rsi else "" +
                    f" MACD: {macd_signal}"
                ),
            }
        ]
        prompt = (
            f"Проанализируй {symbol.upper()}. "
            f"Дай: тренд 1h, RSI, MACD сигнал, контекст, сценарий, риск. "
            f"Ответ в формате: 6 строк, каждая не более 40 символов. "
            f"Без вступлений и заключений."
        )

        try:
            ai_reply = await route_request(prompt, context=context)
            # ТЗ §8.7 — structured template + AI analysis
            lines = [
                f"🧠 <b>{symbol.upper()} — краткий анализ</b>",
                f"Тренд 1h: {trend}",
                f"RSI 1h: {rsi:.0f}" if rsi else "RSI 1h: —",
                f"MACD: {macd_signal}",
            ]
            # Append AI scenario/context lines
            for line in ai_reply.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("🧠"):
                    lines.append(sanitize_html(line))
                    if len(lines) >= 6:
                        break
            await message.answer(
                "\n".join(lines),
                reply_markup=get_analytics_result_keyboard(symbol),
            )
        except Exception as exc:
            log.error("Analytics AI error: %s", exc)
            # Fallback: template only, no AI
            lines = [
                f"🧠 <b>{symbol.upper()} — краткий анализ</b>",
                f"Тренд 1h: {trend}",
                f"RSI 1h: {rsi:.0f}" if rsi else "RSI 1h: —",
                f"MACD: {macd_signal}",
                f"Контекст: {vol_mode} volatility",
                f"Риск: {vol_mode}",
            ]
            await message.answer(
                "\n".join(lines),
                reply_markup=get_analytics_result_keyboard(symbol),
            )
    except Exception as exc:
        log.error("Analytics handler error: %s", exc)
        await message.answer("⚠️ Временная ошибка сервиса. Попробуй позже.")


@router.message(Command("analytics"))
@router.message(Command("analysis"))
async def cmd_analytics(message: Message):
    await show_analytics_menu(message)