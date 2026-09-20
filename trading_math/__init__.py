"""Public surface of the unified trading-math package (ADR-01).

Import these helpers instead of re-implementing liquidation/settlement maths;
``chatbot.services.sim_math`` and ``paper_trading.risk`` both delegate here so
there is exactly one definition of each formula in the tree.
"""
from __future__ import annotations

from trading_math.core import (
    MAINTENANCE_MARGIN_RATE,
    MARGIN_MODES,
    MAX_LEVERAGE,
    TAKER_FEE_RATE,
    funding_accrual,
    liquidation_price,
    one,
    position_size,
    settlement,
    validate_order,
)

__all__ = [
    "TAKER_FEE_RATE",
    "MAINTENANCE_MARGIN_RATE",
    "MAX_LEVERAGE",
    "MARGIN_MODES",
    "validate_order",
    "liquidation_price",
    "settlement",
    "position_size",
    "funding_accrual",
    "one",
]
