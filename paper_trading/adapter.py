"""Paper Trading adapter — bridges webui/chatbot to paper_trading domain service.

This module is imported by webui/paper_api.py and chatbot/handlers/pipeline.py.
It creates PaperRepository and PaperTradingService instances and provides
synchronous-friendly wrappers for the async service methods.

Lives in the shared paper_trading package so both containers can import it.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

# Lazy imports — asyncpg only available in containers
_repo: Optional[Any] = None
_service: Optional[Any] = None


async def get_service() -> Any:
    """Get or create the singleton PaperTradingService."""
    global _repo, _service
    if _service is not None:
        return _service

    # Import inside function — asyncpg only in containers
    from paper_trading.repository import PaperRepository
    from paper_trading.service import PaperTradingService

    # Get pool from existing postgres client
    try:
        from storage.postgres_client import get_pool
        pool = await get_pool()
        if pool is None:
            log.warning("Paper trading: no DB pool available")
            return None
    except ImportError:
        log.warning("Paper trading: postgres_client not available")
        return None

    _repo = PaperRepository(pool)
    _service = PaperTradingService(_repo)
    return _service


def owner_id_from_telegram(telegram_user_id: int) -> str:
    """Map Telegram user ID to stable owner_id."""
    return f"tg:{telegram_user_id}"


def owner_id_from_webui(session_username: str) -> str:
    """Map WebUI session username to stable owner_id."""
    return f"webui:{session_username}"


# ─── Status DTO helpers ────────────────────────────────────────────────

async def get_current_state(owner_id: str) -> Dict[str, Any]:
    """Get current trading day state for an owner."""
    service = await get_service()
    if service is None:
        return {
            "ok": False,
            "error": "service_unavailable",
            "day": None,
            "accounts": [],
            "next_action": "start",
        }
    return await service.get_current_state(owner_id)


async def start_day(
    owner_id: str,
    timezone: str = "Asia/Bangkok",
    end_time_local: str = "21:00",
    mode: str = "baseline_auto",
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Start a new trading day."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}

    from paper_trading.service import StartDayRequest
    req = StartDayRequest(
        owner_id=owner_id,
        timezone=timezone,
        end_time_local=end_time_local,
        mode=mode,
        settings=settings,
    )
    return await service.start_day(req)


async def submit_manual_order(
    owner_id: str,
    account_id: str,
    side: str,
    order_type: str = "market",
    qty: str = "0.001",
    stop_loss: Optional[str] = None,
    take_profit: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a manual order."""
    from decimal import Decimal
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}

    return await service.submit_manual_order(
        owner_id=owner_id,
        account_id=account_id,
        side=side,
        order_type=order_type,
        qty=Decimal(qty),
        stop_loss=Decimal(stop_loss) if stop_loss else None,
        take_profit=Decimal(take_profit) if take_profit else None,
        idempotency_key=idempotency_key,
    )


async def close_position(
    owner_id: str,
    position_id: str,
    partial_qty: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Close a position."""
    from decimal import Decimal
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}

    return await service.close_position(
        owner_id=owner_id,
        position_id=position_id,
        partial_qty=Decimal(partial_qty) if partial_qty else None,
        idempotency_key=idempotency_key,
    )


async def pause_auto(owner_id: str) -> Dict[str, Any]:
    """Pause automatic trading."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.pause_auto(owner_id)


async def resume_auto(owner_id: str) -> Dict[str, Any]:
    """Resume automatic trading."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.resume_auto(owner_id)


async def finish_day(owner_id: str) -> Dict[str, Any]:
    """Finish the trading day."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.finish_day(owner_id)


async def get_command_status(command_id: str) -> Dict[str, Any]:
    """Get command status."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.get_command_status(command_id)


# ─── Runner integration ────────────────────────────────────────────────

_runner_registered = False


def register_runner(scheduler: Any) -> bool:
    """Register the paper trading runner with the collector scheduler.

    Returns True if registered, False if already registered or disabled.
    """
    global _runner_registered
    if _runner_registered:
        return False

    enabled = os.getenv("PAPER_ENGINE_ENABLED", "false").lower() == "true"
    if not enabled:
        log.info("Paper trading runner disabled (PAPER_ENGINE_ENABLED != true)")
        return False

    try:
        import asyncio
        from paper_trading.runner import PaperTradingRunner

        runner = PaperTradingRunner.from_env()

        # Register 2s poll cycle
        interval = int(os.getenv("PAPER_ENGINE_INTERVAL_SECONDS", "2"))
        scheduler.add_job(
            runner.run_cycle,
            "interval",
            seconds=interval,
            id="paper_trading_runner",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Register heartbeat
        hb_interval = int(os.getenv("PAPER_HEARTBEAT_INTERVAL_SECONDS", "5"))
        scheduler.add_job(
            runner.heartbeat,
            "interval",
            seconds=hb_interval,
            id="paper_trading_heartbeat",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _runner_registered = True
        log.info("Paper trading runner registered (interval=%ss, heartbeat=%ss)", interval, hb_interval)
        return True

    except Exception as exc:
        log.error("Failed to register paper trading runner: %s", exc)
        return False