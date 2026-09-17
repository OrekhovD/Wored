"""Paper-trading domain package: market data, risk gates, and execution engine.

HTX BTC-USDT USDT-margined isolated perpetual:
  contract multiplier (contract_size): 0.001 BTC
  price tick: 0.1 USDT
  quantity step: 0.001 contracts

All monetary values are Decimal — never float.
"""
from __future__ import annotations

from paper_trading.execution import (
    CloseResult,
    FillResult,
    FundingResult,
    apply_funding,
    calculate_unrealized,
    estimate_equity,
    execute_close,
    execute_market_order,
)
from paper_trading.market import PerpetualSnapshot, validate_snapshot
from paper_trading.risk import RiskCheckResult, RiskSettings, check_order_risk

__all__ = [
    "CloseResult",
    "FillResult",
    "FundingResult",
    "PerpetualSnapshot",
    "RiskCheckResult",
    "RiskSettings",
    "apply_funding",
    "calculate_unrealized",
    "check_order_risk",
    "estimate_equity",
    "execute_close",
    "execute_market_order",
    "validate_snapshot",
]