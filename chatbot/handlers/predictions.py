from __future__ import annotations

import html
import logging
import os
from typing import Any

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from integrations.webui_client import create_prediction_request, get_webui_public_base_url
from storage.postgres_client import get_latest_prediction_request, get_prediction_request_detail, get_recent_prediction_requests

log = logging.getLogger(__name__)
router = Router()

HORIZON_ROWS = ((1, 2, 3, 4), (8, 16, 24))
ROLE_ALIASES = {"Analyst": "A", "Strategist": "S", "Oracle": "O", "Worker": "W"}
STATUS_EMOJI = {
    "completed": "🟢",
    "active": "🟡",
    "tracking": "🟡",
    "failed": "🔴",
}


def _status_emoji(status: str) -> str:
    return STATUS_EMOJI.get((status or "").lower(), "⚪")


def _model_alias(model: dict[str, Any]) -> str:
    role_name = model.get("role_name") or ""
    short_name = model.get("short_name") or model.get("model_name") or model.get("model_key") or "M"
    alias = ROLE_ALIASES.get(role_name)
    if alias:
        return alias
    return short_name[:1].upper()


def _compact_target_label(value: str | None) -> str:
    if not value:
        return "pending"
    if " " in value:
        parts = value.split()
        if len(parts) >= 2:
            return f"{parts[1]} UTC"
    return value


def build_prediction_menu_keyboard() -> InlineKeyboardMarkup:
    watchlist = [item.strip().lower() for item in os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",") if item.strip()]
    symbol_buttons = [InlineKeyboardButton(text=symbol.upper(), callback_data=f"prediction_symbol:{symbol}") for symbol in watchlist]
    keyboard = []
    if symbol_buttons:
        keyboard.append(symbol_buttons)
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить обзор", callback_data="prediction_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_prediction_horizon_keyboard(symbol: str) -> InlineKeyboardMarkup:
    keyboard = []
    for row in HORIZON_ROWS:
        keyboard.append(
            [
                InlineKeyboardButton(text=f"{hours}h", callback_data=f"prediction_run:{symbol}:{hours}")
                for hours in row
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text="📋 Последний", callback_data=f"prediction_latest:{symbol}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="prediction_menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_prediction_detail_keyboard(detail: dict[str, Any]) -> InlineKeyboardMarkup:
    request_id = detail["id"]
    symbol = detail["symbol"].lower()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Статус", callback_data=f"prediction_status:{request_id}"),
                InlineKeyboardButton(text="🧭 Новый горизонт", callback_data=f"prediction_symbol:{symbol}"),
            ],
            [
                InlineKeyboardButton(text="📋 Прогнозы", callback_data="prediction_menu"),
                InlineKeyboardButton(text="🌐 Matrix", url=f"{get_webui_public_base_url()}/predictions/{request_id}"),
            ],
        ]
    )


def format_prediction_menu_text(items: list[dict[str, Any]]) -> str:
    lines = [
        "🔮 <b>Forecast Lab</b>",
        "",
        "Выберите монету, затем горизонт. Бот запустит почасовой прогноз и покажет live scorecard по моделям.",
    ]
    if not items:
        lines.extend(["", "История прогнозов пока пуста."])
        return "\n".join(lines)

    lines.extend(["", "<b>Последние запросы:</b>"])
    for item in items:
        score = (
            f" · hit {item['avg_accuracy']:.1f}% / miss {item['avg_failure']:.1f}%"
            if item.get("avg_accuracy") is not None
            else ""
        )
        lines.append(
            f"{_status_emoji(item['status'])} "
            f"<b>#{item['id']}</b> {item['symbol'].upper()} {item['horizon_hours']}h "
            f"· {item['completed_models']} ok / {item['failed_models']} fail{score}"
        )
    return "\n".join(lines)


def format_prediction_detail(detail: dict[str, Any]) -> str:
    lines = [
        f"🔮 <b>{detail['symbol'].upper()} · {detail['horizon_hours']}h</b>",
        (
            f"{_status_emoji(detail['status'])} <b>Запрос #{detail['id']}</b> "
            f"· source <code>{html.escape(detail.get('source', 'webui'))}</code> "
            f"· by <code>{html.escape(detail.get('requested_by', 'unknown'))}</code>"
        ),
        (
            f"База <code>${detail['base_price']:.2f}</code> · "
            f"модели {detail['completed_models']} ok / {detail['failed_models']} fail · "
            f"оценено {detail['evaluated_points']}/{detail['total_points']}"
        ),
    ]

    if detail.get("avg_accuracy") is not None:
        lines.append(f"Средний hit <b>{detail['avg_accuracy']:.1f}%</b> · miss <b>{detail['avg_failure']:.1f}%</b>")

    top_model = detail.get("top_model")
    if top_model:
        lines.append(
            f"Лидер: <b>{html.escape(top_model['model_name'])}</b> "
            f"· {top_model['avg_accuracy']:.1f}% hit"
        )

    lines.append("")
    lines.append("<b>Модели:</b>")
    for model in detail.get("models", []):
        status_emoji = _status_emoji(model["status"])
        if model.get("avg_accuracy") is not None:
            lines.append(
                f"{status_emoji} <b>{html.escape(model['model_name'])}</b> "
                f"· hit {model['avg_accuracy']:.1f}% / miss {model['avg_failure']:.1f}%"
            )
        elif model.get("error_message"):
            lines.append(f"{status_emoji} <b>{html.escape(model['model_name'])}</b> · {html.escape(model['error_message'])}")
        else:
            lines.append(f"{status_emoji} <b>{html.escape(model['model_name'])}</b> · pending")

    lines.append("")
    lines.append("<b>Почасовой обзор:</b>")

    if not detail.get("models"):
        lines.append("Нет данных по моделям.")
        return "\n".join(lines)

    max_hour = detail.get("horizon_hours", 0)
    for hour in range(1, max_hour + 1):
        points_by_model: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        target_display = None
        actual_price = None
        actual_change_pct = None

        for model in detail["models"]:
            point = next((item for item in model.get("points", []) if item["forecast_hour"] == hour), None)
            points_by_model.append((model, point))
            if point and not target_display:
                target_display = point.get("target_time_display")
            if point and actual_price is None and point.get("actual_price") is not None:
                actual_price = point["actual_price"]
                actual_change_pct = point["actual_change_pct"]

        header = f"+{hour}h {_compact_target_label(target_display)}"
        if actual_price is not None and actual_change_pct is not None:
            header += f" · real ${actual_price:.2f} ({actual_change_pct:+.2f}%)"
        else:
            header += " · real pending"
        lines.append(header)

        segments = []
        for model, point in points_by_model:
            alias = _model_alias(model)
            if point is None:
                if model["status"] == "failed":
                    segments.append(f"{alias} fail")
                else:
                    segments.append(f"{alias} -")
                continue

            segment = f"{alias} ${point['predicted_price']:.2f} ({point['predicted_change_pct']:+.2f}%)"
            if point.get("accuracy_score") is not None:
                direction_mark = "✅" if point.get("direction_match") else "❌"
                segment += f" {point['accuracy_score']:.0f}%{direction_mark}"
            segments.append(segment)
        lines.append(" · ".join(segments))

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3720].rstrip() + "\n...\nОставшаяся часть скрыта. Для полной matrix откройте кнопку Matrix."
    return text


