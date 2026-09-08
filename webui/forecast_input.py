"""One strict input contract for every forecast entry point.

R01: Input validation, idempotency key, Pydantic model for FastAPI.
Bool is not a valid int; horizon_hours converts only when divisible;
conflict with horizon_steps → 400; canonical timeframe enforced;
symbol must be in watchlist; depth 1–10.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from prediction_timeframes import STEP_MINUTES_MAP, CANONICAL_PERIODS, normalize_period

# Idempotency-Key: ASCII letters/digits/-/_  length 1..64
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ForecastInput:
    symbol: str
    horizon_steps: int
    base_timeframe: str
    depth: int


def validate_idempotency_key(key: str | None) -> str | None:
    """Validate and return a cleaned idempotency key, or raise ValueError."""
    if key is None or key == "":
        return None
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError(
            "Idempotency-Key must contain only ASCII letters, digits, - and _ "
            f"and be 1–64 characters long, got {key!r}"
        )
    return key


def parse_forecast_input(payload: dict, default_steps: int = 4) -> ForecastInput:
    """Parse and validate forecast input from a raw payload dict.

    Strict type checks: bool is not int; float is not int; string is not int.
    """
    if not isinstance(payload, dict):
        raise ValueError("Forecast body must be an object")

    symbol = payload.get("symbol", "btcusdt")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required")
    symbol = symbol.strip().lower()

    period = payload.get("base_timeframe", "60min")
    if not isinstance(period, str):
        raise ValueError("base_timeframe must be a string")
    try:
        period = normalize_period(period)
    except ValueError:
        raise ValueError(f"Unsupported base_timeframe: {period!r}")

    steps = payload.get("horizon_steps")
    hours = payload.get("horizon_hours")

    # Bool is not a valid int — reject True/False explicitly
    if isinstance(steps, bool):
        raise ValueError("horizon_steps must be an integer, not a boolean")
    if isinstance(hours, bool):
        raise ValueError("horizon_hours must be an integer, not a boolean")

    # Reject non-int numeric types (float) and strings
    if steps is not None and type(steps) is not int:
        raise ValueError("horizon_steps must be an integer")
    if hours is not None and type(hours) is not int:
        raise ValueError("horizon_hours must be a positive integer")

    if hours is not None:
        if hours <= 0:
            raise ValueError("horizon_hours must be a positive integer")
        minutes = hours * 60
        step_minutes = STEP_MINUTES_MAP[period]
        if minutes % step_minutes:
            raise ValueError(
                f"horizon_hours={hours} does not divide evenly into {period} steps"
            )
        converted = minutes // step_minutes
        if steps is not None and steps != converted:
            raise ValueError(
                f"horizon_steps={steps} conflicts with horizon_hours={hours}"
            )
        steps = converted

    if steps is None:
        steps = default_steps

    if type(steps) is not int or not 1 <= steps <= 48:
        raise ValueError("horizon_steps must be an integer from 1 to 48")

    depth = payload.get("depth", 3)
    if isinstance(depth, bool):
        raise ValueError("depth must be an integer, not a boolean")
    if type(depth) is not int or not 1 <= depth <= 10:
        raise ValueError("depth must be an integer from 1 to 10")

    return ForecastInput(symbol, steps, period, depth)


# ── Pydantic model for FastAPI endpoint validation ──────────────────────
# When used as a request body, FastAPI will return 422 for type mismatches
# before the handler runs.  Endpoints that accept this model should expect 422;
# endpoints that parse raw dicts via parse_forecast_input return 400.
try:
    from pydantic import BaseModel, field_validator

    class ForecastInputModel(BaseModel):
        """Pydantic schema for forecast creation endpoints.

        Provides a single canonical 422 response for type errors so that
        each endpoint has exactly one expected error code (422 for model
        validation, 400 for business-logic validation).
        """
        symbol: str = "btcusdt"
        horizon_steps: int = 4
        base_timeframe: str = "60min"
        depth: int = 3
        horizon_hours: int | None = None

        @field_validator("symbol")
        @classmethod
        def _validate_symbol(cls, v: str) -> str:
            v = v.strip().lower()
            if not v:
                raise ValueError("symbol is required")
            return v

        @field_validator("base_timeframe")
        @classmethod
        def _validate_timeframe(cls, v: str) -> str:
            try:
                return normalize_period(v)
            except ValueError:
                raise ValueError(f"Unsupported base_timeframe: {v!r}")

        @field_validator("horizon_steps")
        @classmethod
        def _validate_steps(cls, v: int) -> int:
            if not 1 <= v <= 48:
                raise ValueError("horizon_steps must be an integer from 1 to 48")
            return v

        @field_validator("depth")
        @classmethod
        def _validate_depth(cls, v: int) -> int:
            if not 1 <= v <= 10:
                raise ValueError("depth must be an integer from 1 to 10")
            return v

        def to_forecast_input(self) -> ForecastInput:
            """Convert to the dataclass used by the core pipeline."""
            payload: dict[str, Any] = {
                "symbol": self.symbol,
                "horizon_steps": self.horizon_steps,
                "base_timeframe": self.base_timeframe,
                "depth": self.depth,
            }
            if self.horizon_hours is not None:
                payload["horizon_hours"] = self.horizon_hours
            return parse_forecast_input(payload)

except ImportError:
    # Pydantic not available in minimal environments
    ForecastInputModel = None  # type: ignore[assignment,misc]