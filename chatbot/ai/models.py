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


MINIMAX_NVIDIA_MODEL = "minimaxai/minimax-m2.7"
MINIMAX_NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1"
GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/"
DASHSCOPE_ENDPOINT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/"
GOOGLE_OPENAI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_PREMIUM_QWEN_MODEL = "qwen3.6-27b"


def _csv_fallback_model(env_name: str, index: int, default: str) -> str:
    values = [item.strip() for item in os.getenv(env_name, "").split(",") if item.strip()]
    if index < len(values):
        return values[index]
    return default


MODELS = {
    "worker": ModelConfig(
        name="Robotyaga (Qwen 3.6 Flash)",
        model_id=os.getenv("WORKER_QWEN_MODEL", "qwen3.6-flash"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "worker_qwen35": ModelConfig(
        name="Robotyaga (Qwen 3.5 Flash)",
        model_id=_csv_fallback_model("WORKER_QWEN_FALLBACKS", 0, "qwen3.5-flash"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "worker_qwen_legacy": ModelConfig(
        name="Robotyaga (Qwen Flash)",
        model_id=_csv_fallback_model("WORKER_QWEN_FALLBACKS", 1, "qwen-flash"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "worker_glm": ModelConfig(
        name="Robotyaga (GLM-4 Flash)",
        model_id=os.getenv("WORKER_GLM_FALLBACK_MODEL", "glm-4-flash"),
        endpoint=GLM_ENDPOINT,
        api_key_env="GLM_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "worker_gemini": ModelConfig(
        name="Robotyaga (Gemini Flash Preview)",
        model_id=os.getenv("WORKER_GEMINI_FALLBACK_MODEL", "gemini-3-flash-preview"),
        endpoint=GOOGLE_OPENAI_ENDPOINT,
        api_key_env="GOOGLE_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "analyst": ModelConfig(
        name="Analyst (Qwen 3.6 35B A3B)",
        model_id=os.getenv("ANALYST_QWEN_MODEL", "qwen3.6-35b-a3b"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "analyst_qwen27b": ModelConfig(
        name="Analyst (Qwen 3.6 27B)",
        model_id=_csv_fallback_model("ANALYST_QWEN_FALLBACKS", 0, "qwen3.6-27b"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "analyst_qwen_extra": ModelConfig(
        name="Analyst (Extra Qwen Reasoning)",
        model_id=_csv_fallback_model("ANALYST_QWEN_FALLBACKS", 1, ""),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "analyst_glm": ModelConfig(
        name="Analyst (GLM-5.1)",
        model_id=os.getenv("ANALYST_GLM_FALLBACK_MODEL", os.getenv("GLM_MODEL", "glm-5.1")),
        endpoint=GLM_ENDPOINT,
        api_key_env="GLM_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "premium": ModelConfig(
        name="Strategist (Qwen 3.6 27B)",
        model_id=os.getenv("PREMIUM_QWEN_MODEL", DEFAULT_PREMIUM_QWEN_MODEL),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "premium_qwen35b": ModelConfig(
        name="Strategist (Qwen 3.6 35B A3B)",
        model_id=_csv_fallback_model("PREMIUM_QWEN_FALLBACKS", 0, "qwen3.6-35b-a3b"),
        endpoint=DASHSCOPE_ENDPOINT,
        api_key_env="DASHSCOPE_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "premium_glm": ModelConfig(
        name="Strategist (GLM-5.1)",
        model_id=os.getenv("PREMIUM_GLM_FALLBACK_MODEL", os.getenv("GLM_MODEL", "glm-5.1")),
        endpoint=GLM_ENDPOINT,
        api_key_env="GLM_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "minimax": ModelConfig(
        name="Oracle (MiniMax M2.7)",
        model_id=MINIMAX_NVIDIA_MODEL,
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="MINIMAX_API_KEY",
        tier="minimax",
        max_tokens=2048,
        timeout=12.0,
    ),
}


WORKER_MODEL_CHAIN = ["worker", "worker_qwen35", "worker_qwen_legacy", "worker_glm", "worker_gemini"]
ANALYST_MODEL_CHAIN = ["analyst", "analyst_qwen27b", "analyst_qwen_extra", "analyst_glm"]
PREMIUM_MODEL_CHAIN = ["premium", "premium_qwen35b", "premium_glm"]


FALLBACK_ORDER = ["analyst", "worker", "premium", "minimax"]


def expand_fallback_tiers(preferred: str) -> list[str]:
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
