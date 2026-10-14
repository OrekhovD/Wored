"""
Daily Pipeline WORED v2 — Telegram handler (ТЗ new Telegram UX)

§6.1 — session commands and phrases
§8  — mandatory response templates (compact, templated)
§10 — intent-first routing, regex-first for critical commands
§11 — normalized errors only
"""
from __future__ import annotations

import json
import logging
import re
import os
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
# NOTE: /session and /plan are not bound in this router anymore (TB-03). The
# console owns the commands (handlers/now.py) and handlers/deprecated.py
# redirects the old names, so the Command filter is no longer needed here.

log = logging.getLogger(__name__)
router = Router(name="pipeline")

DEFAULT_USER_ID = 5249526259

# ── Inline keyboards (ТЗ §9: max 5 buttons) ──

def _miniapp_url() -> str:
    return os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080/daily-session"

def _session_nav_kb(*, show_review: bool = False, show_day_control: bool = False) -> InlineKeyboardMarkup:
    """Unified navigation keyboard for session views.
    Mini App + context-sensitive action buttons + back to control menu.
    """
    url = _miniapp_url()
    rows = [
        [InlineKeyboardButton(text="📱 Mini App", url=url)],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="pipeline_status"),
            InlineKeyboardButton(text="📦 Позиции", callback_data="pipeline_positions"),
        ],
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data="pipeline_balance"),
            InlineKeyboardButton(text="📋 План", callback_data="pipeline_plan"),
        ],
    ]
    if show_day_control:
        # Q-24: here was «🛑 Close all» — a raw mutation keyed by a callback
        # string with no author, version or confirmation context (ТЗ §3). The
        # button now only points at the /day screen, which never mutates by
        # itself; the change itself needs a confirm ticket.
        rows.append([InlineKeyboardButton(text="🎛 Управление дня", callback_data="pipeline_day_control")])
    if show_review:
        rows.append([
            InlineKeyboardButton(text="🧠 Review", callback_data="pipeline_review"),
            InlineKeyboardButton(text="📊 Прогнозы", callback_data="prediction_menu"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _no_session_kb() -> InlineKeyboardMarkup:
    """ТЗ §11.2 — кнопки когда сессии нет."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Старт сессии", callback_data="pipeline_start")],
        [InlineKeyboardButton(text="📊 Рынок", callback_data="back_to_market")],
    ])


# ── Normalized error messages (ТЗ §11) ──

ERR_NO_SESSION = (
    "ℹ️ <b>Активной сессии нет.</b>\n"
    "Запусти новую дневную сессию для BTCUSDT."
)

ERR_SESSION_UNAVAILABLE = "⚠️ Сессия временно недоступна. Попробуй через несколько секунд."

ERR_MARKET_STALE = "⚠️ Данные рынка временно недоступны. Попробуй обновить через несколько секунд."

ERR_BACKEND = "⚠️ Временная ошибка сервиса. Попробуй позже."

ERR_DOMAIN_UNAVAILABLE = (
    "🔴 <b>BACKEND_UNAVAILABLE</b>\n"
    "Сервис paper-сессий (paper_trading) не ответил — сессия НЕ запущена.\n"
    "Старая дневная сессия (trading_sessions) больше не используется как запасной "
    "вариант: один запуск = одна доменная команда, иначе у одного владельца "
    "появятся два разных баланса.\n"
    "Повтори попытку через минуту; если повторяется — проверь webui/postgres и "
    "PAPER_ENGINE_ENABLED."
)


# ── Router handlers (ТЗ §10.2 — regex-first) ──

def _day_control_redirect(action: Optional[str] = None) -> str:
    """Q-24 — действие дня выполняет команда ``/day``, а не legacy-сессия.

    Живой промер 2026-10-14: активных записей в ``trading_sessions`` — 0, а
    бегущих доменных дней — 2, поэтому прежние pause/tighten/reduce/close_all
    отвечали «сессии нет» ровно там, где день есть. Мутирующий код отсюда удалён
    целиком, а не переведён в чтение: у доменного действия есть предпросмотр,
    подтверждение и ключ идемпотентности карточки, и исполняет его runner.
    Формы команды отдаёт владелец экрана
    (:func:`services.day_control.usage_preview`) — второй список здесь запрещён
    (ТЗ TB-03).
    """
    from application import presenters as ui
    from services import day_control as control

    if action:
        subject = f"🎛 <b>Действие «{ui.esc(action)}» выполняет команда /day</b>"
    else:
        subject = "🎛 <b>Управление дня — команда /day</b>"
    return (
        f"{subject}\n"
        "<i>Старые кнопки писали в trading_sessions, а торговый день живёт в "
        "домене paper_v2. Бот не меняет состояние дня по фразе и не закрывает "
        "позиции без подтверждения.</i>\n"
        f"<code>{ui.esc(control.usage_preview())}</code>"
    )

def _session_control_kb() -> InlineKeyboardMarkup:
    """Inline-меню сессии — чтение плюс один вход в управление дня."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Mini App", url=url)],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="pipeline_status"),
            InlineKeyboardButton(text="📦 Позиции", callback_data="pipeline_positions"),
        ],
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data="pipeline_balance"),
            InlineKeyboardButton(text="📋 План", callback_data="pipeline_plan"),
        ],
        [
            # Q-24 — четыре мутирующие кнопки (Pause / Tighten / Reduce /
            # Close all) отсюда удалены: они писали legacy trading_sessions и
            # на живом дне отвечали «сессии нет». Управление дня — одна команда
            # /day с предпросмотром и подтверждением.
            InlineKeyboardButton(text="🎛 Управление дня", callback_data="pipeline_day_control"),
        ],
        [InlineKeyboardButton(text="🏁 Результат", callback_data="pipeline_result")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_back")],
    ])


