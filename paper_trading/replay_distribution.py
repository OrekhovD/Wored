"""paper_trading.replay_distribution — multi-window replay distribution + benchmarks (P6.4).

ТЗ §35 / §10 MC-21: performance is *not* judged on one lucky session — a
reproducible replay runs over several **non-overlapping** historical windows,
keeps **train** separate from **holdout**, and compares the result against
**no-trade** and **buy-hold** using the *same* cost model, with an insufficient
sample yielding ``N/A`` rather than a point estimate.

This module is the host-computable half of that: it takes per-window outcomes
(the strategy's recorded net plus the equally-costed benchmarks) and produces a
deterministic distribution summary, the train/holdout split, the benchmark
comparison and a canonical data-hash so a replay is reproducible. It performs no
execution and reads no database — the engine that produces the per-window nets is
the replay path covered elsewhere; here we only *aggregate and compare*, with
exact ``Decimal`` arithmetic and no floats leaking into money.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

__all__ = [
    "CostModel",
    "buy_hold_with_costs",
    "WindowOutcome",
    "DistributionSummary",
    "split_train_holdout",
    "assert_disjoint_windows",
    "summarize_distribution",
    "holdout_distribution",
    "train_distribution",
    "compare_to_benchmarks",
    "BenchmarkComparison",
    "replay_data_hash",
]

_ZERO = Decimal(0)
# Below this many windows the shape metrics (stdev) are N/A — a single point has
# no distribution (mirrors MC-08: "insufficient sample gives N/A").
MIN_WINDOWS_FOR_SHAPE = 2
MIN_WINDOWS_FOR_STDEV = 2


def _q(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


# ---------------------------------------------------------------------------
# cost model + buy-hold benchmark (same costs, MC-21)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostModel:
    """The explicit, versioned cost assumptions applied identically everywhere.

    The buy-hold benchmark must be netted with *the same* fee / slippage / funding
    the strategy pays, otherwise "beat the market" is an artefact of ignoring the
    benchmark's costs. Values are ``Decimal``; ``fee_rate`` is per-side taker.
    """

    fee_rate: Decimal
    slippage_bps: Decimal
    funding: Decimal = _ZERO
    version: str = "cost_v1"

    @property
    def slip(self) -> Decimal:
        return self.slippage_bps / Decimal(10000)


def buy_hold_with_costs(
    *, start_price: Decimal, end_price: Decimal, budget: Decimal, costs: CostModel
) -> Decimal:
    """Net P&L of simply holding the instrument over a window, costed the same way.

    Enter long at ``start_price`` slipped up, exit at ``end_price`` slipped down,
    charged taker fee on both legs, then the window's funding cashflow — identical
    assumptions to the strategy so the comparison is apples-to-apples.
    """
    eff_entry = start_price * (Decimal(1) + costs.slip)
    eff_exit = end_price * (Decimal(1) - costs.slip)
    if eff_entry == _ZERO:
        return _ZERO
    qty = budget / eff_entry
    gross = qty * (eff_exit - eff_entry)
    fees = qty * eff_entry * costs.fee_rate + qty * eff_exit * costs.fee_rate
    return gross - fees + costs.funding


# ---------------------------------------------------------------------------
# one replayed window
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowOutcome:
    window_id: str
    split: str                       # "train" | "holdout"
    net_pnl: Decimal                 # strategy recorded net for this window
    buy_hold_net: Decimal            # same-cost benchmark net (see above)
    start_utc: Any = None
    end_utc: Any = None
    data_hash: str | None = None     # hash of the exact bars/plan used

    def __post_init__(self) -> None:
        object.__setattr__(self, "net_pnl", Decimal(self.net_pnl))
        object.__setattr__(self, "buy_hold_net", Decimal(self.buy_hold_net))
        if self.split not in ("train", "holdout"):
            raise ValueError(f"split must be 'train' or 'holdout', got {self.split!r}")


# ---------------------------------------------------------------------------
# train / holdout separation + overlap guard
# ---------------------------------------------------------------------------


def split_train_holdout(
    windows: Sequence[WindowOutcome],
) -> tuple[list[WindowOutcome], list[WindowOutcome]]:
    """Return ``(train, holdout)`` — the two are never mixed into one metric."""
    train = [w for w in windows if w.split == "train"]
    holdout = [w for w in windows if w.split == "holdout"]
    return train, holdout


def assert_disjoint_windows(windows: Sequence[WindowOutcome]) -> None:
    """Reject overlapping replay windows (a window must not double-count bars).

    Only checks pairs that actually carry ``start_utc``/``end_utc``; windows
    without time bounds are simply skipped (they cannot be proven to overlap).
    """
    timed = [w for w in windows if w.start_utc is not None and w.end_utc is not None]
    timed.sort(key=lambda w: w.start_utc)  # type: ignore[arg-type]
    for prev, cur in zip(timed, timed[1:]):
        if cur.start_utc < prev.end_utc:  # type: ignore[operator]
            raise ValueError(
                f"replay windows overlap: {prev.window_id} ends after "
                f"{cur.window_id} starts"
            )


# ---------------------------------------------------------------------------
# distribution summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistributionSummary:
    count: int
    sum: str | None
    min: str | None
    max: str | None
    mean: str | None
    median: str | None
    stdev: str | None               # None when count < MIN_WINDOWS_FOR_STDEV
    p25: str | None
    p75: str | None
    p90: str | None
    share_positive: str | None
    share_negative: str | None
    zero_count: int
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            count=self.count,
            sum=self.sum,
            min=self.min,
            max=self.max,
            mean=self.mean,
            median=self.median,
            stdev=self.stdev,
            p25=self.p25,
            p75=self.p75,
            p90=self.p90,
            share_positive=self.share_positive,
            share_negative=self.share_negative,
            zero_count=self.zero_count,
            reasons=dict(self.reasons),
        )


def _percentile(sorted_vals: list[Decimal], pct: Decimal) -> Decimal:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = Decimal(n - 1) * pct
    lo = int(rank.to_integral_value(rounding="ROUND_FLOOR"))
    hi = min(lo + 1, n - 1)
    frac = rank - Decimal(lo)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _variance(vals: Sequence[Decimal]) -> Decimal | None:
    """Sample variance (n-1 denominator); ``None`` when there is no spread to see."""
    n = len(vals)
    if n < MIN_WINDOWS_FOR_STDEV:
        return None
    mean = sum(vals, _ZERO) / Decimal(n)
    return sum(((v - mean) ** 2 for v in vals), _ZERO) / Decimal(n - 1)


def _sqrt(dec: Decimal) -> Decimal:
    return dec.sqrt()


def summarize_distribution(values: Sequence[Decimal]) -> DistributionSummary:
    """Deterministic ``Decimal`` distribution over a set of window P&L values.

    An empty sample yields all-null metrics with a reason; a single window has a
    defined mean/median but ``stdev``/percentiles-as-shape are flagged
    ``insufficient_sample`` — one point is not a distribution.
    """
    vals = [Decimal(v) for v in values]
    n = len(vals)
    reasons: dict[str, str] = {}
    if n == 0:
        return DistributionSummary(
            count=0, sum=None, min=None, max=None, mean=None, median=None,
            stdev=None, p25=None, p75=None, p90=None, share_positive=None,
            share_negative=None, zero_count=0,
            reasons={"*": "insufficient_sample"},
        )
    svals = sorted(vals)
    total = sum(vals, _ZERO)
    mean = total / Decimal(n)
    # median
    mid = n // 2
    if n % 2:
        median = svals[mid]
    else:
        median = (svals[mid - 1] + svals[mid]) / Decimal(2)
    var = _variance(vals)
    if var is None:
        reasons["stdev"] = "insufficient_sample"
        stdev = None
    else:
        stdev = _sqrt(var)
    pos = sum(1 for v in vals if v > _ZERO)
    neg = sum(1 for v in vals if v < _ZERO)
    zero = sum(1 for v in vals if v == _ZERO)
    return DistributionSummary(
        count=n,
        sum=_q(total),
        min=_q(svals[0]),
        max=_q(svals[-1]),
        mean=_q(mean),
        median=_q(median),
        stdev=_q(stdev),
        p25=_q(_percentile(svals, Decimal("0.25"))),
        p75=_q(_percentile(svals, Decimal("0.75"))),
        p90=_q(_percentile(svals, Decimal("0.90"))),
        share_positive=_q(Decimal(pos) / Decimal(n)),
        share_negative=_q(Decimal(neg) / Decimal(n)),
        zero_count=zero,
        reasons=reasons,
    )


def _nets(windows: Sequence[WindowOutcome]) -> list[Decimal]:
    return [w.net_pnl for w in windows]


def holdout_distribution(windows: Sequence[WindowOutcome]) -> DistributionSummary:
    """Distribution over the *out-of-sample* windows only (train is excluded)."""
    _, holdout = split_train_holdout(windows)
    return summarize_distribution(_nets(holdout))


def train_distribution(windows: Sequence[WindowOutcome]) -> DistributionSummary:
    """Distribution over the *in-sample* windows only (diagnostic, not a PASS basis)."""
    train, _ = split_train_holdout(windows)
    return summarize_distribution(_nets(train))


# ---------------------------------------------------------------------------
# benchmark comparison (same costs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkComparison:
    windows: int
    strategy_mean: str | None
    buy_hold_mean: str | None
    no_trade_mean: str | None        # always 0 by construction, but shown explicitly
    delta_vs_buy_hold: str | None    # strategy_mean - buy_hold_mean
    delta_vs_no_trade: str | None    # strategy_mean - 0
    beats_buy_hold_count: int        # how many holdout windows beat the benchmark
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            windows=self.windows,
            strategy_mean=self.strategy_mean,
            buy_hold_mean=self.buy_hold_mean,
            no_trade_mean=self.no_trade_mean,
            delta_vs_buy_hold=self.delta_vs_buy_hold,
            delta_vs_no_trade=self.delta_vs_no_trade,
            beats_buy_hold_count=self.beats_buy_hold_count,
            reasons=dict(self.reasons),
        )


def compare_to_benchmarks(windows: Sequence[WindowOutcome]) -> BenchmarkComparison:
    """Compare strategy vs buy-hold vs no-trade on the **holdout** windows only.

    ``no_trade`` is a flat 0 net (cash earns nothing, pays nothing); buy-hold is
    the *same-cost* benchmark produced by :func:`buy_hold_with_costs`. With fewer
    than two holdout windows there is no distribution, so the shape deltas are
    reported as ``N/A`` rather than a single-window verdict.
    """
    _, holdout = split_train_holdout(windows)
    n = len(holdout)
    reasons: dict[str, str] = {}
    if n == 0:
        return BenchmarkComparison(
            windows=0, strategy_mean=None, buy_hold_mean=None, no_trade_mean=None,
            delta_vs_buy_hold=None, delta_vs_no_trade=None, beats_buy_hold_count=0,
            reasons={"*": "insufficient_sample"},
        )
    strat_mean = sum((w.net_pnl for w in holdout), _ZERO) / Decimal(n)
    bh_mean = sum((w.buy_hold_net for w in holdout), _ZERO) / Decimal(n)
    beats = sum(1 for w in holdout if w.net_pnl > w.buy_hold_net)
    if n < MIN_WINDOWS_FOR_SHAPE:
        reasons["delta"] = "insufficient_sample"
    return BenchmarkComparison(
        windows=n,
        strategy_mean=_q(strat_mean),
        buy_hold_mean=_q(bh_mean),
        no_trade_mean=_q(_ZERO),
        delta_vs_buy_hold=_q(strat_mean - bh_mean),
        delta_vs_no_trade=_q(strat_mean - _ZERO),
        beats_buy_hold_count=beats,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# reproducibility: canonical data hash over the replay windows
# ---------------------------------------------------------------------------


def replay_data_hash(windows: Sequence[WindowOutcome]) -> str:
    """sha256 over the ordered, canonical window facts.

    Two replays over the same windows (ids, split, nets and per-window
    ``data_hash``) hash identically, pinning MC-21 reproducibility; a single
    changed bar-hash or net flips the digest.
    """
    payload = [
        {
            "window_id": w.window_id,
            "split": w.split,
            "net_pnl": _q(w.net_pnl),
            "buy_hold_net": _q(w.buy_hold_net),
            "data_hash": w.data_hash,
        }
        for w in windows
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
