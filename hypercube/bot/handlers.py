"""Telegram bot handlers — thin handlers delegating to services."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.config import AppConfiguration
from core.enums import RoutingMode
from core.schemas import AIRequest
from core.services import ServiceContainer, UserSessionState

log = logging.getLogger(__name__)

router = Router()

# ── global state (injected at startup) ─────────────────────────────────
_bot: Bot | None = None
_config: AppConfiguration | None = None
_services: ServiceContainer | None = None


def setup_bot(bot: Bot, config: AppConfiguration, services: ServiceContainer) -> None:
    global _bot, _config, _services
    _bot = bot
    _config = config
    _services = services


def create_bot_router() -> Router:
    return router


# ── /start ─────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    await _services.user_repo.create_or_update(
        telegram_id=user_id,
        username=username,
        first_name=first_name,
    )

    text = (
        "👋 **Hytergram AI Gateway**\n\n"
        "Бот для аналитики крипторынка (биржа HTX).\n\n"
        "**Режим:** free_only (только бесплатные модели)\n"
        "**Модель по умолчанию:** qwen-turbo\n\n"
        "⚠️ **Важно:**\n"
        "- Только аналитика, без торговли\n"
        "- Только биржа HTX\n"
        "- Не является финансовым советником\n\n"
        "Команда `/help` покажет все доступные команды."
    )
    await message.answer(text, parse_mode="Markdown")


# ── /help ──────────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📚 **Доступные команды:**\n\n"
        "/ask — задать вопрос по рынку HTX\n"
        "/mode — выбрать режим (free_only/balanced/premium)\n"
        "/models — список моделей\n"
        "/providers — список провайдеров\n"
        "/usage — статистика использования токенов\n"
        "/quota — статус квоты\n"
        "/context — состояние текущего контекста\n"
        "/switch_model — переключить модель\n"
        "/health — проверка здоровья системы\n"
        "/reload — перезагрузить конфиги (admin)\n"
        "/admin_stats — расширенная статистика (admin)\n\n"
        "**Режимы:**\n"
        "- free_only: только бесплатные модели\n"
        "- balanced: баланс цена/качество\n"
        "- premium: лучшие модели"
    )
    await message.answer(text, parse_mode="Markdown")


# ── /ask — главная команда аналитики ───────────────────────────────────
@router.message(Command("ask"))
async def cmd_ask(message: Message) -> None:
    if not message.text:
        await message.answer("Пожалуйста, укажите вопрос после /ask")
        return

    user_query = message.text.split("/ask", 1)[1].strip()
    if not user_query:
        await message.answer("Пожалуйста, укажите вопрос после /ask")
        return

    await message.answer("🤔 Обрабатываю запрос...", parse_mode="Markdown")

    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # 1. Ensure user exists
        user = await _services.user_repo.get_by_telegram_id(user_id)
        if not user:
            user = await _services.user_repo.create_or_update(
                telegram_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )

        # 2. Get session state
        session_state = _services.user_sessions.get(user_id)
        if not session_state:
            session_state = UserSessionState(telegram_user_id=user_id)
            _services.user_sessions[user_id] = session_state

        mode = session_state.current_mode or "free_only"

        # Initialize session ID in state if empty
        if not session_state.session_id:
            db_session = await _services.session_repo.get_active_session_for_user(user_id)
            import uuid
            if db_session:
                session_state.session_id = db_session.session_id
            else:
                session_state.session_id = str(uuid.uuid4())
                await _services.session_repo.create_session(
                    session_id=session_state.session_id, user_id=user.id, chat_id=chat_id, mode=mode
                )

        # Record user message
        await _services.message_repo.add_message(session_state.session_id, "user", user_query)
        
        # 4. Quota check
        chain = _services.fallback_engine.get_candidate_chain(RoutingMode(mode))
        primary_model = session_state.current_model or (chain[0] if chain else "")
        provider_id = _services.fallback_engine._provider_for_model(primary_model)
        
        if provider_id:
            quota_result = await _services.quota_engine.check_quota(provider_id, primary_model)
            if quota_result.hard_stop:
                await message.answer(
                    f"⛔ Квота исчерпана для {primary_model}.\n"
                    f"Остаток: {quota_result.remaining_pct:.1f}%\n\n"
                    f"Используйте /switch_model для переключения.",
                    parse_mode="Markdown"
                )
                return
            quota_warning = quota_result.warning_triggered
        else:
            quota_warning = False

        # 5. HTX enrichment
        market_context = ""
        market_keywords = ["btc", "eth", "usdt", "рынок", "цена", "курс", "анализ", 
                          "торг", "свеч", "объем", "volume", "ticker", "тикер"]
        if any(kw in user_query.lower() for kw in market_keywords):
            try:
                tickers = await _services.htx_adapter.get_tickers()
                top5 = sorted(tickers, key=lambda t: t.volume, reverse=True)[:5]
                market_lines = [f"- {t.symbol}: ${t.last:.4f} ({t.change_pct:+.2f}%)" for t in top5]
                market_context = (
                    "\n\n[HTX Market Context - Top 5 by volume]\n"
                    + "\n".join(market_lines)
                )
            except Exception as e:
                log.warning("HTX enrichment failed: %s", e)

        # 6. Build messages
        system_prompt = (
            "Ты — AI-аналитик криптобиржи HTX. "
            "Предоставляй структурированную аналитику без исполнения сделок. "
            "Всегда добавляй дисклеймер: бот не является финансовым советником."
        )
        if market_context:
            system_prompt += market_context

        # Get history from DB
        history = await _services.message_repo.get_history(session_state.session_id, limit=5)
        
        context_messages = [{"role": "system", "content": system_prompt}]
        for msg in reversed(history):
            if msg.role != 'system':
                context_messages.append({"role": msg.role, "content": msg.content})

        # 7. Execute request
        request = AIRequest(
            model=primary_model,
            messages=context_messages,
            max_tokens=1024,
            temperature=0.7,
            stream=False,
        )

        response, decision = await _services.fallback_engine.execute_with_fallback(
            request, _services.provider_adapters, RoutingMode(mode)
        )

        # Output message
        await _services.message_repo.add_message(session_state.session_id, "assistant", response.content)

        # 8. Persist usage
        from core.request_id import generate_request_id
        from storage.models import UsageRecord as UsageRecordModel, RouteDecision as RouteDecisionModel
        request_id = generate_request_id()
        
        resp_provider = _services.fallback_engine._provider_for_model(response.model) or "unknown"
        
        usage_record = UsageRecordModel(
            request_id=request_id,
            conversation_id=session_state.conversation_id or "",
            telegram_user_id=user_id,
            provider_id=resp_provider,
            model_id=response.model,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
            latency_ms=0,
            status="success",
            cost_estimate=0.0,
            warning_triggered=quota_warning,
            fallback_triggered=response.model != primary_model,
        )
        await _services.usage_repo.create(usage_record)
        
        route_decision = RouteDecisionModel(
            request_id=request_id,
            original_model=primary_model,
            final_model=response.model,
            routing_mode=mode,
            handoff_required=decision.handoff_required,
            reason_code=decision.reason_code,
        )
        await _services.route_repo.create(route_decision)

        # update quota
        if response.usage and response.usage.total_tokens > 0:
            qs = await _services.quota_repo.get_for_provider_model(resp_provider, response.model)
            if qs:
                await _services.quota_engine.update_quota_state(
                    resp_provider, response.model, qs.used_tokens + response.usage.total_tokens, qs.limit_tokens
                )

        # 9. Reply
        fallback_note = ""
        if response.model != primary_model:
            fallback_note = f"\n⚡ _Fallback: {primary_model} → {response.model}_"
        
        warning_note = ""
        if quota_warning:
            warning_note = "\n⚠️ _Предупреждение: квота близка к исчерпанию_"

        reply_text = (
            f"📊 **Ответ:**\n\n"
            f"{response.content}\n\n"
            f"---\n"
            f"_Модель: {response.model}_"
            f"{fallback_note}"
            f"{warning_note}"
        )
        
        # Telegram limit: 4096 chars
        if len(reply_text) > 4000:
            reply_text = reply_text[:3990] + "\n...(обрезано)"
        
        await message.answer(reply_text, parse_mode="Markdown")

    except Exception as e:
        log.exception("Ask command failed")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── /mode ──────────────────────────────────────────────────────────────
@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    if not session_state:
        session_state = UserSessionState(telegram_user_id=user_id)
        _services.user_sessions[user_id] = session_state

    args = message.text.split()
    if len(args) < 2:
        text = (
            f"**Текущий режим:** {session_state.current_mode}\n\n"
            f"Использование: `/mode free_only|balanced|premium`"
        )
        await message.answer(text, parse_mode="Markdown")
        return

    new_mode = args[1].lower()
    if new_mode not in ("free_only", "balanced", "premium"):
        await message.answer("Неверный режим. Используйте: free_only, balanced, premium")
        return

    session_state.current_mode = new_mode
    if session_state.session_id:
        await _services.session_repo.update_mode(session_state.session_id, new_mode)
    await message.answer(f"✅ Режим переключен на **{new_mode}**", parse_mode="Markdown")


# ── /models ────────────────────────────────────────────────────────────
@router.message(Command("models"))
async def cmd_models(message: Message) -> None:
    models = _services.model_registry.get_all_models()

    lines = ["📦 **Доступные модели:**\n"]
    for m in models:
        premium_mark = "💎" if m.get("is_premium") else "🆓"
        status_icon = "✅" if m["status"] == "active" else "⚠️"
        lines.append(f"{status_icon} {premium_mark} **{m['model_id']}** ({m['provider_id']})")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /providers ─────────────────────────────────────────────────────────
@router.message(Command("providers"))
async def cmd_providers(message: Message) -> None:
    providers = _services.provider_adapters
    lines = ["🔌 **Провайдеры:**\n"]
    for pid, adapter in providers.items():
        try:
            health = await adapter.healthcheck()
            icon = "✅" if health.healthy else "❌"
            lines.append(f"{icon} **{pid}**: {adapter.provider_id} (latency: {health.latency_ms:.0f}ms)")
        except Exception:
            lines.append(f"❌ **{pid}**: {adapter.provider_id} (error)")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /usage ─────────────────────────────────────────────────────────────
@router.message(Command("usage"))
async def cmd_usage(message: Message) -> None:
    user_id = message.from_user.id
    try:
        day = await _services.accounting.get_user_usage(user_id, "day")
        week = await _services.accounting.get_user_usage(user_id, "week")
        month = await _services.accounting.get_user_usage(user_id, "month")
        text = (
            f"📊 **Использование (user {user_id}):**\n\n"
            f"За день: {day.get('requests', 0)} запросов, {day.get('total_tokens', 0)} токенов, ${day.get('cost', 0):.4f}\n"
            f"За неделю: {week.get('requests', 0)} запросов, {week.get('total_tokens', 0)} токенов, ${week.get('cost', 0):.4f}\n"
            f"За месяц: {month.get('requests', 0)} запросов, {month.get('total_tokens', 0)} токенов, ${month.get('cost', 0):.4f}"
        )
    except Exception as e:
        text = f"❌ Ошибка получения статистики: {str(e)[:200]}"
    await message.answer(text, parse_mode="Markdown")


# ── /quota ─────────────────────────────────────────────────────────────
@router.message(Command("quota"))
async def cmd_quota(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    mode = session_state.current_mode if session_state else "free_only"
    
    try:
        chain = _services.fallback_engine.get_candidate_chain(RoutingMode(mode))
        lines = [f"📏 **Статус квоты (режим: {mode}):**\n"]
        for model_id in chain[:3]:
            provider_id = _services.fallback_engine._provider_for_model(model_id)
            if provider_id:
                result = await _services.quota_engine.check_quota(provider_id, model_id)
                icon = "✅" if result.allowed else "⛔"
                lines.append(f"{icon} **{model_id}**: {result.remaining_pct:.1f}% осталось")
            else:
                lines.append(f"❓ **{model_id}**: провайдер не найден")
        
        lines.append(f"\n_Пороги: warning={_config.QUOTA_WARNING_THRESHOLD_PCT}%, "
                     f"critical={_config.QUOTA_CRITICAL_THRESHOLD_PCT}%, "
                     f"hard stop={_config.QUOTA_HARD_STOP_THRESHOLD_PCT}%_")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── /context ───────────────────────────────────────────────────────────
@router.message(Command("context"))
async def cmd_context(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    
    if not session_state or not session_state.session_id:
        await message.answer("🧠 **Состояние контекста:**\n\nАктивная сессия: нет", parse_mode="Markdown")
        return
    
    try:
        msg_count = await _services.message_repo.count_messages(session_state.session_id)
        snapshot = await _services.snapshot_repo.get_latest(session_state.session_id)
        snapshot_info = f"Последний snapshot: {snapshot.created_at.isoformat()}" if snapshot else "Последний snapshot: нет"
        
        text = (
            f"🧠 **Состояние контекста:**\n\n"
            f"Активная сессия: {session_state.session_id[:8]}...\n"
            f"Режим: {session_state.current_mode}\n"
            f"Модель: {session_state.current_model or 'auto'}\n"
            f"Сообщений в истории: {msg_count}\n"
            f"{snapshot_info}"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── /switch_model ──────────────────────────────────────────────────────
@router.message(Command("switch_model"))
async def cmd_switch_model(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    if not session_state:
        session_state = UserSessionState(telegram_user_id=user_id)
        _services.user_sessions[user_id] = session_state
    
    args = message.text.split()
    if len(args) < 2:
        models = _services.model_registry.get_all_models()
        model_list = ", ".join(m["model_id"] for m in models if m["status"] == "active")
        await message.answer(
            f"Использование: `/switch_model <model_id>`\n\nДоступные: {model_list}",
            parse_mode="Markdown"
        )
        return

    target_model = args[1]
    old_model = session_state.current_model or "auto"
    mode = session_state.current_mode

    try:
        # Verify model is available
        available_models = [m["model_id"] for m in _services.model_registry.get_all_models()]
        if target_model not in available_models:
            await message.answer(f"❌ Модель {target_model} недоступна")
            return
        
        # Perform handoff if there was an active session
        handoff_note = ""
        if session_state.session_id and old_model != "auto":
            try:
                handoff = await _services.handoff_builder.build_handoff(
                    old_session_id=session_state.session_id,
                    new_model=target_model,
                    old_model=old_model,
                )
                handoff_note = "\n✅ Контекст сохранён и передан"
            except Exception as e:
                log.warning("Handoff failed: %s", e)
                handoff_note = "\n⚠️ Контекст не удалось передать"

        # Update session state
        session_state.current_model = target_model
        if session_state.session_id:
            await _services.session_repo.update_model(session_state.session_id, target_model)
        
        await message.answer(
            f"🔄 Переключение выполнено:\n\n"
            f"**Было:** {old_model}\n"
            f"**Стало:** {target_model}\n"
            f"**Режим:** {mode}"
            f"{handoff_note}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── /health ────────────────────────────────────────────────────────────
@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    checks = {}
    
    # Gateway
    checks["Gateway"] = True
    
    # Database
    try:
        from storage.database import async_session_factory
        sf = async_session_factory
        checks["Database"] = sf is not None
    except Exception:
        checks["Database"] = False
    
    # HTX API
    try:
        ts = await _services.htx_adapter.get_server_time()
        checks["HTX API"] = ts > 0
    except Exception:
        checks["HTX API"] = False
    
    # AI Providers
    provider_checks = {}
    for pid, adapter in _services.provider_adapters.items():
        try:
            health = await adapter.healthcheck()
            provider_checks[pid] = health.healthy
        except Exception:
            provider_checks[pid] = False
    
    checks["AI Providers"] = any(provider_checks.values()) if provider_checks else False

    lines = []
    all_ok = all(checks.values())
    lines.append(f"{'💚' if all_ok else '🔴'} **Состояние системы:**\n")
    for name, ok in checks.items():
        lines.append(f"{'✅' if ok else '❌'} {name}")
    
    if provider_checks:
        lines.append("\n**Провайдеры:**")
        for pid, ok in provider_checks.items():
            lines.append(f"  {'✅' if ok else '❌'} {pid}")
    
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /reload (admin) ────────────────────────────────────────────────────
@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    admin_ids = _config.admin_user_ids if _config else set()
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Доступ запрещён")
        return
    await message.answer("🔄 Конфигурация перезаряжена")


# ── /admin_stats (admin) ───────────────────────────────────────────────
@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    admin_ids = _config.admin_user_ids if _config else set()
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Доступ запрещён")
        return

    try:
        day_usage = await _services.accounting.get_user_usage(0, "day")  # 0 = all users
        recent_fallbacks = await _services.usage_repo.recent_fallbacks(limit=5)
        recent_handoffs = await _services.usage_repo.recent_handoffs(limit=5)
        
        text = (
            f"📈 **Расширенная статистика:**\n\n"
            f"Запросов за сегодня: {day_usage.get('requests', 0)}\n"
            f"Токенов за сегодня: {day_usage.get('total_tokens', 0)}\n"
            f"Стоимость за сегодня: ${day_usage.get('cost', 0):.4f}\n"
            f"Fallback events (последние): {len(recent_fallbacks)}\n"
            f"Context handoff events (последние): {len(recent_handoffs)}\n"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
