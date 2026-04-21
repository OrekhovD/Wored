"""Cost estimator — compute dollar cost from token usage."""
from __future__ import annotations

from providers.interface import CostEstimate, TokenUsage


class CostEstimator:
    def __init__(self, model_prices: dict[str, dict]) -> None:
        """model_prices = {model_id: {"input_per_1k": float, "output_per_1k": float}}."""
        self._prices = model_prices

    def estimate(self, model_id: str, usage: TokenUsage) -> CostEstimate:
        price = self._prices.get(model_id, {})
        ip = price.get("input_per_1k", 0.0)
        op = price.get("output_per_1k", 0.0)
        ic = (usage.input_tokens / 1000) * ip
        oc = (usage.output_tokens / 1000) * op
        discount = 0.0
        if usage.cached_tokens and usage.cached_tokens > 0:
            discount = (usage.cached_tokens / 1000) * ip * 0.5
        total = max(0.0, ic + oc - discount)
        return CostEstimate(
            total_cost=round(total, 6),
            currency="USD",
            input_cost=round(ic, 6),
            output_cost=round(oc, 6),
            per_token_costs={"input_per_1k": ip, "output_per_1k": op},
        )
