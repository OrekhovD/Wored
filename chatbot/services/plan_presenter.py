"""Bounded HTML for Telegram; model output is always escaped."""
from __future__ import annotations

from html import escape

from services.plan_contract import entry_risk, plan_error

REASONS = {
    "stop_beyond_liquidation": "ликвидация раньше стопа",
    "net_reward_risk_below_one": "чистый R:R меньше 1",
    "target_net_profit_not_met": "не достигнут порог чистой прибыли",
    "invalid_price_geometry": "несогласованные цены входа/SL/TP",
    "unsupported_confirmation_rule": "неподдерживаемое подтверждение",
    "direction_mismatch": "направление запрещено настройками",
    "budget_share_out_of_policy": "доля маржи вне лимитов",
}
CONFIRMATIONS = {
    "close_above_zone_on_1m_and_rsi_gt_50": "закрытие 1m выше зоны и RSI > 50",
    "close_below_zone_on_1m_and_rsi_lt_50": "закрытие 1m ниже зоны и RSI < 50",
    "rsi_gt_50": "касание зоны и RSI закрытой 1m > 50",
    "rsi_lt_50": "касание зоны и RSI закрытой 1m < 50",
}


def text(value, limit=250):
    value = str(value if value is not None else "нет данных")
    cut = min(len(value), limit)
    while len(escape(value[:cut])) + (1 if cut < len(value) else 0) > limit:
        cut -= 1
    return escape(value[:cut]) + ("…" if cut < len(value) else "")


def fmt(value):
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except (ValueError, TypeError):
        return "нет данных"


def render_plan(session: dict, plan: dict, rows: list[dict]) -> str:
    error = plan_error(plan, session)
    status = {"accepted": "Ожидание подтверждения входа", "no_trade": "Наблюдение — без входов",
              "rejected": "Входы отклонены проверкой риска"}.get(str(plan.get("validation_status")), "Старый план — не проверен")
    if error and plan.get("validation_status") not in {"no_trade", "rejected"}:
        status = "Вход заблокирован: " + error
    if session.get("status") in {"paused", "in_position", "cooldown", "completed", "stopped", "failed"}:
        status = "Состояние сессии: " + session["status"] + "; новые входы не разрешены"
    context = plan.get("market_snapshot", {})
    header = [f"📋 <b>План v{text(plan.get('version'), 10)} · {text(session.get('symbol'), 20)} · Симуляция</b>",
              f"Сессия: {text(str(session.get('id', ''))[:8], 8)} · {text(status, 110)}",
              f"Данные: {text(context.get('timestamp'), 32)}",
              f"Действует до: {text(plan.get('valid_until'), 32)}",
              f"Модель: {text(plan.get('model_used'), 60)} · режим: {text(plan.get('market_regime'), 20)}",
              f"Бюджет: {fmt(session.get('initial_budget_usdt'))} USDT · {text(session.get('risk_mode'), 15)}"]
    narrative = [f"<b>Картина рынка</b>\n{text(plan.get('thesis'), 450)}",
                 f"<b>Основной сценарий</b>\n{text(plan.get('primary_scenario'), 220)}",
                 f"<b>Альтернатива</b>\n{text(plan.get('alternative_scenario'), 180)}",
                 f"<b>Когда не торговать</b>\n{text(plan.get('no_trade_condition'), 220)}"]
    candidates = rows or plan.get("entries", [])
    candidates = candidates + plan.get("rejected_entries", [])
    blocks = []
    for i, e in enumerate(candidates[:3], 1):
        risk = entry_risk(e, session)
        tp = e.get("take_profit", e.get("take_profit_json", []))
        import json
        if isinstance(tp, str):
            try:
                tp = json.loads(tp)
            except ValueError:
                tp = []
        if not isinstance(tp, list):
            tp = []
        block = [f"<b>{i}. {text(e.get('side'), 8).upper()} · {text(e.get('status', 'кандидат'), 15)}</b>",
                 f"Зона {fmt(e.get('entry_zone_from'))}–{fmt(e.get('entry_zone_to'))}",
                 f"SL {fmt(e.get('stop_loss'))} · отмена {fmt(e.get('invalidation_price'))}",
                 "Цели: " + " / ".join(fmt(v) for v in tp[:3]),
                 f"Подтверждение: {text(CONFIRMATIONS.get(str(e.get('confirmation_rule')), e.get('confirmation_rule')), 70)}",
                 f"Плечо {text(e.get('recommended_leverage'), 4)}× · маржа {fmt(risk.get('margin_usdt'))} USDT · объём {fmt(risk.get('notional_usdt'))} USDT",
                 f"Риск SL: {fmt(risk.get('net_loss_sl_usdt'))} USDT ({fmt(risk.get('risk_pct'))}%) · TP1 net: {fmt(risk.get('net_profit_tp1_usdt'))} USDT",
                 f"Net R:R {fmt(risk.get('net_reward_risk'))} · ликвидация {fmt(risk.get('liquidation_price'))}"]
        if risk["errors"]:
            block.append("⛔ " + text(", ".join(REASONS.get(v, v) for v in risk["errors"]), 150))
        block.append("Основание: " + text(e.get("reason_code"), 100))
        blocks.append("\n".join(block))
    footer = ["<b>Сопровождение</b>: полный выход на TP1; TP2 — ориентир. Без частичных выходов и trailing.",
              f"Лимит позиции: {text(session.get('max_trade_duration_minutes', 15), 5)} мин. Пересмотр плана через час.",
              "Расчёт isolated-v2: комиссия 0,06% и slippage 2 bps на каждой стороне. Funding/стакан не учтены. План не подтверждает сделку.",
              "Текст «когда не торговать» — условие аналитика. Автомат проверяет формальные правила заявок."]
    parts = header + narrative + blocks + footer
    # Never truncate an HTML entity/tag or silently drop order protection/economics.
    while len("\n\n".join(parts)) > 4000 and narrative:
        removed = narrative.pop(0)
        parts.remove(removed)
    return "\n\n".join(parts)
