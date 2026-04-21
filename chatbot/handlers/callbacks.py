from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from ai.router import route_request
from storage.redis_client import get_redis
import json
import logging
from handlers.market import build_market_text, get_market_keyboard

log = logging.getLogger(__name__)
router = Router()

def get_analytics_result_keyboard(symbol: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Второе мнение", callback_data=f"second_opinion:{symbol}")],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"analytics:{symbol}"),
            InlineKeyboardButton(text="◀️ К рынку", callback_data="back_to_market")
        ]
    ])

@router.callback_query(F.data == "refresh_market")
async def cb_refresh_market(call: CallbackQuery):
    text = await build_market_text()
    try:
        await call.message.edit_text(text, reply_markup=get_market_keyboard())
        await call.answer("Цены обновлены!")
    except TelegramBadRequest:
        await call.answer("Данные актуальны", show_alert=False)
    except Exception as e:
        await call.answer("Нет новых данных")

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
    
    await call.message.edit_text(f"⏳ <i>Анализирую {symbol.upper()}...</i>")
    
    r = get_redis()
    data = await r.get(f"ticker:{symbol}")
    
    if not data:
        await call.message.edit_text(
            f"❌ Нет данных по {symbol.upper()}.", 
            reply_markup=get_analytics_result_keyboard(symbol)
        )
        await call.answer()
        return
        
    ticker = json.loads(data)
    
    ctx = [{
        "role": "system",
        "content": f"Текущие данные рынка для {symbol.upper()}: Стоимость ${ticker['price']}, Объем {ticker['volume']}, Изменение {ticker['change_pct']:+.2f}%."
    }]
    
    prompt = f"Проанализируй текущую ситуацию по {symbol.upper()}. Краткий прогноз."
    
    try:
        reply = await route_request(prompt, context=ctx)
        await call.message.edit_text(
            reply, 
            reply_markup=get_analytics_result_keyboard(symbol)
        )
    except Exception as e:
        log.error(f"Render AI error: {e}")
        await call.message.edit_text(
            f"❌ Ошибка вызова AI: {e}",
            reply_markup=get_analytics_result_keyboard(symbol)
        )
        
    await call.answer()

@router.callback_query(F.data.startswith("second_opinion:"))
async def cb_second_opinion(call: CallbackQuery):
    symbol = call.data.split(":")[1]
    
    # Grab the previous analysis from the message
    prev_text = call.message.html_text
    
    # Отправляем НОВОЕ сообщение, чтобы не затирать переданный анализ
    wait_msg = await call.message.answer(f"⚖️ <i>Оракул (MiniMax) изучает анализ {symbol.upper()}...</i>")
    
    prompt = f"Вот анализ {symbol.upper()}, подготовленный предыдущей моделью:\n\n{prev_text}\n\nСформируй второе независимое мнение."
    
    try:
        from ai.router import _call_with_fallback
        reply = await _call_with_fallback("minimax", "minimax_reviewer", prompt, None)
        await wait_msg.edit_text(
            reply,
            reply_markup=get_analytics_result_keyboard(symbol)
        )
    except Exception as e:
        log.error(f"Render MiniMax error: {e}")
        await wait_msg.edit_text(
            f"❌ Ошибка MiniMax AI: {e}",
            reply_markup=get_analytics_result_keyboard(symbol)
        )
        
    await call.answer()
