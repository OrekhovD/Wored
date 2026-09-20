"""Block B tests — the unified :mod:`trading_math` package.

Property-based checks (hypothesis) for the invariants ADR-01 guarantees, a
cross-margin rejection test (D10), an exact settlement identity, and a source
scan proving the tree now carries exactly one definition of ``liquidation_price``.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from trading_math import (
    TAKER_FEE_RATE,
    funding_accrual,
    liquidation_price,
    position_size,
    settlement,
    validate_order,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# Hypothesis strategies (floats kept well inside sane, finite ranges)
# --------------------------------------------------------------------------- #
prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"),
                     allow_nan=False, allow_infinity=False, places=2)
leverages = st.integers(min_value=2, max_value=100)


@given(entry=prices, leverage=st.integers(min_value=2, max_value=99))
@settings(max_examples=200)
def test_long_liquidation_increases_with_leverage(entry, leverage):
    lo = liquidation_price(entry, leverage, "long")
    hi = liquidation_price(entry, leverage + 1, "long")
    assert lo <= hi


@given(entry=prices, leverage=st.integers(min_value=2, max_value=99))
@settings(max_examples=200)
def test_short_liquidation_decreases_with_leverage(entry, leverage):
    lo = liquidation_price(entry, leverage, "short")
    hi = liquidation_price(entry, leverage + 1, "short")
    assert lo >= hi


@given(entry=prices, leverage=leverages)
@settings(max_examples=200)
def test_liquidation_sits_on_correct_side_of_entry(entry, leverage):
    assert liquidation_price(entry, leverage, "long") < entry
    assert liquidation_price(entry, leverage, "short") > entry


@given(entry=prices, leverage=leverages)
@settings(max_examples=200)
def test_long_short_symmetry_without_costs(entry, leverage):
    # With no fee and no maintenance the long and short liquidation prices are
    # exact mirror images about the entry price.
    liq_long = liquidation_price(entry, leverage, "long",
                                 taker_fee_rate=Decimal(0), maintenance_margin_rate=Decimal(0))
    liq_short = liquidation_price(entry, leverage, "short",
                                  taker_fee_rate=Decimal(0), maintenance_margin_rate=Decimal(0))
    # Mirror symmetry to within Decimal-context rounding of 1 +/- 1/L.
    assert abs((liq_short - entry) - (entry - liq_long)) <= Decimal("1e-12")


# --------------------------------------------------------------------------- #
# Exact settlement identity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", ["long", "short"])
def test_settlement_at_entry_costs_only_fees(direction):
    entry = Decimal("50000")
    size = Decimal("0.5")
    entry_fee = Decimal("1.5")
    net, close_fee = settlement(direction, entry, entry, size, entry_fee)
    assert net == -(entry_fee + close_fee)
    assert close_fee == (size * entry) * TAKER_FEE_RATE


def test_settlement_returns_decimal_when_inputs_decimal():
    net, close_fee = settlement("long", Decimal("100"), Decimal("110"), Decimal("1"), Decimal("0.06"))
    assert isinstance(net, Decimal)
    assert net == Decimal("10") - Decimal("0.06") - close_fee


def test_position_size_and_funding_accrual():
    assert position_size(Decimal("100"), 10, Decimal("50000")) == Decimal("0.02")
    # 1 period of 0.01% funding on 10000 notional = 1 USDT cost for a long.
    assert funding_accrual(Decimal("10000"), Decimal("0.0001")) == Decimal("1.0000")


# --------------------------------------------------------------------------- #
# Cross margin rejection (D10 / TZ 6.2)
# --------------------------------------------------------------------------- #
def test_validate_order_rejects_cross_margin():
    with pytest.raises(ValueError, match="cross margin not implemented"):
        validate_order("long", 10, 100.0, 50000.0, margin_mode="cross")


def test_validate_order_accepts_isolated():
    validate_order("long", 10, 100.0, 50000.0)  # no raise


@pytest.mark.parametrize("leverage", [0, 101, -5])
def test_validate_order_bounds_leverage(leverage):
    with pytest.raises(ValueError, match="leverage"):
        validate_order("long", leverage, 100.0, 50000.0)


def test_liquidation_price_preserves_numeric_type():
    assert isinstance(liquidation_price(Decimal("50000"), 10, "long"), Decimal)
    assert isinstance(liquidation_price(50000.0, 10, "long"), float)


# --------------------------------------------------------------------------- #
# Single source of truth (acceptance B): exactly one definition in the tree
# --------------------------------------------------------------------------- #
_SKIP_DIRS = {"Новая папка", ".git", "__pycache__", ".venv", "node_modules", "tests", "test"}


def _iter_source_files():
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_exactly_one_liquidation_price_implementation():
    # ADR-01: the division equation (``equity = maintenance``) is defined once.
    # Wrappers named ``liquidation_price`` may exist (sim_math keeps a versioned
    # one) but only trading_math/core.py may contain the actual division form.
    marker = "unit - m"  # the long-side maintenance divisor, unique to the core
    hits = [p for p in _iter_source_files() if marker in p.read_text(encoding="utf-8")]
    assert [p.relative_to(REPO_ROOT).as_posix() for p in hits] == ["trading_math/core.py"]


def test_risk_and_sim_math_delegate_no_inline_division():
    # Neither the risk wrapper nor the simulator re-implement the division form.
    risk_src = (REPO_ROOT / "paper_trading" / "risk.py").read_text(encoding="utf-8")
    sim_src = (REPO_ROOT / "chatbot" / "services" / "sim_math.py").read_text(encoding="utf-8")
    assert "/ (Decimal(1) - m)" not in risk_src
    assert "(1 - MAINTENANCE_MARGIN)" not in sim_src  # v2 lives only in trading_math
    assert "from trading_math import" in sim_src
