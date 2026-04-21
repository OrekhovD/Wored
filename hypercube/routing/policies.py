"""Routing policies — candidate chains loaded from config or registry."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.enums import RoutingMode

log = logging.getLogger(__name__)

# Fallback hardcoded chains if config file is not available
_FALLBACK_CHAINS: dict[RoutingMode, list[str]] = {
    RoutingMode.FREE_ONLY: [
        "qwen-turbo",
        "ernie-bot-turbo",
        "qwen-plus",
        "ai-minimax/minimax-m2.7",
    ],
    RoutingMode.BALANCED: [
        "glm-5",
        "qwen-plus",
        "ai-minimax/minimax-m2.7",
        "qwen-turbo",
        "ernie-bot-turbo",
    ],
    RoutingMode.PREMIUM: [
        "glm-5",
        "qwen-plus",
        "ai-minimax/minimax-m2.7",
        "qwen-turbo",
        "ernie-bot-turbo",
    ],
}


def _load_registry_from_yaml() -> dict[str, Any] | None:
    """Load provider registry from YAML config file."""
    try:
        import yaml
    except ImportError:
        log.debug("PyYAML not installed, using hardcoded routing chains")
        return None

    # Search for registry file in standard locations
    candidates = [
        Path("examples/provider_registry.yaml"),
        Path("data/provider_registry.yaml"),
        Path("data/provider_registry.json"),
    ]

    for path in candidates:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    if path.suffix == ".json":
                        import json
                        return json.load(f)
                    return yaml.safe_load(f)
            except Exception as e:
                log.warning("Failed to load registry from %s: %s", path, e)

    return None


def _parse_chains_from_registry(registry: dict[str, Any]) -> dict[RoutingMode, list[str]]:
    """Parse candidate chains from registry dict."""
    chains: dict[RoutingMode, list[str]] = {}
    policies = registry.get("routing_policies", {})

    for mode_name, policy in policies.items():
        try:
            mode = RoutingMode(mode_name)
            chain = policy.get("candidate_chain", [])
            if chain:
                chains[mode] = chain
        except ValueError:
            log.warning("Unknown routing mode in registry: %s", mode_name)

    return chains


class RoutingPolicy:
    """Define which models to try and in what order for each mode.
    
    Chains are loaded from YAML config if available, otherwise fall back
    to hardcoded defaults.
    """

    def __init__(self) -> None:
        self._chains: dict[RoutingMode, list[str]] = dict(_FALLBACK_CHAINS)
        self._loaded_from_config = False
        self._registry: dict[str, Any] | None = None
        self._load_config()

    def _load_config(self) -> None:
        """Load routing chains from config file."""
        registry = _load_registry_from_yaml()
        if registry is None:
            log.debug("Using hardcoded routing chains (no config file found)")
            return

        self._registry = registry
        chains = _parse_chains_from_registry(registry)
        if chains:
            self._chains.update(chains)
            self._loaded_from_config = True
            log.info("Loaded routing chains from config: %s", list(chains.keys()))

    def reload(self) -> None:
        """Reload configuration from disk."""
        self._chains = dict(_FALLBACK_CHAINS)
        self._loaded_from_config = False
        self._registry = None
        self._load_config()

    @property
    def is_config_driven(self) -> bool:
        """Whether chains were loaded from config file."""
        return self._loaded_from_config

    def get_candidate_chain(
        self,
        mode: RoutingMode | str,
        excluded_models: list[str] | None = None,
        disabled_providers: list[str] | None = None,
    ) -> list[str]:
        """Get ordered candidate chain for the given mode.
        
        Args:
            mode: Routing mode (free_only, balanced, premium)
            excluded_models: Model IDs to exclude from chain
            disabled_providers: Provider IDs to exclude (models from disabled providers removed)
        
        Returns:
            Ordered list of model IDs
        """
        if isinstance(mode, str):
            try:
                mode = RoutingMode(mode)
            except ValueError:
                mode = RoutingMode.FREE_ONLY

        chain = list(self._chains.get(mode, self._chains[RoutingMode.FREE_ONLY]))

        # Apply excluded models
        if excluded_models:
            chain = [m for m in chain if m not in excluded_models]

        # Apply disabled providers
        if disabled_providers and self._registry:
            providers = self._registry.get("providers", {})
            models_to_remove = set()
            for pid in disabled_providers:
                provider = providers.get(pid, {})
                for model_id in provider.get("models", {}):
                    models_to_remove.add(model_id)
            chain = [m for m in chain if m not in models_to_remove]

        return chain

    def get_policy_metadata(self, mode: RoutingMode | str) -> dict[str, Any]:
        """Get full policy metadata for a mode."""
        if isinstance(mode, str):
            try:
                mode = RoutingMode(mode)
            except ValueError:
                mode = RoutingMode.FREE_ONLY

        if self._registry:
            policies = self._registry.get("routing_policies", {})
            return policies.get(mode.value, {})
        return {}

    def get_disabled_providers(self) -> list[str]:
        """Get list of disabled providers from registry."""
        if not self._registry:
            return []
        providers = self._registry.get("providers", {})
        return [pid for pid, cfg in providers.items() if not cfg.get("enabled", True)]

    def get_fallback_reasons(self) -> dict[str, str]:
        """Get fallback reason codes from registry."""
        if self._registry:
            return self._registry.get("fallback_reasons", {})
        return {
            "timeout": "Provider did not respond within timeout",
            "quota_exceeded": "Token quota exhausted",
            "rate_limit": "Provider rate limit reached",
            "invalid_response": "Provider returned malformed response",
            "provider_unavailable": "Provider is offline or unreachable",
            "policy_rejection": "Model excluded by routing policy",
        }

    def get_health_check_config(self) -> dict[str, int]:
        """Get health check configuration from registry."""
        if self._registry:
            return self._registry.get("health_check", {})
        return {
            "interval_seconds": 300,
            "timeout_seconds": 15,
            "consecutive_failures_for_degraded": 3,
            "consecutive_successes_for_recovery": 2,
        }

    def get_provider_timeout(self, provider_id: str) -> int:
        """Get timeout for a specific provider."""
        if self._registry:
            providers = self._registry.get("providers", {})
            return providers.get(provider_id, {}).get("timeout_seconds", 30)
        return 30

    def get_provider_retry_config(self, provider_id: str) -> dict[str, int]:
        """Get retry config for a specific provider."""
        if self._registry:
            providers = self._registry.get("providers", {})
            cfg = providers.get(provider_id, {})
            return {
                "retry_count": cfg.get("retry_count", 1),
                "retry_delay_ms": cfg.get("retry_delay_ms", 1000),
            }
        return {"retry_count": 1, "retry_delay_ms": 1000}

    def is_premium_unlocked(self, mode: RoutingMode | str, quota_remaining_pct: float) -> bool:
        """Check if premium models are unlocked for the given mode and quota."""
        if isinstance(mode, str):
            try:
                mode = RoutingMode(mode)
            except ValueError:
                return False

        if self._registry:
            unlock_cfg = self._registry.get("premium_unlock", {})
            required_mode = unlock_cfg.get("requires_mode", "premium")
            required_quota = unlock_cfg.get("requires_quota_above_pct", 10.0)
            if mode.value != required_mode:
                return False
            return quota_remaining_pct >= required_quota

        return mode == RoutingMode.PREMIUM
