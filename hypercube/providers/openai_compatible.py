"""OpenAI-compatible HTTP adapter with resilience patterns."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.exceptions import InvalidResponseError, ProviderUnavailableError, RateLimitError
from providers.interface import AIProviderInterface, CostEstimate, HealthResult, TokenUsage
from providers.resilience import get_resilience_handler, CircuitBreakerError


@dataclass
class _ModelInfo:
    input_cost_per_1k: float
    output_cost_per_1k: float
    is_premium: bool = False
    supports_streaming: bool = True
    supports_system_prompt: bool = True


class OpenAICompatibleAdapter(AIProviderInterface):
    def __init__(
        self,
        provider_id: str,
        api_key: str,
        base_url: str,
        models: dict[str, _ModelInfo],
    ) -> None:
        self._pid = provider_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._models = models  # model_id -> _ModelInfo
        self._resilience = get_resilience_handler(provider_id)

    @property
    def provider_id(self) -> str:
        return self._pid

    @property
    def supports_streaming(self) -> bool:
        return all(m.supports_streaming for m in self._models.values())

    @property
    def supports_system_prompt(self) -> bool:
        return all(m.supports_system_prompt for m in self._models.values())

    async def list_models(self) -> list[str]:
        return list(self._models.keys())

    async def invoke(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 1.0,
        stream: bool = False,
    ) -> tuple[str, TokenUsage | None]:
        async def _do_request() -> tuple[str, TokenUsage | None]:
            url = f"{self._base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                body["max_tokens"] = max_tokens
            if stream:
                body["stream"] = True

            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(url, headers=headers, json=body)

            self._check_error(r)

            data = r.json()
            choices = data.get("choices", [])
            if not choices:
                raise InvalidResponseError(f"Empty choices from {self._pid}/{model}")

            content = choices[0].get("message", {}).get("content", "")
            usage_raw = data.get("usage", {})
            usage = None
            if usage_raw:
                usage = TokenUsage(
                    input_tokens=usage_raw.get("prompt_tokens", 0),
                    output_tokens=usage_raw.get("completion_tokens", 0),
                    total_tokens=usage_raw.get("total_tokens"),
                    reasoning_tokens=usage_raw.get("reasoning_tokens"),
                )

            return content, usage

        try:
            return await self._resilience.execute(_do_request)
        except CircuitBreakerError:
            raise ProviderUnavailableError(f"Circuit breaker open for provider {self._pid}")

    async def healthcheck(self) -> HealthResult:
        t0 = time.monotonic()
        url = f"{self._base_url}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers=headers)
            healthy = r.status_code == 200
            if not healthy:
                return HealthResult(self._pid, False, (time.monotonic() - t0) * 1000, f"HTTP {r.status_code}")
            return HealthResult(self._pid, True, (time.monotonic() - t0) * 1000)
        except Exception as e:
            return HealthResult(self._pid, False, (time.monotonic() - t0) * 1000, str(e))

    def estimate_cost(self, model: str, usage: TokenUsage) -> CostEstimate:
        mi = self._models.get(model)
        if mi is None:
            return CostEstimate(total_cost=0.0, currency="USD")
        ic = (usage.input_tokens / 1000) * mi.input_cost_per_1k
        oc = (usage.output_tokens / 1000) * mi.output_cost_per_1k
        return CostEstimate(
            total_cost=ic + oc,
            currency="USD",
            input_cost=ic,
            output_cost=oc,
            per_token_costs={
                "input_per_1k": mi.input_cost_per_1k,
                "output_per_1k": mi.output_cost_per_1k,
            },
        )

    @staticmethod
    def _check_error(r: httpx.Response) -> None:
        if r.status_code == 429:
            raise RateLimitError(f"Rate limit on provider (HTTP 429)")
        if r.status_code in (401, 403):
            raise ProviderUnavailableError(f"Auth error on provider (HTTP {r.status_code})")
        if r.status_code >= 500:
            raise ProviderUnavailableError(f"Server error from provider (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise InvalidResponseError(f"Provider returned HTTP {r.status_code}: {r.text[:200]}")
