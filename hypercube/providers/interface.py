"""Abstract AI-provider adapter interface."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass
class HealthResult:
    provider_id: str
    healthy: bool
    latency_ms: float
    error_message: str | None = None


@dataclass
class CostEstimate:
    total_cost: float
    currency: str = "USD"
    input_cost: float = 0.0
    output_cost: float = 0.0
    per_token_costs: dict[str, float] = field(default_factory=dict)


class AIProviderInterface(ABC):
    """Every AI-provider adapter must implement this."""

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    @property
    @abstractmethod
    def supports_streaming(self) -> bool: ...

    @property
    @abstractmethod
    def supports_system_prompt(self) -> bool: ...

    @abstractmethod
    async def invoke(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 1.0,
        stream: bool = False,
    ) -> tuple[str, TokenUsage | None]:
        """Return (content_text, usage)."""

    @abstractmethod
    async def healthcheck(self) -> HealthResult: ...

    @abstractmethod
    def estimate_cost(self, model: str, usage: TokenUsage) -> CostEstimate: ...
