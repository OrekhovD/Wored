"""Block D — the statistical promotion gate (``paper_trading.learning``).

Covers the primitives (bootstrap CI, Deflated Sharpe Ratio, time-purge split,
comparability) and the three acceptance properties from the TZ:

* 100 pure-noise candidates → approved in <= 5 % of runs (Type-I control);
* a genuine +0.3 %/trade edge → approved;
* a one-bar look-ahead leak (edge present only in replay) → NOT approved.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from paper_trading.learning import (
    Episode,
    LearningEvaluator,
    bootstrap_mean_diff_ci,
    deflated_sharpe_ratio,
)

CAPITAL = Decimal(1000)


def _ts(day: int, minute: int = 0) -> str:
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day, minutes=minute)
    return dt.isoformat()


def make_episodes(version: str, nets: list[Decimal], *, liquid: list[bool] | None = None) -> list[Episode]:
    eps: list[Episode] = []
    n = len(nets)
    for i, net in enumerate(nets):
        # spread across the year so >= min_trading_days distinct dates appear
        day = i * 45 // max(1, n)
        gross = net + Decimal("0.12")
        eps.append(Episode(
            episode_id=f"{version}-{i}", strategy_version=version, instrument="BTC-USDT",
            interval="1m", entry_time=_ts(day), exit_time=_ts(day, 10), side="long",
            entry_price=Decimal("100"), exit_price=Decimal("100") + net, qty=Decimal("1"),
            gross_pnl=gross, entry_fee=Decimal("0.06"), exit_fee=Decimal("0.06"),
            funding_cashflow=Decimal("0"), net_pnl=net, max_drawdown=Decimal("0.5"),
            duration_minutes=10, liquidation=bool(liquid[i]) if liquid else False,
        ))
    return eps


FAST = {
    "min_episodes": 200, "min_holdout_episodes": 60, "min_trading_days": 30,
    "chronological_split": 0.70, "max_lookback_minutes": 0, "max_hold_minutes": 0,
    "bar_minutes": 1, "embargo_bars": 1, "bootstrap_resamples": 400,
    "n_trials": 1, "dsr_threshold": 0.95,
}


# --------------------------------------------------------------------------- #
# Primitive unit tests
# --------------------------------------------------------------------------- #
def test_bootstrap_ci_excludes_zero_when_means_differ():
    rng = random.Random(7)
    a = [rng.gauss(5.0, 1.0) for _ in range(120)]
    b = [rng.gauss(0.0, 1.0) for _ in range(120)]
    point, lo, hi = bootstrap_mean_diff_ci(a, b, resamples=2000, seed=99)
    assert point > 0
    assert lo > 0  # significant


def test_bootstrap_ci_includes_zero_for_equal_populations():
    rng = random.Random(3)
    a = [rng.gauss(0.0, 1.0) for _ in range(100)]
    b = [rng.gauss(0.0, 1.0) for _ in range(100)]
    point, lo, hi = bootstrap_mean_diff_ci(a, b, resamples=2000, seed=11)
    assert lo <= 0 <= hi


def test_dsr_high_for_stable_edge_low_for_noise():
    good = deflated_sharpe_ratio([1.0, 1.1, 0.9, 1.2, 1.0, 1.1, 0.8, 1.3], n_trials=1)
    # a zero-mean symmetric series has Sharpe 0 -> PSR/DSR collapses to 0.5
    zero = deflated_sharpe_ratio([1.0, -1.0, 1.2, -1.2, 0.9, -0.9, 1.3, -1.3], n_trials=1)
    assert good > 0.9
    assert abs(zero - 0.5) < 0.05
    assert good > zero


def test_dsr_decreases_with_more_trials():
    rets = [0.5, 0.6, 0.4, 0.7, 0.5, 0.65, 0.45, 0.6]
    assert deflated_sharpe_ratio(rets, n_trials=1) >= deflated_sharpe_ratio(rets, n_trials=50)


def test_temporal_split_purges_boundary():
    ev = LearningEvaluator({**FAST})
    eps = make_episodes("v", [Decimal(1)] * 100)
    replay, holdout = ev._temporal_split(eps)
    # with zero purge/embargo the split is by time and lossless
    assert len(replay) + len(holdout) == 100


def test_comparability_rejects_mismatch():
    ev = LearningEvaluator({**FAST})
    a = make_episodes("v1", [Decimal(1)] * 10)
    b = make_episodes("v2", [Decimal(1)] * 10)
    assert ev._comparable(a, b)[0] is True
    # different instrument
    b2 = make_episodes("v2", [Decimal(1)] * 10)
    b2[0] = Episode(**{**b2[0].__dict__, "instrument": "ETH-USDT"})
    ok, why = ev._comparable(a, b2)
    assert ok is False and "instrument" in why


# --------------------------------------------------------------------------- #
# Acceptance properties
# --------------------------------------------------------------------------- #
def test_noise_candidates_are_not_promoted():
    approved = 0
    trials = 100
    for t in range(trials):
        rng = random.Random(1000 + t)
        base = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(260)]
        cand = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(260)]
        evaluator = LearningEvaluator(FAST)
        result = evaluator.evaluate(
            make_episodes("cand", cand), make_episodes("base", base), CAPITAL)
        if result.status == "approved":
            approved += 1
    assert approved / trials <= 0.05, f"{approved}/{trials} noise candidates approved"


def test_genuine_edge_is_promoted():
    rng = random.Random(2024)
    base = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(260)]
    # candidate: same noise + a real +3.0 USDT (~0.3 % of capital) per trade edge
    cand = [Decimal(str(round(rng.gauss(0.0, 1.0) + 3.0, 4))) for _ in range(260)]
    evaluator = LearningEvaluator(FAST)
    result = evaluator.evaluate(
        make_episodes("cand", cand), make_episodes("base", base), CAPITAL)
    assert result.status == "approved", (result.reason, result.gate_results)
    assert result.gate_results["holdout_significance_ci"] is True
    assert result.gate_results["holdout_deflated_sharpe"] is True


def test_look_ahead_leak_is_not_promoted():
    """Edge exists only in the replay window; the holdout is pure noise.

    A feature shifted one bar into the future flatters the fit period but the
    gate decides on the holdout, where the leak provides no real edge.
    """
    rng = random.Random(55)
    n = 260
    split = int(n * FAST["chronological_split"])
    base = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(n)]
    cand = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(n)]
    # leak: only the replay segment is boosted (in-sample look-ahead)
    for i in range(split):
        cand[i] = cand[i] + Decimal("6.0")
    evaluator = LearningEvaluator(FAST)
    result = evaluator.evaluate(
        make_episodes("cand", cand), make_episodes("base", base), CAPITAL)
    assert result.status != "approved"
    assert result.gate_results.get("holdout_significance_ci") is False


def test_insufficient_data_when_too_few_days():
    evaluator = LearningEvaluator(FAST)
    cand = make_episodes("cand", [Decimal("5")] * 210)  # >200 eps but <30 days
    base = make_episodes("base", [Decimal("0")] * 210)
    # compress into 20 distinct days by editing entry times
    for e in cand + base:
        i = int(e.episode_id.split("-")[1])
        e.entry_time = _ts(i % 20)
        e.exit_time = _ts(i % 20, 10)
    result = evaluator.evaluate(cand, base, CAPITAL)
    assert result.status == "insufficient_data"
