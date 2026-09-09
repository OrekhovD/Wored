"""WORED Paper API — TD-02/TD-06

API endpoints for paper trading day.
Read-only current state + start/finish/automation + orders + positions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["paper"])


# ─── GET /api/trading-day/current ──────────────────────────────────────────────

@router.get("/trading-day/current")
async def get_current_day(request: Request) -> JSONResponse:
    """Current trading day state: day, accounts, freshness, capabilities."""
    # TD-02 prototype: return mock state for UI testing
    # Real implementation in TD-03+ will use DB
    pool = getattr(request.app.state, 'pg_pool', None)

    if pool is None:
        # Prototype mode — no DB, return draft state
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

    # Real mode — query DB (TD-03+)
    # TODO: query trading_days, paper_accounts, paper_positions
    return JSONResponse({"day": None, "accounts": [], "fresh": False})


# ─── POST /api/trading-day/start ──────────────────────────────────────────────

@router.post("/trading-day/start")
async def start_day(request: Request, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Start a new trading day for both accounts."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        # Prototype mode
        return JSONResponse({
            "command_id": "proto-start",
            "status": "accepted",
            "day_id": "proto-day-1",
        }, status_code=202)

    # Real implementation in TD-03+
    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/trading-day/{day_id}/finish ────────────────────────────────────

@router.post("/trading-day/{day_id}/finish")
async def finish_day(request: Request, day_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Finish the trading day: cancel unfilled, close positions, reconcile."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": "proto-finish", "status": "accepted"}, status_code=202)

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/trading-day/{day_id}/automation ────────────────────────────────

@router.post("/trading-day/{day_id}/automation")
async def automation(request: Request, day_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Auto controls: pause, resume, close_auto, change_limits."""
    action = body.get("action", "")
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": "proto-auto", "status": "accepted", "action": action}, status_code=202)

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/paper/accounts/{account_id}/orders/preview ─────────────────────

@router.post("/paper/accounts/{account_id}/orders/preview")
async def preview_order(request: Request, account_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Preview an order: quantity, notional, fees, liquidation, net at TP/SL."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        # Prototype preview
        return JSONResponse({
            "allowed": True,
            "reasons": [],
            "account_label": "Ручной счёт",
            "quantity": "0.001",
            "notional": "100",
            "reserved_margin": "10",
            "entry_fee": "0.06",
            "estimated_exit_fee": "0.06",
            "funding_status": "unknown",
            "break_even": "100060",
            "estimated_net_at_tp": "5.94",
            "estimated_net_at_sl": "-10.06",
            "liquidation_price": "90000",
            "liquidation_quality": "simplified",
            "price_as_of": datetime.now(timezone.utc).isoformat(),
            "expires_at": None,
        })

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/paper/accounts/{account_id}/orders ─────────────────────────────

@router.post("/paper/accounts/{account_id}/orders")
async def place_order(request: Request, account_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Place a paper order."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": "proto-order", "status": "accepted"}, status_code=202)

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/paper/orders/{order_id}/cancel ─────────────────────────────────

@router.post("/paper/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Cancel a paper order."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": "proto-cancel", "status": "accepted"}, status_code=202)

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── POST /api/paper/positions/{position_id}/actions ──────────────────────────

@router.post("/paper/positions/{position_id}/actions")
async def position_action(request: Request, position_id: str, body: Dict[str, Any] = Body(...)) -> JSONResponse:
    """Position actions: update_sl, update_tp, partial_close, close."""
    action = body.get("action", "")
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": "proto-pos-action", "status": "accepted", "action": action}, status_code=202)

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")


# ─── GET /api/paper/commands/{command_id} ─────────────────────────────────────

@router.get("/paper/commands/{command_id}")
async def get_command(request: Request, command_id: str) -> JSONResponse:
    """Get command status by ID."""
    pool = getattr(request.app.state, 'pg_pool', None)
    if pool is None:
        return JSONResponse({"command_id": command_id, "status": "accepted", "result": None})

    raise HTTPException(status_code=503, detail="Not yet implemented with DB")