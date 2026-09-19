from datetime import datetime, timedelta, timezone
from decimal import Decimal

from paper_trading.market import PerpetualSnapshot, RiskTier, snapshot_from_dict, snapshot_to_dict
from paper_trading.risk import OrderRequest, RiskSettings, check_order_risk


def snapshot(*, risk_tier: RiskTier | None) -> PerpetualSnapshot:
    now = datetime.now(timezone.utc).isoformat()
    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code="BTC-USDT",
        bid=Decimal("99990"),
        ask=Decimal("100000"),
        last=Decimal("99995"),
        mark=Decimal("99995"),
        index=Decimal("99994"),
        funding_rate=Decimal("0"),
        next_funding_at=None,
        component_times={"ticker": now, "index": now, "mark": now, "funding": now},
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at=now,
        received_at=now,
        source="test",
        quality="live",
        risk_tier=risk_tier,
    )


def test_snapshot_round_trip_preserves_risk_tier() -> None:
    tier = RiskTier(1, 100, Decimal("0.0028"), datetime.now(timezone.utc).isoformat(), "fixture")

    restored = snapshot_from_dict(snapshot_to_dict(snapshot(risk_tier=tier)))

    assert restored.risk_tier == tier


def test_strict_risk_policy_blocks_entry_without_tier() -> None:
    result = check_order_risk(
        OrderRequest(
            instrument="BTC-USDT",
            direction="long",
            risk_amount=Decimal("1"),
            stop_price=Decimal("99900"),
            take_profit=Decimal("100200"),
            leverage=10,
        ),
        snapshot(risk_tier=None),
        RiskSettings(require_risk_tier=True),
        available_margin=Decimal("1000"),
    )

    assert result.denied
    assert "risk_tier_unavailable" in result.reasons


def test_strict_risk_policy_blocks_stale_tier() -> None:
    stale_tier = RiskTier(
        1,
        100,
        Decimal("0.0028"),
        (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "fixture",
    )
    result = check_order_risk(
        OrderRequest(
            instrument="BTC-USDT",
            direction="long",
            risk_amount=Decimal("1"),
            stop_price=Decimal("99900"),
            take_profit=Decimal("100200"),
            leverage=10,
        ),
        snapshot(risk_tier=stale_tier),
        RiskSettings(require_risk_tier=True, risk_tier_max_age_seconds=60),
        available_margin=Decimal("1000"),
    )

    assert result.denied
    assert "risk_tier_stale" in result.reasons


def test_zero_available_margin_is_not_treated_as_unlimited() -> None:
    tier = RiskTier(
        1,
        100,
        Decimal("0.0028"),
        datetime.now(timezone.utc).isoformat(),
        "fixture",
    )
    result = check_order_risk(
        OrderRequest(
            instrument="BTC-USDT",
            direction="long",
            risk_amount=Decimal("1"),
            stop_price=Decimal("99900"),
            take_profit=Decimal("100200"),
            leverage=10,
        ),
        snapshot(risk_tier=tier),
        RiskSettings(require_risk_tier=True),
        available_margin=Decimal("0"),
    )

    assert result.denied
    assert "insufficient_margin" in result.reasons