def _risk_mode_kb() -> InlineKeyboardMarkup:
    """Выбор risk_mode при старте сессии."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛡 Defensive", callback_data="session_start_defensive"),
            InlineKeyboardButton(text="⚖️ Balanced", callback_data="session_start_balanced"),
            InlineKeyboardButton(text="🔥 Aggressive", callback_data="session_start_aggressive"),
        ],
    ])


# /session and /plan are no longer bound here (ТЗ Telegram Bot V1 §2, P2):
# the console router (``handlers/now.py``) and the redirect router own those
# command names, and one command name must not have two implementations. The
# same screens remain reachable through the text phrases below and the
# ``pipeline_status`` / ``pipeline_plan`` callbacks, which call
# ``_build_status_response`` and ``_handle_plan`` directly.


@router.message(F.text.regexp(r"(?i)(старт сессии|начать сессию|daily session|запусти сессию|старт торговли|запусти дневную)"))
async def msg_start_session(message: Message):
    """ТЗ §8.5 — старт сессии через текстовое сообщение."""
    intent = classify_pipeline_intent(message.text or "")
    if intent:
        text, kb = await _handle_start_safe(message.from_user.id, intent)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(остановить сессию|стоп сессия|stop session|закрыть сессию)"))
async def msg_stop_session(message: Message):
    """Q-24 — остановка дня не пишется в legacy-сессию, а указывает на /day."""
    await message.answer(_day_control_redirect("finish"), parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(статус сессии|сессия статус|session status|состояние сессии)"))
async def msg_session_status(message: Message):
    """ТЗ §8.1 — компактный статус."""
    text, kb = await _build_status_response(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(результат сессии|итог сессии|session result)"))
async def msg_session_result(message: Message):
    """ТЗ §8.4 — результат сессии."""
    text, kb = await _build_result_response(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(пауза сессии|pause session|поставь паузу)"))
async def msg_pause_session(message: Message):
    """ТЗ §6.1 — пауза через текст; Q-24 — только как переход к /day."""
    await message.answer(_day_control_redirect("pause"), parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(продолжить сессию|resume session|continue session)"))
async def msg_continue_session(message: Message):
    """ТЗ §6.1 — продолжить через текст; Q-24 — только как переход к /day."""
    await message.answer(_day_control_redirect("resume"), parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(сколько позиций|мои позиции|портфель|баланс|pnl|покажи pnl)"))
async def msg_portfolio_commands(message: Message):
    """ТЗ §6.5/§8.2/§8.3 — позиции и баланс через regex-first."""
    msg = (message.text or "").lower().strip()
    if any(p in msg for p in ["баланс", "pnl", "покажи pnl"]):
        text, kb = await _build_balance_response(message.from_user.id)
    else:
        text, kb = await _build_positions_response(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Callback handlers for inline buttons ──

@router.callback_query(F.data == "pipeline_start")
async def cb_start(callback):
    await callback.answer()
    # Показываем выбор risk_mode
    await callback.message.edit_text(
        "🚀 <b>Запуск сессии</b>\n\nВыбери режим риска:\n"
        "🛡 Defensive — низкий риск, 3% DD лимит\n"
        "⚖️ Balanced — средний риск, 5% DD лимит\n"
        "🔥 Aggressive — высокий риск, 8% DD лимит",
        reply_markup=_risk_mode_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("session_start_"))
async def cb_start_with_risk(callback):
    """Старт сессии с выбранным risk_mode."""
    await callback.answer()
    risk_mode = callback.data.replace("session_start_", "")
    intent = {"intent": "pipeline_start", "budget": 100.0, "risk_mode": risk_mode,
              "trade_profile": {"trade_direction": "auto", "trade_horizon": "fast"}}
    text, kb = await _handle_start_safe(callback.from_user.id, intent)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_status")
async def cb_status(callback):
    await callback.answer()
    text, _ = await _build_status_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=_session_control_kb(), parse_mode="HTML")


@router.callback_query(F.data == "pipeline_positions")
async def cb_positions(callback):
    await callback.answer()
    text, kb = await _build_positions_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_balance")
async def cb_balance(callback):
    await callback.answer()
    text, kb = await _build_balance_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_plan")
async def cb_plan(callback):
    await callback.answer()
    text = await _handle_plan(callback.from_user.id)
    kb = _no_session_kb() if text == ERR_NO_SESSION else None
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_result")
async def cb_result(callback):
    await callback.answer()
    text, kb = await _build_result_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_day_control")
async def cb_day_control(callback):
    """Q-24 — единственный оставшийся control-callback: он ничего не меняет.

    Прежние ``pipeline_pause`` / ``pipeline_tighten`` / ``pipeline_reduce`` /
    ``pipeline_close_all`` вызывали ``apply_revision_command`` по строке
    callback_data — без автора, версии ресурса и подтверждённого контекста
    (ТЗ §3). Кнопка остаётся только указанием на экран ``/day``.
    """
    await callback.answer()
    await callback.message.answer(_day_control_redirect(), parse_mode="HTML")


@router.callback_query(F.data == "pipeline_review")
async def cb_review(callback):
    await callback.answer()
    text = await _build_review_response(callback.from_user.id)
    await callback.message.answer(text, parse_mode="HTML")


def parse_session_start_text(text: str) -> dict:
    """§5 — Parse natural language session start for trade profile.
    
    Examples:
        "старт сессии 100 usdt агрессивно fast long"
        "старт сессии 50$ defensive medium target 3"
        "запусти сессию fast short"
    """
    msg = (text or "").lower().strip()
    
    # Budget
    budget_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:usdt|\$|дол|бакс)", msg)
    budget = float(budget_match.group(1)) if budget_match else 100.0

    # Risk mode
    risk = "balanced"
    if "агрессив" in msg or "aggressive" in msg:
        risk = "aggressive"
    elif "защит" in msg or "defensive" in msg or "консерват" in msg:
        risk = "defensive"

    # §5 — Trade direction
    direction = "auto"
    if re.search(r"\b(long|лонг|вверх|быч)\b", msg):
        direction = "long"
    elif re.search(r"\b(short|шорт|вниз|медв)\b", msg):
        direction = "short"
    elif re.search(r"\b(both|оба|двусторон)\b", msg):
        direction = "both"

    # §5 — Trade horizon
    horizon = "fast"
    if re.search(r"\b(medium|средн|внутридневн)\b", msg):
        horizon = "medium"
    elif re.search(r"\b(long|длинн|свинг|swing)\b", msg) and direction == "auto":
        # "long" could mean direction or horizon — check context
        if "свинг" in msg or "swing" in msg or "длинн" in msg:
            horizon = "long"
    elif re.search(r"\b(swing|свинг|длинный горизонт)\b", msg):
        horizon = "long"

    # §5 — Target net profit
    target_match = re.search(r"(?:target|цель|таргет)\s*(\d+(?:\.\d+)?)", msg)
    target_net_profit = float(target_match.group(1)) if target_match else None

    # §5 — Max duration
    dur_match = re.search(r"(?:dur|длительность|duration)\s*(\d+)", msg)
    max_duration = int(dur_match.group(1)) if dur_match else None

    # §5 — Cost filter
    cost_filter = True
    if "no cost filter" in msg or "без cost filter" in msg or "disable cost" in msg:
        cost_filter = False

    profile = {
        "trade_direction": direction,
        "trade_horizon": horizon,
        "cost_filter_enabled": cost_filter,
    }
    if target_net_profit is not None:
        profile["target_net_profit_usdt"] = target_net_profit
    if max_duration is not None:
        profile["max_trade_duration_minutes"] = max_duration

    return {
        "intent": "pipeline_start",
        "budget": budget,
        "risk_mode": risk,
        "trade_profile": profile,
    }


# ── Intent classifier (ТЗ §10.1 — intent-first, regex-first) ──

def classify_pipeline_intent(message: str) -> dict | None:
    msg = message.lower().strip()

    # Start session (ТЗ §10.2 — regex-first, §5 — trade profile parsing)
    if any(p in msg for p in ["старт сессии", "начать сессию", "daily session", "старт торговли", "запусти сессию", "запусти дневную"]):
        return parse_session_start_text(message)

    # Остановить/пауза/продолжить остаются распознанными intents и после Q-24:
    # снимается не распознавание, а мутирующий исполнитель. Если убрать ветки
    # целиком, «пауза сессии» уйдёт в AI-фолбэк, а модель отвечает на такие
    # фразы торговыми числами без контракта (ТЗ §7.5). Диспетчер отвечает
    # переходом на /day — см. handle_pipeline_intent.

    # Stop session
    if any(p in msg for p in ["остановить сессию", "стоп сессия", "stop session", "закрыть сессию"]):
        return {"intent": "pipeline_stop"}

    # Revision commands (ТЗ §10.2 — regex-first)
    if any(p in msg for p in ["пауза сессии", "pause session", "поставь паузу"]):
        return {"intent": "pipeline_revision_cmd", "command": "pause"}
    if any(p in msg for p in ["продолжить сессию", "resume session", "continue session"]):
        return {"intent": "pipeline_revision_cmd", "command": "continue"}
    if msg.strip() in ("tighten", "reduce", "close all", "close_all", "continue", "pause"):
        cmd_map = {"close all": "close_all", "close_all": "close_all"}
        return {"intent": "pipeline_revision_cmd", "command": cmd_map.get(msg.strip(), msg.strip())}

    # Status
    if any(p in msg for p in ["статус сессии", "сессия статус", "session status", "состояние сессии"]):
        return {"intent": "pipeline_status"}

    # Result
    if any(p in msg for p in ["результат сессии", "итог сессии", "session result"]):
        return {"intent": "pipeline_result"}

    # Portfolio (ТЗ §10.2 — regex-first)
    if any(p in msg for p in ["сколько позиций", "мои позиции", "позиции сессии", "session positions", "открытые сделки сессии"]):
        return {"intent": "pipeline_positions"}
    if any(p in msg for p in ["баланс", "pnl", "покажи pnl", "прибыль сессии", "результат сессии"]) and "сесс" in msg:
        return {"intent": "pipeline_pnl"}

    # Plan
    if any(p in msg for p in ["активный план", "текущий план", "active plan", "план сессии"]):
        return {"intent": "pipeline_plan"}

    # Revision history
    if any(p in msg for p in ["последняя ревизия", "latest revision", "ревизия плана"]):
        return {"intent": "pipeline_revision"}

    # Detail queries
    why_entry = re.search(r"почему вход\s+(\S+)", msg)
    if why_entry:
        return {"intent": "pipeline_why_entry", "entry_id": why_entry.group(1)}
    why_exit = re.search(r"почему выход\s+(\S+)", msg)
    if why_exit:
        return {"intent": "pipeline_why_exit", "trade_id": why_exit.group(1)}
    pos_detail = re.search(r"детали позиции\s+(\S+)", msg)
    if pos_detail:
        return {"intent": "pipeline_position_detail", "trade_id": pos_detail.group(1)}

    return None


# ── Safe handlers (ТЗ §7.4 — normalized errors, §8 — templates) ──

async def _handle_start_safe(user_id: int, intent: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """ТЗ §8.5 — старт сессии, безопасный ответ. §5 — trade profile support.

    Q-01 (curator decision, 2026-10-08) + ТЗ Telegram Bot V1 §0: the paper_trading
    domain is the only start path.  The former silent fallthrough to
    ``services.session_manager`` — which wrote ``trading_sessions``/``session_plans``
    whenever the adapter import failed — is removed: a failed start now returns an
    explicit ``BACKEND_UNAVAILABLE`` and writes nothing anywhere.
    """
    budget = intent.get("budget", 100.0)
    risk = intent.get("risk_mode", "balanced")
    trade_profile = intent.get("trade_profile")

    # ── paper_trading domain service (the only start path) ──
    try:
        from paper_trading.adapter import start_day as pt_start_day, owner_id_from_telegram, get_current_state

        owner_id = owner_id_from_telegram(user_id)
        existing = await get_current_state(owner_id)
        if existing.get("day"):
            # Idempotent — the running day is reported from the domain state.
            # The legacy trading_sessions renderer is deliberately not used here:
            # it answers "нет сессии" when there is no legacy row, i.e. it would
            # contradict a paper_v2 day that is actually running (Q-01).
            day = existing.get("day") or {}
            accounts = existing.get("accounts") or []
            from application import presenters as _ui
            from integrations.wored_api import enum_value

            # the domain hands back a raw DayState member; str() would print
            # "DayState.running" where the dashboard says "running"
            day_state = str(enum_value(day.get("state")) or "unknown").upper()

            account_lines = "\n".join(
                f"Счёт {a.get('kind')}: {_ui.money(a.get('cash'), a.get('currency') or 'USDT')} · "
                f"открытых позиций {int(a.get('open_positions') or 0)}"
                for a in accounts
            )
            text = (
                f"🎯 <b>Торговый день уже идёт</b>\n"
                f"ID: <code>{str(day.get('id') or '')[:8]}</code>\n"
                f"Состояние: {_ui.esc(day_state)}\n"
                f"{account_lines or 'Счетов пока нет'}\n"
                f"<i>Новая попытка старта не создаёт второй день.</i>"
            )
            return text, _session_nav_kb()

        result = await pt_start_day(
            owner_id=owner_id,
            mode="baseline_auto",
        )

        if result.get("ok"):
            day_id = result.get("day_id", "")[:8]
            profile_str = (
                f"Направление: {trade_profile.get('trade_direction', 'auto') if trade_profile else 'auto'}\n"
                f"Горизонт: {trade_profile.get('trade_horizon', 'fast') if trade_profile else 'fast'}\n"
            ) if trade_profile else ""
            return (
                f"🚀 <b>Торговый день запущен</b>\n"
                f"ID: <code>{day_id}</code>\n"
                f"Режим: baseline_auto\n"
                f"Риск: {risk}\n"
                f"Бюджет: {budget:.0f} USDT\n"
                f"{profile_str}"
                f"Инструмент: BTCUSDT\n"
                f"Стратегия: baseline v1 (EMA/ATR)\n"
                f"Авто: наблюдает рынок, ждёт сигнал",
                _session_nav_kb()
            )
        log.warning(
            "paper_trading start_day refused: %s — no legacy fallback will run",
            result.get("error"),
        )
        return ERR_DOMAIN_UNAVAILABLE, None
    except Exception as exc:
        log.error("paper_trading adapter unavailable: %s", exc)
        return ERR_DOMAIN_UNAVAILABLE, None


async def _build_status_response(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """ТЗ §8.1 — компактный статус сессии."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION, _no_session_kb()

        from storage.postgres_client import get_pool
        pool = await get_pool()
        if not pool:
            return ERR_SESSION_UNAVAILABLE, None

        async with pool.acquire() as conn:
            metrics = await conn.fetchrow("SELECT * FROM session_metrics WHERE session_id = $1", str(session["id"]))
            trades = await conn.fetch(
                "SELECT * FROM executed_trades WHERE session_id = $1 ORDER BY opened_at DESC",
                str(session["id"]),
            )
            last_rev = await conn.fetchrow(
                "SELECT execution_command FROM session_revisions WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
                str(session["id"]),
            )

        open_count = sum(1 for t in trades if t["status"] == "open")
        # ТЗ §8.1 — Realized PnL = sum of closed trades' realised_pnl_usdt
        realized_pnl = sum(
            float(t.get("realised_pnl_usdt") or 0)
            for t in trades
            if t["status"] == "closed"
        )

        # ТЗ §8.1 — Unrealized PnL = total_pnl - realized
        total_pnl = float(metrics.get("total_pnl_usdt") or 0) if metrics else realized_pnl
        unrealized_pnl = total_pnl - realized_pnl

        # ТЗ §7.2 — приоритет аварийности: авария → статус → PnL → позиции → детали
        # ТЗ §8.1 — шаблон
        risk_limit_pct = 6.0  # default risk limit
        risk_status = "normal"
        if metrics:
            max_dd = float(metrics.get("max_drawdown_pct", 0))
            if max_dd >= risk_limit_pct * 0.8:
                risk_status = "warning"
            if max_dd >= risk_limit_pct:
                risk_status = "critical"

        lines = []
        # 1. Авария / критический риск (если есть)
        if risk_status == "critical":
            lines.append("🚨 <b>КРИТИЧЕСКИЙ РИСК: Max DD превышен</b>")
        elif risk_status == "warning":
            lines.append("⚠️ <b>Риск: warning</b>")
        # 2. Статус сессии
        lines.append("🎯 <b>Сессия симуляции</b>")
        lines.append(f"ID: <code>{str(session['id'])[:8]}</code>")
        lines.append(f"Статус: {session['status'].upper()}")
        # Countdown до окончания
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        session_end = session.get("session_end")
        if session_end:
            if isinstance(session_end, str):
                session_end = datetime.fromisoformat(session_end.replace("Z", "+00:00"))
            remaining = session_end - now
            if remaining.total_seconds() > 0:
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                lines.append(f"Осталось: {hours}ч {minutes}мин")
            else:
                lines.append("Осталось: завершается")
        lines.append(f"План: v{session['active_plan_version']}")
        lines.append(f"Режим риска: {session['risk_mode']}")
        lines.append(f"Бюджет: {float(session['initial_budget_usdt']):.0f} USDT")
        lines.append(f"Открыто позиций: {open_count}")
        from services.execution_status import read, render
        from storage.redis_client import get_redis
        try:
            execution = await read(get_redis(), str(session["id"]))
        except Exception:
            execution = None
        lines.append(render(execution))
        # 3. Агрегированный PnL и риск (ТЗ §8.1 — separate Unrealized/Realized)
        if metrics:
            budget = float(session['initial_budget_usdt'])
            pnl_pct = (unrealized_pnl / budget * 100) if budget > 0 else 0.0
            lines.append(f"Unrealized PnL: {unrealized_pnl:+.2f} USDT ({pnl_pct:+.1f}%)")
            lines.append(f"Realized PnL: {realized_pnl:+.2f} USDT")
            lines.append(f"Max DD: {float(metrics['max_drawdown_pct']):.1f}% / лимит {risk_limit_pct:.1f}%")
        # 4. Последняя команда
        if last_rev:
            lines.append(f"Последняя команда: {last_rev['execution_command']}")

        # Keep execution evidence and P&L visible; do not replace them with a six-line digest.
        text = "\n".join(lines)
        return text, _session_nav_kb()
    except Exception as exc:
        log.error("Pipeline status error: %s", exc)
        return ERR_SESSION_UNAVAILABLE, None


