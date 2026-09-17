"""Paper trading integration tests — golden financial calculations.

These tests verify the core financial arithmetic against the mandatory
benchmarks from ACCEPTANCE.md. They run without a database (pure Python).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Golden financial benchmarks (AC-22) ──────────────────────────────

class TestGoldenLong:
    """Long entry 10000, exit 10100, qty 1, fee 0.0006, slippage 0.
    
    gross = 100; entry_fee = 6; exit_fee = 6.06; net = 87.94 USDT.
    """

    def test_gross_pnl(self):
        from paper_trading.ledger import calculate_gross_pnl
        fixture = load_fixture("golden_long.json")
        exp = fixture["expected"]
        gross = calculate_gross_pnl(
            side="long",
            qty=Decimal(fixture["quantity"]),
            entry_price=Decimal(fixture["entry_price"]),
            exit_price=Decimal(fixture["exit_price"]),
        )
        assert gross == Decimal(exp["gross_pnl"])

    def test_net_pnl(self):
        from paper_trading.ledger import calculate_net_pnl
        fixture = load_fixture("golden_long.json")
        exp = fixture["expected"]
        net = calculate_net_pnl(
            side="long",
            qty=Decimal(fixture["quantity"]),
            entry_price=Decimal(fixture["entry_price"]),
            exit_price=Decimal(fixture["exit_price"]),
            entry_fee=Decimal(exp["entry_fee"]),
            exit_fee=Decimal(exp["exit_fee"]),
        )
        assert net == Decimal(exp["net_pnl"])


class TestGoldenShort:
    """Short entry 10000, exit 9900, qty 1, fee 0.0006.
    
    gross = 100; entry_fee = 6; exit_fee = 5.94; net = 88.06 USDT.
    """

    def test_gross_pnl(self):
        from paper_trading.ledger import calculate_gross_pnl
        fixture = load_fixture("golden_short.json")
        exp = fixture["expected"]
        gross = calculate_gross_pnl(
            side="short",
            qty=Decimal(fixture["quantity"]),
            entry_price=Decimal(fixture["entry_price"]),
            exit_price=Decimal(fixture["exit_price"]),
        )
        assert gross == Decimal(exp["gross_pnl"])

    def test_net_pnl(self):
        from paper_trading.ledger import calculate_net_pnl
        fixture = load_fixture("golden_short.json")
        exp = fixture["expected"]
        net = calculate_net_pnl(
            side="short",
            qty=Decimal(fixture["quantity"]),
            entry_price=Decimal(fixture["entry_price"]),
            exit_price=Decimal(fixture["exit_price"]),
            entry_fee=Decimal(exp["entry_fee"]),
            exit_fee=Decimal(exp["exit_fee"]),
        )
        assert net == Decimal(exp["net_pnl"])


class TestGoldenPartialClose:
    """Partial close: qty 1, entry 10000, close 0.4 @ 10100.
    
    gross = 40, allocated entry_fee = 2.4, exit_fee = 2.424, net = 35.176
    remaining qty = 0.6, unallocated entry_fee = 3.6
    """

    def test_partial_allocation(self):
        from paper_trading.ledger import allocate_partial_exit
        fixture = load_fixture("golden_partial_close.json")
        exp = fixture["expected"]
        result = allocate_partial_exit(
            side="long",
            total_qty=Decimal(fixture["total_quantity"]),
            close_qty=Decimal(fixture["close_quantity"]),
            avg_entry_price=Decimal(fixture["entry_price"]),
            exit_price=Decimal(fixture["exit_price"]),
            total_entry_fee=Decimal(exp["entry_fee_total"]),
            exit_fee=Decimal(exp["exit_fee"]),
        )
        assert result.closed_entry_fee_share == Decimal(exp["entry_fee_allocated"])
        assert result.remaining_entry_fee_share == Decimal(exp["unallocated_entry_fee"])


class TestGoldenFunding:
    """Funding event: mark 10000, qty 1, rate +0.0001.
    
    Long cashflow = -1, Short cashflow = +1.
    """

    def test_positive_rate_long(self):
        from paper_trading.execution import apply_funding
        from paper_trading.contracts import Position, PositionSide, PositionStatus
        from uuid import uuid4
        fixture = load_fixture("golden_funding.json")
        exp = fixture["expected"]
        
        pos = Position(
            position_id=uuid4(), account_id=uuid4(), day_id=uuid4(),
            instrument="BTC-USDT", side=PositionSide.long,
            qty=Decimal(fixture["quantity"]),
            avg_entry_price=Decimal("10000"),
            isolated_margin=Decimal("1000"),
            stop_loss=Decimal("9500"),
            take_profit=Decimal("11000"),
            status=PositionStatus.open,
        )
        result = apply_funding(
            position=pos,
            funding_rate=Decimal(fixture["funding_rate"]),
            mark_price=Decimal(fixture["mark_price"]),
        )
        assert result.funding_amount == Decimal(exp["long_funding"])

    def test_positive_rate_short(self):
        from paper_trading.execution import apply_funding
        from paper_trading.contracts import Position, PositionSide, PositionStatus
        from uuid import uuid4
        fixture = load_fixture("golden_funding.json")
        exp = fixture["expected"]
        
        pos = Position(
            position_id=uuid4(), account_id=uuid4(), day_id=uuid4(),
            instrument="BTC-USDT", side=PositionSide.short,
            qty=Decimal(fixture["quantity"]),
            avg_entry_price=Decimal("10000"),
            isolated_margin=Decimal("1000"),
            stop_loss=Decimal("10500"),
            take_profit=Decimal("9000"),
            status=PositionStatus.open,
        )
        result = apply_funding(
            position=pos,
            funding_rate=Decimal(fixture["funding_rate"]),
            mark_price=Decimal(fixture["mark_price"]),
        )
        assert result.funding_amount == Decimal(exp["short_funding"])


# ─── Strategy (AC-13) ───────────────────────────────────────────────────

class TestBaselineStrategy:
    """EMA/ATR calculation and signal generation."""

    def test_ema_seed_sma(self):
        from paper_trading.strategy import EMA
        ema = EMA(period=20)
        values = [Decimal(str(100 + i)) for i in range(20)]
        for v in values:
            ema.update(v)
        # SMA of first 20 values = mean(100..119) = 109.5
        assert ema.value == Decimal("109.5")

    def test_atr14_seed(self):
        from paper_trading.strategy import ATR14
        atr = ATR14()
        # Generate 14 bars with known true ranges
        for i in range(14):
            atr.update(high=Decimal(str(100 + i)), low=Decimal(str(90 + i)), close=Decimal(str(95 + i)))
        # Should have a value (SMA of first 14 TRs)
        assert atr.value is not None
        assert atr.value > 0


# ─── Learning (AC-23) ───────────────────────────────────────────────────

class TestLearningGate:
    """Candidate evaluation gate."""

    def test_insufficient_data(self):
        from paper_trading.learning import LearningEvaluator, Episode
        evaluator = LearningEvaluator()
        # Few episodes → insufficient_data
        candidate = [Episode(
            episode_id="1", strategy_version="v2", instrument="BTC-USDT", interval="1m",
            entry_time="2026-01-01T00:00:00Z", exit_time="2026-01-01T00:10:00Z",
            side="long", entry_price=Decimal("100"), exit_price=Decimal("101"),
            qty=Decimal("1"), gross_pnl=Decimal("1"), entry_fee=Decimal("0.06"),
            exit_fee=Decimal("0.06"), funding_cashflow=Decimal("0"),
            net_pnl=Decimal("0.88"), max_drawdown=Decimal("0.5"), duration_minutes=10,
        )]
        baseline = candidate.copy()
        result = evaluator.evaluate(candidate, baseline)
        assert result.status == "insufficient_data"

    def test_rejected_on_worse_drawdown(self):
        from paper_trading.learning import LearningEvaluator, Episode
        evaluator = LearningEvaluator({"min_episodes": 4, "min_holdout_episodes": 2, "chronological_split": 0.5})
        def make_eps(version: str, dd: str, count: int):
            return [Episode(
                episode_id=f"{version}-{i}", strategy_version=version, instrument="BTC-USDT", interval="1m",
                entry_time=f"2026-01-0{i+1}T00:00:00Z", exit_time=f"2026-01-0{i+1}T00:10:00Z",
                side="long", entry_price=Decimal("100"), exit_price=Decimal("101"),
                qty=Decimal("1"), gross_pnl=Decimal("1"), entry_fee=Decimal("0.06"),
                exit_fee=Decimal("0.06"), funding_cashflow=Decimal("0"),
                net_pnl=Decimal("0.88"), max_drawdown=Decimal(dd), duration_minutes=10,
            ) for i in range(count)]
        
        candidate = make_eps("v2", "5", 4)  # worse drawdown
        baseline = make_eps("v1", "1", 4)   # better drawdown
        result = evaluator.evaluate(candidate, baseline)
        assert result.status == "rejected"
