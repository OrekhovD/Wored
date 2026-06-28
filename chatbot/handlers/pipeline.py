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
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="pipeline")

DEFAULT_USER_ID = 5249526259

# ── Inline keyboards (ТЗ §9: max 5 buttons) ──

def _miniapp_url() -> str:
    return os.getenv("TG_MINIAPP_URL") or os.getenv("WEBUI_URL") or "http://localhost:8080/daily-session"

def _session_kb() -> InlineKeyboardMarkup:
    """ТЗ §8.1 — кнопки для статус сессии."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Mini App", web_app={"url": url} if False else None, url=url)],
        [
            InlineKeyboardButton(text="📦 Позиции", callback_data="pipeline_positions"),
            InlineKeyboardButton(text="⏸ Pause", callback_data="pipeline_pause"),
        ],
        [
            InlineKeyboardButton(text="🛡 Tighten", callback_data="pipeline_tighten"),
            InlineKeyboardButton(text="🛑 Close all", callback_data="pipeline_close_all"),
        ],
    ])

def _start_kb() -> InlineKeyboardMarkup:
    """ТЗ §8.5 — кнопки после старта сессии."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Mini App", url=url)],
        [
            InlineKeyboardButton(text="📈 Статус сессии", callback_data="pipeline_status"),
            InlineKeyboardButton(text="⏸ Pause", callback_data="pipeline_pause"),
        ],
    ])

def _no_session_kb() -> InlineKeyboardMarkup:
    """ТЗ §11.2 — кнопки когда сессии нет."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Старт сессии", callback_data="pipeline_start")],
        [InlineKeyboardButton(text="📊 Рынок", callback_data="back_to_market")],
    ])

def _portfolio_kb() -> InlineKeyboardMarkup:
    """ТЗ §8.2 — кнопки для позиций."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Mini App", url=url)],
        [
            InlineKeyboardButton(text="📄 Все позиции", callback_data="pipeline_positions"),
            InlineKeyboardButton(text="🛑 Close all", callback_data="pipeline_close_all"),
        ],
    ])

