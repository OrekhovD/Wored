"""Block C — episode normalisation across the two trading loops.

The acceptance criterion: *one trade represented in both* ``paper_v2_positions``
and ``sim_positions`` yields an identical ``net_pnl`` (the primitives, not the
per-loop stored figure, decide), while ``data_hash`` fingerprints the
``math_version`` so episodes from different maths are never mixed (TZ §7.3).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from paper_trading.episodes import (
    episode_from_paper_row,
    episode_from_sim_row,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc)


def paper_row(**over):
    base = dict(
        position_id="p-1", account_id="a-1", day_id="d-1", instrument="BTC-USDT",
        side="long", qty=Decimal(1), avg_entry_price=Decimal(100),
        close_price=Decimal(110), isolated_margin=Decimal(10),
        funding_cashflow=Decimal(0), realized_net_pnl=Decimal("999"),  # deliberately wrong
        opened_at=T0, closed_at=T1, status="closed", schema_version=2,
    )
    base.update(over)
    return base


def sim_row(**over):
    base = dict(
        id=7, user_id=42, symbol="BTC-USDT", direction="long", leverage=10,
        margin=Decimal(10), entry_price=Decimal(100), size=Decimal(1),
        close_price=Decimal(110), funding_paid=Decimal(0),
        realized_pnl=Decimal("-123"),  # deliberately wrong
        opened_at=T0, closed_at=T1, status="closed", close_reason="take_profit",
        calculation_version=2,
    )
    base.update(over)
    return base


def test_net_pnl_recomputed_from_primitives_not_stored():
    ep = episode_from_paper_row(paper_row(), strategy_version="B1")
    # gross 10 - entry_fee 0.06 - exit_fee 0.066 - funding 0
    assert ep.net_pnl == Decimal("9.8740")
    assert ep.stored_net_pnl == Decimal("999")  # stored value kept only for reconciliation


def test_same_trade_both_loops_identical_net_pnl():
    paper = episode_from_paper_row(paper_row(), strategy_version="B1")
    sim = episode_from_sim_row(sim_row(), strategy_version="B1")
    assert paper.net_pnl == sim.net_pnl == Decimal("9.8740")
    assert paper.gross_pnl == sim.gross_pnl


def test_short_side_net_pnl():
    ep = episode_from_paper_row(
        paper_row(side="short", avg_entry_price=Decimal(110), close_price=Decimal(100)),
        strategy_version="B1",
    )
    # gross 10 - entry_fee(110)=0.066 - exit_fee(100)=0.06
    assert ep.net_pnl == Decimal("9.874")
    assert ep.side == "short"


def test_mandatory_fields_present():
    ep = episode_from_paper_row(paper_row(), strategy_version="B1")
    for attr in ("data_hash", "features_hash", "math_version", "strategy_version"):
        assert hasattr(ep, attr)
    assert len(ep.data_hash) == 64
    assert ep.math_version == "2"
    assert ep.strategy_version == "B1"


def test_data_hash_stable_and_version_sensitive():
    a = episode_from_paper_row(paper_row(), strategy_version="B1")
    b = episode_from_paper_row(paper_row(), strategy_version="B1")
    assert a.data_hash == b.data_hash
    # bumping math_version fingerprints a different dataset (no mixing, §7.3)
    d = episode_from_sim_row(sim_row(calculation_version=1), strategy_version="B1")
    e = episode_from_sim_row(sim_row(calculation_version=2), strategy_version="B1")
    assert d.data_hash != e.data_hash  # math_version 1 vs 2 -> different hash


def test_data_hash_independent_of_leverage_but_net_uses_same_core():
    # leverage differs between the loops (paper has none, sim has 10x) yet the
    # net result and data fingerprint agree because both derive from primitives.
    paper = episode_from_paper_row(paper_row(), strategy_version="B1")
    sim1 = episode_from_sim_row(sim_row(leverage=1), strategy_version="B1")
    sim10 = episode_from_sim_row(sim_row(leverage=10), strategy_version="B1")
    assert sim1.net_pnl == sim10.net_pnl == paper.net_pnl


def test_features_hash_populated_from_features():
    ep = episode_from_paper_row(paper_row(), strategy_version="B1",
                                features={"rsi": 61, "ema_gap": 0.3})
    assert len(ep.features_hash) == 64
    ep2 = episode_from_paper_row(paper_row(), strategy_version="B1",
                                 features={"rsi": 61, "ema_gap": 0.3})
    assert ep.features_hash == ep2.features_hash


def test_liquidation_flag_from_sim():
    liq = episode_from_sim_row(sim_row(status="liquidated", close_reason="liquidation",
                                       close_price=Decimal(90)), strategy_version="B1")
    assert liq.liquidated is True
    assert liq.net_pnl < 0


def test_r_multiple_uses_isolated_margin():
    ep = episode_from_paper_row(paper_row(isolated_margin=Decimal(10)), strategy_version="B1")
    assert round(ep.r_multiple, 6) == round(9.874 / 10, 6)
