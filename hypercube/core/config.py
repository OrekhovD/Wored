"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings


class AppConfiguration(BaseSettings):
    """All settings for the Hytergram AI Gateway."""

    # ── Telegram ────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    ADMIN_TELEGRAM_IDS: str = ""

    # ── HTX (read-only) ─────────────────────────────────────────────────
    HTX_API_KEY: str = ""
    HTX_API_SECRET: str = ""
    HTX_BASE_URL: str = "https://api.htx.com"

    # ── Database ────────────────────────────────────────────────────────
    SQLITE_DB_URL: str = "sqlite+aiosqlite:///./data/hytergram.db"

    # ── AI Providers ────────────────────────────────────────────────────
    DASHSCOPE_API_KEY: str = ""
    NVAPI_API_KEY: str = ""
    GLM5_API_KEY: str = ""
    AI_STUDIO_API_KEY: str = ""

    # ── Gateway ─────────────────────────────────────────────────────────
    GATEWAY_HOST: str = "0.0.0.0"
    GATEWAY_PORT: int = 8000

    # ─ behaviour defaults ───────────────────────────────────────────────
    DEFAULT_ROUTING_MODE: str = "free_only"
    LOG_LEVEL: str = "INFO"
    CONTEXT_MAX_TOKENS: int = 4000
    QUOTA_WARNING_THRESHOLD_PCT: float = 20.0
    QUOTA_CRITICAL_THRESHOLD_PCT: float = 10.0
    QUOTA_HARD_STOP_THRESHOLD_PCT: float = 3.0
    REQUEST_TIMEOUT_SECONDS: int = 120
    FALLBACK_RETRY_COUNT: int = 2
    CACHING_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 60
    TELEGRAM_POLL_TIMEOUT: int = 10

    @property
    def provider_configs(self) -> dict[str, dict[str, Any]]:
        """Return a mapping of provider-id → connection config."""
        return {
            "dashscope": {
                "provider_id": "dashscope",
                "display_name": "DashScope (Qwen Cloud)",
                "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                "api_key": self.DASHSCOPE_API_KEY,
                "models": ["qwen-plus", "qwen-turbo"],
                "costs": {
                    "qwen-plus": {"input": 0.0002, "output": 0.0006},
                    "qwen-turbo": {"input": 0.0001, "output": 0.0003},
                },
                "supported_models": {
                    "qwen-plus": {
                        "is_premium": False,
                        "supports_streaming": True,
                        "supports_system_prompt": True,
                    },
                    "qwen-turbo": {
                        "is_premium": False,
                        "supports_streaming": True,
                        "supports_system_prompt": True,
                    },
                },
            },
            "nvapi": {
                "provider_id": "nvapi",
                "display_name": "NVIDIA nvapi (MiniMax)",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "api_key": self.NVAPI_API_KEY,
                "models": ["ai-minimax/minimax-m2.7"],
                "costs": {
                    "ai-minimax/minimax-m2.7": {"input": 0.00015, "output": 0.0006},
                },
                "supported_models": {
                    "ai-minimax/minimax-m2.7": {
                        "is_premium": False,
                        "supports_streaming": True,
                        "supports_system_prompt": True,
                    },
                },
            },
            "zhipu": {
                "provider_id": "zhipu",
                "display_name": "Zhipu AI (GLM-5)",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key": self.GLM5_API_KEY,
                "models": ["glm-5"],
                "costs": {
                    "glm-5": {"input": 0.0001, "output": 0.0005},
                },
                "supported_models": {
                    "glm-5": {
                        "is_premium": True,
                        "supports_streaming": True,
                        "supports_system_prompt": True,
                    },
                },
            },
            "ai_studio": {
                "provider_id": "ai_studio",
                "display_name": "Baidu AI Studio",
                "base_url": "https://qianfan.baidubce.com/v2",
                "api_key": self.AI_STUDIO_API_KEY,
                "models": ["ernie-bot-turbo"],
                "costs": {
                    "ernie-bot-turbo": {"input": 0.0001, "output": 0.0004},
                },
                "supported_models": {
                    "ernie-bot-turbo": {
                        "is_premium": False,
                        "supports_streaming": False,
                        "supports_system_prompt": True,
                    },
                },
            },
        }

    @property
    def admin_user_ids(self) -> set[int]:
        """Parsed admin Telegram IDs."""
        if not self.ADMIN_TELEGRAM_IDS.strip():
            return set()
        return {int(x.strip()) for x in self.ADMIN_TELEGRAM_IDS.split(",") if x.strip()}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