async def _build_positions_response(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """ТЗ §8.2 — digest позиций, максимум 3 по приоритету."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION, _no_session_kb()

        from storage.postgres_client import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            trades = await conn.fetch(
                "SELECT * FROM executed_trades WHERE session_id = $1 ORDER BY opened_at DESC",
                str(session["id"]),
            )

        if not trades:
            return "📦 <b>Открытых позиций нет.</b>\nСессия активна, входы ожидаются.", _session_nav_kb(show_day_control=True)

        open_trades = [t for t in trades if t["status"] == "open"]
        total_margin = sum(float(t["margin_used_usdt"] or 0) for t in open_trades)

        # ТЗ §8.2 — digest
        longs = sum(1 for t in open_trades if t["side"] == "long")
        shorts = sum(1 for t in open_trades if t["side"] == "short")

        # ТЗ §8.2 — приоритет показа: 1) ближайшая к ликвидации 2) самая убыточная 3) самая большая по марже
        def _position_priority(t):
            lev = float(t.get("leverage") or 1)
            margin = float(t.get("margin_used_usdt") or 0)
            pnl = float(t.get("realised_pnl_usdt") or 0)
            return (-lev, pnl, -margin)  # higher lev = closer to liq, lower pnl = more losing, higher margin

        open_trades_sorted = sorted(open_trades, key=_position_priority)

        # Get unrealized PnL from metrics if available
        from storage.postgres_client import get_pool as _get_pool
        unrealized_pnl = 0.0
        risk_status = "normal"
        try:
            pool2 = await _get_pool()
            if pool2:
                async with pool2.acquire() as conn2:
                    metrics = await conn2.fetchrow(
                        "SELECT * FROM session_metrics WHERE session_id = $1", str(session["id"])
                    )
                    if metrics:
                        realized_pnl = sum(
                            float(t.get("realised_pnl_usdt") or 0)
                            for t in trades if t["status"] == "closed"
                        )
                        unrealized_pnl = float(metrics.get("total_pnl_usdt") or 0) - realized_pnl
                        max_dd = float(metrics.get("max_drawdown_pct") or 0)
                        if max_dd >= 4.8:
                            risk_status = "warning"
                        if max_dd >= 6.0:
                            risk_status = "critical"
        except Exception:
            pass

        # ТЗ §8.2 — ближайшая ликвидация (позиция с макс leverage)
        nearest_liq = "—"
        if open_trades_sorted:
            liq_trade = open_trades_sorted[0]  # already sorted by highest leverage first
            liq_side = "long" if liq_trade["side"] == "long" else "short"
            nearest_liq = f"{liq_trade['side'].upper()} #{str(liq_trade['id'])[:8]}"

        # ТЗ §8.2 — определяем символы для digest (используем символы из trades, не hardcoded)
        symbols_in_positions = set()
        for t in open_trades:
            sym = t.get("symbol") or "BTCUSDT"
            symbols_in_positions.add(sym)

        lines = [
            f"📦 <b>Открытые позиции</b>",
            f"Всего: {len(open_trades)}",
        ]
        if longs:
            long_syms = [t.get("symbol", "BTCUSDT").upper() for t in open_trades if t["side"] == "long"]
            long_sym_str = "/".join(sorted(set(long_syms)))
            lines.append(f"LONG {long_sym_str}: {longs}")
        if shorts:
            short_syms = [t.get("symbol", "BTCUSDT").upper() for t in open_trades if t["side"] == "short"]
            short_sym_str = "/".join(sorted(set(short_syms)))
            lines.append(f"SHORT {short_sym_str}: {shorts}")
        lines.append(f"Общий unrealized PnL: {unrealized_pnl:+.2f} USDT")
        lines.append(f"Общая маржа: {total_margin:.2f} USDT")
        lines.append(f"Ближайшая ликвидация: {nearest_liq}")
        lines.append(f"Риск: {risk_status}")

        # ТЗ §8.2 — максимум 3 позиции по приоритету
        if len(open_trades_sorted) > 3:
            lines.append(f"\n<i>Показаны 3 позиции из {len(open_trades_sorted)} по приоритету.</i>")
            shown = open_trades_sorted[:3]
        else:
            shown = open_trades_sorted

        for t in shown:
            side_emoji = "🟢" if t["side"] == "long" else "🔴"
            lines.append(
                f"{side_emoji} #{str(t['id'])[:8]} {t['side'].upper()} {t['leverage']}x "
                f"entry={float(t['entry_price']):.1f} [{t['status']}]"
            )

        return "\n".join(lines), _session_nav_kb(show_day_control=True)
    except Exception as exc:
        log.error("Pipeline positions error: %s", exc)
        return ERR_BACKEND, None


async def _build_balance_response(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """ТЗ §8.3 — агрегированный финансовый summary."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION, _no_session_kb()

        from storage.postgres_client import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            metrics = await conn.fetchrow("SELECT * FROM session_metrics WHERE session_id = $1", str(session["id"]))
            trades = await conn.fetch("SELECT * FROM executed_trades WHERE session_id = $1", str(session["id"]))

        open_count = sum(1 for t in trades if t["status"] == "open")
        budget = float(session["initial_budget_usdt"])

        # Calculate realized and unrealized PnL separately (ТЗ §8.3)
        realized_pnl = sum(float(t.get("realised_pnl_usdt") or 0) for t in trades if t["status"] == "closed")

        if metrics:
            equity = float(metrics.get("current_equity") or budget)
            total_pnl = float(metrics.get("total_pnl_usdt") or 0)
            unrealized_pnl = total_pnl - realized_pnl
            total_pct = float(metrics.get("total_pnl_pct") or 0)
            max_dd = float(metrics.get("max_drawdown_pct") or 0)
        else:
            equity = budget
            total_pnl = realized_pnl
            unrealized_pnl = 0.0
            total_pct = 0.0
            max_dd = 0.0

        # ТЗ §8.3 — шаблон с separate Realized/Unrealized PnL
        text = (
            f"💰 <b>Баланс и PnL</b>\n"
            f"Стартовый бюджет: {budget:.2f} USDT\n"
            f"Текущий equity: {equity:.2f} USDT\n"
            f"Realized PnL: {realized_pnl:+.2f} USDT\n"
            f"Unrealized PnL: {unrealized_pnl:+.2f} USDT\n"
            f"Total PnL: {total_pnl:+.2f} USDT ({total_pct:+.2f}%)\n"
            f"Max drawdown: {max_dd:.1f}%\n"
            f"Открыто позиций: {open_count}"
        )
        return text, _session_nav_kb()
    except Exception as exc:
        log.error("Pipeline balance error: %s", exc)
        return ERR_BACKEND, None


async def _build_result_response(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """ТЗ §8.4 — финальный summary digest."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION, _no_session_kb()

        from storage.postgres_client import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            metrics = await conn.fetchrow("SELECT * FROM session_metrics WHERE session_id = $1", str(session["id"]))

        budget = float(session["initial_budget_usdt"])

        if metrics:
            # ТЗ §8.4 — шаблон
            text = (
                f"🏁 <b>Результат сессии</b>\n"
                f"Статус: {session['status'].upper()}\n"
                f"Период: 8h\n"
                f"Бюджет: {budget:.2f} USDT\n"
                f"Итог equity: {float(metrics.get('current_equity') or budget):.2f} USDT\n"
                f"Total PnL: {float(metrics['total_pnl_usdt']):+.2f} USDT ({float(metrics['total_pnl_pct']):+.1f}%)\n"
                f"Сделок: {metrics['trade_count']}\n"
                f"Win/Loss: {metrics['win_count']}/{metrics['loss_count']}\n"
                f"Ликвидаций: {metrics['liquidation_count']}\n"
                f"Max DD: {float(metrics['max_drawdown_pct']):.1f}%\n"
                f"Profit factor: {float(metrics['profit_factor']) if metrics['profit_factor'] else 'N/A'}"
            )
        else:
            text = (
                f"🏁 <b>Результат сессии</b>\n"
                f"Статус: {session['status'].upper()}\n"
                f"Бюджет: {budget:.2f} USDT\n"
                f"Метрики ещё не рассчитаны."
            )
        return text, _session_nav_kb(show_review=True)
    except Exception as exc:
        log.error("Pipeline result error: %s", exc)
        return ERR_BACKEND, None


async def _build_review_response(user_id: int) -> str:
    """ТЗ §8.4 — review (Premium model анализ сессии)."""
    try:
        from services.session_manager import get_active_session
        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION

        from storage.postgres_client import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            review = await conn.fetchrow(
                "SELECT * FROM daily_reviews WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
                str(session["id"]),
            )

        if not review:
            return "🧠 <b>Review</b>\nReview ещё не сформирован. Сессия должна быть завершена."

        return (
            f"🧠 <b>Review сессии</b>\n"
            f"Модель: {review['review_model']}\n"
            f"Статус: {review['status']}\n\n"
            f"{review.get('review_text', '') or ''}"
        )
    except Exception as exc:
        log.error("Pipeline review error: %s", exc)
        return ERR_BACKEND


async def handle_pipeline_intent(intent: dict, user_id: int = DEFAULT_USER_ID) -> str:
    """Обработать pipeline intent и вернуть текстовый ответ для Telegram."""
    intent_type = intent.get("intent", "")
    try:
        if intent_type == "pipeline_start":
            text, _ = await _handle_start_safe(user_id, intent)
            return text
        elif intent_type == "pipeline_stop":
            # Q-24: legacy-закрытие сессии отсюда удалено; завершение дня —
            # доменная команда /day finish с подтверждением карточки.
            return _day_control_redirect("finish")
        elif intent_type == "pipeline_status":
            text, _ = await _build_status_response(user_id)
            return text
        elif intent_type == "pipeline_result":
            text, _ = await _build_result_response(user_id)
            return text
        elif intent_type == "pipeline_revision_cmd":
            # Q-24: pause/continue/tighten/reduce/close_all больше не пишут в
            # trading_sessions; доменная форма команды — /day <действие>.
            return _day_control_redirect(intent.get("command", ""))
        elif intent_type == "pipeline_positions":
            text, _ = await _build_positions_response(user_id)
            return text
        elif intent_type == "pipeline_pnl":
            text, _ = await _build_balance_response(user_id)
            return text
        elif intent_type == "pipeline_plan":
            return await _handle_plan(user_id)
        elif intent_type == "pipeline_revision":
            return await _handle_revision(user_id)
        elif intent_type == "pipeline_why_entry":
            return await _handle_why_entry(intent.get("entry_id", ""))
        elif intent_type == "pipeline_why_exit":
            return await _handle_why_exit(intent.get("trade_id", ""))
        elif intent_type == "pipeline_position_detail":
            return await _handle_position_detail(intent.get("trade_id", ""))
        else:
            return "❌ Неизвестная команда pipeline"
    except Exception as exc:
        log.error("Pipeline handler error: %s", exc)
        return ERR_BACKEND


# ── Legacy detail handlers (unchanged) ──

async def _handle_plan(user_id: int) -> str:
    from services.session_manager import get_active_session
    session = await get_active_session(user_id)
    if not session:
        return ERR_NO_SESSION

    from storage.postgres_client import get_pool
    from services.plan_store import decode
    from services.plan_presenter import render_plan
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        current = await conn.fetchrow("SELECT * FROM trading_sessions WHERE id=$1", str(session["id"]))
        session = dict(current)
        plan = await conn.fetchrow(
            "SELECT * FROM session_plans WHERE session_id=$1 AND version=$2",
            str(session["id"]), session["active_plan_version"])
        if not plan:
            return ("⛔ Активная версия плана отсутствует. Новые входы заблокированы. "
                    "История сохранена; начните новую сессию после завершения текущей.")
        entries = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id=$1 AND plan_version=$2 ORDER BY created_at",
            str(session["id"]), plan["version"])
    return render_plan(session, decode(plan["plan_json"]), [dict(e) for e in entries])


async def _handle_revision(user_id: int) -> str:
    from services.session_manager import get_active_session
    session = await get_active_session(user_id)
    if not session:
        return ERR_NO_SESSION

    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rev = await conn.fetchrow(
            "SELECT * FROM session_revisions WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
            str(session["id"]),
        )

    if not rev:
        return "📭 Ревизий нет"

    rev_data = rev["revision_json"]
    if isinstance(rev_data, str):
        rev_data = json.loads(rev_data)

    cmd_emoji = {"continue": "✅", "tighten": "🛡", "reduce": "📉", "pause": "⏸", "close_all": "🛑"}.get(
        rev["execution_command"], "❓"
    )

    return (
        f"🔄 <b>Последняя ревизия</b>\n"
        f"v{rev['base_version']} → v{rev['new_version']}\n"
        f"{cmd_emoji} Command: {rev['execution_command']}\n"
        f"Summary: {rev_data.get('summary', '?')}\n"
        f"Regime: {rev_data.get('market_regime_status', '?')}"
    )


async def _handle_why_entry(entry_id: str) -> str:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        entry = await conn.fetchrow("SELECT * FROM planned_entries WHERE id = $1", entry_id)

    if not entry:
        return f"❌ Entry {entry_id} не найден"

    return (
        f"🔍 <b>Почему вход {entry_id[:8]}</b>\n"
        f"Side: {entry['side'].upper()}\n"
        f"Reason: {entry['reason_code']}\n"
        f"Confirmation: {entry['confirmation_rule']}\n"
        f"Zone: {float(entry['entry_zone_from']):.2f} - {float(entry['entry_zone_to']):.2f}\n"
        f"SL: {float(entry['stop_loss']):.2f}\n"
        f"Leverage: {entry['recommended_leverage']}x"
    )


async def _handle_why_exit(trade_id: str) -> str:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        trade = await conn.fetchrow("SELECT * FROM executed_trades WHERE id = $1", trade_id)

    if not trade:
        return f"❌ Trade {trade_id} не найден"

    return (
        f"🔍 <b>Почему выход {trade_id[:8]}</b>\n"
        f"Reason: {trade['close_reason'] or 'N/A'}\n"
        f"Entry: {float(trade['entry_price']):.2f}\n"
        f"Exit: {float(trade['exit_price']):.2f}\n"
        f"PnL: {float(trade['realised_pnl_usdt'] or 0):+.4f} USDT"
    )


async def _handle_position_detail(trade_id: str) -> str:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        trade = await conn.fetchrow("SELECT * FROM executed_trades WHERE id = $1", trade_id)

    if not trade:
        return f"❌ Trade {trade_id} не найден"

    lines = [
        f"📊 <b>Детали позиции {trade_id[:8]}</b>",
        f"Side: {trade['side'].upper()} | Lev: {trade['leverage']}x",
        f"Margin: {trade['margin_mode']}",
        f"Entry: {float(trade['entry_price']):.2f}",
        f"Qty: {float(trade['position_qty']):.8f}",
        f"Notional: {float(trade['position_notional_usdt']):.2f} USDT",
        f"Margin used: {float(trade['margin_used_usdt']):.2f} USDT",
        f"Open fee: {float(trade['open_fee_usdt']):.4f} USDT",
        f"Status: {trade['status']}",
    ]

    if trade["status"] == "closed":
        lines.append(f"Exit: {float(trade['exit_price']):.2f}")
        lines.append(f"Close fee: {float(trade['close_fee_usdt']):.4f} USDT")
        lines.append(f"PnL: {float(trade['realised_pnl_usdt'] or 0):+.4f} USDT")
        lines.append(f"Reason: {trade['close_reason']}")

    return "\n".join(lines)