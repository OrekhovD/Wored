"""A persisted fill must record the rate that actually produced its fee.

The audit column ``paper_v2_fills.fee_rate`` used to be the ``Fill`` dataclass
default (0.0006) on every row, while ``execute_market_order``/``execute_close``
charged ``execution.FEE_RATE`` (0.0005). Measured live: a 50-contract BTC-USDT
entry showed fee 2.04678428 on a 4093.5686 notional (= 0.0005) beside a
recorded rate of 0.00060000. These tests tie the two together.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from paper_trading.contracts import Fill, OrderSide
from paper_trading.execution import FEE_RATE, execute_close, execute_market_order
from paper_trading.market import PerpetualSnapshot, RiskTier


def _snapshot(*, bid: str, ask: str) -> PerpetualSnapshot:
    now = datetime.now(timezone.utc).isoformat()
    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code="BTC-USDT",
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(bid),
        mark=Decimal(bid),
        index=Decimal(bid),
        funding_rate=Decimal("0"),
        next_funding_at=None,
        component_times={"ticker": now, "index": now, "mark": now, "funding": now},
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at=now,
        received_at=now,
        source="fixture",
        quality="live",
        risk_tier=RiskTier(1, 100, Decimal("0.0028"), now, "fixture"),
    )


def test_open_fill_reports_the_rate_it_charged() -> None:
    opened = execute_market_order(
        _snapshot(bid="9999", ask="10000"),
        direction="long",
        requested_quantity=Decimal("10"),
        leverage=10,
        stop_price=Decimal("9900"),
        slippage_bps=0,
        available_fraction=Decimal("1"),
    )

    assert opened.filled
    assert opened.fee_rate == FEE_RATE
    # notional = 10 contracts * 0.001 * 10000 = 100.000 -> fee = 100 * 0.0005
    assert opened.notional * opened.fee_rate == opened.entry_fee


def test_close_fill_reports_the_rate_it_charged() -> None:
    opened = execute_market_order(
        _snapshot(bid="9999", ask="10000"),
        direction="long",
        requested_quantity=Decimal("10"),
        leverage=10,
        stop_price=Decimal("9900"),
        slippage_bps=0,
        available_fraction=Decimal("1"),
    )
    assert opened.position is not None

    closed = execute_close(
        opened.position,
        _snapshot(bid="10100", ask="10101"),
        slippage_bps=0,
    )

    assert closed.fee_rate == FEE_RATE
    # base qty = 10 * 0.001 = 0.01 BTC, long closes at bid 10100
    assert Decimal("0.01") * Decimal("10100") * closed.fee_rate == closed.close_fee


def test_fill_without_a_rate_records_zero_not_a_plausible_number() -> None:
    fill = Fill(
        fill_id=uuid4(),
        order_id=uuid4(),
        account_id=uuid4(),
        execution_quote_id="quote-default",
        side=OrderSide.buy,
        price=Decimal("10000"),
        qty=Decimal("10"),
        fee=Decimal("0.05"),
    )

    assert fill.fee_rate == Decimal(0)


def test_fill_carries_the_rate_from_its_own_result() -> None:
    opened = execute_market_order(
        _snapshot(bid="9999", ask="10000"),
        direction="long",
        requested_quantity=Decimal("10"),
        leverage=10,
        stop_price=Decimal("9900"),
        slippage_bps=0,
        available_fraction=Decimal("1"),
    )
    fill = Fill(
        fill_id=uuid4(),
        order_id=uuid4(),
        account_id=uuid4(),
        execution_quote_id="quote-from-result",
        side=OrderSide.buy,
        price=opened.fill_price,
        qty=opened.filled_quantity,
        fee=opened.entry_fee,
        fee_rate=opened.fee_rate,
    )

    assert fill.fee_rate == Decimal("0.0005")
    assert fill.fee == opened.notional * fill.fee_rate
