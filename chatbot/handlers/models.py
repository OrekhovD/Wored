"""
Models handler - /models command, model selection, quota display.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

log = logging.getLogger(__name__)
router = Router(name="models")


def _models_keyboard() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="⚡ Auto (gateway routing)", callback_data="model_auto")],
        [InlineKeyboardButton(text="🏃 Worker (flash)", callback_data="model_worker_ollama")],
        [InlineKeyboardButton(text="🧠 Analyst (pro)", callback_data="model_analyst_ollama")],
        [InlineKeyboardButton(text="👑 Premium (glm-5.2)", callback_data="model_premium_ollama")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("models"))
async def cmd_models(message: Message):
    from ai.models import MODELS, WORKER_MODEL_CHAIN, ANALYST_MODEL_CHAIN, PREMIUM_MODEL_CHAIN
    from storage.postgres_client import get_user_model
    user_id = message.from_user.id
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
    await message.reply("\n".join(lines), reply_markup=_models_keyboard())


@router.callback_query(F.data.startswith("model_"))
async def cb_model_select(callback: CallbackQuery):
    data = callback.data.replace("model_", "")
    user_id = callback.from_user.id

    if data == "auto":
        from storage.postgres_client import save_user_model
        await save_user_model(user_id, "auto", auto_mode=True)
        await callback.answer("✅ Auto mode включён")
        await callback.message.edit_text("✅ Автоматическая маршрутизация через gateway active slot.")
        return

    # model_<tier> e.g. model_worker_ollama
    parts = data.split("_", 1)
    if len(parts) < 2:
        await callback.answer("Неизвестная модель")
        return

    tier = data  # full tier name like worker_ollama
    from ai.models import MODELS
    from storage.postgres_client import save_user_model
    cfg = MODELS.get(tier)
    if not cfg:
        await callback.answer(f"Модель {tier} не найдена")
        return

    await save_user_model(user_id, tier, auto_mode=False)
    await callback.answer(f"✅ Выбрана: {cfg.name}")
    await callback.message.edit_text(
        f"✅ Выбрана модель:\n<b>{cfg.name}</b>\n{cfg.model_id}\n\nИспользуется для всех запросов до следующего выбора."
    )
