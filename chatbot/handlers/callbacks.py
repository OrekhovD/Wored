import json
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ai.router import route_request
from handlers.chat import sanitize_html, strip_html
from handlers.market import build_market_text, get_market_keyboard
from storage.redis_client import get_redis

log = logging.getLogger(__name__)
router = Router()


def get_analytics_result_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚖️ Второе мнение", callback_data=f"second_opinion:{symbol}"),
                InlineKeyboardButton(text="🔮 Прогноз", callback_data=f"prediction_symbol:{symbol}"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить анализ", callback_data=f"analytics:{symbol}"),
                InlineKeyboardButton(text="◀️ К рынку", callback_data="back_to_market"),
            ],
        ]
    )


async def answer_callback_early(call: CallbackQuery, text: str = "⏳ Запрос принят"):
    try:
        await call.answer(text, show_alert=False)
    except TelegramBadRequest as exc:
        log.info("Callback %s expired before ack: %s", call.data, exc)
    except Exception as exc:
        log.warning("Callback %s ack failed: %s", call.data, exc)


@router.callback_query(F.data == "refresh_market")
async def cb_refresh_market(call: CallbackQuery):
    text = await build_market_text()
    try:
        await call.message.edit_text(text, reply_markup=get_market_keyboard())
        await call.answer("Цены обновлены")
    except TelegramBadRequest:
        await call.answer("Данные уже актуальны", show_alert=False)
    except Exception:
        await call.answer("Не удалось обновить рынок")


@router.callback_query(F.data == "back_to_market")
async def cb_back_to_market(call: CallbackQuery):
    text = await build_market_text()
    try:
        await call.message.edit_text(text, reply_markup=get_market_keyboard())
        await call.answer()
    except TelegramBadRequest:
        await call.answer()
    except Exception:
        await call.answer()


@router.callback_query(F.data.startswith("analytics:"))
async def cb_analytics(call: CallbackQuery):
    symbol = call.data.split(":")[1]
    await answer_callback_early(call)
    await call.message.edit_text(f"⏳ <i>Анализирую {symbol.upper()}...</i>")

    redis_client = get_redis()
    data = await redis_client.get(f"ticker:{symbol}")
    if not data:
        await call.message.edit_text(
            f"❌ Нет данных по {symbol.upper()}.",
            reply_markup=get_analytics_result_keyboard(symbol),
        )
        return

    ticker = json.loads(data)
    context = [
        {
            "role": "system",
            "content": (
                f"Текущие данные рынка для {symbol.upper()}: "
                f"цена ${ticker['price']}, объём {ticker['volume']}, изменение {ticker['change_pct']:+.2f}%."
            ),
        }
    ]
    prompt = f"Проанализируй текущую ситуацию по {symbol.upper()}. Краткий прогноз и ключевые риски."

    try:
        reply = await route_request(prompt, context=context)
        try:
            await call.message.edit_text(
                sanitize_html(reply),
                reply_markup=get_analytics_result_keyboard(symbol),
            )
        except TelegramBadRequest:
            await call.message.edit_text(
                strip_html(reply),
                reply_markup=get_analytics_result_keyboard(symbol),
                parse_mode=None,
            )
    except Exception as exc:
        log.error("Render AI error: %s", exc)
        await call.message.edit_text(
            f"❌ Ошибка вызова AI: {exc}",
            reply_markup=get_analytics_result_keyboard(symbol),
            parse_mode=None,
        )


@router.callback_query(F.data.startswith("second_opinion:"))
async def cb_second_opinion(call: CallbackQuery):
    symbol = call.data.split(":")[1]
    await answer_callback_early(call)
    previous_text = call.message.html_text
    wait_message = await call.message.answer(f"⚖️ <i>Oracle изучает анализ {symbol.upper()}...</i>")

    prompt = (
        f"Вот анализ {symbol.upper()}, подготовленный предыдущей моделью:\n\n"
        f"{previous_text}\n\n"
        "Сформируй второе независимое мнение."
    )

    try:
        from ai.router import _call_with_fallback

        reply = await _call_with_fallback("minimax", "minimax_reviewer", prompt, None)
        try:
            await wait_message.edit_text(
                sanitize_html(reply),
                reply_markup=get_analytics_result_keyboard(symbol),
            )
        except TelegramBadRequest:
            await wait_message.edit_text(
                strip_html(reply),
                reply_markup=get_analytics_result_keyboard(symbol),
                parse_mode=None,
            )
    except Exception as exc:
        log.error("Render MiniMax error: %s", exc)
        await wait_message.edit_text(
            f"❌ Ошибка Oracle: {exc}",
            reply_markup=get_analytics_result_keyboard(symbol),
            parse_mode=None,
        )
