"""Paper-trading domain package: market data, risk gates, and execution engine.

HTX BTC-USDT USDT-margined isolated perpetual:
  contract multiplier (contract_size): 0.001 BTC
  price tick: 0.1 USDT
  quantity step: 0.001 contracts

All monetary values are Decimal — never float.
"""
from __future__ import annotations

from paper_trading.market import PerpetualSnapshot, validate_snapshot
from paper_trading.risk import RiskSettings, RiskCheckResult, check_order_risk
from paper_trading.execution import (
    FillResult,
    CloseResult,
    FundingResult,
    execute_market_order,
    execute_close,
    apply_funding,
    calculate_unrealized,
    estimate_equity,
)

__all__ = [
    "PerpetualSnapshot",
    "validate_snapshot",
    "RiskSettings",
    "RiskCheckResult",
    "check_order_risk",
    "FillResult",
    "CloseResult",
    "FundingResult",
    "execute_market_order",
    "execute_close",
    "apply_funding",
    "calculate_unrealized",
    "estimate_equity",
]