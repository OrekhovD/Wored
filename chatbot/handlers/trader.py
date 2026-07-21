"""
Trader handler — unified menu for crypto trader functionality + model selection.
Merged /trader + /models into one menu (v2).
Provides: Session, Market, Analytics, Models, Strategy, Journal, Results, Settings.
"""
from __future__ import annotations

import logging
import os

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="trader")

ADMIN_ID = 5249526259


# ── Unified trader menu ──

def _trader_menu() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="🎯 Сессия", callback_data="trader_session"),
            InlineKeyboardButton(text="📊 Рынок", callback_data="trader_market"),
        ],
        [
            InlineKeyboardButton(text="🧠 Анализ", callback_data="trader_analyze"),
            InlineKeyboardButton(text="🔮 Прогнозы", callback_data="trader_predictions"),
        ],
        [
            InlineKeyboardButton(text="📦 Портфель", callback_data="trader_portfolio"),
            InlineKeyboardButton(text="🔔 Алерты", callback_data="trader_alerts"),
        ],
        [
            InlineKeyboardButton(text="🤖 Модели", callback_data="trader_models"),
            InlineKeyboardButton(text="⚙️ Система", callback_data="trader_settings"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ── Models submenu ──

def _models_keyboard() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="⚡ Auto (gateway routing)", callback_data="model_auto")],
        [InlineKeyboardButton(text="🏃 Worker (flash)", callback_data="model_worker_ollama")],
        [InlineKeyboardButton(text="🧠 Analyst (pro)", callback_data="model_analyst_ollama")],
        [InlineKeyboardButton(text="👑 Premium (glm-5.2)", callback_data="model_premium_ollama")],
        [InlineKeyboardButton(text="← Назад", callback_data="trader_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="trader_back")],
    ])


@router.message(Command("trader"))
async def cmd_trader(message: Message):
    await message.reply(
        "🏴‍☠️ <b>Командный мостик</b>\n\nВыбери действие:",
        reply_markup=_trader_menu(),
    )


@router.callback_query(F.data == "trader_back")
async def cb_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🏴‍☠️ <b>Командный мостик</b>\n\nВыбери действие:",
        reply_markup=_trader_menu(),
    )


@router.callback_query(F.data == "trader_session")
async def cb_session(callback: CallbackQuery):
    await callback.answer()
    from handlers.pipeline import _build_status_response, _session_control_kb
    text, _ = await _build_status_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=_session_control_kb(), parse_mode="HTML")


@router.callback_query(F.data == "trader_market")
async def cb_market(callback: CallbackQuery):
    await callback.answer()
    from handlers.market import build_market_text, get_market_keyboard
    text = await build_market_text()
    await callback.message.edit_text(text, reply_markup=get_market_keyboard())


@router.callback_query(F.data == "trader_analyze")
async def cb_analyze(callback: CallbackQuery):
    await callback.answer()
    from handlers.analytics import show_analytics_menu
    await show_analytics_menu(callback.message)


@router.callback_query(F.data == "trader_predictions")
async def cb_predictions(callback: CallbackQuery):
    await callback.answer()
    from handlers.predictions import send_prediction_menu
    await send_prediction_menu(callback.message)


@router.callback_query(F.data == "trader_portfolio")
async def cb_portfolio(callback: CallbackQuery):
    await callback.answer()
    from handlers.pipeline import _build_positions_response
    text, kb = await _build_positions_response(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "trader_alerts")
async def cb_alerts(callback: CallbackQuery):
    await callback.answer()
    from handlers.alerts import send_alerts
    await send_alerts(callback.message)


@router.callback_query(F.data == "trader_settings")
async def cb_settings(callback: CallbackQuery):
    await callback.answer()
    from handlers.settings import send_settings
    await send_settings(callback.message)


@router.callback_query(F.data == "trader_models")
async def cb_models_menu(callback: CallbackQuery):
    """Показать меню выбора моделей (merged из models.py)."""
    await callback.answer()
    from ai.models import MODELS, WORKER_MODEL_CHAIN, ANALYST_MODEL_CHAIN, PREMIUM_MODEL_CHAIN
    from storage.postgres_client import get_user_model

    user_id = callback.from_user.id
    pref = await get_user_model(user_id)
    current = pref["model_alias"] if pref else "auto"

    lines = ["<b>🤖 Доступные модели</b>\n"]
    tiers = [
        ("Worker", WORKER_MODEL_CHAIN, "Быстрые задачи, классификация, парсинг"),
        ("Analyst", ANALYST_MODEL_CHAIN, "Анализ рынка, прогнозы, сигналы"),
        ("Premium", PREMIUM_MODEL_CHAIN, "Стратегия, deep research, корректировка"),
    ]
    for role, chain, desc in tiers:
        lines.append(f"<b>{role}</b> — {desc}")
        for tier in chain[:2]:
            cfg = MODELS.get(tier)
            if cfg and cfg.model_id:
                lines.append(f"  • {cfg.name} ({cfg.model_id})")
        lines.append("")

    lines.append(f"<i>Текущий выбор: {current}</i>")
    await callback.message.edit_text("\n".join(lines), reply_markup=_models_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("model_"))
async def cb_model_select(callback: CallbackQuery):
    """Выбор модели (merged из models.py)."""
    data = callback.data.replace("model_", "")
    user_id = callback.from_user.id

    if data == "auto":
        from storage.postgres_client import save_user_model
        await save_user_model(user_id, "auto", auto_mode=True)
        await callback.answer("✅ Auto mode включён")
        await callback.message.edit_text(
            "✅ Автоматическая маршрутизация через gateway active slot.",
            reply_markup=_models_keyboard(),
        )
        return

    parts = data.split("_", 1)
    if len(parts) < 2:
        await callback.answer("Неизвестная модель")
        return

    tier = data
    from ai.models import MODELS
    from storage.postgres_client import save_user_model
    cfg = MODELS.get(tier)
    if not cfg:
        await callback.answer(f"Модель {tier} не найдена")
        return

    await save_user_model(user_id, tier, auto_mode=False)
    await callback.answer(f"✅ Выбрана: {cfg.name}")
    await callback.message.edit_text(
        f"✅ Выбрана модель:\n<b>{cfg.name}</b>\n{cfg.model_id}\n\n"
        f"Используется для всех запросов до следующего выбора.",
        reply_markup=_models_keyboard(),
    )