import re
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from ai.router import route_request

log = logging.getLogger(__name__)
router = Router()

def sanitize_html(text: str) -> str:
    """Fix common broken HTML from AI responses for Telegram."""
    allowed = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'blockquote'}
    for tag in allowed:
        opens = len(re.findall(rf'<{tag}(?:\s[^>]*)?>', text, re.IGNORECASE))
        closes = len(re.findall(rf'</{tag}>', text, re.IGNORECASE))
        for _ in range(opens - closes):
            text += f'</{tag}>'
    return text

def strip_html(text: str) -> str:
    """Remove all HTML tags as a last resort."""
    return re.sub(r'<[^>]+>', '', text)

def _is_product_intent(text: str) -> bool:
    """ТЗ §10.2 — перехват продуктовых intents до AI chat (regex-first)."""
    from handlers.pipeline import classify_pipeline_intent
    intent = classify_pipeline_intent(text or "")
    if intent:
        return True
    msg = (text or "").lower().strip()
    # ТЗ §6.2 — market keywords
    if any(p in msg for p in ["рынок", "цена btc", "цена eth", "btcusdt", "ethusdt", "watchlist", "покажи рынок", "снэпшот рынка", "снепшот рынка"]):
        return True
    # ТЗ §6.3 — analytics
    if any(p in msg for p in ["анализ btc", "анализ eth", "сравни", "что по btc", "что по eth", "reasoning по"]):
        return True
    # ТЗ §6.4 — forecast
    if any(p in msg for p in ["прогноз btc", "прогноз eth", "forecast lab", "scorecard", "прогнозы"]):
        return True
    # ТЗ §6.5 — portfolio
    if any(p in msg for p in ["портфель", "баланс", "pnl", "покажи pnl", "сколько позиций", "мои позиции"]):
        return True
    # ТЗ §6.6 — alerts
    if any(p in msg for p in ["алерты", "добавить алерт", "мои алерты", "удалить алерт"]):
        return True
    return False

async def _dispatch_product_intent(text: str, message: Message):
    """ТЗ §10.2 — deterministic dispatch для продуктовых intents."""
    msg = text.lower().strip()

    # Pipeline intents (session control)
    from handlers.pipeline import classify_pipeline_intent, handle_pipeline_intent
    intent = classify_pipeline_intent(text or "")
    if intent:
        resp = await handle_pipeline_intent(intent, user_id=message.from_user.id)
        await message.answer(resp, parse_mode="HTML")
        return

    # ТЗ §6.2 — Market
    if any(p in msg for p in ["рынок", "цена btc", "цена eth", "btcusdt", "ethusdt", "watchlist", "покажи рынок", "снэпшот рынка", "снепшот рынка"]):
        from handlers.market import send_market_data
        await send_market_data(message)
        return

    # ТЗ §6.3 — Analytics
    if any(p in msg for p in ["анализ btc", "анализ eth", "сравни", "что по btc", "что по eth", "reasoning по"]):
        from handlers.analytics import handle_analytics_text
        await handle_analytics_text(message, text)
        return

    # ТЗ §6.4 — Forecast
    if any(p in msg for p in ["прогноз btc", "прогноз eth", "forecast lab", "scorecard", "прогнозы"]):
        from handlers.predictions import send_prediction_menu
        await send_prediction_menu(message)
        return

    # ТЗ §6.5 — Portfolio
    if any(p in msg for p in ["портфель", "баланс", "pnl", "покажи pnl", "сколько позиций", "мои позиции"]):
        from handlers.pipeline import _build_balance_response, _build_positions_response
        if any(p in msg for p in ["баланс", "pnl", "покажи pnl"]):
            text_resp, kb = await _build_balance_response(message.from_user.id)
        else:
            text_resp, kb = await _build_positions_response(message.from_user.id)
        await message.answer(text_resp, reply_markup=kb, parse_mode="HTML")
        return

    # ТЗ §6.6 — Alerts
    if any(p in msg for p in ["алерты", "добавить алерт", "мои алерты", "удалить алерт"]):
        from handlers.alerts import send_alerts
        await send_alerts(message)
        return

@router.message(F.text)
async def cmd_chat(message: Message):
    """ТЗ §7.5 — один intent → один режим → один тип ответа.

    ТЗ §10.2 — regex-first: перехватываем продуктовые intents до AI chat.
    """
    # ТЗ §10.2 — deterministic dispatch для продуктовых intents
    if _is_product_intent(message.text or ""):
        await _dispatch_product_intent(message.text or "", message)
        return

    # U9 — помощь → главное меню
    msg_lower = (message.text or "").lower().strip()
    if msg_lower in ("помощь", "help", "меню", "menu", "начать", "старт", "/start"):
        from handlers.start import cmd_start
        await cmd_start(message)
        return

    # Fallback: AI chat for non-product queries (ТЗ §10.3 — worker-tier only)
    wait_msg = await message.answer("🤔 <i>Анализирую…</i>")
    try:
        reply = await route_request(message.text)
        try:
            await wait_msg.edit_text(sanitize_html(reply))
        except TelegramBadRequest:
            log.warning("HTML sanitize failed, sending as plain text")
            await wait_msg.edit_text(strip_html(reply), parse_mode=None)
    except Exception as e:
        log.error(f"AI call error: {e}")
        # ТЗ §7.4 — normalized error, no raw backend error
        await wait_msg.edit_text("⚠️ Временная ошибка сервиса. Попробуй позже.", parse_mode=None)