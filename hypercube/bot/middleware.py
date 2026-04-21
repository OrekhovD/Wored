"""aiogram middleware classes."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, User

from core.config import AppConfiguration
from core.request_id import generate_request_id

log = logging.getLogger(__name__)


class TrackingMiddleware(BaseMiddleware):
    """Add request_id to state and track processing time."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        start = time.monotonic()
        data["request_id"] = generate_request_id()
        try:
            return await handler(event, data)
        finally:
            elapsed = time.monotonic() - start
            log.debug("Request %s processed in %.2fs", data["request_id"], elapsed)


class SecurityMiddleware(BaseMiddleware):
    """Check admin allowlist for admin commands."""

    def __init__(self, admin_ids: set[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        data["is_admin"] = event.from_user.id in self._admin_ids
        return await handler(event, data)


class UsageMiddleware(BaseMiddleware):
    """Prepend usage tracking context."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        data["usage_tracked"] = False
        return await handler(event, data)
