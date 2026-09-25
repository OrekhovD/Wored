"""Model registry for the WORED Telegram chatbot.

Model policy (owner decision, 2026-09-25): only models available in the
Ollama PRO subscription plus the workstation-local Bonsai model.

Cloud (Ollama Pro subscription, :cloud suffix):
  glm-5.3:cloud               premium reasoning
  glm-5.3-flash:cloud         fast analyst / oracle
  deepseek-v4.1-flash:cloud   worker / fast JSON
  gemma4:31b:cloud            cheap fallback

Local (workstation RTX 2070, free, unlimited):
  bonsai-27b:lmstudio-q1      fallback for every tier

Chains: cloud first (fast, quality), local Bonsai as failover.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class ModelConfig:
    name: str
    model_id: str
    endpoint: str
    api_key_env: str
    tier: str
    max_tokens: int
    timeout: float
    provider: str = "openai"  # "openai" | "local_ollama"


OLLAMA_CLOUD_ENDPOINT = os.getenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
LOCAL_LLM_ENDPOINT = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8088") + "/v1"
_LOCAL_MODEL = os.getenv("LOCAL_LLM_MODEL", "bonsai-27b:lmstudio-q1")


MODELS = {
    # ─── Ollama Pro cloud ──────────────────────────────────────────────
    "worker_ollama": ModelConfig(
        name="Robotyaga (Ollama Pro)",
        model_id=os.getenv("OLLAMA_CHATBOT_WORKER_MODEL", "deepseek-v4.1-flash:cloud"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=30.0,
    ),
    "analyst_ollama": ModelConfig(
        name="Analyst (Ollama Pro)",
        model_id=os.getenv("OLLAMA_CHATBOT_ANALYST_MODEL", "glm-5.3:cloud"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="analyst",
        max_tokens=4000,
        timeout=90.0,
    ),
    "premium_ollama": ModelConfig(
        name="Strategist (Ollama Pro)",
        model_id=os.getenv("OLLAMA_CHATBOT_PREMIUM_MODEL", "glm-5.3:cloud"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),

    # ─── Local Bonsai (free, unlimited, workstation) ──────────────────
    "analyst_bonsai": ModelConfig(
        name="Analyst (Local Bonsai-27B)",
        model_id=_LOCAL_MODEL,
        endpoint=LOCAL_LLM_ENDPOINT,
        api_key_env="LOCAL_LLM_API_KEY",
        tier="analyst",
        max_tokens=4000,
        timeout=180.0,
        provider="local_ollama",
    ),
    "premium_bonsai": ModelConfig(
        name="Strategist (Local Bonsai-27B)",
        model_id=_LOCAL_MODEL,
        endpoint=LOCAL_LLM_ENDPOINT,
        api_key_env="LOCAL_LLM_API_KEY",
        tier="premium",
        max_tokens=4000,
        timeout=180.0,
        provider="local_ollama",
    ),
    "worker_bonsai": ModelConfig(
        name="Robotyaga (Local Bonsai-27B)",
        model_id=_LOCAL_MODEL,
        endpoint=LOCAL_LLM_ENDPOINT,
        api_key_env="LOCAL_LLM_API_KEY",
        tier="worker",
        max_tokens=1000,
        timeout=180.0,
        provider="local_ollama",
    ),
}


WORKER_MODEL_CHAIN = ["worker_ollama", "worker_bonsai"]
ANALYST_MODEL_CHAIN = ["analyst_ollama", "analyst_bonsai"]
PREMIUM_MODEL_CHAIN = ["premium_ollama", "premium_bonsai"]

# Local Bonsai server needs no auth, but AsyncOpenAI requires a non-empty key.
_LOCAL_LLM_DUMMY_KEY = "ollama"

FALLBACK_ORDER = ["analyst", "worker", "premium"]


def expand_fallback_tiers(preferred: str) -> list[str]:
    """Expand a tier preference into an ordered candidate key list.

    Only Ollama Pro cloud and the local Bonsai failover exist under the
    Pro-only policy; the legacy ``minimax`` NVIDIA NIM oracle tier was retired
    together with the free-model routing subsystem.
    """
    order: list[str] = []

    def add_tier(tier: str) -> None:
        if tier == "worker":
            candidates = WORKER_MODEL_CHAIN
        elif tier == "analyst":
            candidates = ANALYST_MODEL_CHAIN
        elif tier == "premium":
            candidates = PREMIUM_MODEL_CHAIN
        else:
            candidates = [tier]
        for candidate in candidates:
            if candidate in MODELS and MODELS[candidate].model_id.strip() and candidate not in order:
                order.append(candidate)

    add_tier(preferred)
    for tier in FALLBACK_ORDER:
        if tier != preferred:
            add_tier(tier)
    return order