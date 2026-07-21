"""
Admin handler - /admin command for diagnostics, health-check, quotas.
Only accessible to admin user.
"""
from __future__ import annotations

import logging
import os

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return user_id == int(os.getenv("TELEGRAM_ADMIN_ID", "0"))


def _admin_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="🏥 Health Check", callback_data="admin_health")],
        [InlineKeyboardButton(text="🔍 Active Route", callback_data="admin_route")],
        [InlineKeyboardButton(text="📊 Quota Dashboard", callback_data="admin_quotas")],
        [InlineKeyboardButton(text="🔬 Model Lab: Probe All", callback_data="admin_probe")],
        [InlineKeyboardButton(text="🔄 Rotate Active Slot", callback_data="admin_rotate")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply("⛔ Только для администратора")
        return
    await message.reply("<b>⚙️ Админ-панель</b>", reply_markup=_admin_menu())


@router.callback_query(F.data == "admin_health")
async def cb_health(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔")
        return
    await callback.answer()
    await callback.message.edit_text("🏥 Проверяю...")

    results = []
    # Redis
    try:
        from storage.redis_client import get_redis
        r = get_redis()
        r.ping()
        results.append("✅ Redis: OK")
    except Exception as e:
        results.append(f"❌ Redis: {e}")

    # Postgres
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            results.append("✅ Postgres: OK")
        else:
            results.append("❌ Postgres: no pool")
    except Exception as e:
        results.append(f"❌ Postgres: {e}")

    # WebUI
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get("http://webui:8000/api/health")
            results.append(f"{'✅' if resp.status_code == 200 else '❌'} WebUI: {resp.status_code}")
    except Exception as e:
        results.append(f"❌ WebUI: {e}")

    # Ollama Cloud
    try:
        import httpx
        api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{os.getenv('OLLAMA_CLOUD_BASE_URL', 'https://ollama.com/v1')}/models", headers={"Authorization": f"Bearer {api_key}"})
            results.append(f"{'✅' if resp.status_code == 200 else '❌'} Ollama Cloud: {resp.status_code}")
    except Exception as e:
        results.append(f"❌ Ollama Cloud: {e}")

    await callback.message.answer("\n".join(results))


@router.callback_query(F.data == "admin_route")
async def cb_route(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔")
        return
    await callback.answer()
    await callback.message.edit_text("🔍 Диагностика роутинга...")
    from ai.model_lab import get_active_route
    route = await get_active_route()
    lines = ["<b>🔍 Active Route</b>\n"]
    for chain_name in ("worker_chain", "analyst_chain", "premium_chain"):
        chain = route.get(chain_name, [])
        lines.append(f"<b>{chain_name}:</b>")
        for m in chain[:3]:
            emoji = "✅" if m["status"] == "ok" else "❌"
            lat = f" {m.get('latency_ms','?')}ms" if m.get("latency_ms") else ""
            lines.append(f"  {emoji} {m['model']}{lat} [{m['status']}]")
        lines.append("")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "admin_quotas")
async def cb_quotas(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔")
        return
    await callback.answer()
    from ai.quota import get_quota_status
    status = await get_quota_status(callback.from_user.id)
    lines = ["<b>📊 Quota Dashboard</b>\n"]
    for tier, info in status.items():
        lines.append(f"<b>{tier}</b>: {info['usage_pct']:.0f}% ({info['remaining']}/{info['daily_limit']})")
        if info.get("warning"):
            lines.append(f"  ⚠️ {info['warning']}")
    await callback.message.edit_text("\n".join(lines))


@router.callback_query(F.data == "admin_probe")
async def cb_probe(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔")
        return
    await callback.answer()
    await callback.message.edit_text("🔬 Пробую все модели...")
    from ai.model_lab import probe_all
    results = await probe_all()
    lines = ["<b>🔬 Model Probe Results</b>\n"]
    for r in results:
        if isinstance(r, dict):
            emoji = "✅" if r.get("status") == "ok" else "❌"
            lines.append(f"{emoji} {r.get('tier','?')}: {r.get('model','?')} [{r.get('status','?')}]")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "admin_rotate")
async def cb_rotate(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔")
        return
    await callback.answer()
    await callback.message.edit_text("🔄 Ротация active slot...")
    from ai.model_lab import rotate_active_slot
    result = await rotate_active_slot()
    await callback.message.answer(f"🔄 Результат: {result.get('status','?')} → {result.get('model','?')}")
