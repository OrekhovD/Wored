"""Routing layer — model registry, policies, and fallback engine."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.enums import ModelStatus, RequestStatus, RoutingMode
from core.exceptions import NoCandidateModelError
from core.request_id import generate_request_id
from core.schemas import (
    AIRequest,
    AIResponse,
    CostEstimate,
    HealthResult,
    HandoffPackage,
    RouteDecision,
    TokenUsage,
)

logger = logging.getLogger(__name__)


# ── Default candidate chains per mode ────────────────────────────────────

import yaml
from pathlib import Path

def _load_registry() -> dict:
    paths = [
        Path("examples/provider_registry.yaml"),
        Path("D:/WORED/hypercube/examples/provider_registry.yaml"),
    ]
    for p in paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    return {}

_registry_data = _load_registry()

def _build_chains_from_registry(reg: dict) -> dict[str, list[str]]:
    chains = {}
    policies = reg.get("routing_policies", {})
    if policies:
        for mode, policy in policies.items():
            chains[mode] = policy.get("candidate_chain", [])
    if chains:
        return chains
    return {
        "free_only": ["qwen-turbo", "ernie-bot-turbo", "qwen-plus", "ai-minimax/minimax-m2.7", "glm-5"],
        "balanced": ["glm-5", "qwen-plus", "ai-minimax/minimax-m2.7", "qwen-turbo", "ernie-bot-turbo"],
        "premium": ["glm-5", "qwen-plus", "ai-minimax/minimax-m2.7", "qwen-turbo", "ernie-bot-turbo"],
    }

def _build_costs_from_registry(reg: dict) -> dict[str, dict[str, float]]:
    costs = {}
    for pid, pdata in reg.get("providers", {}).items():
        for mid, mdata in pdata.get("models", {}).items():
            costs[mid] = {
                "input": mdata.get("cost", {}).get("input_per_1k", 0.0),
                "output": mdata.get("cost", {}).get("output_per_1k", 0.0),
            }
    if costs:
        return costs
    return {
        "qwen-turbo": {"input": 0.0001, "output": 0.0003},
        "ernie-bot-turbo": {"input": 0.0001, "output": 0.0004},
        "qwen-plus": {"input": 0.0002, "output": 0.0006},
        "ai-minimax/minimax-m2.7": {"input": 0.00015, "output": 0.0006},
        "glm-5": {"input": 0.0001, "output": 0.0005},
    }

def _build_mode_map_from_registry(reg: dict) -> dict[str, dict[str, bool]]:
    mode_map = {}
    for pid, pdata in reg.get("providers", {}).items():
        for mid, mdata in pdata.get("models", {}).items():
            is_premium = mdata.get("is_premium", False)
            mode_map[mid] = {
                "free_only": not is_premium,
                "balanced": True,
                "premium": True,
            }
    if mode_map:
        return mode_map
    return {
        "qwen-turbo": {"free_only": True, "balanced": True, "premium": True},
        "ernie-bot-turbo": {"free_only": True, "balanced": True, "premium": True},
        "qwen-plus": {"free_only": False, "balanced": True, "premium": True},
        "ai-minimax/minimax-m2.7": {"free_only": False, "balanced": True, "premium": True},
        "glm-5": {"free_only": False, "balanced": True, "premium": True},
    }

def _build_provider_map_from_registry(reg: dict) -> dict[str, str]:
    mapping = {}
    for pid, pdata in reg.get("providers", {}).items():
        for mid in pdata.get("models", {}).keys():
            mapping[mid] = pid
    if mapping:
        return mapping
    return {
        "qwen-turbo": "dashscope",
        "qwen-plus": "dashscope",
        "ernie-bot-turbo": "ai_studio",
        "ai-minimax/minimax-m2.7": "nvapi",
        "glm-5": "zhipu",
    }

_DEFAULT_CHAINS = _build_chains_from_registry(_registry_data)
_MODEL_COSTS = _build_costs_from_registry(_registry_data)
_MODE_MODEL_MAP = _build_mode_map_from_registry(_registry_data)
_PROVIDER_MAP = _build_provider_map_from_registry(_registry_data)


class ModelRegistry:
    """In-memory model registry backed by DB seed data."""

    def __init__(self):
        self._models: dict[str, dict[str, Any]] = {}
        self._populate_defaults()

    def _populate_defaults(self) -> None:
        for model_id in _DEFAULT_CHAINS["free_only"]:
            is_premium = not _MODE_MODEL_MAP.get(model_id, {}).get("free_only", True)
            self._models[model_id] = {
                "model_id": model_id,
                "status": "active",
                "is_premium": is_premium,
                "costs": _MODEL_COSTS.get(model_id, {"input": 0.0, "output": 0.0}),
            }

    def get_active_models(self, mode: str | None = None) -> list[dict[str, Any]]:
        results = []
        for info in self._models.values():
            if info["status"] != "active":
                continue
            if mode and info["is_premium"] and mode == "free_only":
                continue
            results.append(info)
        return results

    def is_model_available(self, model_id: str, mode: str) -> bool:
        info = self._models.get(model_id)
        if not info or info["status"] != "active":
            return False
        if info["is_premium"] and mode == "free_only":
            return False
        allowed = _MODE_MODEL_MAP.get(model_id, {})
        return allowed.get(mode, True)

    def get_cost(self, model_id: str) -> dict[str, float]:
        return _MODEL_COSTS.get(model_id, {"input": 0.0, "output": 0.0})

    def update_status(self, model_id: str, status: str) -> None:
        if model_id in self._models:
            self._models[model_id]["status"] = status

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._models.values())


class FallbackEngine:
    """Resolves models via policy chain with automatic fallback."""

    def __init__(
        self,
        registry: ModelRegistry,
        adapters: dict[str, Any],
    ):
        self.registry = registry
        self.adapters = adapters
        self._in_flight: dict[str, tuple[str, list[str]]] = {}

    # ── Resolve initial candidate chain ──────────────────────────────────

    def get_candidate_chain(
        self,
        mode: str,
        excluded: list[str] | None = None,
        context_size_estimate: int = 0,
    ) -> list[str]:
        base = list(_DEFAULT_CHAINS.get(RoutingMode(mode) if isinstance(mode, str) else mode, _DEFAULT_CHAINS["free_only"]))
        excluded_set = set(excluded or [])

        chain: list[str] = []
        for mid in base:
            if mid in excluded_set:
                continue
            if not self.registry.is_model_available(mid, mode):
                continue
            if self.registry._models.get(mid, {}).get("status") != "active":
                continue
            chain.append(mid)

        if not chain:
            raise NoCandidateModelError(f"No active candidates in mode={mode}")

        return chain

    async def resolve_chain(
        self,
        mode: str,
        excluded: list[str] | None = None,
        context_size_estimate: int = 0,
    ) -> list[str]:
        return self.get_candidate_chain(mode, excluded, context_size_estimate)

    # ── Execute with full fallback loop ──────────────────────────────────

    async def execute_with_fallback(
        self,
        request: AIRequest,
        mode: str,
        user_id: int,
        conversation_id: str | None = None,
    ) -> AIResponse:
        candidate_chain = await self.resolve_chain(mode)
        route_decision = RouteDecision(
            request_type="market_analysis",
            mode=mode,
            current_model=candidate_chain[0] if candidate_chain else "",
            candidate_chain=candidate_chain,
            selected_model="",
            handoff_required=False,
            handoff_summary_version="v1",
        )

        attempts: list[str] = []
        last_error: Exception | None = None

        for candidate in candidate_chain:
            attempts.append(candidate)
            adapter_key = self._provider_for_model(candidate)
            if not adapter_key or adapter_key not in self.adapters:
                logger.warning("No adapter for model=%s candidate=%s", candidate, attempts)
                last_error = RuntimeError(f"No adapter for {candidate}")
                continue

            try:
                adapter = self.adapters[adapter_key]
                ai_req = AIRequest(
                    model=candidate,
                    messages=request.messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    stream=False,
                )
                response = await adapter.invoke(ai_req)
                response.model = candidate
                route_decision.selected_model = candidate
                route_decision.reason_current_model_excluded = (
                    f"tried_first_then_{','.join(attempts[:-1])}" if len(attempts) > 1 else None
                )
                return response

            except Exception as exc:
                last_error = exc
                logger.info(
                    "Fallback candidate %s failed (%s), trying next…",
                    candidate, type(exc).__name__,
                )
                continue

        route_decision.selected_model = "none"
        route_decision.reason_current_model_excluded = f"all_candidates_failed:{','.join(attempts)}"
        raise ProviderError(
            f"All fallback candidates exhausted ({', '.join(attempts)}). Last error: {last_error}"
        ) from last_error

    # ── Manual switch with handoff ───────────────────────────────────────

    async def manual_switch(
        self,
        session_id: str,
        target_model: str,
        old_model: str,
        mode: str,
        market_facts: str | None = None,
    ) -> tuple[str, HandoffPackage]:
        if not self.registry.is_model_available(target_model, mode):
            raise ValueError(f"Model {target_model} not available in mode {mode}")

        adapter_key = self._provider_for_model(target_model)
        if not adapter_key or adapter_key not in self.adapters:
            raise ValueError(f"No adapter for target model {target_model}")

        handoff = HandoffPackage(
            version="v1",
            system_rules=(
                "You are an AI market analyst specializing in HTX exchange data. "
                "Provide structured, actionable analysis without executing trades. "
                "Always include risk disclaimers."
            ),
            handoff_summary=(
                f"Context switched from '{old_model}' to '{target_model}'. "
                f"Mode: {mode}. "
                + (f"Market facts at handoff: {market_facts}" if market_facts else "")
            ),
            last_user_request="",
            delta_market_update=market_facts,
        )
        return target_model, handoff

    @staticmethod
    def _provider_for_model(model_id: str) -> str | None:
        return _PROVIDER_MAP.get(model_id)
