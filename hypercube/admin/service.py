"""Admin diagnostics and system management service."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class AdminService:
    """Provides admin diagnostics and system information."""

    def __init__(self, container) -> None:
        """container: ServiceContainer instance."""
        self._c = container

    async def get_system_status(self) -> dict[str, Any]:
        """Get overall system health status."""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": True,
            "providers": {},
            "htx_api": False,
        }
        
        # Check providers
        for pid, adapter in self._c.provider_adapters.items():
            try:
                health = await adapter.healthcheck()
                status["providers"][pid] = {
                    "healthy": health.healthy,
                    "latency_ms": health.latency_ms,
                }
            except Exception as e:
                status["providers"][pid] = {"healthy": False, "error": str(e)}
        
        # Check HTX
        try:
            ts = await self._c.htx_adapter.get_server_time()
            status["htx_api"] = ts > 0
        except Exception:
            status["htx_api"] = False
        
        return status

    async def get_usage_summary(self, window: str = "day") -> dict[str, Any]:
        """Get aggregated usage summary."""
        # Aggregate across all users (user_id=0 convention)
        return await self._c.accounting.get_user_usage(0, window)

    async def get_recent_events(self, limit: int = 20) -> dict[str, Any]:
        """Get recent system events."""
        return {
            "recent_fallbacks": await self._c.usage_repo.recent_fallbacks(limit),
            "recent_handoffs": await self._c.usage_repo.recent_handoffs(limit),
        }

    async def log_admin_action(self, admin_id: int, action: str, details: str | None = None) -> None:
        """Log an admin action."""
        await self._c.admin_repo.log_event(
            event_type=action,
            admin_id=admin_id,
            details=details,
        )
