"""P6.4 — multi-window replay distribution + same-cost benchmarks (MC-21, host half).

Pure host tests, no database. They lock the properties ТЗ §35 / MC-21 demands:

  * a **distribution**, not a single session (mean/median/percentiles/share-positive);
  * **train is kept out of the holdout** verdict (out-of-sample only);
  * **no-trade and buy-hold** are compared **with the same cost model** (a flat
    window costs money through fees/slippage, so "beat the market" isn't free);
  * **insufficient sample gives N/A**, not a point estimate;
  * the replay is **reproducible** — a canonical data-hash pins the window set.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_trading.replay_distribution import (
    CostModel,
    WindowOutcome,
    assert_disjoint_windows,
    buy_hold_with_costs,
    compare_to_benchmarks,
    holdout_distribution,
    replay_data_hash,
    split_train_holdout,
    summarize_distribution,
    train_distribution,
)

_D = Decimal


def _w(win_id, split, net, bh, *, dh="data", start=None, end=None):
    return WindowOutcome(
        window_id=win_id, split=split, net_pnl=_D(net), buy_hold_net=_D(bh),
        data_hash=dh, start_utc=start, end_utc=end,
    )


# ---------------------------------------------------------------------------
# distribution
# ---------------------------------------------------------------------------


def test_distribution_math_is_exact_and_deterministic():
    s = summarize_distribution([_D("-5"), _D("0"), _D("5"), _D("10")])
    assert s.count == 4
    assert s.sum == "10"
    assert s.min == "-5"
    assert s.max == "10"
    assert s.mean == "2.5"
    assert s.median == "2.5"
    # linear-interpolated percentiles (compare as Decimal: scale is not fixed)
    assert _D(s.p25) == _D("-1.25")
    assert _D(s.p75) == _D("6.25")
    assert _D(s.p90) == _D("8.5")
    assert s.share_positive == "0.5"  # 2 of 4
    assert s.share_negative == "0.25"
    assert s.zero_count == 1
    assert s.stdev is not None and _D(s.stdev) > 0


def test_single_window_has_no_shape():
    s = summarize_distribution([_D("7")])
    assert s.count == 1
    assert s.mean == "7"
    assert s.stdev is None
    assert s.reasons.get("stdev") == "insufficient_sample"


def test_empty_sample_is_all_null():
    s = summarize_distribution([])
    assert s.count == 0
    assert s.mean is None and s.median is None and s.stdev is None
    assert s.reasons["*"] == "insufficient_sample"


# ---------------------------------------------------------------------------
# train / holdout separation
# ---------------------------------------------------------------------------


def test_holdout_excludes_train():
    windows = [
        _w("t1", "train", "500", "10"),   # huge train outlier
        _w("h1", "holdout", "-5", "1"),
        _w("h2", "holdout", "5", "-1"),
    ]
    holdout = holdout_distribution(windows)
    assert holdout.count == 2
    assert holdout.mean == "0"           # (-5 + 5)/2, train's 500 ignored
    train = train_distribution(windows)
    assert train.count == 1
    assert train.mean == "500"


def test_split_partitions_windows():
    windows = [_w("a", "train", "1", "1"), _w("b", "holdout", "2", "2")]
    train, holdout = split_train_holdout(windows)
    assert [w.window_id for w in train] == ["a"]
    assert [w.window_id for w in holdout] == ["b"]


def test_invalid_split_rejected():
    with pytest.raises(ValueError):
        _w("x", "test", "1", "1")


# ---------------------------------------------------------------------------
# same-cost benchmarks
# ---------------------------------------------------------------------------


def test_buy_hold_flat_window_still_costs_money():
    costs = CostModel(fee_rate=_D("0.0006"), slippage_bps=_D("5"))
    net = buy_hold_with_costs(
        start_price=_D("10000"), end_price=_D("10000"), budget=_D("1000"), costs=costs
    )
    # no price move, yet fees + slippage make the benchmark net negative
    assert net < 0


def test_buy_hold_rising_window_is_positive():
    costs = CostModel(fee_rate=_D("0.0006"), slippage_bps=_D("5"))
    net = buy_hold_with_costs(
        start_price=_D("10000"), end_price=_D("20000"), budget=_D("1000"), costs=costs
    )
    assert net > 0


def test_buy_hold_funding_is_applied_as_a_cost():
    base = CostModel(fee_rate=_D("0.0006"), slippage_bps=_D("5"), funding=_D("0"))
    neg = CostModel(fee_rate=_D("0.0006"), slippage_bps=_D("5"), funding=_D("-5"))
    kw = dict(start_price=_D("10000"), end_price=_D("10000"), budget=_D("1000"))
    assert buy_hold_with_costs(costs=neg, **kw) == buy_hold_with_costs(costs=base, **kw) - _D("5")


def test_compare_strategy_vs_benchmarks_on_holdout_only():
    windows = [
        _w("t", "train", "999", "0"),                 # train must not leak in
        _w("h1", "holdout", "5", "1"),                # beats buy-hold
        _w("h2", "holdout", "-2", "1"),               # loses to buy-hold
    ]
    cmp = compare_to_benchmarks(windows)
    assert cmp.windows == 2
    assert cmp.strategy_mean == "1.5"                 # (5 + -2)/2
    assert cmp.buy_hold_mean == "1"                   # (1 + 1)/2
    assert cmp.no_trade_mean == "0"
    assert cmp.delta_vs_buy_hold == "0.5"
    assert cmp.beats_buy_hold_count == 1              # only h1


def test_compare_with_no_holdout_is_null():
    cmp = compare_to_benchmarks([_w("t", "train", "1", "1")])
    assert cmp.windows == 0
    assert cmp.strategy_mean is None
    assert cmp.reasons["*"] == "insufficient_sample"


# ---------------------------------------------------------------------------
# reproducibility + disjointness
# ---------------------------------------------------------------------------


def test_replay_data_hash_is_reproducible_and_sensitive():
    windows = [_w("a", "holdout", "5", "1", dh="bars-a"), _w("b", "train", "2", "3", dh="bars-b")]
    h1 = replay_data_hash(windows)
    h2 = replay_data_hash([_w("a", "holdout", "5", "1", dh="bars-a"),
                           _w("b", "train", "2", "3", dh="bars-b")])
    assert h1 == h2
    assert h1.startswith("sha256:")
    # changing a single window's bar-hash flips the digest
    h3 = replay_data_hash([_w("a", "holdout", "5", "1", dh="bars-CHANGED"),
                           _w("b", "train", "2", "3", dh="bars-b")])
    assert h3 != h1
    # and so does changing a net
    h4 = replay_data_hash([_w("a", "holdout", "6", "1", dh="bars-a"),
                           _w("b", "train", "2", "3", dh="bars-b")])
    assert h4 != h1


def _dt(minute):
    return datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=minute)


def test_disjoint_windows_pass():
    windows = [
        _w("a", "holdout", "1", "1", start=_dt(0), end=_dt(60)),
        _w("b", "holdout", "2", "2", start=_dt(60), end=_dt(120)),
    ]
    assert_disjoint_windows(windows)  # no raise


def test_overlapping_windows_are_rejected():
    windows = [
        _w("a", "holdout", "1", "1", start=_dt(0), end=_dt(90)),
        _w("b", "holdout", "2", "2", start=_dt(60), end=_dt(120)),
    ]
    with pytest.raises(ValueError):
        assert_disjoint_windows(windows)