async def answer_callback_early(call: CallbackQuery, text: str = "⏳ Прогноз запускается") -> None:
    try:
        await call.answer(text, show_alert=False)
    except TelegramBadRequest as exc:
        log.info("Prediction callback %s expired before ack: %s", call.data, exc)
    except Exception as exc:
        log.warning("Prediction callback %s ack failed: %s", call.data, exc)


async def send_prediction_menu(message: Message) -> None:
    items = await get_recent_prediction_requests(limit=4)
    await message.answer(
        format_prediction_menu_text(items),
        reply_markup=build_prediction_menu_keyboard(),
    )


@router.message(Command("predictions"))
async def cmd_predictions(message: Message):
    await send_prediction_menu(message)


@router.callback_query(F.data == "prediction_menu")
async def cb_prediction_menu(call: CallbackQuery):
    await answer_callback_early(call, "Обновляю прогнозы")
    items = await get_recent_prediction_requests(limit=4)
    await call.message.edit_text(
        format_prediction_menu_text(items),
        reply_markup=build_prediction_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("prediction_symbol:"))
async def cb_prediction_symbol(call: CallbackQuery):
    symbol = call.data.split(":", 1)[1]
    await answer_callback_early(call, f"{symbol.upper()} выбран")
    await call.message.edit_text(
        f"🔮 <b>{symbol.upper()}</b>\n\nВыберите горизонт прогноза.",
        reply_markup=build_prediction_horizon_keyboard(symbol),
    )


@router.callback_query(F.data.startswith("prediction_latest:"))
async def cb_prediction_latest(call: CallbackQuery):
    symbol = call.data.split(":", 1)[1]
    await answer_callback_early(call, "Загружаю последний прогноз")
    detail = await get_latest_prediction_request(symbol=symbol)
    if detail is None:
        await call.message.edit_text(
            f"🔮 <b>{symbol.upper()}</b>\n\nДля этой монеты ещё нет прогнозов.",
            reply_markup=build_prediction_horizon_keyboard(symbol),
        )
        return

    await call.message.edit_text(
        format_prediction_detail(detail),
        reply_markup=build_prediction_detail_keyboard(detail),
    )


@router.callback_query(F.data.startswith("prediction_status:"))
async def cb_prediction_status(call: CallbackQuery):
    request_id = int(call.data.split(":", 1)[1])
    await answer_callback_early(call, "Обновляю статус")
    detail = await get_prediction_request_detail(request_id)
    if detail is None:
        await call.message.edit_text(
            f"❌ Прогноз #{request_id} не найден.",
            reply_markup=build_prediction_menu_keyboard(),
        )
        return

    await call.message.edit_text(
        format_prediction_detail(detail),
        reply_markup=build_prediction_detail_keyboard(detail),
    )


@router.callback_query(F.data.startswith("prediction_run:"))
async def cb_prediction_run(call: CallbackQuery):
    _, symbol, raw_horizon = call.data.split(":")
    horizon_hours = int(raw_horizon)
    await answer_callback_early(call)
    await call.message.edit_text(f"⏳ <i>Строю прогноз {symbol.upper()} на {horizon_hours}h...</i>")

    requested_by = call.from_user.username or f"telegram:{call.from_user.id}"
    try:
        detail = await create_prediction_request(symbol=symbol, horizon_hours=horizon_hours, requested_by=requested_by)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        await call.message.edit_text(
            f"❌ Не удалось запустить прогноз: HTTP {exc.response.status_code}\n<code>{html.escape(body)}</code>",
            reply_markup=build_prediction_horizon_keyboard(symbol),
        )
        return
    except Exception as exc:
        await call.message.edit_text(
            f"❌ Не удалось запустить прогноз: {html.escape(str(exc))}",
            reply_markup=build_prediction_horizon_keyboard(symbol),
        )
        return

    await call.message.edit_text(
        format_prediction_detail(detail),
        reply_markup=build_prediction_detail_keyboard(detail),
    )
