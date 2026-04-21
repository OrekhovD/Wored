"""Internal API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.schemas import AIRequest, HandoffPackage

internal_router = APIRouter(tags=["internal"])


@internal_router.post("/ask")
async def internal_ask(request: AIRequest) -> dict:
    """Route an AI request through the system."""
    # Placeholder — full implementation in Phase 3
    return {
        "status": "ok",
        "content": f"Received request for model {request.model}",
        "model": request.model,
    }


@internal_router.post("/switch-model")
async def internal_switch_model(model_id: str, session_id: str) -> dict:
    """Trigger model switch with context handoff."""
    return {
        "status": "ok",
        "message": f"Switching to {model_id} for session {session_id}",
    }


@internal_router.post("/context/handoff")
async def internal_context_handoff(handoff: HandoffPackage) -> dict:
    """Manually trigger context handoff."""
    return {"status": "ok", "handoff_version": handoff.version}


@internal_router.get("/providers")
async def internal_providers() -> list[dict]:
    """List all registered providers."""
    return [
        {"provider_id": "dashscope", "display_name": "DashScope (Qwen Cloud)", "enabled": True},
        {"provider_id": "nvapi", "display_name": "NVIDIA nvapi", "enabled": True},
        {"provider_id": "zhipu", "display_name": "Zhipu AI (GLM-5)", "enabled": True},
        {"provider_id": "ai_studio", "display_name": "Baidu AI Studio", "enabled": True},
    ]


@internal_router.get("/models")
async def internal_models() -> list[dict]:
    """List all available models."""
    from routing.model_registry import ModelRegistry
    reg = ModelRegistry()
    return reg.get_all_models()


@internal_router.get("/usage/summary")
async def internal_usage_summary(user_id: int | None = None) -> dict:
    """Get usage summary."""
    return {
        "requests": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "period": "day",
    }
