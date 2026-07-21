"""
Plans handler — /plans command shows plan revision history, accuracy, and diffs.
"""
from __future__ import annotations

import json
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.session_manager import get_active_session

router = Router()
log = logging.getLogger(__name__)


@router.message(Command("plans"))
async def cmd_plans(message: Message):
    """Show plan revision history with accuracy summary."""
    user_id = message.from_user.id
    session = await get_active_session(user_id)
    if not session:
        await message.answer("Нет активной сессии. Используйте /plan для создания.")
        return

    from services.plan_accuracy import get_accuracy_summary, get_plan_diff
    from storage.postgres_client import get_pool

    sid = str(session["id"])
    current_version = session["active_plan_version"]

    # Get accuracy summary
    summary = await get_accuracy_summary(sid)

    lines = ["📋 <b>История планов</b>", f"Сессия: {sid[:8]} | Текущая версия: v{current_version}", ""]

    if summary["total_predictions"] == 0:
        lines.append("📊 Точность: нет данных (первый план создан, ожидаем 1ч)")
    else:
        lines.append(f"📊 <b>Точность прогнозов</b>")
        lines.append(f"  Всего прогнозов: {summary['total_predictions']}")
        lines.append(f"  Оценено: {summary['evaluated']}")
        lines.append(f"  Средний score: {summary['avg_accuracy_score']}/100")
        lines.append(f"  Направление верно: {summary['direction_accuracy_pct']}%")
        lines.append(f"  TP hit: {summary['profit_count']} | SL hit: {summary['loss_count']}")
        lines.append("")

    # Version breakdown
    if summary.get("versions"):
        lines.append("📝 <b>По версиям</b>")
        lines.append("Ver | Total | Avg | Profit | Loss | Pending")
        lines.append("----|-------|-----|--------|------|-------")
        for v in summary["versions"]:
            avg = round(float(v.get("avg_score") or 0), 1)
            lines.append(
                f" v{v['plan_version']} | {v['total']:5d} | {avg:4.1f} | {v['profit']:6d} | {v['loss']:4d} | {v['pending']:6d}"
            )
        lines.append("")

    # Recent revisions
    pool = await get_pool()
    if pool:
        async with pool.acquire() as conn:
            revisions = await conn.fetch(
                """SELECT sr.*, sp.plan_json
                FROM session_revisions sr
                LEFT JOIN session_plans sp ON sp.session_id = sr.session_id AND sp.version = sr.new_version
                WHERE sr.session_id = $1
                ORDER BY sr.created_at DESC LIMIT 10""",
                sid,
            )
            plans = await conn.fetch(
                "SELECT version, created_at FROM session_plans WHERE session_id=$1 ORDER BY version",
                sid,
            )

        if plans:
            lines.append("🔄 <b>Версии планов</b>")
            for p in plans:
                lines.append(f"  v{p['version']} — {p['created_at'].strftime('%H:%M UTC')}")
            lines.append("")

        if revisions:
            lines.append("🔧 <b>Последние ревизии</b>")
            for r in revisions[:5]:
                rj = r["revision_json"]
                if isinstance(rj, str):
                    rj = json.loads(rj)
                cmd = rj.get("execution_command", "?")
                regime = rj.get("market_regime_status", "?")
                summary_text = rj.get("summary", "")[:80]
                lines.append(f"  v{r['base_version']}→v{r['new_version']} | {cmd} | {regime}")
                if summary_text:
                    lines.append(f"    {summary_text}")
            lines.append("")

    lines.append("💡 /diff N M — сравнить версии N и M")
    lines.append("💡 /accuracy — детальная точность")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("diff"))
async def cmd_diff(message: Message):
    """Compare two plan versions: /diff 1 2"""
    user_id = message.from_user.id
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /diff 1 2 (версия A, версия B)")
        return
    try:
        va = int(parts[1])
        vb = int(parts[2])
    except ValueError:
        await message.answer("Версии должны быть числами. Пример: /diff 1 2")
        return

    session = await get_active_session(user_id)
    if not session:
        await message.answer("Нет активной сессии.")
        return

    from services.plan_accuracy import get_plan_diff

    diff = await get_plan_diff(str(session["id"]), va, vb)

    lines = [f"🔄 <b>Сравнение v{va} → v{vb}</b>", ""]

    if diff.get("revision"):
        rev = diff["revision"]
        rj = rev.get("revision_json")
        if isinstance(rj, str):
            rj = json.loads(rj)
        lines.append(f"Команда: {rj.get('execution_command', '?')}")
        lines.append(f"Регим: {rj.get('market_regime_status', '?')}")
        summary = rj.get("summary", "")
        if summary:
            lines.append(f"Описание: {summary}")
        lines.append("")

    lines.append(f"<b>Записи v{va}</b> ({len(diff['entries_a'])}):")
    for e in diff["entries_a"]:
        lines.append(f"  {e['side'].upper()} {e['entry_from']:.0f}-{e['entry_to']:.0f} SL={e['stop_loss']:.0f} [{e['status']}]")

    lines.append("")
    lines.append(f"<b>Записи v{vb}</b> ({len(diff['entries_b'])}):")
    for e in diff["entries_b"]:
        lines.append(f"  {e['side'].upper()} {e['entry_from']:.0f}-{e['entry_to']:.0f} SL={e['stop_loss']:.0f} [{e['status']}]")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("accuracy"))
async def cmd_accuracy(message: Message):
    """Detailed accuracy report."""
    user_id = message.from_user.id
    session = await get_active_session(user_id)
    if not session:
        await message.answer("Нет активной сессии.")
        return

    from services.plan_accuracy import get_accuracy_summary, evaluate_pending_predictions

    # Trigger evaluation first
    await evaluate_pending_predictions()

    summary = await get_accuracy_summary(str(session["id"]))

    lines = ["🎯 <b>Детальная точность</b>", ""]

    if summary["total_predictions"] == 0:
        lines.append("Нет данных. Создайте план (/plan) и подождите 1 час.")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    lines.append(f"Всего прогнозов: {summary['total_predictions']}")
    lines.append(f"Оценено: {summary['evaluated']}")
    lines.append(f"Средний score: {summary['avg_accuracy_score']}/100")
    lines.append(f"Направление верно: {summary['direction_accuracy_pct']}%")
    lines.append(f"TP hit: {summary['profit_count']} | SL hit: {summary['loss_count']}")
    lines.append("")

    if summary.get("versions"):
        lines.append("По версиям:")
        for v in summary["versions"]:
            avg = round(float(v.get("avg_score") or 0), 1)
            lines.append(
                f"  v{v['plan_version']}: {v['total']} прогнозов, score={avg}, "
                f"profit={v['profit']}, loss={v['loss']}, pending={v['pending']}"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")