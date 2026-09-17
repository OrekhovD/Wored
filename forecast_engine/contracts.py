"""forecast_engine.contracts — Phase 3 domain schemas for the forecast engine.

All monetary values are ``decimal.Decimal``.  IDs are ``uuid.UUID``.
Python 3.9+ compatible.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List
from uuid import UUID, uuid4

# --------------------------------------------------------------------------- #
# Enum
# --------------------------------------------------------------------------- #


class ModelVersion(str, enum.Enum):
    """Forecast model identifiers (deterministic baselines only in Phase 3)."""

    B0_NAIVE = "B0_NAIVE"
    B1_QUANTILE = "B1_QUANTILE"


# --------------------------------------------------------------------------- #
# OHLCV bar — input to indicators / features / models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OHLCVBar:
    """A single closed OHLCV candle.

    All price/volume fields are ``Decimal``.  ``time`` is the bar open time.
    """

    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("OHLCVBar: high must be >= low")
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise ValueError("OHLCVBar: prices must be positive")


# --------------------------------------------------------------------------- #
# Forecast output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForecastCandle:
    """A single predicted candle for the t+h horizon.

    ``predicted_high`` / ``predicted_low`` form the quantile band
    (q90 / q10 for B1, or equal to the close for B0).

    ``p_up`` is the model's probability that the close exceeds the open
    (``Decimal`` in [0, 1]).
    """

    open_time: datetime
    close_time: datetime
    predicted_open: Decimal
    predicted_high: Decimal
    predicted_low: Decimal
    predicted_close: Decimal
    confidence: Decimal
    p_up: Decimal


@dataclass
class ForecastRun:
    """A complete forecast run for one contract and horizon.

    ``horizon_minutes`` is the total horizon in minutes (e.g. 15 for a
    3-step × 5-min forecast).  ``status`` mirrors the DB CHECK constraint
    values: ``running``, ``completed``, ``failed``, ``expired``.
    """

    run_id: UUID
    contract_code: str
    horizon_minutes: int
    model_version: ModelVersion
    data_cutoff: datetime
    status: str = "running"
    candles: List[ForecastCandle] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in {"running", "completed", "failed", "expired"}:
            raise ValueError(f"ForecastRun: invalid status {self.status!r}")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def new_run_id() -> UUID:
    """Generate a new UUID4 for a forecast run."""
    return uuid4()