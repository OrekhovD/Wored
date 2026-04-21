from dataclasses import dataclass
import os

@dataclass
class ModelConfig:
    name: str           # Human readable name
    model_id: str       # API model ID
    endpoint: str       # Base URL
    api_key_env: str    # Environment variable holding the key
    tier: str           # "worker" | "analyst" | "premium"
    max_tokens: int
    timeout: float

MODELS = {
    "worker": ModelConfig(
        name="Роботяга (GLM-4 Flash)",
        model_id="glm-4-flash",
        endpoint="https://open.bigmodel.cn/api/paas/v4/",
        api_key_env="GLM_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "analyst": ModelConfig(
        name="Аналитик (GLM-5.1)",
        model_id=os.getenv("GLM_MODEL", "glm-5.1"),
        endpoint="https://open.bigmodel.cn/api/paas/v4/",
        api_key_env="GLM_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "premium": ModelConfig(
        name="Стратег (Gemini Pro)",
        model_id=os.getenv("GEMINI_MODEL", "gemini-3.1-pro"),
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GOOGLE_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "minimax": ModelConfig(
        name="Оракул (MiniMax)",
        model_id="minimax/minimax-text-01",
        endpoint="https://integrate.api.nvidia.com/v1/",
        api_key_env="MINIMAX_API_KEY",
        tier="minimax",
        max_tokens=2048,
        timeout=60.0,
    ),
}

# The route_request will start with the preferred model for a task,
# and if it fails, it will attempt other models in this fallback order.
FALLBACK_ORDER = ["analyst", "worker", "premium", "minimax"]
