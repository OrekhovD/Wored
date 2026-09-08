"""Budget Policy — default limits and scope management for LLM usage.

R04 defaults:
  runtime/global: daily 200 attempts / 20M tokens, weekly 1K / 100M, monthly 2K / 200M
  Provider/model ceilings default to global; registry limit takes priority.
  Metered monetary ceiling default 0.
  Paid_enabled default false.

These are LOCAL SAFETY POLICIES, not Ollama Pro limits or pricing promises.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from ai.contracts import CostClass


@dataclass
class BucketLimits:
    """Limits for a single bucket."""
    request_limit: int | None
    token_limit: int | None
    cost_limit: float | None  # NUMERIC(20,8)


# ── Default limits ────────────────────────────────────────────────────────

# Global/runtime defaults (R04 §R04.A)
GLOBAL_DEFAULTS: dict[str, dict[str, int | float]] = {
    "day":   {"requests": 200,  "tokens": 20_000_000, "cost": 0.0},
    "week":  {"requests": 1000, "tokens": 100_000_000, "cost": 0.0},
    "month": {"requests": 2000, "tokens": 200_000_000, "cost": 0.0},
}

# Per-provider and per-model ceilings default to global values
# but can be overridden in provider_registry.json
PROVIDER_DEFAULTS = GLOBAL_DEFAULTS.copy()
MODEL_DEFAULTS = GLOBAL_DEFAULTS.copy()


class BudgetPolicy:
    """Budget policy for LLM usage limits.

    Enforces request, token, and cost limits per scope/period/cost_class.
    Can be configured via environment variables or programmatically.
    """

    def __init__(
        self,
        global_overrides: dict | None = None,
        provider_overrides: dict | None = None,
        model_overrides: dict | None = None,
    ):
        self._global = self._merge_defaults(GLOBAL_DEFAULTS, global_overrides)
        self._provider = self._merge_defaults(PROVIDER_DEFAULTS, provider_overrides)
        self._model = self._merge_defaults(MODEL_DEFAULTS, model_overrides)
        self._paid_enabled = os.getenv("LLM_PAID_ENABLED", "false").strip().lower() in ("true", "1", "yes")

    @staticmethod
    def _merge_defaults(
        base: dict[str, dict[str, int | float]],
        overrides: dict | None,
    ) -> dict[str, dict[str, int | float]]:
        """Merge environment variable overrides into base defaults."""
        result = {k: dict(v) for k, v in base.items()}
        if overrides:
            for period, values in overrides.items():
                if period in result:
                    result[period].update(values)
                else:
                    result[period] = dict(values)
        return result

    def get_limits(
        self,
        scope_key: str,
        period_kind: str,
        cost_class: CostClass,
    ) -> dict[str, Any]:
        """Get bucket limits for a given scope, period, and cost class.

        Returns dict with keys: request_limit, token_limit, cost_limit.
        For metered models, cost_limit comes from environment or is 0 by default.
        For free/included, cost_limit is always None (no monetary limit needed).
        """
        # Determine base limits based on scope
        if scope_key == "global":
            base = self._global.get(period_kind, GLOBAL_DEFAULTS.get(period_kind, {}))
        elif scope_key.startswith("provider:"):
            base = self._provider.get(period_kind, self._global.get(period_kind, {}))
        elif scope_key.startswith("model:"):
            base = self._model.get(period_kind, self._global.get(period_kind, {}))
        else:
            base = self._global.get(period_kind, {})

        request_limit = base.get("requests")
        token_limit = base.get("tokens")

        # Cost limits: only for metered
        if cost_class == CostClass.METERED:
            if not self._paid_enabled:
                cost_limit = 0.0  # No paid access → zero budget
            else:
                cost_limit = base.get("cost", 0.0)
                # Override from environment if available
                env_key = f"LLM_COST_LIMIT_{period_kind.upper()}"
                env_val = os.getenv(env_key)
                if env_val:
                    try:
                        cost_limit = float(env_val)
                    except ValueError:
                        pass
        else:
            # Free/included: no monetary limit, but requests/tokens still tracked
            cost_limit = None

        return {
            "request_limit": request_limit,
            "token_limit": token_limit,
            "cost_limit": cost_limit,
        }

    @property
    def paid_enabled(self) -> bool:
        return self._paid_enabled

    def allows_tier(self, routing_mode: str, cost_class: CostClass) -> bool:
        """Check if routing mode permits this cost class.

        free_only: only free
        balanced: free, then included
        premium: free, included, metered (if paid_enabled)
        """
        if cost_class == CostClass.FREE:
            return True
        if cost_class == CostClass.INCLUDED:
            return routing_mode in ("balanced", "premium")
        if cost_class == CostClass.METERED:
            return routing_mode == "premium" and self._paid_enabled
        return False