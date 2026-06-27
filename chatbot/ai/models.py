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
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/"
OLLAMA_CLOUD_ENDPOINT = "https://ollama.com/v1"
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
        api_key_env="NVIDIA_API_KEY",
        tier="minimax",
        max_tokens=2048,
        timeout=12.0,
    ),
    "omniroute_reasoning": ModelConfig(
        name="OmniRoute Reasoning (Qwen 3.7 Max)",
        model_id=os.getenv("REASONING_MODEL", "qwen/qwen3.7-max"),
        endpoint=os.getenv("AI_GATEWAY_BASE_URL", "https://cloud.omniroute.online/v1/"),
        api_key_env="AI_GATEWAY_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "omniroute_execution": ModelConfig(
        name="OmniRoute Execution (Gemini 3.5 Flash)",
        model_id=os.getenv("EXECUTION_MODEL", "google/gemini-3.5-flash"),
        endpoint=os.getenv("AI_GATEWAY_BASE_URL", "https://cloud.omniroute.online/v1/"),
        api_key_env="AI_GATEWAY_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "worker_deepseek": ModelConfig(
        name="Robotyaga (DeepSeek V4 Flash)",
        model_id="deepseek-v4-flash",
        endpoint=DEEPSEEK_ENDPOINT,
        api_key_env="DEEPSEEK_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "analyst_deepseek": ModelConfig(
        name="Analyst (DeepSeek V4 Pro)",
        model_id="deepseek-v4-pro",
        endpoint=DEEPSEEK_ENDPOINT,
        api_key_env="DEEPSEEK_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "worker_deepseek_or": ModelConfig(
        name="Robotyaga (DeepSeek 3.2 OR)",
        model_id="deepseek/deepseek-v3.2",
        endpoint=OPENROUTER_ENDPOINT,
        api_key_env="OPENROUTER_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=10.0,
    ),
    "analyst_deepseek_or": ModelConfig(
        name="Analyst (DeepSeek v4 Pro OR)",
        model_id="deepseek/deepseek-v4-pro",
        endpoint=OPENROUTER_ENDPOINT,
        api_key_env="OPENROUTER_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "worker_ollama": ModelConfig(
        name="Robotyaga (Ollama Cloud)",
        model_id=os.getenv("OLLAMA_WORKER_MODEL", "deepseek-v4-flash"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="worker",
        max_tokens=256,
        timeout=15.0,
    ),
    "analyst_ollama": ModelConfig(
        name="Analyst (Ollama Cloud)",
        model_id=os.getenv("OLLAMA_ANALYST_MODEL", "deepseek-v4-pro"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=60.0,
    ),
    "premium_ollama": ModelConfig(
        name="Strategist (Ollama Cloud)",
        model_id=os.getenv("OLLAMA_PREMIUM_MODEL", "glm-5.2"),
        endpoint=OLLAMA_CLOUD_ENDPOINT,
        api_key_env="OLLAMA_CLOUD_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    # ─── NVIDIA NIM models (17 working keys, 26 Jun 2026) ─────────────
    "worker_gemma_nim": ModelConfig(
        name="Robotyaga (Gemma 4 31B NIM)",
        model_id="google/gemma-4-31b-it",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_GEMMA_API_KEY",
        tier="worker",
        max_tokens=512,
        timeout=15.0,
    ),
    "worker_nemotron_nano30": ModelConfig(
        name="Robotyaga (Nemotron Nano 30B)",
        model_id="nvidia/nemotron-3-nano-30b-a3b",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_NEMOTRON_NANO30_API_KEY",
        tier="worker",
        max_tokens=512,
        timeout=20.0,
    ),
    "worker_mixtral": ModelConfig(
        name="Robotyaga (Mixtral 8x7B)",
        model_id="mistralai/mixtral-8x7b-instruct-v0.1",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MIXTRAL_API_KEY",
        tier="worker",
        max_tokens=512,
        timeout=15.0,
    ),
    "worker_ministral": ModelConfig(
        name="Robotyaga (Ministral 14B)",
        model_id="mistralai/ministral-14b-instruct-2512",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MINISTRAL_API_KEY",
        tier="worker",
        max_tokens=512,
        timeout=15.0,
    ),
    "analyst_minimax_m3": ModelConfig(
        name="Analyst (MiniMax M3 NIM)",
        model_id="minimaxai/minimax-m3",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MINIMAX_M3_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_minimax_m27": ModelConfig(
        name="Analyst (MiniMax M2.7 NIM)",
        model_id="minimaxai/minimax-m2.7",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MINIMAX_M27_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_kimi": ModelConfig(
        name="Analyst (Kimi K2.6 NIM)",
        model_id="moonshotai/kimi-k2.6",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_KIMI_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_glm_nim": ModelConfig(
        name="Analyst (GLM-5.1 NIM)",
        model_id="z-ai/glm-5.1",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_GLM_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_mistral_medium": ModelConfig(
        name="Analyst (Mistral Medium 3.5 128B)",
        model_id="mistralai/mistral-medium-3.5-128b",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MISTRAL_MEDIUM_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_mistral_small": ModelConfig(
        name="Analyst (Mistral Small 4 119B)",
        model_id="mistralai/mistral-small-4-119b-2603",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MISTRAL_SMALL_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_qwen35_nim": ModelConfig(
        name="Analyst (Qwen 3.5 122B NIM)",
        model_id="qwen/qwen3.5-122b-a10b",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_QWEN35_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_qwen_next": ModelConfig(
        name="Analyst (Qwen 3 Next 80B NIM)",
        model_id="qwen/qwen3-next-80b-a3b-instruct",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_QWEN_NEXT_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "analyst_mistral_nemotron": ModelConfig(
        name="Analyst (Mistral Nemotron)",
        model_id="mistralai/mistral-nemotron",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MISTRAL_NEMOTRON_API_KEY",
        tier="analyst",
        max_tokens=2048,
        timeout=30.0,
    ),
    "premium_nemotron_ultra": ModelConfig(
        name="Strategist (Nemotron Ultra 550B)",
        model_id="nvidia/nemotron-3-ultra-550b-a55b",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_NEMOTRON_ULTRA_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "premium_nemotron_super": ModelConfig(
        name="Strategist (Nemotron Super 120B)",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_NEMOTRON_SUPER_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=90.0,
    ),
    "premium_nemotron_49b": ModelConfig(
        name="Strategist (Nemotron Super 49B)",
        model_id="nvidia/llama-3.3-nemotron-super-49b-v1",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_NEMOTRON_49B_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=60.0,
    ),
    "premium_nemotron_49b_v15": ModelConfig(
        name="Strategist (Nemotron Super 49B v1.5)",
        model_id="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_NEMOTRON_49B_V15_API_KEY",
        tier="premium",
        max_tokens=8192,
        timeout=60.0,
    ),
    "premium_mistral_large": ModelConfig(
        name="Strategist (Mistral Large 3 675B)",
        model_id="mistralai/mistral-large-3-675b-instruct-2512",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_MISTRAL_LARGE_API_KEY",
        tier="premium",
        max_tokens=2048,
        timeout=60.0,
    ),
    "premium_dracarys": ModelConfig(
        name="Strategist (Dracarys Llama 3.1 70B)",
        model_id="abacusai/dracarys-llama-3.1-70b-instruct",
        endpoint=MINIMAX_NVIDIA_ENDPOINT,
        api_key_env="NVIDIA_DRACARYS_API_KEY",
        tier="premium",
        max_tokens=4096,
        timeout=60.0,
    ),
}


WORKER_MODEL_CHAIN = ["worker_ollama", "omniroute_execution", "worker", "worker_qwen35", "worker_qwen_legacy", "worker_deepseek", "worker_deepseek_or", "worker_glm", "worker_gemini", "worker_gemma_nim", "worker_nemotron_nano30", "worker_mixtral", "worker_ministral"]
ANALYST_MODEL_CHAIN = ["analyst_ollama", "omniroute_reasoning", "analyst", "analyst_qwen27b", "analyst_qwen_extra", "analyst_deepseek", "analyst_deepseek_or", "analyst_glm", "analyst_minimax_m3", "analyst_minimax_m27", "analyst_kimi", "analyst_glm_nim", "analyst_mistral_medium", "analyst_mistral_small", "analyst_qwen35_nim", "analyst_qwen_next", "analyst_mistral_nemotron"]
PREMIUM_MODEL_CHAIN = ["premium_ollama", "omniroute_reasoning", "premium", "premium_qwen35b", "analyst_deepseek_or", "premium_glm", "premium_nemotron_ultra", "premium_nemotron_super", "premium_nemotron_49b", "premium_nemotron_49b_v15", "premium_mistral_large", "premium_dracarys"]


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
