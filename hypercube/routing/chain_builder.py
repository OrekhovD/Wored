"""Chain builder — builds candidate chains based on routing policies and provider registry."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from core.enums import RoutingMode
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy

log = logging.getLogger(__name__)


class ChainBuilder:
    """Builds ordered chains of models to try for a given mode.
    
    Chains are built from the policy file, with fallback to hardcoded defaults.
    Disabled providers and excluded models are filtered out.
    """

    def __init__(self, model_registry: ModelRegistry, routing_policy: RoutingPolicy) -> None:
        self._model_registry = model_registry
        self._routing_policy = routing_policy

    def build_chain(
        self,
        mode: RoutingMode | str,
        excluded_models: Optional[List[str]] = None,
        disabled_providers: Optional[List[str]] = None,
    ) -> List[str]:
        chain = self._routing_policy.get_candidate_chain(mode, excluded_models, disabled_providers)
        log.debug("Built chain for %s: %s", mode, chain)
        return chain

    def get_chain_metadata(self, mode: RoutingMode | str) -> dict[str, Any]:
        return self._routing_policy.get_policy_metadata(mode)