def _balance_kb() -> InlineKeyboardMarkup:
    """ТЗ §8.3 — кнопки для баланса."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Mini App", url=url)],
        [
            InlineKeyboardButton(text="📈 Сессия", callback_data="pipeline_status"),
            InlineKeyboardButton(text="📦 Позиции", callback_data="pipeline_positions"),
        ],
    ])

def _result_kb() -> InlineKeyboardMarkup:
    """ТЗ §8.4 — кнопки для результата сессии."""
    url = _miniapp_url()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть обзор", url=url)],
        [
            InlineKeyboardButton(text="🧠 Review", callback_data="pipeline_review"),
            InlineKeyboardButton(text="📊 Прогнозы", callback_data="prediction_menu"),
        ],
    ])


# ── Normalized error messages (ТЗ §11) ──

ERR_NO_SESSION = (
    "ℹ️ <b>Активной сессии нет.</b>\n"
    "Запусти новую дневную сессию для BTCUSDT."
)

ERR_SESSION_UNAVAILABLE = "⚠️ Сессия временно недоступна. Попробуй через несколько секунд."

ERR_REVISION_NOT_ALLOWED = (
    "⚠️ Команда сейчас недоступна для текущего состояния сессии.\n"
    "Проверь статус сессии и повтори действие."
)

ERR_MARKET_STALE = "⚠️ Данные рынка временно недоступны. Попробуй обновить через несколько секунд."

ERR_BACKEND = "⚠️ Временная ошибка сервиса. Попробуй позже."


# ── Router handlers (ТЗ §10.2 — regex-first) ──

@router.message(Command("session"))
async def cmd_session(message: Message):
    """ТЗ §6.1 — /session = статус сессии."""
    text, kb = await _build_status_response(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(старт сессии|начать сессию|daily session|запусти сессию|старт торговли|запусти дневную)"))
async def msg_start_session(message: Message):
    """ТЗ §8.5 — старт сессии через текстовое сообщение."""
    intent = classify_pipeline_intent(message.text or "")
    if intent:
        text, kb = await _handle_start_safe(message.from_user.id, intent)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(остановить сессию|стоп сессия|stop session|закрыть сессию)"))
async def msg_stop_session(message: Message):
    text = await _handle_stop_safe(message.from_user.id)
    await message.answer(text, parse_mode="HTML")


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
    """ТЗ §6.1 — пауза через текст."""
    text = await _handle_revision_safe(message.from_user.id, "pause")
    await message.answer(text, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(продолжить сессию|resume session|continue session)"))
async def msg_continue_session(message: Message):
    """ТЗ §6.1 — продолжить через текст."""
    text = await _handle_revision_safe(message.from_user.id, "continue")
    await message.answer(text, parse_mode="HTML")


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
    text, kb = await _handle_start_safe(callback.from_user.id, {"intent": "pipeline_start", "budget": 100.0, "risk_mode": "balanced"})
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_status")
async def cb_status(callback):
    await callback.answer()
    text, kb = await _build_status_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_positions")
async def cb_positions(callback):
    await callback.answer()
    text, kb = await _build_positions_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_pause")
async def cb_pause(callback):
    await callback.answer()
    text = await _handle_revision_safe(callback.from_user.id, "pause")
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_tighten")
async def cb_tighten(callback):
    await callback.answer()
    text = await _handle_revision_safe(callback.from_user.id, "tighten")
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_close_all")
async def cb_close_all(callback):
    await callback.answer()
    text = await _handle_revision_safe(callback.from_user.id, "close_all")
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "pipeline_review")
async def cb_review(callback):
    await callback.answer()
    text = await _build_review_response(callback.from_user.id)
    await callback.message.answer(text, parse_mode="HTML")


# ── Intent classifier (ТЗ §10.1 — intent-first, regex-first) ──

def classify_pipeline_intent(message: str) -> dict | None:
    msg = message.lower().strip()

    # Start session (ТЗ §10.2 — regex-first)
    if any(p in msg for p in ["старт сессии", "начать сессию", "daily session", "старт торговли", "запусти сессию", "запусти дневную"]):
        budget_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:usdt|\$|дол|бакс)", msg)
        budget = float(budget_match.group(1)) if budget_match else 100.0
        risk = "balanced"
        if "агрессив" in msg or "aggressive" in msg:
            risk = "aggressive"
        elif "защит" in msg or "defensive" in msg or "консерват" in msg:
            risk = "defensive"
        return {"intent": "pipeline_start", "budget": budget, "risk_mode": risk}

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
    """ТЗ §8.5 — старт сессии, безопасный ответ."""
    try:
        from services.session_manager import create_session, generate_initial_plan, get_active_session

        existing = await get_active_session(user_id)
        if existing:
            # Idempotent — return current session
            text, _ = await _build_status_response(user_id)
            return text, _start_kb()

        budget = intent.get("budget", 100.0)
        risk = intent.get("risk_mode", "balanced")

        result = await create_session(user_id=user_id, budget_usdt=budget, duration_hours=8, risk_mode=risk)

        if "error" in result:
            return ERR_BACKEND, None

        plan = await generate_initial_plan(result["session_id"])
        if "error" in plan:
            return (
                f"🚀 <b>Сессия запущена</b>\n"
                f"ID: <code>{result['session_id'][:8]}</code>\n"
                f"Статус: ARMED\n"
                f"Режим риска: {risk}\n"
                f"Бюджет: {budget:.0f} USDT\n"
                f"Горизонт: 8 часов\n"
                f"Инструмент: BTCUSDT\n\n"
                f"⚠️ План генерируется…",
                _start_kb()
            )

        # ТЗ §8.5 — шаблон подтверждения
        text = (
            f"🚀 <b>Сессия запущена</b>\n"
            f"ID: <code>{result['session_id'][:8]}</code>\n"
            f"Статус: ARMED\n"
            f"Режим риска: {risk}\n"
            f"Бюджет: {budget:.0f} USDT\n"
            f"Горизонт: 8 часов\n"
            f"Инструмент: BTCUSDT\n"
            f"План: v{plan.get('version', 1)} · {plan.get('market_regime', '—')}"
        )
        return text, _start_kb()
    except Exception as exc:
        log.error("Pipeline start error: %s", exc)
        return ERR_BACKEND, None


async def _handle_stop_safe(user_id: int) -> str:
    """ТЗ §6.1 — остановка сессии, безопасный ответ."""
    try:
        from services.session_manager import get_active_session
        from services.stats_audit import session_closeout

        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION

        result = await session_closeout(str(session["id"]))
        return (
            f"🛑 <b>Сессия остановлена</b>\n"
            f"ID: <code>{str(session['id'])[:8]}</code>\n"
            f"Сделок: {result.get('trade_count', 0)}\n"
            f"PnL: {result.get('total_pnl_usdt', 0):+.4f} USDT\n"
            f"Max DD: {result.get('max_drawdown_pct', 0):.2f}%\n"
            f"Profit factor: {result.get('profit_factor', 'N/A')}"
        )
    except Exception as exc:
        log.error("Pipeline stop error: %s", exc)
        return ERR_BACKEND


async def _handle_revision_safe(user_id: int, command: str) -> str:
    """ТЗ §6.1/§10.2 — revision команда через regex-first."""
    try:
        from services.session_manager import get_active_session, apply_revision_command

        session = await get_active_session(user_id)
        if not session:
            return ERR_NO_SESSION

        if session["status"] in ("STOPPED", "COMPLETED"):
            return ERR_REVISION_NOT_ALLOWED

        result = await apply_revision_command(str(session["id"]), command, source="telegram", actor_user_id=user_id)

        if not result.get("ok"):
            return ERR_REVISION_NOT_ALLOWED

        cmd_emoji = {"continue": "✅", "tighten": "🛡", "reduce": "📉", "pause": "⏸", "close_all": "🛑"}.get(command, "🔧")
        return f"{cmd_emoji} <b>{command}</b> → {result.get('new_status', session['status'])}"
    except Exception as exc:
        log.error("Pipeline revision error: %s", exc)
        return ERR_REVISION_NOT_ALLOWED


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
        lines.append("🎯 <b>Активная сессия</b>")
        lines.append(f"ID: <code>{str(session['id'])[:8]}</code>")
        lines.append(f"Статус: {session['status'].upper()}")
        lines.append(f"План: v{session['active_plan_version']}")
        lines.append(f"Режим риска: {session['risk_mode']}")
        lines.append(f"Бюджет: {float(session['initial_budget_usdt']):.0f} USDT")
        lines.append(f"Открыто позиций: {open_count}")
        # 3. Агрегированный PnL и риск (ТЗ §8.1 — separate Unrealized/Realized)
        if metrics:
            lines.append(f"Unrealized PnL: {unrealized_pnl:+.2f} USDT ({(unrealized_pnl / float(session['initial_budget_usdt']) * 100):+.1f}%)")
            lines.append(f"Realized PnL: {realized_pnl:+.2f} USDT")
            lines.append(f"Max DD: {float(metrics['max_drawdown_pct']):.1f}% / лимит {risk_limit_pct:.1f}%")
        # 4. Последняя команда
        if last_rev:
            lines.append(f"Последняя команда: {last_rev['execution_command']}")

        # ТЗ §7.3 — если > 12 строк, digest + Mini App кнопка
        text = "\n".join(lines)
        if len(lines) > 12:
            # Digest: только приоритетные поля
            digest_lines = lines[:6] + ["\n📱 <i>Подробнее — в Mini App</i>"]
            text = "\n".join(digest_lines)
        return text, _session_kb()
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
            return "📦 <b>Открытых позиций нет.</b>\nСессия активна, входы ожидаются.", _portfolio_kb()

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

        return "\n".join(lines), _portfolio_kb()
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
        return text, _balance_kb()
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
        return text, _result_kb()
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
            return await _handle_stop_safe(user_id)
        elif intent_type == "pipeline_status":
            text, _ = await _build_status_response(user_id)
            return text
        elif intent_type == "pipeline_result":
            text, _ = await _build_result_response(user_id)
            return text
        elif intent_type == "pipeline_revision_cmd":
            return await _handle_revision_safe(user_id, intent.get("command", ""))
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT * FROM session_plans WHERE session_id = $1 ORDER BY version DESC LIMIT 1",
            str(session["id"]),
        )
        entries = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id = $1 AND plan_version = $2 ORDER BY created_at",
            str(session["id"]), session["active_plan_version"],
        )

    if not plan:
        return "📭 План не найден"

    plan_data = plan["plan_json"]
    if isinstance(plan_data, str):
        plan_data = json.loads(plan_data)

    lines = [
        f"📋 <b>Активный план v{plan['version']}</b>",
        f"Regime: {plan_data.get('market_regime', '?')}",
        f"Thesis: {plan_data.get('thesis', '?')}",
        f"Model: {plan_data.get('model_used', '?')}",
        "",
    ]

    for i, e in enumerate(entries):
        side_emoji = "🟢" if e["side"] == "long" else "🔴"
        lines.append(f"{side_emoji} Entry #{i+1} ({e['side'].upper()})")
        lines.append(f"  Zone: {float(e['entry_zone_from']):.2f} - {float(e['entry_zone_to']):.2f}")
        lines.append(f"  SL: {float(e['stop_loss']):.2f} | TP: {e['take_profit_json']}")
        lines.append(f"  Lev: {e['recommended_leverage']}x | Share: {float(e['budget_share_pct']):.1f}%")
        lines.append(f"  Status: {e['status']}")
        lines.append("")

    # ТЗ §7.3 — если длинный, offer Mini App
    if len(lines) > 12:
        url = _miniapp_url()
        lines.append(f'📱 <a href="{url}">Открыть Mini App</a> для полного плана')

    return "\n".join(lines)


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