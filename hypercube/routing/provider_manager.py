"""Provider manager — manages provider health, availability, and fallbacks."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.enums import ModelStatus, RoutingMode
from routing.chain_builder import ChainBuilder
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy

log = logging.getLogger(__name__)


class ProviderManager:
    """Manages the state of providers, including health checks, fallbacks, and premium unlock policies.
    
    Providers are loaded from the registry file, with fallback to hardcoded defaults.
    Health checks are performed periodically, and chains are updated as needed.
    """

    def __init__(self, model_registry: ModelRegistry, chain_builder: ChainBuilder, routing_policy: RoutingPolicy) -> None:
        self._model_registry = model_registry
        self._chain_builder = chain_builder
        self._routing_policy = routing_policy
        self._disabled_providers: List[str] = []
        self._health_check_config: Dict[str, int] = self._routing_policy.get_health_check_config()
        self._fallback_reasons: Dict[str, str] = self._routing_policy.get_fallback_reasons()

    def get_chain(self, mode: RoutingMode | str, excluded_models: Optional[List[str]] = None) -> List[str]:
        return self._chain_builder.build_chain(
            mode, excluded_models=excluded_models, disabled_providers=self._disabled_providers
        )

    def get_chain_metadata(self, mode: RoutingMode | str) -> dict[str, Any]:
        return self._chain_builder.get_chain_metadata(mode)

    def get_disabled_providers(self) -> List[str]:
        return self._disabled_providers

    def is_provider_enabled(self, provider_id: str) -> bool:
        return self._model_registry.is_provider_enabled(provider_id)

    def get_provider_config(self, provider_id: str) -> dict[str, Any]:
        return self._model_registry.get_provider_config(provider_id)

    def get_model(self, model_id: str) -> dict | None:
        return self._model_registry.get_model(model_id)

    def get_active_models(self, provider_id: Optional[str] = None) -> List[dict]:
        return self._model_registry.get_active_models(provider_id)

    def get_models_by_provider(self, provider_id: str) -> List[dict]:
        return self._model_registry.get_models_by_provider(provider_id)

    def update_model_status(self, model_id: str, status: str) -> None:
        self._model_registry.update_model_status(model_id, status)

    def is_model_available(self, model_id: str, mode: RoutingMode | str) -> bool:
        return self._model_registry.is_model_available(model_id, mode)

    def get_fallback_reason(self, reason_code: str) -> str:
        return self._fallback_reasons.get(reason_code, "Unknown reason")

    def is_premium_unlocked(self, mode: RoutingMode | str, quota_remaining_pct: float) -> bool:
        return self._routing_policy.is_premium_unlocked(mode, quota_remaining_pct)
