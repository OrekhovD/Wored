"""Unit tests for the P6.1 immutable session/account report (ТЗ §7, MC-18/MC-20).

Pure, deterministic, no DB, no clock.  These lock the report contract:
metrics come from the ledger/equity series, sample-sensitive values null out
with a reason instead of a fabricated 0, manual and auto stay independent, and
a report is only *final* after settlement + reconciliation with nothing open.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_trading.session_report import (
    MIN_TRADES_DEFAULT,
    AccountReportV1,
    Trade,
    build_account_report,
    build_session_report,
    canonical_hash,
)


def _t(i, net, *, liq=False, gross=None, fee_in=None, fee_out=None,
       funding=None, planned=None, notional=None, opened=None, closed=None):
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return Trade(
        position_id=f"p{i}",
        realized_net=Decimal(str(net)) if net is not None else None,
        realized_gross=Decimal(str(gross)) if gross is not None else None,
        entry_fee=Decimal(str(fee_in)) if fee_in is not None else None,
        exit_fee=Decimal(str(fee_out)) if fee_out is not None else None,
        funding_cashflow=Decimal(str(funding)) if funding is not None else None,
        is_liquidated=liq,
        planned_risk=Decimal(str(planned)) if planned is not None else None,
        notional=Decimal(str(notional)) if notional is not None else None,
        opened_at=base + timedelta(minutes=i) if opened is None else opened,
        closed_at=base + timedelta(minutes=i + 2) if closed is None else closed,
    )


def _many(n, sign=1):
    # enough trades to clear MIN_TRADES for the sample-sensitive metrics
    return [_t(i, sign * Decimal("0.5") if i % 3 else sign * -Decimal("0.2")) for i in range(n)]


# ── net / gross / fees / funding are read from the ledger, summed exactly ──────


def test_net_pnl_and_fees_sum_from_trades():
    trades = [
        _t(0, 1.5, gross=1.6, fee_in=0.05, fee_out=0.05, funding=0.1),
        _t(1, -0.4, gross=-0.3, fee_in=0.05, fee_out=0.05, funding=-0.02),
    ]
    rep = build_account_report("manual", trades, initial_budget=10)
    assert Decimal(rep.net_pnl) == Decimal("1.1")
    assert Decimal(rep.realized_gross) == Decimal("1.3")
    # entry fees 0.10 + exit fees 0.10
    assert Decimal(rep.entry_fees) == Decimal("0.1")
    assert Decimal(rep.exit_fees) == Decimal("0.1")
    assert Decimal(rep.total_fees) == Decimal("0.2")
    assert Decimal(rep.funding_cashflow) == Decimal("0.08")
    # final equity = initial + net
    assert Decimal(rep.final_equity) == Decimal("11.1")


def test_liquidated_trades_are_counted_and_included():
    trades = [_t(0, -5, liq=True), _t(1, 2)]
    rep = build_account_report("auto", trades, initial_budget=100)
    assert rep.trades_closed == 2
    assert rep.trades_liquidated == 1


# ── MC-20: ROI / drawdown / win rate / expectancy / PF semantics ──────────────


def test_roi_is_net_over_budget_as_decimal_string():
    trades = [_t(0, 5)]
    rep = build_account_report("manual", trades, initial_budget=100)
    assert Decimal(rep.roi) == Decimal("0.05")


def test_roi_division_by_zero_is_null_with_reason():
    trades = [_t(0, 5)]
    rep = build_account_report("manual", trades, initial_budget=0)
    assert rep.roi is None
    assert rep.reasons["roi"] == "division_by_zero"


def test_sample_metrics_null_below_min_trades():
    trades = _many(MIN_TRADES_DEFAULT - 1)  # 9 trades
    rep = build_account_report("manual", trades, initial_budget=100)
    assert rep.win_rate is None
    assert rep.expectancy is None
    assert rep.profit_factor is None
    assert rep.reasons["win_rate"] == "insufficient_sample"
    assert rep.reasons["expectancy"] == "insufficient_sample"
    assert rep.reasons["profit_factor"] == "insufficient_sample"


def test_sample_metrics_present_at_or_above_min_trades():
    trades = _many(MIN_TRADES_DEFAULT)  # exactly the floor
    rep = build_account_report("manual", trades, initial_budget=100)
    assert rep.win_rate is not None
    assert Decimal(rep.win_rate) > 0
    assert rep.expectancy is not None
    assert rep.profit_factor is not None
    # all trades are the same series → wins count is deterministic
    expected_wins = sum(1 for t in trades if t.realized_net > 0)
    assert Decimal(rep.win_rate) == Decimal(expected_wins) / Decimal(len(trades))


def test_max_drawdown_is_chronological_not_newest_first():
    # equity 100 → 110 → 90 → 95; realised drawdown is 20 (peak 110 → trough 90),
    # NOT the last-trade PnL.  Order matters, so this catches the documented bug.
    trades = [_t(0, 10), _t(1, -20), _t(2, 5)]
    rep = build_account_report("manual", trades, initial_budget=100)
    assert Decimal(rep.max_drawdown) == Decimal("20")


def test_time_in_market_sums_open_to_close():
    trades = [_t(0, 1), _t(1, 1)]  # each 2 minutes
    rep = build_account_report("manual", trades, initial_budget=100)
    assert rep.time_in_market_seconds == 240


# ── manual vs auto stay independent (no pooling) ──────────────────────────────


def test_session_keeps_accounts_separate():
    manual = build_account_report("manual", [_t(0, 5)], initial_budget=100)
    auto = build_account_report("auto", [_t(0, -3)], initial_budget=50)
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sess = build_session_report(
        session_id="s1", day_id="d1", instrument_key="htx:linear-swap:BTC-USDT",
        as_of=now, accounts={"manual": manual, "auto": auto},
        settlement_complete=True, reconciliation_ok=True,
    )
    d = sess.to_dict()
    # balances are NOT merged — each account carries its own numbers
    assert Decimal(d["accounts"]["manual"]["net_pnl"]) == Decimal("5")
    assert Decimal(d["accounts"]["auto"]["net_pnl"]) == Decimal("-3")
    assert "total_net" not in d  # there is deliberately no pooled figure


# ── MC-18: final only after settlement + reconciliation, nothing open ──────────


def test_report_not_final_when_settlement_pending():
    acc = {"manual": build_account_report("manual", [_t(0, 1)], initial_budget=10)}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sess = build_session_report(
        session_id="s", day_id="d", instrument_key="k", as_of=now, accounts=acc,
        settlement_complete=False, reconciliation_ok=True,
    )
    assert sess.is_final is False
    assert sess.not_final_reason == "settlement_pending"


def test_report_not_final_when_reconciliation_fails():
    acc = {"manual": build_account_report("manual", [_t(0, 1)], initial_budget=10)}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sess = build_session_report(
        session_id="s", day_id="d", instrument_key="k", as_of=now, accounts=acc,
        settlement_complete=True, reconciliation_ok=False,
        reconciliation_detail={"expected": "10", "observed": "9"},
    )
    assert sess.is_final is False
    assert sess.not_final_reason == "reconciliation_failed"


def test_report_not_final_when_positions_remain_open():
    acc = {"manual": build_account_report("manual", [_t(0, 1)], initial_budget=10)}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sess = build_session_report(
        session_id="s", day_id="d", instrument_key="k", as_of=now, accounts=acc,
        settlement_complete=True, reconciliation_ok=True, open_positions=2,
    )
    assert sess.is_final is False
    assert sess.not_final_reason == "open_positions_remain"


def test_report_final_when_settled_reconciled_and_flat():
    acc = {"manual": build_account_report("manual", [_t(0, 1)], initial_budget=10)}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    sess = build_session_report(
        session_id="s", day_id="d", instrument_key="k", as_of=now, accounts=acc,
        settlement_complete=True, reconciliation_ok=True, open_positions=0,
    )
    assert sess.is_final is True
    assert sess.not_final_reason is None


# ── canonical hash anchors immutability ───────────────────────────────────────


def _sess(**over):
    acc = {"manual": build_account_report("manual", [_t(0, 5)], initial_budget=100)}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    kwargs = dict(
        session_id="s1", day_id="d1", instrument_key="k", as_of=now, accounts=acc,
        settlement_complete=True, reconciliation_ok=True,
    )
    kwargs.update(over)
    return build_session_report(**kwargs)


def test_canonical_hash_is_stable_and_flips_on_change():
    h1 = canonical_hash(_sess())
    h2 = canonical_hash(_sess())
    assert h1 == h2  # same content → same digest (reproducible)
    h3 = canonical_hash(_sess(day_id="d2"))
    assert h3 != h1  # any byte-level change flips the digest


def test_empty_account_report_has_null_money_but_counts_zero():
    rep = build_account_report("auto", [], initial_budget=100)
    assert rep.net_pnl is None
    assert rep.roi is None
    assert rep.trades_closed == 0
    assert rep.reasons["max_drawdown"] == "insufficient_sample"
