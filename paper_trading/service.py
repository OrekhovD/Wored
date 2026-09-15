"""Paper Trading Service — commands for day, manual and auto accounts.

This is the domain service that Telegram and WebUI adapters call.
It does NOT import FastAPI Request, Telegram types, or UI templates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from paper_trading.contracts import (
    Account, AccountKind, Command,
    Position, ReasonCode, StatusDTO, TradingDay,
)
from paper_trading.repository import PaperRepository

log = logging.getLogger(__name__)

# Default risk settings for new owner without saved settings
DEFAULT_SETTINGS = {
    "opening_capital": "1000",
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "max_positions_per_instrument": "1",
    "end_time_local": "21:00",
    "timezone": "Asia/Bangkok",
}


@dataclass
class StartDayRequest:
    owner_id: str
    timezone: str = "Asia/Bangkok"
    end_time_local: str = "21:00"
    settings: Optional[Dict[str, Any]] = None
    strategy_version: str = "baseline_v1"
    mode: str = "baseline_auto"  # or "ai_plan"


class PaperTradingService:
    """Domain service for paper trading day operations."""

    def __init__(self, repo: PaperRepository):
        self.repo = repo

    async def start_day(self, req: StartDayRequest) -> Dict[str, Any]:
        """Start a new trading day for both manual and auto accounts."""
        # Ensure owner exists
        try:
            await self.repo.create_owner(req.owner_id, display_name=f"owner-{req.owner_id[:8]}")
        except Exception:
            pass  # owner already exists is OK

        # Ensure both accounts exist with default capital
        settings = {**DEFAULT_SETTINGS, **(req.settings or {})}
        capital = Decimal(settings["opening_capital"])

        manual = await self._get_or_create_account(req.owner_id, "manual", "USDT", capital)
        auto = await self._get_or_create_account(req.owner_id, "auto", "USDT", capital)

        # Check no unclosed day exists
        existing = await self.repo.get_active_day(req.owner_id)
        if existing and existing.state not in ("closed", "settlement_pending"):
            return {
                "ok": False,
                "error": "day_already_active",
                "day_id": str(existing.day_id),
            }

        # Create new day
        now = datetime.now(timezone.utc)
        # Compute end_utc from end_time_local + timezone
        # For v1, use 8h from start as approximation
        from datetime import timedelta
        from uuid import uuid4, UUID
        end_utc = now + timedelta(hours=8)

        day = await self.repo.create_day(
            day_id=uuid4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            timezone=req.timezone,
            start_utc=now,
            end_utc=end_utc,
            settings_snapshot=settings,
            strategy_version=req.strategy_version,
        )

        # Submit start commands for both accounts
        from uuid import uuid4 as _u4
        from paper_trading.contracts import CommandType
        cmd_manual = await self.repo.submit_command(
            command_id=_u4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            idempotency_key=f"start-{day.id}-manual",
            command_type=CommandType("start_day"),
            payload={"account_kind": "manual", "mode": req.mode},
            account_id=UUID(str(manual.account_id)) if manual else None,
            day_id=UUID(str(day.day_id)) if day else None,
        )
        cmd_auto = await self.repo.submit_command(
            command_id=_u4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            idempotency_key=f"start-{day.id}-auto",
            command_type=CommandType("start_day"),
            payload={"account_kind": "auto", "mode": req.mode},
            account_id=UUID(str(auto.account_id)) if auto else None,
            day_id=UUID(str(day.day_id)) if day else None,
        )

        return {
            "ok": True,
            "day_id": str(day.day_id),
            "manual_account_id": str(manual.account_id),
            "auto_account_id": str(auto.account_id),
            "start_at": now.isoformat(),
            "end_at": end_utc.isoformat(),
            "commands": [str(cmd_manual), str(cmd_auto)],
        }

    async def _get_or_create_account(self, owner_id: str, kind: str, currency: str, opening_deposit: Decimal):
        """Get or create an account for an owner."""
        from uuid import uuid4, UUID

        owner_uuid = UUID(owner_id) if isinstance(owner_id, str) else owner_id
        kind_enum = AccountKind(kind) if isinstance(kind, str) else kind

        try:
            account_id = uuid4()
            return await self.repo.create_account(
                account_id=account_id,
                owner_id=owner_uuid,
                kind=kind_enum,
                currency=currency,
                opening_deposit=opening_deposit,
            )
        except Exception:
            # Account already exists — find it
            return await self.get_account_by_kind(owner_uuid, kind_enum)

    async def get_current_state(self, owner_id: str) -> Dict[str, Any]:
        """Get current trading day state with both accounts."""
        day = await self.repo.get_active_day(owner_id)
        if not day:
            return {
                "ok": True,
                "day": None,
                "next_action": "start",
                "accounts": [],
            }

        manual = await self.repo.get_account_by_kind(owner_id, "manual")
        auto = await self.repo.get_account_by_kind(owner_id, "auto")

        accounts = []
        for acct, kind in [(manual, "manual"), (auto, "auto")]:
            if not acct:
                continue
            positions = await self.repo.get_open_positions_by_account(str(acct.id))
            balance = await self.repo.get_account_balance(str(acct.id))
            accounts.append({
                "id": str(acct.id),
                "kind": kind,
                "currency": acct.currency,
                "cash": str(balance),
                "open_positions": len(positions),
                "positions": [
                    {
                        "id": str(p.id),
                        "side": p.side,
                        "qty": str(p.qty),
                        "avg_entry": str(p.avg_entry),
                        "stop": str(p.stop_loss) if p.stop_loss else None,
                        "target": str(p.take_profit) if p.take_profit else None,
                    }
                    for p in positions
                ],
            })

        return {
            "ok": True,
            "day": {
                "id": str(day.day_id),
                "state": day.state,
                "start_at": day.start_at.isoformat() if day.start_at else None,
                "end_at": day.end_at.isoformat() if day.end_at else None,
            },
            "accounts": accounts,
            "next_action": "trade" if day.state == "running" else "start",
        }

    async def submit_manual_order(
        self,
        owner_id: str,
        account_id: str,
        side: str,
        order_type: str,
        qty: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a manual order."""
        day = await self.repo.get_active_day(owner_id)
        if not day:
            return {"ok": False, "error": "no_active_day"}

        key = idempotency_key or f"order-{uuid4()}"
        cmd = await self.repo.submit_command(
            owner_id=owner_id,
            account_id=account_id,
            day_id=str(day.day_id),
            command_type="place_order",
            idempotency_key=key,
            payload={
                "side": side,
                "order_type": order_type,
                "qty": str(qty),
                "stop_loss": str(stop_loss) if stop_loss else None,
                "take_profit": str(take_profit) if take_profit else None,
                "origin": "manual",
                "actor": "user",
            },
        )
        return {"ok": True, "command_id": str(cmd)}

    async def close_position(
        self,
        owner_id: str,
        position_id: str,
        partial_qty: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close a position (full or partial)."""
        key = idempotency_key or f"close-{position_id}-{uuid4()}"
        # Get position to find account/day
        pos = await self.repo.get_position_by_id(position_id)
        if not pos:
            return {"ok": False, "error": "position_not_found"}

        cmd = await self.repo.submit_command(
            owner_id=owner_id,
            account_id=str(pos.account_id),
            day_id=str(pos.day_id),
            command_type="close_position",
            idempotency_key=key,
            payload={
                "position_id": position_id,
                "partial_qty": str(partial_qty) if partial_qty else None,
            },
        )
        return {"ok": True, "command_id": str(cmd)}

    async def pause_auto(self, owner_id: str) -> Dict[str, Any]:
        """Pause automatic trading (new entries blocked, positions protected)."""
        day = await self.repo.get_active_day(owner_id)
        if not day:
            return {"ok": False, "error": "no_active_day"}

        auto = await self.repo.get_account_by_kind(owner_id, "auto")
        if not auto:
            return {"ok": False, "error": "no_auto_account"}

        cmd = await self.repo.submit_command(
            owner_id=owner_id,
            account_id=str(auto.account_id),
            day_id=str(day.day_id),
            command_type="pause_auto",
            idempotency_key=f"pause-{day.id}",
            payload={},
        )
        return {"ok": True, "command_id": str(cmd)}

    async def resume_auto(self, owner_id: str) -> Dict[str, Any]:
        """Resume automatic trading."""
        day = await self.repo.get_active_day(owner_id)
        if not day:
            return {"ok": False, "error": "no_active_day"}

        auto = await self.repo.get_account_by_kind(owner_id, "auto")
        if not auto:
            return {"ok": False, "error": "no_auto_account"}

        cmd = await self.repo.submit_command(
            owner_id=owner_id,
            account_id=str(auto.account_id),
            day_id=str(day.day_id),
            command_type="resume_auto",
            idempotency_key=f"resume-{day.id}",
            payload={},
        )
        return {"ok": True, "command_id": str(cmd)}

    async def finish_day(self, owner_id: str) -> Dict[str, Any]:
        """Finish the trading day — closeout both accounts."""
        day = await self.repo.get_active_day(owner_id)
        if not day:
            return {"ok": False, "error": "no_active_day"}

        cmd = await self.repo.submit_command(
            owner_id=owner_id,
            account_id="",  # both accounts
            day_id=str(day.day_id),
            command_type="finish_day",
            idempotency_key=f"finish-{day.id}",
            payload={},
        )
        return {"ok": True, "command_id": str(cmd)}

    async def get_command_status(self, command_id: str) -> Dict[str, Any]:
        """Get command status by ID."""
        cmd = await self.repo.get_command(command_id)
        if not cmd:
            return {"ok": False, "error": "command_not_found"}
        return {
            "ok": True,
            "command_id": str(cmd.id),
            "status": cmd.status,
            "result": cmd.result,
            "error": cmd.error,
        }

    # ------------------------------------------------------------------
    # Delegate methods (thin wrappers over repository)
    # ------------------------------------------------------------------

    async def get_account_by_kind(self, owner_id, kind) -> Optional[Account]:
        """Get the account for an owner with the given kind, or None."""
        from uuid import UUID
        owner_uuid = UUID(owner_id) if isinstance(owner_id, str) else owner_id
        kind_enum = AccountKind(kind) if isinstance(kind, str) else kind
        return await self.repo.get_account_by_kind(owner_uuid, kind_enum)

    async def get_open_positions_by_account(self, account_id) -> List[Position]:
        """Get all open positions for an account."""
        from uuid import UUID
        acct_uuid = UUID(account_id) if isinstance(account_id, str) else account_id
        return await self.repo.get_open_positions_by_account(acct_uuid)

    async def get_account_balance(self, account_id) -> Decimal:
        """Return the current balance (sum of postings) for an account."""
        from uuid import UUID
        acct_uuid = UUID(account_id) if isinstance(account_id, str) else account_id
        return await self.repo.get_account_balance(acct_uuid)

    async def get_position_by_id(self, position_id) -> Optional[Position]:
        """Get a position by its ID, or None."""
        from uuid import UUID
        pos_uuid = UUID(position_id) if isinstance(position_id, str) else position_id
        return await self.repo.get_position_by_id(pos_uuid)