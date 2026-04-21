"""Provider factory — registers adapters and creates them from config."""
from __future__ import annotations

from typing import Any

from core.config import AppConfiguration
from core.exceptions import ConfigError
from providers.htx_adapter import HTXMarketDataAdapter
from providers.interface import AIProviderInterface
from providers.openai_compatible import OpenAICompatibleAdapter, _ModelInfo


class ProviderFactory:
    def __init__(self) -> None:
        self._adapters: dict[str, AIProviderInterface] = {}

    def register_provider(self, provider_id: str, adapter: AIProviderInterface) -> None:
        self._adapters[provider_id] = adapter

    def get_adapter(self, provider_id: str) -> AIProviderInterface:
        if provider_id not in self._adapters:
            raise ConfigError(f"Provider '{provider_id}' not registered")
        return self._adapters[provider_id]

    def list_available(self) -> dict[str, AIProviderInterface]:
        return dict(self._adapters)

    def build_from_config(self, config: AppConfiguration) -> None:
        """Create and register all enabled providers from *config*."""
        for pid, pc in config.provider_configs.items():
            api_key = pc.get("api_key", "")
            if not api_key:
                continue
            models: dict[str, _ModelInfo] = {}
            for mid, minfo in pc["supported_models"].items():
                costs = pc["costs"].get(mid, {})
                models[mid] = _ModelInfo(
                    input_cost_per_1k=costs.get("input", 0),
                    output_cost_per_1k=costs.get("output", 0),
                    is_premium=minfo.get("is_premium", False),
                    supports_streaming=minfo.get("supports_streaming", True),
                    supports_system_prompt=minfo.get("supports_system_prompt", True),
                )
            self.register_provider(
                pid,
                OpenAICompatibleAdapter(
                    provider_id=pid,
                    api_key=api_key,
                    base_url=pc["base_url"],
                    models=models,
                ),
            )

    def create_htx_adapter(self, config: AppConfiguration) -> HTXMarketDataAdapter:
        return HTXMarketDataAdapter(
            api_key=config.HTX_API_KEY,
            api_secret=config.HTX_API_SECRET,
            base_url=config.HTX_BASE_URL,
        )

def create_provider_adapters(config: AppConfiguration) -> dict[str, Any]:
    factory = ProviderFactory()
    factory.build_from_config(config)
    return factory.list_available()
