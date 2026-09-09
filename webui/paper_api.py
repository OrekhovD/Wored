"""WORED Paper API — TD-02/TD-06

API endpoints for paper trading day.
Read-only current state + start/finish/automation + orders + positions.
TD-02 prototype: always returns mock data. Real DB in TD-03+ after migrations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["paper"])


@router.get("/trading-day/current")
async def get_current_day(request: Request) -> JSONResponse:
    """Current trading day state."""
    return JSONResponse({
        "ui_schema_version": 1,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "day": None,
        "accounts": [
            {"id": "proto-manual", "kind": "manual", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
            {"id": "proto-auto", "kind": "auto", "currency": "USDT", "cash": "1000", "available_margin": "1000"},
        ],
        "risk_policy": {"max_daily_loss_usdt": "50", "max_risk_per_order_usdt": "10", "max_total_exposure": "500", "max_leverage": 10},
        "fresh": True,
        "capabilities": {"can_start": True, "reason_code": None},
        "reason_code": None,
        "next_action": "start",
        "end_time_local": "21:00",
        "timezone": "Asia/Bangkok",
        "auto_state": "observing",
        "auto_state_label": "Наблюдает за рынком",
    })


@router.post("/trading-day/start")
async def start_day(request: Request, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Start a new trading day for both accounts."""
    return JSONResponse({
        "command_id": str(uuid4()),
        "status": "accepted",
        "day_id": "proto-day-" + datetime.now().strftime("%Y%m%d"),
        "day": {
            "id": "proto-day-" + datetime.now().strftime("%Y%m%d"),
            "local_date": datetime.now().strftime("%Y-%m-%d"),
            "state": "active",
            "start_at": datetime.now(timezone.utc).isoformat(),
            "end_at": "2026-09-10T14:00:00Z",
        },
        "accounts": [
            {"id": "proto-manual", "kind": "manual", "currency": "USDT",
             "cash": "1000", "available_margin": "1000", "equity": "1000",
             "realized_net": "0", "unrealized": "0", "total_costs": "0",
             "loss_budget": "50", "open_positions": 0},
            {"id": "proto-auto", "kind": "auto", "currency": "USDT",
             "cash": "1000", "available_margin": "1000", "equity": "1000",
             "realized_net": "0", "unrealized": "0", "total_costs": "0",
             "loss_budget": "50", "open_positions": 0},
        ],
        "auto_state": "observing",
        "auto_state_label": "Наблюдает за рынком",
        "positions": [],
        "events": [],
        "fresh": True,
    }, status_code=202)


@router.post("/trading-day/{day_id}/finish")
async def finish_day(request: Request, day_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Finish the trading day."""
    return JSONResponse({
        "command_id": str(uuid4()),
        "status": "accepted",
        "report": {
            "manual": {
                "realized_net": "12.50",
                "daily_return": "0.0125",
                "trades_count": 3,
                "total_fees": "0.36",
            },
            "auto": {
                "realized_net": "-5.00",
                "daily_return": "-0.005",
                "trades_count": 1,
                "total_fees": "0.12",
            },
        },
    }, status_code=202)


@router.post("/trading-day/{day_id}/automation")
async def automation(request: Request, day_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Auto controls: pause, resume, close_auto, change_limits."""
    action = body.get("action", "")
    return JSONResponse({
        "command_id": str(uuid4()),
        "status": "accepted",
        "action": action,
        "auto_state": "paused" if action == "pause" else "observing",
    }, status_code=202)


@router.post("/paper/accounts/{account_id}/orders/preview")
async def preview_order(request: Request, account_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Preview an order."""
    side = body.get("side", "buy")
    risk = Decimal(str(body.get("risk", "10")))
    stop = body.get("stop_price")
    entry_price = Decimal("64250")

    qty = risk / Decimal("500")  # rough: risk / (entry - stop)
    notional = qty * entry_price
    entry_fee = notional * Decimal("0.0006")
    exit_fee = notional * Decimal("0.0006")

    return JSONResponse({
        "allowed": True,
        "reasons": [],
        "account_label": "Ручной счёт",
        "quantity": str(qty),
        "notional": str(notional),
        "reserved_margin": str(risk),
        "entry_fee": str(entry_fee),
        "estimated_exit_fee": str(exit_fee),
        "funding_status": "unknown",
        "break_even": str(entry_price + entry_fee / qty + exit_fee / qty),
        "estimated_net_at_tp": str(risk * 2),
        "estimated_net_at_sl": str(-risk),
        "liquidation_price": str(entry_price * Decimal("0.9")),
        "liquidation_quality": "simplified",
        "price_as_of": datetime.now(timezone.utc).isoformat(),
        "expires_at": None,
    })


@router.post("/paper/accounts/{account_id}/orders")
async def place_order(request: Request, account_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Place a paper order."""
    return JSONResponse({
        "command_id": str(uuid4()),
        "status": "accepted",
        "order_id": str(uuid4()),
    }, status_code=202)


@router.post("/paper/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Cancel a paper order."""
    return JSONResponse({"command_id": str(uuid4()), "status": "accepted"}, status_code=202)


@router.post("/paper/positions/{position_id}/actions")
async def position_action(request: Request, position_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Position actions: update_sl, update_tp, partial_close, close."""
    action = body.get("action", "")
    return JSONResponse({
        "command_id": str(uuid4()),
        "status": "accepted",
        "action": action,
    }, status_code=202)


@router.get("/paper/commands/{command_id}")
async def get_command(request: Request, command_id: str) -> JSONResponse:
    """Get command status by ID."""
    return JSONResponse({"command_id": command_id, "status": "accepted", "result": None})