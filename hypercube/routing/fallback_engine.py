"""Fallback engine — resolve a model, try it, fallback on failure."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.enums import RoutingMode
from core.exceptions import NoCandidateModelError, ProviderError, RateLimitError
from core.request_id import generate_request_id
from core.schemas import AIRequest, AIResponse, RouteDecision

log = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    selected_model: str
    route_decision: RouteDecision
    fallback_needed: bool


class FallbackEngine:
    """Select a candidate and execute with automatic fallback."""

    def __init__(self) -> None:
        self._provider_adapters: dict = {}

    def set_provider_adapters(self, adapters: dict[str, object]) -> None:
        """Map model_id → provider_adapter for this invocation."""
        self._provider_adapters = adapters

    def resolve(
        self,
        mode: RoutingMode | str,
        excluded_models: list[str] | None = None,
    ) -> ResolveResult:
        from routing.policies import RoutingPolicy
        policy = RoutingPolicy()
        chain = policy.get_candidate_chain(mode, excluded_models)
        if not chain:
            raise NoCandidateModelError("No candidate models available")

        selected = chain[0]
        decision = RouteDecision(
            request_type="market_analysis",
            mode=str(mode),
            current_model=chain[0],
            candidate_chain=chain,
            selected_model=selected,
            handoff_required=len(chain) > 1,
            handoff_summary_version="v1",
            reason_current_model_excluded=None,
        )
        return ResolveResult(selected_model=selected, route_decision=decision, fallback_needed=len(chain) > 1)

    async def execute_with_fallback(
        self,
        request: AIRequest,
        providers_by_model: dict[str, object],
        mode: RoutingMode | str,
    ) -> tuple[AIResponse, RouteDecision]:
        from routing.policies import RoutingPolicy
        policy = RoutingPolicy()
        chain = policy.get_candidate_chain(mode)

        last_error: Exception | None = None
        selected = chain[0] if chain else None
        final_response: AIResponse | None = None
        attempts: list[tuple[str, bool, str | None]] = []

        for model_id in chain:
            adapter = providers_by_model.get(model_id)
            if adapter is None:
                attempts.append((model_id, False, "adapter_not_registered"))
                log.warning("No adapter for model %s", model_id)
                continue

            try:
                content, usage = await adapter.invoke(
                    model=model_id,
                    messages=request.messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    stream=request.stream,
                )
                usage_dict = None
                if usage:
                    usage_dict = {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                final_response = AIResponse(
                    content=content,
                    model=model_id,
                    provider=adapter.provider_id if hasattr(adapter, "provider_id") else "unknown",
                    usage=usage_dict,
                    finish_reason="stop",
                )
                selected = model_id
                attempts.append((model_id, True, None))
                break
            except (ProviderError, RateLimitError, Exception) as e:
                last_error = e
                attempts.append((model_id, False, str(type(e).__name__)))
                log.warning("Model %s failed, trying next: %s", model_id, e)
                continue

        if final_response is None:
            raise NoCandidateModelError(
                f"All {len(chain)} candidate(s) failed. Last error: {last_error}"
            )

        decision = RouteDecision(
            request_type="market_analysis",
            mode=str(mode),
            current_model=chain[0] if chain else "",
            candidate_chain=chain,
            selected_model=selected or "",
            handoff_required=len(attempts) > 1,
            handoff_summary_version="v1",
            reason_current_model_excluded=", ".join(
                f"{m}:{'ok' if ok else err}" for m, ok, err in attempts
            ),
        )
        return final_response, decision
