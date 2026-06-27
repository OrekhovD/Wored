"""
Daily Pipeline WORED v2 — Telegram handler для pipeline команд.

ТЗ 13.1 — минимальный набор intents:
  статус сессии
  активный план
  последняя ревизия
  мои позиции
  детали позиции {id}
  pnl
  почему вход {id}
  почему выход {id}
  остановить сессию

Также: старт сессии (daily_session, старт сессии, начать сессию)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="pipeline")

DEFAULT_USER_ID = 5249526259


@router.message(Command("session"))
async def cmd_session(message: Message):
    """Команда /session — статус daily pipeline сессии."""
    await message.answer("📊 Загрузка...", parse_mode="HTML")
    text = await handle_pipeline_intent({"intent": "pipeline_status"}, message.from_user.id)
    await message.answer(text, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(старт сессии|начать сессию|daily session|запусти сессию|старт торговли)"))
async def msg_start_session(message: Message):
    """Запуск daily session через текстовое сообщение."""
    intent = classify_pipeline_intent(message.text or "")
    if intent:
        text = await handle_pipeline_intent(intent, message.from_user.id)
        await message.answer(text, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(остановить сессию|стоп сессия|stop session|закрыть сессию)"))
async def msg_stop_session(message: Message):
    """Остановка daily session."""
    text = await handle_pipeline_intent({"intent": "pipeline_stop"}, message.from_user.id)
    await message.answer(text, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)(статус сессии|сессия статус|session status|состояние сессии)"))
async def msg_session_status(message: Message):
    """Статус daily session."""
    text = await handle_pipeline_intent({"intent": "pipeline_status"}, message.from_user.id)
    await message.answer(text, parse_mode="HTML")


def classify_pipeline_intent(message: str) -> dict | None:
    """
    Определить pipeline intent из сообщения пользователя.
    Возвращает {"intent": "pipeline_*", ...} или None если не pipeline.
    """
    msg = message.lower().strip()

    # Start session
    if any(p in msg for p in ["старт сессии", "начать сессию", "daily session", "старт торговли", "запусти сессию"]):
        # Extract budget if present
        budget_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:usdt|\$|дол|бакс)", msg)
        budget = float(budget_match.group(1)) if budget_match else 100.0

        # Extract risk mode
        risk = "balanced"
        if "агрессив" in msg or "aggressive" in msg:
            risk = "aggressive"
        elif "защит" in msg or "defensive" in msg or "консерват" in msg:
            risk = "defensive"

        return {"intent": "pipeline_start", "budget": budget, "risk_mode": risk}

    # Stop session
    if any(p in msg for p in ["остановить сессию", "стоп сессия", "stop session", "закрыть сессию"]):
        return {"intent": "pipeline_stop"}

    # Session status
    if any(p in msg for p in ["статус сессии", "сессия статус", "session status", "состояние сессии"]):
        return {"intent": "pipeline_status"}

    # Active plan
    if any(p in msg for p in ["активный план", "текущий план", "active plan", "план сессии"]):
        return {"intent": "pipeline_plan"}

    # Latest revision
    if any(p in msg for p in ["последняя ревизия", "latest revision", "ревизия плана"]):
        return {"intent": "pipeline_revision"}

    # My positions
    if any(p in msg for p in ["мои позиции", "позиции сессии", "session positions", "открытые сделки сессии"]):
        return {"intent": "pipeline_positions"}

    # PnL
    if any(p in msg for p in ["pnl сессии", "pnl", "прибыль сессии", "результат сессии"]) and "сесс" in msg:
        return {"intent": "pipeline_pnl"}

    # Why entry {id}
    why_entry = re.search(r"почему вход\s+(\S+)", msg)
    if why_entry:
        return {"intent": "pipeline_why_entry", "entry_id": why_entry.group(1)}

    # Why exit {id}
    why_exit = re.search(r"почему выход\s+(\S+)", msg)
    if why_exit:
        return {"intent": "pipeline_why_exit", "trade_id": why_exit.group(1)}

    # Position details {id}
    pos_detail = re.search(r"детали позиции\s+(\S+)", msg)
    if pos_detail:
        return {"intent": "pipeline_position_detail", "trade_id": pos_detail.group(1)}

    return None


async def handle_pipeline_intent(intent: dict, user_id: int = DEFAULT_USER_ID) -> str:
    """Обработать pipeline intent и вернуть текстовый ответ для Telegram."""
    intent_type = intent.get("intent", "")

    try:
        if intent_type == "pipeline_start":
            return await _handle_start(user_id, intent)
        elif intent_type == "pipeline_stop":
            return await _handle_stop(user_id)
        elif intent_type == "pipeline_status":
            return await _handle_status(user_id)
        elif intent_type == "pipeline_plan":
            return await _handle_plan(user_id)
        elif intent_type == "pipeline_revision":
            return await _handle_revision(user_id)
        elif intent_type == "pipeline_positions":
            return await _handle_positions(user_id)
        elif intent_type == "pipeline_pnl":
            return await _handle_pnl(user_id)
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
        return f"❌ Ошибка: {exc}"


async def _handle_start(user_id: int, intent: dict) -> str:
    from services.session_manager import create_session, generate_initial_plan, get_active_session

    # Check if already has active session
    existing = await get_active_session(user_id)
    if existing:
        return f"⚠️ У вас уже есть активная сессия #{existing['id'][:8]}... Статус: {existing['status']}"

    budget = intent.get("budget", 100.0)
    risk = intent.get("risk_mode", "balanced")

    result = await create_session(
        user_id=user_id,
        budget_usdt=budget,
        duration_hours=8,
        risk_mode=risk,
    )

    if "error" in result:
        return f"❌ {result['error']}"

    # Generate initial plan
    plan = await generate_initial_plan(result["session_id"])
    if "error" in plan:
        return f"⚠️ Сессия создана, но план не сгенерирован: {plan['error']}"

    return (
        f"🚀 <b>Daily Session запущена</b>\n"
        f"ID: <code>{result['session_id'][:8]}</code>\n"
        f"Бюджет: {budget:.2f} USDT\n"
        f"Режим: {risk}\n"
        f"Длительность: 8ч\n"
        f"Модель: {plan.get('model_used', '?')}\n"
        f"Regime: {plan.get('market_regime', '?')}\n"
        f"Thesis: {plan.get('thesis', '?')}\n"
        f"Entries: {plan.get('entries_count', 0)}\n\n"
        f"📊 Pipeline активен. Пиши «статус сессии» для проверки."
    )


async def _handle_stop(user_id: int) -> str:
    from services.session_manager import get_active_session, update_session_status
    from services.stats_audit import session_closeout

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

    result = await session_closeout(str(session["id"]))
    return (
        f"🛑 <b>Сессия остановлена</b>\n"
        f"Trades: {result.get('trade_count', 0)}\n"
        f"PnL: {result.get('total_pnl_usdt', 0):+.4f} USDT\n"
        f"Drawdown: {result.get('max_drawdown_pct', 0):.2f}%\n"
        f"Profit factor: {result.get('profit_factor', 'N/A')}"
    )


async def _handle_status(user_id: int) -> str:
    from services.session_manager import get_active_session

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

    from storage.postgres_client import get_pool
    pool = await get_pool()
    if not pool:
        return "❌ Нет БД"

    async with pool.acquire() as conn:
        metrics = await conn.fetchrow(
            "SELECT * FROM session_metrics WHERE session_id = $1",
            str(session["id"]),
        )
        trades = await conn.fetch(
            "SELECT COUNT(*) as count, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count FROM executed_trades WHERE session_id = $1",
            str(session["id"]),
        )

    status_emoji = {"armed": "🎯", "in_position": "📈", "paused": "⏸", "cooldown": "❄️", "idle": "💤"}.get(session["status"], "❓")

    lines = [
        f"{status_emoji} <b>Статус сессии</b>",
        f"ID: <code>{str(session['id'])[:8]}</code>",
        f"Состояние: <b>{session['status'].upper()}</b>",
        f"Plan v{session['active_plan_version']}",
        f"Бюджет: {float(session['initial_budget_usdt']):.2f} USDT",
    ]

    if metrics:
        lines.append(f"Equity: {float(metrics['current_equity'] if 'current_equity' in metrics.keys() else 0):.2f} USDT")
        lines.append(f"PnL: {float(metrics['total_pnl_usdt']):+.4f} USDT ({float(metrics['total_pnl_pct']):+.2f}%)")
        lines.append(f"Trades: {metrics['trade_count']} (W:{metrics['win_count']} L:{metrics['loss_count']})")
        lines.append(f"Drawdown: {float(metrics['max_drawdown_pct']):.2f}%")
        pf = metrics['profit_factor']
        lines.append(f"Profit factor: {float(pf) if pf else 'N/A'}")

    return "\n".join(lines)


async def _handle_plan(user_id: int) -> str:
    from services.session_manager import get_active_session

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

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

    return "\n".join(lines)


async def _handle_revision(user_id: int) -> str:
    from services.session_manager import get_active_session

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

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

    cmd_emoji = {"continue": "✅", "tighten": "🔧", "reduce": "📉", "pause": "⏸", "close_all": "🛑"}.get(
        rev["execution_command"], "❓"
    )

    return (
        f"🔄 <b>Последняя ревизия</b>\n"
        f"v{rev['base_version']} → v{rev['new_version']}\n"
        f"{cmd_emoji} Command: {rev['execution_command']}\n"
        f"Summary: {rev_data.get('summary', '?')}\n"
        f"Regime: {rev_data.get('market_regime_status', '?')}"
    )


async def _handle_positions(user_id: int) -> str:
    from services.session_manager import get_active_session

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id = $1 ORDER BY opened_at DESC",
            str(session["id"]),
        )

    if not trades:
        return "📭 Сделок в сессии нет"

    lines = [f"📊 <b>Позиции сессии</b> ({len(trades)})"]
    for t in trades:
        side_emoji = "🟢" if t["side"] == "long" else "🔴"
        status_emoji = "📈" if t["status"] == "open" else "✅" if t["status"] == "closed" else "💥"
        pnl_str = ""
        if t["realised_pnl_usdt"] is not None:
            pnl_str = f" PnL: {float(t['realised_pnl_usdt']):+.4f}"
        lines.append(
            f"{side_emoji}{status_emoji} #{str(t['id'])[:8]} {t['side'].upper()} {t['leverage']}x "
            f"entry={float(t['entry_price']):.2f}{pnl_str} [{t['status']}]"
        )

    return "\n".join(lines)


async def _handle_pnl(user_id: int) -> str:
    from services.session_manager import get_active_session

    session = await get_active_session(user_id)
    if not session:
        return "📭 Нет активной сессии"

    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        metrics = await conn.fetchrow(
            "SELECT * FROM session_metrics WHERE session_id = $1",
            str(session["id"]),
        )

    if not metrics:
        return "📭 Метрик нет (сессия только началась)"

    return (
        f"💰 <b>PnL сессии</b>\n"
        f"Total: {float(metrics['total_pnl_usdt']):+.4f} USDT ({float(metrics['total_pnl_pct']):+.2f}%)\n"
        f"Win: {metrics['win_count']} | Loss: {metrics['loss_count']} | Liq: {metrics['liquidation_count']}\n"
        f"Max DD: {float(metrics['max_drawdown_pct']):.2f}%\n"
        f"PF: {float(metrics['profit_factor']) if metrics['profit_factor'] else 'N/A'}\n"
        f"Time in market: {float(metrics['time_in_market_pct']):.1f}%"
    )


async def _handle_why_entry(entry_id: str) -> str:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        entry = await conn.fetchrow(
            "SELECT * FROM planned_entries WHERE id = $1",
            entry_id,
        )

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
        trade = await conn.fetchrow(
            "SELECT * FROM executed_trades WHERE id = $1",
            trade_id,
        )

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
        trade = await conn.fetchrow(
            "SELECT * FROM executed_trades WHERE id = $1",
            trade_id,
        )

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