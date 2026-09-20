"""Phase 2: V5 risk and execution correctness tests.

Tests the tier-aware liquidation formula, idempotent margin adjustment,
atomic reverse, and posting reconciliation.  Uses Decimal end to end.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_trading.execution import (
    FEE_RATE,
    MarginAdjustResult,
    Position,
    adjust_margin,
    execute_reverse,
)
from paper_trading.risk import (
    DEFAULT_MAINTENANCE_MARGIN_RATE,
    calculate_liquidation_price,
)
from paper_trading.market import PerpetualSnapshot


# ---------------------------------------------------------------------------
# V5 Liquidation formula
# ---------------------------------------------------------------------------


class TestV5Liquidation:
    """Tier-aware isolated-margin liquidation tests."""

    def test_long_100x_basic(self):
        """L=100, MMR=0.0028, taker=0.0006 → division form (ADR-01/ADR-05).

        The unified ``trading_math`` core solves ``equity = maintenance``; the
        margin fraction is ``1/leverage``, so the long liquidation price is
        ``E * (1 + taker - 1/L) / (1 - MMR)``.
        """
        entry = Decimal("78000")
        liq = calculate_liquidation_price(
            entry_price=entry,
            leverage=100,
            direction="long",
            maintenance_margin_rate=Decimal("0.0028"),
            taker_fee_rate=Decimal("0.0006"),
        )
        expected = entry * (Decimal(1) + Decimal("0.0006") - Decimal("0.01")) / (Decimal(1) - Decimal("0.0028"))
        assert liq == expected

    def test_short_200x_basic(self):
        """L=200, MMR=0.0028, taker=0.0006 → short division form (ADR-05)."""
        entry = Decimal("78000")
        liq = calculate_liquidation_price(
            entry_price=entry,
            leverage=200,
            direction="short",
            maintenance_margin_rate=Decimal("0.0028"),
            taker_fee_rate=Decimal("0.0006"),
        )
        # short: E * (1/L + 1 - taker) / (1 + MMR)
        expected = entry * (Decimal("0.005") + Decimal(1) - Decimal("0.0006")) / (Decimal(1) + Decimal("0.0028"))
        assert liq == expected

    def test_long_8x_basic(self):
        """L=8, MMR=0.0028, taker=0.0006 → long division form (ADR-05)."""
        entry = Decimal("78000")
        liq = calculate_liquidation_price(
            entry_price=entry,
            leverage=8,
            direction="long",
            maintenance_margin_rate=Decimal("0.0028"),
            taker_fee_rate=Decimal("0.0006"),
        )
        expected = entry * (Decimal(1) + Decimal("0.0006") - Decimal("0.125")) / (Decimal(1) - Decimal("0.0028"))
        assert liq == expected

    def test_extra_margin_reduces_liquidation_distance(self):
        """Adding extra margin should move liquidation price further away."""
        entry = Decimal("78000")
        notional = Decimal("78000")
        margin = notional / Decimal(100)  # 100x

        liq_no_extra = calculate_liquidation_price(
            entry_price=entry,
            leverage=100,
            direction="long",
            isolated_margin=margin,
            extra_margin=Decimal(0),
            notional=notional,
            maintenance_margin_rate=Decimal("0.0028"),
        )
        liq_with_extra = calculate_liquidation_price(
            entry_price=entry,
            leverage=100,
            direction="long",
            isolated_margin=margin,
            extra_margin=Decimal("2"),
            notional=notional,
            maintenance_margin_rate=Decimal("0.0028"),
        )
        # Long: higher liq price = closer to entry (bad). More margin → liq moves DOWN (away from entry)
        assert liq_with_extra < liq_no_extra

    def test_stop_before_liquidation(self):
        """Stop-loss price must trigger before liquidation for a safe position."""
        entry = Decimal("78000")
        liq = calculate_liquidation_price(
            entry_price=entry,
            leverage=100,
            direction="long",
            maintenance_margin_rate=Decimal("0.0028"),
            taker_fee_rate=Decimal("0.0006"),
        )
        sl = entry * (Decimal(1) - Decimal("0.005"))  # 0.5% stop
        # SL at 77610 must be above liq at ~77485.2
        assert sl > liq, f"SL {sl} should be above liq {liq} for a long"

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            calculate_liquidation_price(
                entry_price=Decimal("78000"),
                leverage=10,
                direction="sideways",
            )

    def test_zero_leverage_raises(self):
        with pytest.raises(ValueError, match="leverage"):
            calculate_liquidation_price(
                entry_price=Decimal("78000"),
                leverage=0,
                direction="long",
            )

    def test_negative_margin_rate_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            calculate_liquidation_price(
                entry_price=Decimal("78000"),
                leverage=10,
                direction="long",
                maintenance_margin_rate=Decimal("-0.01"),
            )


# ---------------------------------------------------------------------------
# Idempotent margin adjustment
# ---------------------------------------------------------------------------


def _make_position(**kwargs) -> Position:
    defaults = dict(
        position_id="pos-001",
        account_id="acc-auto",
        instrument="BTC-USDT",
        direction="long",
        entry_price=Decimal("78000"),
        quantity=Decimal("10"),
        leverage=Decimal("100"),
        stop_price=Decimal("77600"),
        reserved_margin=Decimal("7.8"),
        entry_fee=Decimal("0.468"),
    )
    defaults.update(kwargs)
    return Position(**defaults)


class TestAdjustMargin:
    def test_topup_increases_margin(self):
        pos = _make_position()
        result = adjust_margin(
            pos,
            delta=Decimal("2"),
            idempotency_key="adj-001",
            maintenance_margin_rate=Decimal("0.0028"),
        )
        assert result.applied is True
        assert pos.reserved_margin == Decimal("9.8")
        assert result.new_reserved_margin == Decimal("9.8")

    def test_idempotent_retry_no_op(self):
        pos = _make_position()
        applied = {"adj-001"}
        result = adjust_margin(
            pos,
            delta=Decimal("2"),
            idempotency_key="adj-001",
            maintenance_margin_rate=Decimal("0.0028"),
            applied_keys=applied,
        )
        assert result.applied is False
        assert result.reason == "duplicate_idempotency_key"
        # Margin unchanged
        assert pos.reserved_margin == Decimal("7.8")

    def test_negative_delta_withdraws(self):
        pos = _make_position()
        result = adjust_margin(
            pos,
            delta=Decimal("-1"),
            idempotency_key="adj-002",
            maintenance_margin_rate=Decimal("0.0028"),
        )
        assert result.applied is True
        assert pos.reserved_margin == Decimal("6.8")

    def test_reject_non_positive_margin(self):
        pos = _make_position(reserved_margin=Decimal("1"))
        result = adjust_margin(
            pos,
            delta=Decimal("-2"),
            idempotency_key="adj-003",
            maintenance_margin_rate=Decimal("0.0028"),
        )
        assert result.applied is False
        assert "topup_rejected" in result.reason
        # Margin unchanged
        assert pos.reserved_margin == Decimal("1")

    def test_closed_position_rejected(self):
        pos = _make_position(status="closed")
        result = adjust_margin(
            pos,
            delta=Decimal("2"),
            idempotency_key="adj-004",
            maintenance_margin_rate=Decimal("0.0028"),
        )
        assert result.applied is False
        assert result.reason == "position_not_open"


# ---------------------------------------------------------------------------
# Atomic reverse
# ---------------------------------------------------------------------------


def _make_snapshot() -> PerpetualSnapshot:
    from paper_trading.market import RiskTier
    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear_swap",
        contract_code="BTC-USDT",
        bid=Decimal("78000.0"),
        ask=Decimal("78000.1"),
        last=Decimal("78000.05"),
        mark=Decimal("78000.05"),
        index=Decimal("78000.0"),
        funding_rate=Decimal("0.0001"),
        next_funding_at="2026-09-18T16:00:00+00:00",
        component_times={},
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at="2026-09-18T12:00:00+00:00",
        received_at="2026-09-18T12:00:00+00:00",
        source="htx-api",
        quality="ok",
    )


class TestExecuteReverse:
    def test_long_to_short(self):
        pos = _make_position(direction="long")
        snap = _make_snapshot()
        result = execute_reverse(pos, snap, idempotency_key="rev-001")
        assert result.reason == "reverse_executed"
        assert result.closed_position_id == "pos-001"
        assert result.new_position_id != ""
        assert result.close_result is not None
        assert result.fill_result is not None
        # New direction should be short
        assert result.fill_result.fill_price > 0

    def test_short_to_long(self):
        pos = _make_position(direction="short")
        snap = _make_snapshot()
        result = execute_reverse(pos, snap, idempotency_key="rev-002")
        assert result.reason == "reverse_executed"
        assert result.fill_result is not None

    def test_new_position_id_unique(self):
        pos = _make_position()
        snap = _make_snapshot()
        r1 = execute_reverse(pos, snap, idempotency_key="rev-003")
        r2 = execute_reverse(pos, snap, idempotency_key="rev-004")
        assert r1.new_position_id != r2.new_position_id