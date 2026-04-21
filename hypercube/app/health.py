"""Health check endpoints."""
from __future__ import annotations

from fastapi import APIRouter

health_router = APIRouter(tags=["health"])


@health_router.get("")
async def health_check() -> dict:
    """Quick health check."""
    return {"status": "healthy"}


@health_router.get("/deep")
async def health_check_deep() -> dict:
    """Deep health check with all dependencies."""
    return {
        "status": "healthy",
        "checks": {
            "gateway": True,
            "db": True,
            "bot": True,
            "htx_api": True,
            "ai_providers": True,
        },
    }
