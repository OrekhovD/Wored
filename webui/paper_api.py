"""Interactive paper-trading flow with a durable prototype ledger.

The execution price and liquidation formula remain explicit TD-02 placeholders.
When Postgres is available, settings, state and every mutation are persisted with
optimistic revisions and an append-only audit event. The verified perpetual
market adapter replaces placeholder execution in TD-04.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

try:
    from .paper_store import StoredPaperState, get_paper_store, paper_owner_key
    from .paper_market import PerpetualSnapshot, paper_market_mode, resolve_market_snapshot
    from .paper_learning import build_learning_review
except ImportError:  # uvicorn runs with /app as the import root inside the container
    from paper_store import StoredPaperState, get_paper_store, paper_owner_key
    from paper_market import PerpetualSnapshot, paper_market_mode, resolve_market_snapshot
    from paper_learning import build_learning_review

router = APIRouter(prefix="/api", tags=["paper-trading"])

# T06: paper_trading domain service bridge
# When paper_trading.adapter is available, routes delegate to it.
# Otherwise, fallback to the legacy paper_store implementation.
_pt_available = False
try:
    from paper_trading.adapter import (
        get_current_state as _pt_get_state,
        start_day as _pt_start_day,
        finish_day as _pt_finish_day,
        get_command_status as _pt_cmd_status,
        owner_id_from_webui as _pt_owner_id,
    )
    _pt_available = True
except ImportError:
    pass


def _use_pt_domain(request: Request) -> bool:
    """Use the PostgreSQL domain bridge only when its runtime dependency exists."""
    return _pt_available and getattr(request.app.state, "pg_pool", None) is not None


DEFAULT_SETTINGS = {
    "end_time_local": "21:00",
    "timezone": "Asia/Bangkok",
    "opening_capital": "1000",
    "max_daily_loss_usdt": "50",
    "max_risk_per_order_usdt": "10",
    "max_leverage": 10,
}
FEE_RATE = Decimal("0.0006")


def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field}: неверное число") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise HTTPException(status_code=422, detail=f"{field}: требуется положительное число")
    return result


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") or "0"


def _settings(row: StoredPaperState) -> Dict[str, Any]:
    return {**DEFAULT_SETTINGS, **row.settings}


def _accounts(settings: Dict[str, Any]) -> list[Dict[str, Any]]:
    capital = str(settings["opening_capital"])
    loss = str(settings["max_daily_loss_usdt"])
    return [
        {"id": "proto-manual", "kind": "manual", "currency": "USDT", "cash": capital,
         "opening_equity": capital, "available_margin": capital, "equity": capital,
         "realized_net": "0", "unrealized": "0", "total_costs": "0",
         "loss_budget": loss, "open_positions": 0, "closed_trades": 0},
        {"id": "proto-auto", "kind": "auto", "currency": "USDT", "cash": capital,
         "opening_equity": capital, "available_margin": capital, "equity": capital,
         "realized_net": "0", "unrealized": "0", "total_costs": "0",
         "loss_budget": loss, "open_positions": 0, "closed_trades": 0},
    ]


def _event(event_type: str, message: str) -> Dict[str, Any]:
    return {"id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type, "message": message}


async def _save(request: Request, row: StoredPaperState, state: Dict[str, Any] | None,
                event_type: str, payload: Dict[str, Any],
                idempotency_key: str | None = None) -> Dict[str, Any] | None:
    if state is not None:
        state["events"] = state.get("events", [])[-8:]
    store = get_paper_store(request)
    return await store.save(
        paper_owner_key(request), settings=_settings(row), state=state,
        expected_revision=row.revision, event_type=event_type, payload=payload,
        idempotency_key=idempotency_key,
    )


async def _load(request: Request) -> StoredPaperState:
    return await get_paper_store(request).load(paper_owner_key(request))


def _empty_payload(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ui_schema_version": 1,
        "mode": "prototype",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "day": None,
        "accounts": _accounts(settings),
        "risk_policy": {
            "max_daily_loss_usdt": settings["max_daily_loss_usdt"],
            "max_risk_per_order_usdt": settings["max_risk_per_order_usdt"],
            "max_total_exposure": _money(_decimal(settings["opening_capital"], "capital") / 2),
            "max_leverage": settings["max_leverage"],
        },
        "fresh": True,
        "capabilities": {"can_start": True, "reason_code": None},
        "reason_code": None,
        "next_action": "start",
        "end_time_local": settings["end_time_local"],
        "timezone": settings["timezone"],
        "auto_state": "observing",
        "auto_state_label": "Ожидает запуска дня",
        "positions": [],
        "closed_positions": [],
        "orders": [],
        "events": [],
    }


async def _current_payload(request: Request) -> Dict[str, Any]:
    row = await _load(request)
    payload = row.state or _empty_payload(_settings(row))
    market_mode = paper_market_mode(request)
    payload["market_mode"] = market_mode
    payload["mode"] = "prototype" if market_mode == "demo" else "paper-live-data"
    if market_mode == "demo":
        payload["fresh"] = False
        payload["reason_code"] = "Тестовая цена; реальные perpetual-данные не подключены"
    else:
        try:
            market = await resolve_market_snapshot(request, "btcusdt")
            payload["market"] = market.public_dict()
            payload["fresh"] = True
            payload["reason_code"] = None
        except HTTPException as exc:
            payload["fresh"] = False
            payload["reason_code"] = str(exc.detail)
            if payload.get("day") is None:
                payload.setdefault("capabilities", {})["can_start"] = False
            elif payload.get("day", {}).get("state") == "active":
                payload["auto_state_label"] = f"Рыночные данные заблокированы: {exc.detail}"
    payload["storage_mode"] = (
        "postgres" if getattr(request.app.state, "pg_pool", None) is not None else "memory"
    )
    return payload


def _account(state: Dict[str, Any], account_id: str) -> Dict[str, Any]:
    account = next((item for item in state.get("accounts", []) if item["id"] == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    return account


def _require_active(state: Dict[str, Any]) -> None:
    if not state.get("day") or state["day"].get("state") != "active":
        raise HTTPException(status_code=409, detail="Торговый день не активен")


def _preview(
    state: Dict[str, Any],
    account_id: str,
    body: Dict[str, Any],
    market: PerpetualSnapshot,
) -> Dict[str, Any]:
    _require_active(state)
    account = _account(state, account_id)
    if account["kind"] != "manual":
        raise HTTPException(status_code=403, detail="Ручная заявка доступна только на ручном счёте")
    side = str(body.get("side", "buy")).lower()
    if side not in {"buy", "sell"}:
        raise HTTPException(status_code=422, detail="Сторона должна быть buy или sell")
    order_type = str(body.get("order_type", "market")).lower()
    if order_type not in {"market", "limit"}:
        raise HTTPException(status_code=422, detail="Тип должен быть market или limit")
    risk = _decimal(body.get("risk"), "Риск")
    max_risk = _decimal(state["risk_policy"]["max_risk_per_order_usdt"], "Лимит риска")
    if risk > max_risk:
        raise HTTPException(status_code=422, detail="Риск превышает лимит одной заявки")
    entry = market.entry_price(side) if order_type == "market" else _decimal(body.get("price"), "Цена Limit")
    stop = _decimal(body.get("stop_price"), "Стоп")
    if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
        raise HTTPException(status_code=422, detail="Стоп должен быть ниже Long и выше Short")
    quantity = risk / abs(entry - stop)
    notional = quantity * entry
    max_leverage = int(state["risk_policy"]["max_leverage"])
    try:
        leverage = int(body.get("leverage", min(10, max_leverage)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Плечо должно быть целым числом") from exc
    if not 1 <= leverage <= max_leverage:
        raise HTTPException(
            status_code=422,
            detail=f"Плечо должно быть от 1x до лимита {max_leverage}x",
        )
    reserved_margin = notional / Decimal(leverage)
    if reserved_margin > _decimal(account["available_margin"], "Доступная маржа"):
        raise HTTPException(status_code=422, detail="Недостаточно доступной маржи")
    entry_fee = notional * FEE_RATE
    target = body.get("take_profit")
    target_price = _decimal(target, "Цель") if target not in (None, "") else None
    if target_price is not None and ((side == "buy" and target_price <= entry) or
                                     (side == "sell" and target_price >= entry)):
        raise HTTPException(status_code=422, detail="Цель должна быть выше Long и ниже Short")
    exit_reference = target_price or stop
    exit_fee = quantity * exit_reference * FEE_RATE
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    net_at_sl = sign * quantity * (stop - entry) - entry_fee - quantity * stop * FEE_RATE
    net_at_tp = None
    if target_price is not None:
        net_at_tp = sign * quantity * (target_price - entry) - entry_fee - exit_fee
    fee_per_unit = (entry_fee + quantity * entry * FEE_RATE) / quantity
    break_even = entry + fee_per_unit if side == "buy" else entry - fee_per_unit
    liquidation = entry * (Decimal(1) - Decimal(1) / Decimal(leverage)) if side == "buy" else entry * (Decimal(1) + Decimal(1) / Decimal(leverage))
    return {
        "allowed": True, "reasons": [], "account_label": "Ручной счёт",
        "quantity": _money(quantity), "notional": _money(notional),
        "reserved_margin": _money(reserved_margin), "entry_fee": _money(entry_fee),
        "estimated_exit_fee": _money(exit_fee),
        "funding_status": "known" if market.funding_rate is not None else "unknown",
        "funding_rate": _money(market.funding_rate) if market.funding_rate is not None else None,
        "break_even": _money(break_even),
        "estimated_net_at_tp": _money(net_at_tp) if net_at_tp is not None else None,
        "estimated_net_at_sl": _money(net_at_sl), "liquidation_price": _money(liquidation),
        "liquidation_quality": "simplified", "price_as_of": market.source_at,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
        "entry_price": _money(entry), "side": side, "order_type": order_type,
        "leverage": leverage,
        "market": market.public_dict(),
    }


@router.get("/trading-day/current")
async def get_current_day(request: Request) -> JSONResponse:
    # T06: try paper_trading domain service first
    if _use_pt_domain(request):
        try:
            owner_id = _pt_owner_id("admin")  # TODO: get from session
            state = await _pt_get_state(owner_id)
            if state.get("ok") and state.get("day"):
                return JSONResponse(state)
            # No active day in paper_trading — return pre-start state
            if state.get("ok") and not state.get("day"):
                return JSONResponse({
                    "ui_schema_version": 1,
                    "day": None,
                    "accounts": [
                        {"id": "proto-manual", "kind": "manual", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
                        {"id": "proto-auto", "kind": "auto", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
                    ],
                    "risk_policy": {"max_daily_loss_usdt": "50", "max_risk_per_order_usdt": "10", "max_total_exposure": "500", "max_leverage": 10},
                    "fresh": True,
                    "capabilities": {"can_start": True, "reason_code": None},
                    "next_action": "start",
                    "end_time_local": "21:00",
                    "timezone": "Asia/Bangkok",
                    "auto_state": "observing",
                    "auto_state_label": "Наблюдает за рынком",
                    "engine_status": "running",
                    "engine_heartbeat_age": 2,
                })
        except Exception:
            pass  # fall through to legacy
    return JSONResponse(await _current_payload(request))


@router.post("/trading-day/settings")
async def save_settings(request: Request, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    if (row.state or {}).get("day", {}).get("state") == "active":
        raise HTTPException(status_code=409, detail="Завершите день перед изменением условий")
    timezone_name = str(body.get("timezone", "Asia/Bangkok"))
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс") from exc
    end_time = str(body.get("end_time_local", "21:00"))
    try:
        hour, minute = (int(part) for part in end_time.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Время должно быть в формате HH:MM") from exc
    capital = _decimal(body.get("opening_capital"), "Начальный капитал")
    loss = _decimal(body.get("max_daily_loss_usdt"), "Дневной лимит")
    risk = _decimal(body.get("max_risk_per_order_usdt"), "Риск заявки")
    try:
        leverage = int(body.get("max_leverage"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Плечо должно быть целым числом") from exc
    if loss > capital or risk > loss or not 1 <= leverage <= 100:
        raise HTTPException(status_code=422, detail="Проверьте капитал, лимиты и плечо 1–100x")
    settings = {"end_time_local": end_time, "timezone": timezone_name,
                "opening_capital": _money(capital), "max_daily_loss_usdt": _money(loss),
                "max_risk_per_order_usdt": _money(risk), "max_leverage": leverage}
    response = {"ok": True, "settings": settings}
    await store.save(
        owner_key, settings=settings, state=None, expected_revision=row.revision,
        event_type="settings_saved", payload=response,
    )
    return JSONResponse(response)


@router.post("/trading-day/start")
async def start_day(request: Request, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    # T06: try paper_trading domain service first
    if _use_pt_domain(request):
        try:
            owner_id = _pt_owner_id("admin")
            result = await _pt_start_day(owner_id=owner_id, mode="baseline_auto")
            if result.get("ok"):
                return JSONResponse({
                    "command_id": result.get("commands", [""])[0] if result.get("commands") else "",
                    "status": "accepted",
                    "day_id": result.get("day_id"),
                    "start_at": result.get("start_at"),
                    "end_at": result.get("end_at"),
                    "accounts": [
                        {"id": result.get("manual_account_id"), "kind": "manual", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
                        {"id": result.get("auto_account_id"), "kind": "auto", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
                    ],
                    "auto_state": "observing",
                    "auto_state_label": "Наблюдает за рынком",
                    "fresh": True,
                }, status_code=202)
        except Exception:
            pass  # fall through to legacy
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    idempotency_key = str(body.get("idempotency_key") or uuid4())
    repeated = await store.find_result(owner_key, idempotency_key)
    if repeated is not None:
        return JSONResponse(repeated, status_code=200)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    if row.state and row.state.get("day", {}).get("state") == "active":
        return JSONResponse(row.state, status_code=200)
    settings = _settings(row)
    market_mode = paper_market_mode(request)
    opening_market = await resolve_market_snapshot(request, "btcusdt")
    zone = ZoneInfo(settings["timezone"])
    local_now = datetime.now(zone)
    hour, minute = (int(part) for part in settings["end_time_local"].split(":"))
    local_end = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_end <= local_now:
        local_end += timedelta(days=1)
    day_id = "proto-day-" + uuid4().hex[:12]
    state = {
        "ui_schema_version": 1,
        "mode": "prototype" if market_mode == "demo" else "paper-live-data",
        "market_mode": market_mode,
        "market": opening_market.public_dict(),
        "server_time": datetime.now(timezone.utc).isoformat(),
        "day": {"id": day_id, "local_date": local_now.date().isoformat(), "state": "active",
                "start_at": datetime.now(timezone.utc).isoformat(),
                "end_at": local_end.astimezone(timezone.utc).isoformat()},
        "accounts": _accounts(settings),
        "risk_policy": {"max_daily_loss_usdt": settings["max_daily_loss_usdt"],
                        "max_risk_per_order_usdt": settings["max_risk_per_order_usdt"],
                        "max_total_exposure": _money(_decimal(settings["opening_capital"], "capital") / 2),
                        "max_leverage": settings["max_leverage"]},
        "fresh": market_mode == "live", "capabilities": {"can_start": False},
        "reason_code": (
            None if market_mode == "live"
            else "Тестовая цена; реальные perpetual-данные не подключены"
        ),
        "next_action": "trade", "end_time_local": settings["end_time_local"],
        "timezone": settings["timezone"], "auto_state": "observing",
        "auto_state_label": "Наблюдает за рынком; подходящего входа пока нет",
        "positions": [], "closed_positions": [], "orders": [],
        "events": [_event("day_started", "День запущен для ручного счёта и автомата")],
    }
    response = {"command_id": str(uuid4()), "status": "completed", **state}
    replay = await _save(request, row, state, "day_started", response, idempotency_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200)
    return JSONResponse(response, status_code=202)


@router.post("/trading-day/{day_id}/automation")
async def automation(request: Request, day_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    idempotency_key = str(body.get("idempotency_key") or uuid4())
    repeated = await store.find_result(owner_key, idempotency_key)
    if repeated is not None:
        return JSONResponse(repeated, status_code=200)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    state = row.state or _empty_payload(_settings(row))
    _require_active(state)
    if state["day"]["id"] != day_id:
        raise HTTPException(status_code=404, detail="Торговый день не найден")
    action = str(body.get("action", ""))
    labels = {"pause": "Новые входы приостановлены; защита позиций продолжает работать",
              "resume": "Наблюдает за рынком; подходящего входа пока нет",
              "close_auto": "Автомат остановлен; открытых позиций нет"}
    if action not in labels:
        raise HTTPException(status_code=422, detail="Неизвестная команда автомата")
    state["auto_state"] = "paused" if action in {"pause", "close_auto"} else "observing"
    state["auto_state_label"] = labels[action]
    state["events"].append(_event("automation", labels[action]))
    response = {"command_id": str(uuid4()), "status": "completed", "action": action,
                "auto_state": state["auto_state"]}
    replay = await _save(request, row, state, "automation_changed", response, idempotency_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200)
    return JSONResponse(response, status_code=202)


@router.post("/paper/accounts/{account_id}/orders/preview")
async def preview_order(request: Request, account_id: str,
                        body: Dict[str, Any] = Body(...)) -> JSONResponse:
    instrument = str(body.get("instrument", "btcusdt"))
    market = await resolve_market_snapshot(request, instrument)
    return JSONResponse(_preview(await _current_payload(request), account_id, body, market))


@router.post("/paper/accounts/{account_id}/orders")
async def place_order(request: Request, account_id: str,
                      body: Dict[str, Any] = Body(...)) -> JSONResponse:
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    idempotency_key = str(body.get("idempotency_key") or uuid4())
    repeated = await store.find_result(owner_key, idempotency_key)
    if repeated is not None:
        return JSONResponse(repeated, status_code=200)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    state = row.state or _empty_payload(_settings(row))
    instrument = str(body.get("instrument", "btcusdt"))
    market = await resolve_market_snapshot(request, instrument)
    preview = _preview(state, account_id, body, market)
    if preview["order_type"] == "limit":
        limit_price = _decimal(preview["entry_price"], "Цена Limit")
        marketable = (
            preview["side"] == "buy" and limit_price >= market.ask
        ) or (
            preview["side"] == "sell" and limit_price <= market.bid
        )
        if not marketable:
            raise HTTPException(
                status_code=422,
                detail="Нерыночная Limit-заявка требует отдельного движка ожидающих ордеров",
            )
    account = _account(state, account_id)
    order_id = str(uuid4())
    position_id = str(uuid4())
    entry_fee = _decimal(preview["entry_fee"], "Комиссия")
    margin = _decimal(preview["reserved_margin"], "Маржа")
    cash = _decimal(account["cash"], "Баланс") - entry_fee
    account["cash"] = _money(cash)
    account["equity"] = _money(cash)
    account["available_margin"] = _money(cash - margin)
    account["realized_net"] = _money(-entry_fee)
    account["total_costs"] = _money(entry_fee)
    account["open_positions"] = int(account["open_positions"]) + 1
    position = {"id": position_id, "account_id": account_id, "order_id": order_id,
                "symbol": instrument.upper(),
                "side": "long" if preview["side"] == "buy" else "short", "origin": "manual",
                 "entry_price": preview["entry_price"], "quantity": preview["quantity"],
                 "reserved_margin": preview["reserved_margin"], "entry_fee": preview["entry_fee"],
                 "leverage": preview["leverage"], "stop_price": body.get("stop_price"),
                 "take_profit": body.get("take_profit"),
                 "opened_at": datetime.now(timezone.utc).isoformat(),
                 "unrealized_net": _money(-entry_fee), "status": "open",
                 "market_snapshot": market.public_dict()}
    state["positions"].append(position)
    state["events"].append(_event("manual_fill", f"Ручная позиция {position['side']} открыта"))
    response = {"command_id": str(uuid4()), "status": "completed",
                "order_id": order_id, "position_id": position_id,
                "account_kind": "manual", "amount": _money(-entry_fee)}
    replay = await _save(request, row, state, "manual_order_filled", response, idempotency_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200)
    return JSONResponse(response, status_code=202)


def _close_position(
    state: Dict[str, Any],
    position: Dict[str, Any],
    market: PerpetualSnapshot,
    *,
    exit_reason: str,
) -> Dict[str, str]:
    account = _account(state, position["account_id"])
    quantity = _decimal(position["quantity"], "Количество")
    entry = _decimal(position["entry_price"], "Цена входа")
    close_price = market.close_price(position["side"])
    sign = Decimal(1) if position["side"] == "long" else Decimal(-1)
    gross_pnl = sign * quantity * (close_price - entry)
    close_fee = quantity * close_price * FEE_RATE
    net_change = gross_pnl - close_fee
    cash = _decimal(account["cash"], "Баланс") + net_change
    account["cash"] = _money(cash)
    account["equity"] = _money(cash)
    account["available_margin"] = _money(cash)
    account["realized_net"] = _money(
        _decimal(account["realized_net"], "PnL", positive=False) + net_change
    )
    account["total_costs"] = _money(_decimal(account["total_costs"], "Расходы", positive=False) + close_fee)
    account["open_positions"] = max(0, int(account["open_positions"]) - 1)
    account["closed_trades"] = int(account.get("closed_trades", 0)) + 1
    state["positions"] = [item for item in state["positions"] if item["id"] != position["id"]]
    closed_trade = {
        **position,
        "status": "closed",
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "close_price": _money(close_price),
        "close_fee": _money(close_fee),
        "gross_pnl": _money(gross_pnl),
        "net_pnl": _money(net_change - _decimal(position["entry_fee"], "Комиссия входа")),
        "total_fees": _money(close_fee + _decimal(position["entry_fee"], "Комиссия входа")),
        "exit_reason": exit_reason,
        "close_market_snapshot": market.public_dict(),
    }
    state.setdefault("closed_positions", []).append(closed_trade)
    source_label = "тестовой" if market.mode == "demo" else "HTX bid/ask"
    state["events"].append(_event("position_closed", f"Позиция закрыта по {source_label} цене"))
    return {
        "close_price": _money(close_price),
        "gross_pnl": _money(gross_pnl),
        "close_fee": _money(close_fee),
        "net_change": _money(net_change),
    }


@router.post("/paper/positions/{position_id}/actions")
async def position_action(request: Request, position_id: str,
                          body: Dict[str, Any] = Body(...)) -> JSONResponse:
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    idempotency_key = str(body.get("idempotency_key") or uuid4())
    repeated = await store.find_result(owner_key, idempotency_key)
    if repeated is not None:
        return JSONResponse(repeated, status_code=200)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    state = row.state or _empty_payload(_settings(row))
    _require_active(state)
    if body.get("action") != "close":
        raise HTTPException(status_code=422, detail="В прототипе доступно только полное закрытие")
    position = next((item for item in state["positions"] if item["id"] == position_id), None)
    if not position:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    market = await resolve_market_snapshot(request, position["symbol"])
    close_result = _close_position(state, position, market, exit_reason="manual_close")
    response = {"command_id": str(uuid4()), "status": "completed", "action": "close",
                "account_kind": "manual", "market": market.public_dict(), **close_result}
    replay = await _save(request, row, state, "position_closed", response, idempotency_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200)
    return JSONResponse(response, status_code=202)


def _report_account(
    account: Dict[str, Any],
    closed_trades: list[Dict[str, Any]],
) -> Dict[str, Any]:
    opening = _decimal(account["opening_equity"], "Начальный капитал")
    closing = _decimal(account["cash"], "Баланс")
    net = closing - opening
    trades = [item for item in closed_trades if item["account_id"] == account["id"]]
    net_values = [_decimal(item["net_pnl"], "Net", positive=False) for item in trades]
    gross_values = [_decimal(item["gross_pnl"], "Gross", positive=False) for item in trades]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value < 0]
    gross_profit = sum((value for value in gross_values if value > 0), Decimal(0))
    gross_loss = abs(sum((value for value in gross_values if value < 0), Decimal(0)))
    return {"opening_equity": _money(opening), "closing_equity": _money(closing),
            "realized_net": _money(net), "daily_return": _money(net / opening),
            "trades_count": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate": _money(Decimal(len(wins)) / Decimal(len(trades))) if trades else None,
            "expectancy": _money(sum(net_values, Decimal(0)) / Decimal(len(trades))) if trades else None,
            "profit_factor": _money(gross_profit / gross_loss) if gross_loss else None,
            "gross_pnl": _money(sum(gross_values, Decimal(0))),
            "total_fees": account["total_costs"]}


@router.post("/trading-day/{day_id}/finish")
async def finish_day(request: Request, day_id: str,
                     body: Dict[str, Any] = Body(...)) -> JSONResponse:
    # T06: try paper_trading domain service first
    if _use_pt_domain(request):
        try:
            owner_id = _pt_owner_id("admin")
            result = await _pt_finish_day(owner_id)
            if result.get("ok"):
                return JSONResponse({
                    "command_id": result.get("command_id"),
                    "status": "accepted",
                }, status_code=202)
        except Exception:
            pass
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    idempotency_key = str(body.get("idempotency_key") or uuid4())
    repeated = await store.find_result(owner_key, idempotency_key)
    if repeated is not None:
        return JSONResponse(repeated, status_code=200)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    state = row.state or _empty_payload(_settings(row))
    _require_active(state)
    if state["day"]["id"] != day_id:
        raise HTTPException(status_code=404, detail="Торговый день не найден")
    close_batch: list[tuple[Dict[str, Any], PerpetualSnapshot]] = []
    for position in list(state["positions"]):
        close_batch.append(
            (position, await resolve_market_snapshot(request, position["symbol"]))
        )
    for position, market in close_batch:
        _close_position(state, position, market, exit_reason="end_of_day")
    state["day"]["state"] = "reconciled"
    state["auto_state"] = "stopped"
    manual = next(item for item in state["accounts"] if item["kind"] == "manual")
    auto = next(item for item in state["accounts"] if item["kind"] == "auto")
    closed_trades = state.get("closed_positions", [])
    manual_report = _report_account(manual, closed_trades)
    auto_report = _report_account(auto, closed_trades)
    report_quality = "live" if paper_market_mode(request) == "live" else "prototype"
    state["report"] = {"version": 1, "quality": report_quality,
                       "manual": manual_report, "auto": auto_report,
                       "closed_trades": closed_trades,
                       "learning": build_learning_review(
                           manual_report, auto_report, closed_trades
                       )}
    state["events"].append(_event("day_finished", "Демонстрационный день завершён и сверён"))
    response = {"command_id": str(uuid4()), "status": "completed",
                "report": state["report"]}
    replay = await _save(request, row, state, "day_reconciled", response, idempotency_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200)
    return JSONResponse(response, status_code=202)


@router.post("/trading-day/next")
async def next_day(request: Request) -> JSONResponse:
    store = get_paper_store(request)
    owner_key = paper_owner_key(request)
    async with store.lock(owner_key):
        row = await store.load(owner_key)
    response = {"ok": True, "next_action": "start"}
    await _save(request, row, None, "next_day_prepared", response)
    return JSONResponse(response)


@router.get("/paper/commands/{command_id}")
async def get_command(request: Request, command_id: str) -> JSONResponse:
    # T06: try paper_trading domain service first
    if _pt_available:
        try:
            result = await _pt_cmd_status(command_id)
            if result.get("ok"):
                return JSONResponse(result)
        except Exception:
            pass
    return JSONResponse({"command_id": command_id, "status": "completed", "result": None})
