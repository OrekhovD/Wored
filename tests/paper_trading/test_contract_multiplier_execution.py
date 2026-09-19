from datetime import datetime, timezone
from decimal import Decimal

from paper_trading.execution import execute_close, execute_market_order
from paper_trading.market import PerpetualSnapshot, RiskTier
from paper_trading.risk import OrderRequest, RiskSettings, check_order_risk


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


def test_risk_sizes_contracts_using_multiplier() -> None:
    market = _snapshot(bid="9999", ask="10000")
    result = check_order_risk(
        OrderRequest(
            instrument="BTC-USDT",
            direction="long",
            risk_amount=Decimal("1"),
            stop_price=Decimal("9900"),
            take_profit=Decimal("10200"),
            leverage=10,
        ),
        market,
        RiskSettings(require_risk_tier=True, min_net_rr=Decimal("0")),
        available_margin=Decimal("1000"),
    )

    assert result.quantity == Decimal("10")
    assert result.risk_to_sl == Decimal("1.000")
    assert result.reserved_margin == Decimal("10.0000")


def test_open_and_close_use_same_contract_multiplier() -> None:
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
    assert opened.notional == Decimal("100.000")
    assert opened.entry_fee == Decimal("0.0600000")
    assert opened.position is not None

    closed = execute_close(
        opened.position,
        _snapshot(bid="10100", ask="10101"),
        slippage_bps=0,
    )

    assert closed.gross_pnl == Decimal("1.000")
    assert closed.close_fee == Decimal("0.0606000")
    assert closed.realized_net == Decimal("0.8794000")
