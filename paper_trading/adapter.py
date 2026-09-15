"""Paper Trading adapter — bridges webui/chatbot to paper_trading domain service.

This module is imported by webui/paper_api.py and chatbot/handlers/pipeline.py.
It creates PaperRepository and PaperTradingService instances and provides
synchronous-friendly wrappers for the async service methods.

Lives in the shared paper_trading package so both containers can import it.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Lazy imports — asyncpg only available in containers
_repo: Any | None = None
_service: Any | None = None


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
    """Map Telegram user ID to stable owner_id (deterministic UUID5).

    This is the canonical owner_id — both Telegram and WebUI must resolve to the same UUID.
    """
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wored:owner:{telegram_user_id}"))


def owner_id_from_webui(session_username: str, telegram_user_id: int | None = None) -> str:
    """Map WebUI session username to stable owner_id.

    If telegram_user_id is provided, uses the same namespace as Telegram (unified identity).
    Otherwise, uses the username directly — but this creates a separate owner and should
    be resolved via identity mapping in the database.

    For v1, 'admin' maps to the first registered Telegram owner.
    """
    import uuid
    if telegram_user_id is not None:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wored:owner:{telegram_user_id}"))
    # Fallback: use username-based UUID (should be replaced by identity mapping)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wored:owner:{session_username}"))


# ─── Status DTO helpers ────────────────────────────────────────────────

async def get_current_state(owner_id: str) -> dict[str, Any]:
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
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    stop_loss: str | None = None,
    take_profit: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
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
    partial_qty: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
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


async def pause_auto(owner_id: str) -> dict[str, Any]:
    """Pause automatic trading."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.pause_auto(owner_id)


async def resume_auto(owner_id: str) -> dict[str, Any]:
    """Resume automatic trading."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.resume_auto(owner_id)


async def finish_day(owner_id: str) -> dict[str, Any]:
    """Finish the trading day."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.finish_day(owner_id)


async def get_command_status(command_id: str) -> dict[str, Any]:
    """Get command status."""
    service = await get_service()
    if service is None:
        return {"ok": False, "error": "service_unavailable"}
    return await service.get_command_status(command_id)


# ─── Runner integration ────────────────────────────────────────────────

_runner_registered = False
_runner_instance: Any | None = None


def register_runner(scheduler: Any) -> bool:
    """Register the paper trading runner with the collector scheduler.

    Wires real dependencies (market_data from Redis, command_source from
    PostgreSQL, recovery_store from PostgreSQL, repository for the
    signal→order→fill→ledger chain) and calls recover() after registration.

    Returns True if registered, False if already registered or disabled.
    """
    global _runner_registered, _runner_instance
    if _runner_registered:
        return False

    enabled = os.getenv("PAPER_ENGINE_ENABLED", "false").lower() == "true"
    if not enabled:
        log.info("Paper trading runner disabled (PAPER_ENGINE_ENABLED != true)")
        return False

    try:
        from paper_trading.runner import PaperTradingRunner

        runner = PaperTradingRunner.from_env()
        _runner_instance = runner

        # Wire real dependencies (async — use scheduler's event loop)
        import asyncio
        async def _wire_and_recover():
            """Wire PostgreSQL + Redis dependencies, then recover."""
            try:
                # Get PostgreSQL pool
                from storage.postgres_client import get_pool as _get_pool
                pool = await _get_pool()
                if pool is not None:
                    runner._wire_dependencies(pg_pool=pool)
                    log.info("Paper trading: PostgreSQL dependencies wired")
            except Exception as exc:
                log.warning("Paper trading: failed to wire PostgreSQL: %s", exc)

            try:
                # Get Redis client
                from storage.redis_client import get_redis as _get_redis
                redis = await _get_redis() if hasattr(_get_redis(), '__await__') else _get_redis()
                if redis is not None:
                    runner._wire_dependencies(redis_client=redis)
                    log.info("Paper trading: Redis market data source wired")
            except Exception as exc:
                log.warning("Paper trading: failed to wire Redis: %s", exc)

            # Run recovery
            if runner.recovery_store is not None:
                report = await runner.recover()
                log.info("Paper trading recovery: %s", report)
            elif runner.repository is None:
                # No DB — can't recover, entries stay blocked
                log.warning("Paper trading: no DB pool — entries blocked, runner in degraded mode")
            else:
                # Has repository but no recovery_store — shouldn't happen after wiring
                log.warning("Paper trading: repository wired but no recovery_store")

        # Schedule wiring + recovery as a one-shot job
        scheduler.add_job(
            _wire_and_recover,
            "date",
            id="paper_trading_wire_and_recover",
            replace_existing=True,
        )

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

        async def _heartbeat():
            """Heartbeat wrapper — publish runner status to Redis."""
            import json
            import time
            try:
                from storage.redis_client import get_redis
                redis = get_redis()
                if redis:
                    data = {
                        "instance_id": runner.instance_id,
                        "run_id": runner.run_id,
                        "completed_at": time.time(),
                        "last_success": getattr(runner, "_last_poll", 0),
                        "last_error": getattr(runner, "_last_error", None),
                        "strategy_version": "baseline_v1",
                        "entries_blocked": getattr(runner, "_entries_blocked", True),
                    }
                    await redis.set(runner.heartbeat_key, json.dumps(data))
            except Exception:
                pass

        scheduler.add_job(
            _heartbeat,
            "interval",
            seconds=hb_interval,
            id="paper_trading_heartbeat",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Register recovery job — runs once on startup to wire dependencies
        # and call recover()
        async def _startup_recovery():
            """Wire dependencies and run recovery on startup."""
            try:
                # Wire PostgreSQL dependencies
                pg_pool = None
                try:
                    from storage.postgres_client import get_pool as _get_pool
                    pg_pool = await _get_pool()
                except ImportError:
                    log.debug("postgres_client not available for runner wiring")

                # Wire Redis dependencies
                redis_client = None
                try:
                    from storage.redis_client import get_redis as _get_redis
                    redis_client = _get_redis()
                except ImportError:
                    log.debug("redis_client not available for runner wiring")

                # Wire dependencies into runner
                runner._wire_dependencies(
                    pg_pool=pg_pool,
                    redis_client=redis_client,
                )

                # Call recover() after wiring
                if runner.recovery_store is not None:
                    report = await runner.recover()
                    log.info(
                        "Paper trading runner recovered: %s",
                        report.get("recovered", False),
                    )
                else:
                    log.info("Paper trading runner: no recovery store, skipping recovery")

            except Exception:
                log.exception("Paper trading runner startup recovery failed")

        scheduler.add_job(
            _startup_recovery,
            "date",
            id="paper_trading_startup_recovery",
            replace_existing=True,
        )

        _runner_registered = True
        log.info("Paper trading runner registered (interval=%ss, heartbeat=%ss)", interval, hb_interval)
        return True

    except Exception as exc:
        log.error("Failed to register paper trading runner: %s", exc)
        return False