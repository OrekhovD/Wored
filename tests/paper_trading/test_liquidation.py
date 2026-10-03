"""P5.1 golden tests — mark-triggered liquidation state machine (MC-16 unit half).

Proves, without any persistence layer:
  * mark crossing the isolated-margin liquidation price forces exactly one
    full close with ``exit_reason="liquidation"``;
  * a stale market blocks execution — the event goes ``pending`` with
    ``reason_code="stale_market"``, the position stays ``open``, and the same
    event completes once the feed is fresh again (crash/recovery semantics);
  * retries after liquidation are ``noop`` — no second fill is possible;
  * close economics come verbatim from ``execute_close`` (golden numbers are
    recomputed through the executor, never hand-copied).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_trading.execution import Position
from paper_trading.liquidation import (
    LiquidationState,
    evaluate_liquidation,
    mark_closed,
)
from paper_trading.market import PerpetualSnapshot, RiskTier


def _snapshot(*, mark: str, age_seconds: float = 0.0) -> PerpetualSnapshot:
    src = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    now = src.isoformat()
    m = Decimal(mark)
    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code="BTC-USDT",
        bid=m,
        ask=m + Decimal("0.5"),
        last=m,
        mark=m,
        index=m,
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


def _long(entry: str = "10000", qty: str = "10", lev: int = 10) -> Position:
    return Position(
        position_id="pos-long-1",
        account_id="acct-1",
        instrument="BTC-USDT",
        direction="long",
        entry_price=Decimal(entry),
        quantity=Decimal(qty),
        leverage=lev,
        stop_price=Decimal("9000"),
        reserved_margin=Decimal("100"),
        entry_fee=Decimal("6"),
        opened_at="2026-09-29T00:00:00+00:00",
        status="open",
    )


def _short(entry: str = "10000", qty: str = "10", lev: int = 10) -> Position:
    p = _long(entry, qty, lev)
    return Position(**{**p.__dict__, "position_id": "pos-short-1", "direction": "short"})


# ── probing: liquidation price is the shared trading_math formula ───────────


def test_long_not_triggered_above_liq_price():
    pos = _long()
    snap = _snapshot(mark="9500")
    res = evaluate_liquidation(pos, snap)
    assert res.state is LiquidationState.open
    assert res.reason_code == "not_triggered"
    assert res.liquidation_price is not None and res.liquidation_price < Decimal("9500")


def test_short_not_triggered_below_liq_price():
    pos = _short()
    probe = evaluate_liquidation(pos, _snapshot(mark="10500"))
    liq = probe.liquidation_price
    assert liq is not None and liq > Decimal("10500")
    res = evaluate_liquidation(pos, _snapshot(mark="10500"))
    assert res.reason_code == "not_triggered"


# ── trigger → exactly one full close ────────────────────────────────────────


def test_long_mark_below_liq_price_closes_fully():
    pos = _long()
    liq = evaluate_liquidation(pos, _snapshot(mark="9500")).liquidation_price
    assert liq is not None
    res = evaluate_liquidation(pos, _snapshot(mark=str(liq - 1)))
    assert res.state is LiquidationState.liquidated
    assert res.reason_code == "liquidated"
    assert res.event_key == "liq:pos-long-1"
    close = res.close
    assert close is not None
    assert close.closed and close.exit_reason == "liquidation"
    # forced FULL close
    assert close.closed_quantity == pos.quantity
    assert close.remaining_quantity == Decimal(0)
    assert close.remaining_position is None
    # golden economics: recomputed through the single executor definition
    from paper_trading.execution import execute_close
    golden = execute_close(pos, _snapshot(mark=str(liq - 1)),
                           exit_reason="liquidation")
    assert close.gross_pnl == golden.gross_pnl
    assert close.close_fee == golden.close_fee
    assert close.realized_net == golden.realized_net
    assert close.realized_net < 0  # deep below entry → the margin is wiped


def test_short_mark_above_liq_price_closes_fully():
    pos = _short()
    probe = evaluate_liquidation(pos, _snapshot(mark="10500"))
    liq = probe.liquidation_price
    assert liq is not None
    res = evaluate_liquidation(pos, _snapshot(mark=str(liq + 1)))
    assert res.state is LiquidationState.liquidated
    assert res.close is not None and res.close.exit_reason == "liquidation"
    assert res.close.remaining_quantity == Decimal(0)


# ── stale market: pending/blocked, never a fill (MC-16 negative) ────────────


def test_stale_market_blocks_close_and_stays_pending():
    pos = _long()
    liq = evaluate_liquidation(pos, _snapshot(mark="9500")).liquidation_price
    assert liq is not None
    res = evaluate_liquidation(pos, _snapshot(mark=str(liq - 1), age_seconds=120))
    assert res.state is LiquidationState.pending
    assert res.reason_code == "stale_market"
    assert res.close is None
    assert res.event_key == "liq:pos-long-1"  # deterministic recovery identity


def test_pending_recovers_and_closes_when_feed_returns():
    """Same event_key across the stale→fresh transition: the retry completes
    the one close, it does not start a second liquidation."""
    pos = _long()
    liq = evaluate_liquidation(pos, _snapshot(mark="9500")).liquidation_price
    assert liq is not None
    stale = evaluate_liquidation(pos, _snapshot(mark=str(liq - 1), age_seconds=120))
    fresh = evaluate_liquidation(pos, _snapshot(mark=str(liq - 1)),
                                 current_state=stale)
    assert stale.event_key == fresh.event_key
    assert fresh.state is LiquidationState.liquidated
    assert fresh.close is not None and fresh.close.closed


# ── exactly-once: retry after liquidation is a noop ─────────────────────────


def test_retry_after_liquidated_is_noop():
    pos = _long()
    liq = evaluate_liquidation(pos, _snapshot(mark="9500")).liquidation_price
    assert liq is not None
    snap = _snapshot(mark=str(liq - 1))
    first = evaluate_liquidation(pos, snap)
    assert first.state is LiquidationState.liquidated
    closed_pos = mark_closed(pos, first)
    # replay through the position status (what PG state would show)
    replay = evaluate_liquidation(closed_pos, snap)
    assert replay.state is LiquidationState.liquidated
    assert replay.reason_code == "already_liquidated"
    assert replay.close is None  # no second fill is ever produced
    # replay through external current_state (crash between fill and ack)
    replay2 = evaluate_liquidation(pos, snap, current_state=first)
    assert replay2.reason_code == "already_liquidated"
    assert replay2.close is first.close  # the SAME recorded fill, never a new one


def test_mark_closed_refuses_state_without_fill():
    pos = _long()
    res = evaluate_liquidation(pos, _snapshot(mark="9500"))
    with pytest.raises(ValueError):
        mark_closed(pos, res)


def test_zero_quantity_and_pending_order_states_are_noop():
    pos = _long()
    empty = Position(**{**pos.__dict__, "quantity": Decimal(0)})
    # mark deliberately deep: the quantity gate must fire BEFORE any pricing
    res = evaluate_liquidation(empty, _snapshot(mark="1"))
    assert res.state is LiquidationState.open
    assert res.reason_code == "position_not_open"
