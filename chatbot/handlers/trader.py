"""
Trader handler - Telegram menu for crypto trader functionality.
Provides: Analysis, Simulation, Results, Strategy, Journal, Model selection.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="trader")

ADMIN_ID = 5249526259


def _trader_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📊 Анализ BTC/USDT", callback_data="trader_analyze")],
        [InlineKeyboardButton(text="📈 Симуляция фьючерсов", callback_data="trader_sim")],
        [InlineKeyboardButton(text="📋 Результаты симуляций", callback_data="trader_results")],
        [InlineKeyboardButton(text="🧠 Стратегия", callback_data="trader_strategy")],
        [InlineKeyboardButton(text="📓 Журнал агента", callback_data="trader_journal")],
        [InlineKeyboardButton(text="🤖 Выбор модели", callback_data="trader_models")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("trader"))
async def cmd_trader(message: Message):
    await message.reply(
        "<b>🏴‍☠️ Криптотрейдер</b>\n\nВыбери действие:",
        reply_markup=_trader_menu(),
    )


@router.callback_query(F.data == "trader_analyze")
async def cb_analyze(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("📊 Анализирую BTC/USDT...")
    from ai.router import route_request
    result = await route_request("анализ btcusdt", [])
    await callback.message.answer(result or "Ошибка анализа")


@router.callback_query(F.data == "trader_sim")
async def cb_sim(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "<b>📈 Симуляция фьючерсов</b>\n\n"
        "Отправь команду в формате:\n"
        "<code>фьючерсы кросс 200x лонг btc на 30$</code>\n\n"
        "Параметры: направление (лонг/шорт), плечо (100-200x), маржа (cross/isolated), сумма$\n"
        "Дополнительно: <code>торгуй</code> — AI управляет позицией\n"
        "Просмотр: <code>мои позиции</code>, <code>закрой позицию #N</code>"
    )


@router.callback_query(F.data == "trader_results")
async def cb_results(callback: CallbackQuery):
    await callback.answer()
    from services.sim_engine import get_user_history
    from services.sim_engine import get_open_positions
    open_pos = await get_open_positions(ADMIN_ID)
    closed_pos = await get_user_history(ADMIN_ID, limit=10)
    lines = [f"<b>📋 Результаты симуляций</b>\n", f"Открытые: {len(open_pos)}", ""]
    for p in open_pos[:5]:
        from services.sim_engine import calculate_unrealized_pnl
        pnl = calculate_unrealized_pnl(p, float(p["entry_price"]))
        lines.append(f"#{p['id']} {p['direction'].upper()} {p['symbol'].upper()} {p['leverage']}x PnL:{pnl['roi_pct']:+.1f}%")
    lines.append(f"\nЗакрытые: {len(closed_pos)}")
    for p in closed_pos[:5]:
        rpnl = float(p.get("realized_pnl") or 0)
        lines.append(f"#{p['id']} {p['direction'].upper()} {p['symbol'].upper()} PnL:{rpnl:+.4f} ({p.get('close_reason','manual')})")
    await callback.message.edit_text("\n".join(lines))


@router.callback_query(F.data == "trader_strategy")
async def cb_strategy(callback: CallbackQuery):
    await callback.answer()
    from storage.postgres_client import get_latest_strategy_rules
    rules = await get_latest_strategy_rules()
    if not rules:
        await callback.message.edit_text("🧠 Стратегия: правила ещё не сформированы. Нужно провести серию симуляций (5+) для оценки.")
        return
    text = f"<b>🧠 Стратегия v{rules['version']}</b>\nСоздана: {rules.get('created_at','?')}\n\n"
    r = rules.get("rules", {})
    if isinstance(r, dict):
        text += f"<i>{r.get('summary','')}</i>\n\n"
        for adj in r.get("adjustments", []):
            text += f"• {adj.get('parameter','?')}: {adj.get('old','?')}→{adj.get('new','?')} ({adj.get('reason','')})\n"
    else:
        text += str(r)
    await callback.message.edit_text(text)


@router.callback_query(F.data == "trader_journal")
async def cb_journal(callback: CallbackQuery):
    await callback.answer()
    from storage.postgres_client import get_recent_prediction_requests
    items = await get_recent_prediction_requests(limit=5)
    lines = ["<b>📓 Журнал агента</b>\n"]
    for item in items:
        lines.append(f"#{item['id']} {item['symbol']} {item['status']} acc:{item.get('avg_accuracy','?')}%")
    if not items:
        lines.append("Записей пока нет.")
    await callback.message.edit_text("\n".join(lines))


@router.callback_query(F.data == "trader_models")
async def cb_models(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("🤖 Выбор модели — используй /models")
