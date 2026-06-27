"""
Model Lab - discover, probe, rank, rotate models across providers.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


async def discover_models(provider: str = "ollama") -> list[dict]:
    """List available models from a provider via /v1/models."""
    from ai.models import MODELS, OLLAMA_CLOUD_ENDPOINT
    from ai.router import get_client
    import os

    configs = {
        "ollama": {"endpoint": OLLAMA_CLOUD_ENDPOINT, "key_env": "OLLAMA_CLOUD_API_KEY"},
    }
    cfg = configs.get(provider)
    if not cfg:
        return []

    api_key = os.getenv(cfg["key_env"], "")
    if not api_key:
        return []

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{cfg['endpoint']}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("data", [])
            return [{"id": m["id"], "provider": provider} for m in models]
    except Exception as exc:
        log.error("Discover failed for %s: %s", provider, exc)
        return []


async def probe_model(tier: str) -> dict:
    """Send a test request to a model and check if it responds."""
    from ai.models import MODELS
    from ai.router import get_client

    cfg = MODELS.get(tier)
    if not cfg:
        return {"tier": tier, "status": "not_found"}

    client = get_client(tier)
    if client is None:
        return {"tier": tier, "model": cfg.model_id, "status": "no_key", "error": "API key not set"}

    try:
        start = time.monotonic()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=cfg.model_id,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=cfg.timeout,
        )
        latency = int((time.monotonic() - start) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        return {
            "tier": tier,
            "model": cfg.model_id,
            "status": "ok" if content else "empty",
            "latency_ms": latency,
            "response": content[:50],
        }
    except asyncio.TimeoutError:
        return {"tier": tier, "model": cfg.model_id, "status": "timeout"}
    except Exception as exc:
        error_type = type(exc).__name__
        return {"tier": tier, "model": cfg.model_id, "status": "error", "error_type": error_type, "error": str(exc)[:100]}


async def probe_all() -> list[dict]:
    """Probe all configured model tiers."""
    from ai.models import MODELS
    tiers = list(MODELS.keys())
    results = await asyncio.gather(*[probe_model(t) for t in tiers], return_exceptions=True)
    return [r if isinstance(r, dict) else {"status": "exception", "error": str(r)} for r in results]


async def rank_models() -> list[dict]:
    """Rank models by availability and latency."""
    probes = await probe_all()
    ok_models = [p for p in probes if p.get("status") == "ok"]
    ok_models.sort(key=lambda x: x.get("latency_ms", 9999))
    return ok_models


async def rotate_active_slot() -> dict:
    """Promote the best available fallback model to active slot."""
    ranked = await rank_models()
    if not ranked:
        return {"status": "no_models", "message": "No working models found"}

    best = ranked[0]
    log.info("Active slot rotated to: %s (%s, %dms)", best["tier"], best["model"], best.get("latency_ms", 0))
    return {
        "status": "rotated",
        "new_active": best["tier"],
        "model": best["model"],
        "latency_ms": best.get("latency_ms", 0),
    }


async def get_active_route() -> dict:
    """Get current routing diagnostics."""
    from ai.models import WORKER_MODEL_CHAIN, ANALYST_MODEL_CHAIN, PREMIUM_MODEL_CHAIN, MODELS

    probes = await probe_all()
    by_tier = {p["tier"]: p for p in probes if isinstance(p, dict)}

    def chain_status(chain):
        result = []
        for tier in chain:
            cfg = MODELS.get(tier)
            probe = by_tier.get(tier, {})
            result.append({
                "tier": tier,
                "model": cfg.model_id if cfg else "?",
                "status": probe.get("status", "not_probed"),
                "latency_ms": probe.get("latency_ms"),
                "error": probe.get("error"),
            })
        return result

    return {
        "worker_chain": chain_status(WORKER_MODEL_CHAIN),
        "analyst_chain": chain_status(ANALYST_MODEL_CHAIN),
        "premium_chain": chain_status(PREMIUM_MODEL_CHAIN),
    }
