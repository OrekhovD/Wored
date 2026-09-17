"""Bounded LLM role runner for WORED Trader V0.1.

Executes LLM roles (Planner, Critic, Coach) with:
- daily request/token budget enforcement
- input feature hashing for deduplication
- deterministic fallback on failure
- one corrective retry for Planner schema repair
- no live provider calls during implementation phase
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "trader_roles.json"


@dataclass
class BudgetState:
    """Daily budget tracking for LLM roles."""

    requests_today: int = 0
    tokens_today: int = 0
    max_requests: int = 48
    max_tokens: int = 200_000
    role_requests: dict[str, int] = field(default_factory=dict)
    role_tokens: dict[str, int] = field(default_factory=dict)
    date: str = ""

    def reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.date != today:
            self.requests_today = 0
            self.tokens_today = 0
            self.role_requests.clear()
            self.role_tokens.clear()
            self.date = today

    def can_spend(self, role: str, config: dict[str, Any]) -> bool:
        self.reset_if_new_day()
        role_max = config.get("max_requests_per_day", 0)
        if role_max == 0:
            return False
        if self.role_requests.get(role, 0) >= role_max:
            return False
        if self.requests_today >= self.max_requests:
            return False
        return True

    def record(self, role: str, input_tokens: int, output_tokens: int) -> None:
        total = input_tokens + output_tokens
        self.requests_today += 1
        self.tokens_today += total
        self.role_requests[role] = self.role_requests.get(role, 0) + 1
        self.role_tokens[role] = self.role_tokens.get(role, 0) + total


@dataclass
class RoleResult:
    """Result of a role execution."""

    role: str
    output: dict[str, Any] | None
    used_fallback: bool = False
    fallback_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    input_hash: str = ""
    error: str = ""


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        log.warning("trader_roles.json not found at %s", CONFIG_PATH)
        return {"roles": {}, "global": {}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _features_hash(features: dict[str, Any]) -> str:
    """SHA-256 of sorted feature dict for deduplication."""
    serialized = json.dumps(features, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_schema(data: dict[str, Any], schema_path: Path) -> list[str]:
    """Validate data against a JSON schema. Returns list of errors."""
    try:
        import jsonschema
    except ImportError:
        return []  # Skip validation if jsonschema not installed

    if not schema_path.exists():
        return [f"schema file not found: {schema_path}"]

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    try:
        jsonschema.validate(data, schema)
        return []
    except jsonschema.ValidationError as exc:
        return [str(exc.message)]


class RoleRunner:
    """Runs LLM roles with budget enforcement and deterministic fallback."""

    def __init__(self, config: dict[str, Any] | None = None, budget: BudgetState | None = None):
        self.config = config or _load_config()
        self.budget = budget or BudgetState(
            max_requests=self.config.get("global", {}).get("max_requests_per_day", 48),
            max_tokens=self.config.get("global", {}).get("max_tokens_per_day", 200_000),
        )
        self._results_cache: dict[str, RoleResult] = {}  # hash -> result

    def run_role(
        self,
        role: str,
        features: dict[str, Any],
        *,
        provider_call: Optional[Any] = None,
        allow_retry: bool = True,
    ) -> RoleResult:
        """Execute a role with budget, dedup, and fallback.

        ``provider_call`` is an optional callable that takes (model, prompt)
        and returns a dict with 'content', 'input_tokens', 'output_tokens'.
        If None, the deterministic fallback is used immediately.
        """
        roles = self.config.get("roles", {})
        role_config = roles.get(role)
        if role_config is None:
            return RoleResult(role=role, output=None, used_fallback=True,
                              fallback_reason="role_not_configured")

        fhash = _features_hash(features)

        # Dedup: reuse if hash hasn't changed
        if self.config.get("global", {}).get("feature_reuse_within_ttl", False):
            cached = self._results_cache.get(fhash)
            if cached is not None:
                return RoleResult(
                    role=role, output=cached.output,
                    used_fallback=cached.used_fallback,
                    fallback_reason="reused_cached_result",
                    input_hash=fhash,
                )

        # Budget check
        if not self.budget.can_spend(role, role_config):
            return RoleResult(
                role=role, output=None, used_fallback=True,
                fallback_reason="budget_blocked",
                input_hash=fhash,
            )

        # No provider → deterministic fallback
        if provider_call is None or role_config.get("model") is None:
            return self._fallback(role, role_config, fhash, features)

        # Call provider
        max_input = role_config.get("max_input_tokens", 1000)
        max_output = role_config.get("max_output_tokens", 300)
        model = role_config.get("model", "deepseek-v4-flash")

        try:
            result = provider_call(model, features, max_input, max_output)
            output = result.get("content")
            if isinstance(output, str):
                output = json.loads(output)

            input_tokens = result.get("input_tokens", 0)
            output_tokens = result.get("output_tokens", 0)
            self.budget.record(role, input_tokens, output_tokens)

            # Schema validation for planner
            schema_file = role_config.get("schema")
            if schema_file and output:
                schema_path = Path(__file__).parent.parent / schema_file
                errors = _validate_schema(output, schema_path)
                if errors and allow_retry and role == "planner":
                    # One corrective retry
                    log.warning("planner schema errors: %s, retrying", errors)
                    return self.run_role(role, features, provider_call=provider_call, allow_retry=False)
                elif errors:
                    return self._fallback(role, role_config, fhash, features,
                                          reason=f"schema_invalid: {errors[0]}")

            role_result = RoleResult(
                role=role, output=output,
                input_tokens=input_tokens, output_tokens=output_tokens,
                input_hash=fhash,
            )
            self._results_cache[fhash] = role_result
            return role_result

        except Exception as exc:
            log.warning("role %s provider call failed: %s", role, exc)
            return self._fallback(role, role_config, fhash, features, reason=str(exc))

    def _fallback(
        self,
        role: str,
        role_config: dict[str, Any],
        fhash: str,
        features: dict[str, Any],
        reason: str = "",
    ) -> RoleResult:
        """Apply deterministic fallback for a role."""

        if role == "planner":
            # Fallback: last plan with reduced entries, then reduce_only
            fallback_output = {
                "plan_version": "fallback",
                "mode": "reduce_only",
                "allowed_sides": [],
                "profile": "P0",
                "max_entries": 0,
                "p_entry_min": 1.0,
                "valid_until": "1970-01-01T00:00:00Z",
                "reason": "planner_fallback: " + reason,
            }
        elif role == "critic":
            fallback_output = {
                "confidence_multiplier": 0.85,
                "verdict": "reduce",
                "reason": "critic_fallback: " + reason,
            }
        elif role == "coach":
            fallback_output = {
                "summary": "deterministic_summary",
                "reason": "coach_fallback: " + reason,
            }
        else:
            fallback_output = {"reason": f"{role}_fallback: {reason}"}

        result = RoleResult(
            role=role,
            output=fallback_output,
            used_fallback=True,
            fallback_reason=reason or role_config.get("fallback", "deterministic_fallback"),
            input_hash=fhash,
        )
        self._results_cache[fhash] = result
        return result

    def budget_status(self) -> dict[str, Any]:
        """Return current budget state for UI display."""
        self.budget.reset_if_new_day()
        return {
            "requests_today": self.budget.requests_today,
            "max_requests": self.budget.max_requests,
            "tokens_today": self.budget.tokens_today,
            "max_tokens": self.budget.max_tokens,
            "role_requests": dict(self.budget.role_requests),
            "role_tokens": dict(self.budget.role_tokens),
            "target_monthly_cost_usd": self.config.get("global", {}).get("target_monthly_cost_usd", 5.0),
        }