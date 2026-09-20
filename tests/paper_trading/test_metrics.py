"""Block C — deterministic trading metrics (``paper_trading.metrics``).

Focus: the drawdown that the fixed ``/api/strategy/evaluate`` path now reports
must be the *chronological* peak-to-trough of the equity curve, verifiable to
1e-8 on a hand-computed example (TZ acceptance C), plus the supporting risk
statistics the block-D gate will consume.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_trading.metrics import (
    average_r,
    daily_returns,
    equity_curve,
    fee_turnover,
    liquidation_share,
    max_drawdown,
    profit_factor,
    risk_adjusted_net_pnl,
    sharpe,
    sortino,
)


def test_equity_curve_is_cumulative_with_seed():
    curve = equity_curve([Decimal(10), Decimal(-4), Decimal(5)])
    assert curve == [Decimal(0), Decimal(10), Decimal(6), Decimal(11)]


def test_max_drawdown_known_answer_decimal_exact():
    # Equity: 0 -> 100 -> 50 -> 120 -> 20.  Peaks 0,100,100,120,120.
    # Drawdowns: 0,0,50,0,100 -> max 100 (the trough at the final point).
    curve = [Decimal(0), Decimal(100), Decimal(50), Decimal(120), Decimal(20)]
    assert max_drawdown(curve) == Decimal(100)


def test_max_drawdown_monotonic_is_zero():
    assert max_drawdown([Decimal(x) for x in (0, 5, 9, 20)]) == Decimal(0)


def test_max_drawdown_empty_and_negative_start():
    assert max_drawdown([]) == Decimal(0)
    # from -50 peak to -80 trough is a 30 drawdown
    assert max_drawdown([Decimal(-50), Decimal(-30), Decimal(-80)]) == Decimal(50)


def test_max_drawdown_preserves_float_and_decimal():
    assert isinstance(max_drawdown([0.0, 5.0, -5.0]), float)
    assert isinstance(max_drawdown([Decimal(0), Decimal(5)]), Decimal)


def test_daily_returns_and_ratios_smoke():
    curve = [100.0, 110.0, 105.0, 120.0]
    rets = daily_returns(curve)
    assert len(rets) == 3
    assert rets[0] == pytest.approx(0.10)
    # positive mean with some downside -> positive Sharpe and Sortino
    mixed = [0.05, -0.02, 0.03, -0.01, 0.04]
    assert sharpe(mixed) > 0
    assert sortino(mixed) > 0
    # an all-positive series has no downside deviation -> Sortino guards to 0
    assert sortino([0.02, 0.03, 0.01]) == 0.0
    assert sharpe([]) == 0.0
    assert sharpe([0.01]) == 0.0


def test_profit_factor_average_r_liq_share():
    assert profit_factor([Decimal(3), Decimal(-1), Decimal(2)]) == pytest.approx(5.0)
    assert profit_factor([Decimal(2), Decimal(1)]) == float("inf")
    assert profit_factor([]) == 0.0
    assert average_r([1.0, -0.5, 2.0]) == pytest.approx(2.5 / 3)
    assert liquidation_share([True, False, False, True]) == pytest.approx(0.5)
    assert liquidation_share([]) == 0.0


def test_fee_turnover_sums_abs_and_preserves_type():
    assert fee_turnover([Decimal("0.5"), Decimal("-0.25")]) == Decimal("0.75")
    assert fee_turnover([0.5, -0.25]) == pytest.approx(0.75)


def test_risk_adjusted_net_pnl_haircut():
    # profit penalised one-for-one by realised drawdown
    assert risk_adjusted_net_pnl(Decimal(500), Decimal(120)) == 380.0
    assert risk_adjusted_net_pnl(-50, 10) == -60.0
