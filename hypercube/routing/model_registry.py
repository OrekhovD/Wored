"""Model registry — loads model metadata from config or registry file."""
from __future__ import annotations

import logging
from typing import Any

from core.enums import ModelStatus, RoutingMode
from routing.policies import _load_registry_from_yaml

log = logging.getLogger(__name__)

# Default model catalogue (fallback if no config)
_DEFAULTS: list[dict] = [
    {
        "model_id": "glm-5",
        "provider_id": "zhipu",
        "display_name": "GLM-5",
        "status": "active",
        "is_premium": True,
        "supports_streaming": True,
        "supports_system_prompt": True,
        "max_tokens": 4096,
        "tags": ["premium", "best_quality"],
    },
    {
        "model_id": "qwen-plus",
        "provider_id": "dashscope",
        "display_name": "Qwen Plus",
        "status": "active",
        "is_premium": False,
        "supports_streaming": True,
        "supports_system_prompt": True,
        "max_tokens": 8192,
        "tags": ["balanced", "analysis"],
    },
    {
        "model_id": "qwen-turbo",
        "provider_id": "dashscope",
        "display_name": "Qwen Turbo",
        "status": "active",
        "is_premium": False,
        "supports_streaming": True,
        "supports_system_prompt": True,
        "max_tokens": 8192,
        "tags": ["free", "fast", "fallback"],
    },
    {
        "model_id": "ai-minimax/minimax-m2.7",
        "provider_id": "nvapi",
        "display_name": "MiniMax M2.7",
        "status": "active",
        "is_premium": False,
        "supports_streaming": True,
        "supports_system_prompt": True,
        "max_tokens": 4096,
        "tags": ["balanced", "reliable"],
    },
    {
        "model_id": "ernie-bot-turbo",
        "provider_id": "ai_studio",
        "display_name": "ERNIE Bot Turbo",
        "status": "active",
        "is_premium": False,
        "supports_streaming": False,
        "supports_system_prompt": True,
        "max_tokens": 2048,
        "tags": ["free", "fallback"],
    },
]


class ModelRegistry:
    """Holds model metadata and answers availability queries.
    
    Models are loaded from YAML config if available, otherwise fall back
    to hardcoded defaults.
    """

    def __init__(self, extra_models: list[dict] | None = None) -> None:
        self._models: dict[str, dict] = {}
        self._loaded_from_config = False
        self._registry: dict[str, Any] | None = None

        # Load from config first
        self._load_from_config()

        # Apply hardcoded defaults as fallback for any missing models
        for m in _DEFAULTS:
            if m["model_id"] not in self._models:
                self._models[m["model_id"]] = dict(m)

        # Apply extra models (highest priority)
        for m in (extra_models or []):
            self._models[m["model_id"]] = dict(m)

    def _load_from_config(self) -> None:
        """Load models from provider registry YAML."""
        if self._registry is not None:
            log.info("Using cached registry, skipping reload")
            return

        registry = _load_registry_from_yaml()
        if registry is None:
            return

        self._registry = registry
        providers = registry.get("providers", {})

        for pid, provider_cfg in providers.items():
            if not provider_cfg.get("enabled", True):
                log.info("Provider %s is disabled in config, skipping its models", pid)
                continue

            for mid, model_cfg in provider_cfg.get("models", {}).items():
                self._models[mid] = {
                    "model_id": mid,
                    "provider_id": pid,
                    "display_name": model_cfg.get("display_name", mid),
                    "status": model_cfg.get("status", ModelStatus.ACTIVE),
                    "is_premium": model_cfg.get("is_premium", False),
                    "supports_streaming": model_cfg.get("supports_streaming", True),
                    "supports_system_prompt": model_cfg.get("supports_system_prompt", True),
                    "max_tokens": model_cfg.get("max_tokens", 4096),
                    "tags": model_cfg.get("tags", []),
                    "cost": model_cfg.get("cost", {}),
                }

        if self._models:
            self._loaded_from_config = True
            log.info("Loaded %d models from config", len(self._models))

    @property
    def is_config_driven(self) -> bool:
        return self._loaded_from_config

    def reload(self) -> None:
        """Reload models from config."""
        self._models.clear()
        self._loaded_from_config = False
        self._registry = None
        self._load_from_config()
        for m in _DEFAULTS:
            if m["model_id"] not in self._models:
                self._models[m["model_id"]] = dict(m)

    def get_model(self, model_id: str) -> dict | None:
        return self._models.get(model_id)

    def get_active_models(self, provider_id: Optional[str] = None) -> List[dict]:
        result = [m for m in self._models.values() if m["status"] == ModelStatus.ACTIVE]
        if provider_id:
            result = [m for m in result if m["provider_id"] == provider_id]
        return result

    def get_models_by_provider(self, provider_id: str) -> List[dict]:
        return [m for m in self._models.values() if m["provider_id"] == provider_id]

    def get_all_models(self) -> List[dict]:
        return list(self._models.values())

    def update_model_status(self, model_id: str, status: str) -> None:
        if model_id in self._models:
            self._models[model_id]["status"] = status

    def is_model_available(self, model_id: str, mode: RoutingMode | str) -> bool:
        m = self._models.get(model_id)
        if m is None or m["status"] != ModelStatus.ACTIVE:
            return False
        if m.get("is_premium") and mode == RoutingMode.FREE_ONLY:
            return False
        return True

    def get_provider_ids(self) -> List[str]:
        """Get list of all provider IDs."""
        return list({m["provider_id"] for m in self._models.values()})

    def is_provider_enabled(self, provider_id: str) -> bool:
        """Check if provider is enabled."""
        if self._registry:
            providers = self._registry.get("providers", {})
            return providers.get(provider_id, {}).get("enabled", True)
        return True

    def get_provider_config(self, provider_id: str) -> Dict[str, Any]:
        """Get full provider config from registry."""
        if self._registry:
            providers = self._registry.get("providers", {})
            return providers.get(provider_id, {})
        return {}

