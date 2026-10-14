import re
import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from ai.router import route_request
from handlers import telegram_surface as surface

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

def _bare_payload_refusal(text: str) -> Optional[str]:
    """Подсказка для ``key=value``-строки, набранной без команды; None — не она.

    Форма принадлежит поверхности (:func:`telegram_surface.looks_like_key_values`),
    распознавание ключей — сервису-владельцу: анкету считает
    :func:`services.simulation_proposals.recognised_answer_keys`, аргументы
    тикета — :func:`services.paper_orders.recognises_ticket_args`, лимиты дня —
    :func:`services.day_control.recognises_limit_args`.  Второй парсер
    или второй список ключей отсюда запрещены (ТЗ TB-03).
    """
    from application import presenters as ui
    from handlers import telegram_surface as surface
    from services import day_control as control
    from services import paper_orders as deals
    from services import simulation_proposals as proposals

    if not surface.looks_like_key_values(text):
        return None

    keys = ", ".join(sorted(proposals.ANSWER_KEYS))
    answered = proposals.recognised_answer_keys(text)
    if answered:
        return (
            "🗳️ <b>Это ответ анкеты, но без команды — анкета так не принимается.</b>\n"
            f"нужно: <code>{ui.esc(proposals.answer_template())}</code>\n"
            f"распознано полей: {answered} · разрешённые ключи: {keys}\n"
            "<i>Свободный текст без команды ушёл бы в модель, а модель отвечает "
            "торговыми числами без контракта. Ни одно предложение не создано, в "
            "модуль этот текст не отправлен.</i>"
        )

    if deals.recognises_ticket_args(text):
        return (
            "🎯 <b>Это аргументы заявки, но без команды — тикет так не собирается.</b>\n"
            f"нужно: <code>{ui.esc(deals.usage_preview(instrument=deals.tradable_instrument()))}</code>\n"
            f"разрешённые ключи: {', '.join(sorted(deals.ARG_KEYS))}\n"
            "<i>Числа ручной сделки считает доменный тикет-гейт, и только у команды "
            "есть предпросмотр с подтверждением. В модель этот текст не отправлен, "
            "заявка не записана.</i>"
        )

    if control.recognises_limit_args(text):
        return (
            "🛡 <b>Это лимиты дня, но без команды — посреди дня так не меняются.</b>\n"
            f"нужно: <code>{ui.esc(control.usage_preview())}</code>\n"
            "<i>Лимиты живут в снапшоте дня, и их изменение — доменная команда с "
            "подтверждением. В модель этот текст не отправлен, день не изменён.</i>"
        )

    return None


@router.message(F.text)
async def cmd_chat(message: Message, principal=None):
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
        # TB-03: главное меню живёт в консоли, а не в отдельной старой версии
        # /start. Principal приходит из guard-middleware; если вызов прямой
        # (тест, внутренний код), резолвим тем же серверным правилом.
        from handlers import now as console
        from security.principal import resolve

        await console.cmd_start(message, principal or resolve(message.from_user.id))
        return

    # Q-15 (live P3c finding): a slash-shaped input that survived every Command
    # filter is a mistyped or unknown command, and the deterministic paths above
    # have already declined it.  It must not reach the model: the LLM answers
    # "plans" with a generated trade plan — entry, stop, target, confidence —
    # which looks exactly like the plan surface while having no plan_hash, no
    # violation list and no contract behind it ("no invented numbers").
    if surface.looks_like_command(message.text or ""):
        log.info("unrecognised command declined without AI: %r", (message.text or "")[:40])
        await message.answer(surface.SLASH_HINT, parse_mode="HTML")
        return

    # Q-20 (RFC, фаза P4): payload keys typed without their command.  The
    # questionnaire answer and the deal-ticket arguments are the two inputs this
    # bot accepts as a row of ``key=value`` tokens.  Left alone they are prose to
    # every Command filter, land here, and get a model answer — a text about
    # entries and stops with no contract behind it, the same defect Q-15 found on
    # the mistyped command.  The payload is named back as the exact command and
    # the model is not called; ownership of the keys belongs to the services that
    # parse them, not to this router.
    refusal = _bare_payload_refusal(message.text or "")
    if refusal:
        log.info("bare command payload declined without AI: %d tokens", len((message.text or "").split()))
        await message.answer(refusal, parse_mode="HTML")
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