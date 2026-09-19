"""Explain execution decisions independently of a session's administrative status."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape

from services.plan_contract import utc

REASONS = {
    "entry_not_confirmed": "сигнал входа не подтверждён закрытой свечой",
    "zone_not_reached": "цена ещё не достигла зоны входа",
    "closed_candle_required": "нет закрытой свечи или её индикаторов",
    "entry_risk_rejected": "расчёт риска отклонил вход",
    "entry_rejected_cost_filter": "чистая прибыль не покрывает заданный порог",
    "invalidation_in_same_candle": "сценарий отменён движением цены",
    "plan_expired": "срок плана истёк — нужен пересмотр",
    "plan_not_validated": "план не разрешает входы",
    "no_trade": "аналитик не предложил допустимый вход",
    "rejected": "все кандидаты отклонены проверкой риска",
    "no_pending_entries": "нет неисполненных заявок — нужен пересмотр плана",
    "market_data_unavailable": "нет полного свежего набора рыночных данных",
    "stale_market_data": "рыночная цена устарела",
    "no_market_data": "рыночная цена отсутствует",
    "active_plan_version_missing": "активная версия плана отсутствует",
    "entry_version_mismatch": "версия заявки не совпадает с планом",
    "position_open": "открытая позиция сопровождается",
    "entry_executed": "вход исполнен в симуляции",
    "position_closed": "позиция закрыта; следующий цикл проверит возможность нового входа",
    "paused": "новые входы приостановлены пользователем или защитой",
    "cooldown": "пауза после стоп-лосса",
    "session_expired": "время сессии истекло",
    "session_not_armed": "сессия не разрешает новые входы",
    "engine_error": "ошибка цикла исполнения — требуется диагностика",
}


def key(session_id: str) -> str:
    return "session:execution:" + session_id


def entry_observation(entry: dict, candle: dict, result: dict) -> dict:
    lo, hi = float(entry["entry_zone_from"]), float(entry["entry_zone_to"])
    price = float(candle.get("execution_price", candle.get("close", 0)))
    reason = result.get("reason", "entry_executed" if result.get("executed") else "engine_error")
    touched = float(candle.get("low", price)) <= hi and float(candle.get("high", price)) >= lo
    if reason == "entry_not_confirmed" and not touched:
        reason = "zone_not_reached"
    distance = (lo - price if price < lo else price - hi if price > hi else 0.0)
    return {"entry_id": str(entry["id"]), "side": entry["side"], "reason": reason,
            "zone_from": lo, "zone_to": hi, "current_price": price,
            "distance_pct": distance / price * 100 if price > 0 else None,
            "risk_errors": result.get("risk", {}).get("errors", [])}


async def publish(redis, session_id: str, result: dict) -> None:
    payload = {"checked_at": datetime.now(timezone.utc).isoformat(),
               "reason": result.get("reason", "position_open" if result.get("open_count") else "no_pending_entries"),
               "pending_count": result.get("pending_count", 0),
               "open_count": result.get("open_count", 0), "entries": result.get("entries", [])[:3],
               "plan_version": result.get("plan_version"),
               "cooldown_until": result.get("cooldown_until")}
    await redis.set(key(session_id), json.dumps(payload, allow_nan=False), ex=180)


async def read(redis, session_id: str) -> dict | None:
    try:
        value = json.loads(await redis.get(key(session_id)) or "null")
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def render(payload: dict | None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not payload:
        return "⚠️ Исполнитель: нет подтверждения работы. Статус сессии не подтверждает торговлю."
    try:
        age = (now - utc(payload["checked_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = 9999
    if age < 0 or age > 60:
        return "⚠️ Исполнитель не подтверждает работу более минуты. Последний статус устарел."
    reason = str(payload.get("reason", "engine_error"))
    lines = [f"Проверка исполнения: {int(age)} сек. назад · заявок: {int(payload.get('pending_count', 0))}",
             escape(REASONS.get(reason, reason)[:180])]
    for e in payload.get("entries", [])[:3]:
        explanation = REASONS.get(e.get("reason"), str(e.get("reason")))
        distance = e.get("distance_pct")
        location = f" · до зоны {distance:.3f}%" if isinstance(distance, (int, float)) else ""
        lines.append(escape(f"{str(e.get('side', '')).upper()}: {explanation}{location}"))
        if e.get("risk_errors"):
            lines.append(escape(", ".join(str(v) for v in e["risk_errors"])[:220]))
    if payload.get("cooldown_until"):
        lines.append("Пауза до " + escape(str(payload["cooldown_until"])[:32]))
    return "\n".join(lines)
