"""Paper Trading Learning — candidate evaluation, holdout, promotion gate.

Statistically sound replacement for the old count-based gate (defect D5).  The
previous ``evaluate`` purged the replay/holdout boundary by a *count* of
episodes, judged the candidate on a raw ``net_pnl > 0.001×capital`` threshold
with no standard error, and computed a per-episode ``max`` drawdown.  That let a
lucky or look-ahead-cheating candidate sail through.

This module implements the block-D contract:

1. **Walk-forward with time purge + embargo** — the boundary is a *timestamp*;
   episodes whose feature/outcome window reaches within ``max_lookback +
   max_hold`` of it are dropped (purge), and a further ``embargo`` of senior-TF
   bars is skipped so no future information bleeds into the replay fit.
2. **Bootstrap significance** — a ``resamples``-draw bootstrap 95 % CI of the
   candidate−baseline mean net-PnL difference; the gate is *lower bound > 0*,
   not a point threshold.
3. **Deflated Sharpe Ratio** — the candidate's holdout Sharpe is deflated by the
   number of strategy trials ``n_trials`` (Bailey & López de Prado) to punish
   multiple-testing; a DSR probability above ``dsr_threshold`` is required.
4. **Sample minimums** — ``min_episodes`` 200, ``min_holdout_episodes`` 60, and
   ``min_trading_days`` 30 (TZ §7.1).
5. **Comparability** — candidate and baseline must cover the same instrument(s)
   and (near-)identical time window, else ``status="incomparable"``.
6. **Risk gate** — chronological equity drawdown not worse, liquidation share not
   higher, risk violations not more, reconciliation clean.

A candidate is ``approved`` only when every gate clears on the *holdout*.  The
acceptance targets are empirical: ≤ 5 % of pure-noise candidates approved, a
genuine +0.3 %/trade edge approved, and a one-bar look-ahead leak rejected.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import NormalDist
from typing import Any, Iterable, Sequence
from uuid import uuid4

log = logging.getLogger(__name__)

# Default evaluation config — block-D statistical minimums (TZ §7.1).
DEFAULT_EVAL_CONFIG: dict[str, Any] = {
    "min_episodes": 200,            # minimum comparable episodes (both sides)
    "min_holdout_episodes": 60,     # minimum holdout episodes (both sides)
    "min_trading_days": 30,         # distinct UTC calendar days represented
    "chronological_split": 0.70,    # 70 % replay / 30 % holdout, by *time*
    "max_lookback_minutes": 240,    # longest feature window (senior TF warmup)
    "max_hold_minutes": 240,        # longest episode holding time
    "embargo_bars": 1,              # embargo measured in senior-TF bars
    "bar_minutes": 60,              # senior timeframe bar length
    "bootstrap_resamples": 10000,   # CI resolution
    "confidence": 0.95,             # bootstrap CI level
    "n_trials": 1,                  # strategy trials so far (DSR deflation)
    "dsr_threshold": 0.95,          # required deflated-Sharpe probability
    "window_tolerance": 0.02,       # allowed fractional gap in candidate/baseline window
    "bootstrap_seed": 12345,        # reproducible resampling
}


@dataclass
class Episode:
    """A single trading episode for evaluation."""
    episode_id: str
    strategy_version: str       # "baseline_v1" or candidate version
    instrument: str
    interval: str               # e.g. "1m"
    entry_time: str             # ISO timestamp
    exit_time: str
    side: str                   # "long" | "short"
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    gross_pnl: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    funding_cashflow: Decimal
    net_pnl: Decimal
    max_drawdown: Decimal
    duration_minutes: int
    risk_violations: int = 0
    liquidation: bool = False
    reconciliation_passed: bool = True

    @property
    def is_win(self) -> bool:
        return self.net_pnl > Decimal(0)

    @property
    def is_loss(self) -> bool:
        return self.net_pnl < Decimal(0)

    @property
    def is_breakeven(self) -> bool:
        return self.net_pnl == Decimal(0)


@dataclass
class EvaluationResult:
    """Result of candidate evaluation."""
    evaluation_id: str
    candidate_version: str
    baseline_version: str
    status: str  # "approved" | "rejected" | "insufficient_data" | "incomparable"
    replay_metrics: dict[str, Any] = field(default_factory=dict)
    holdout_metrics: dict[str, Any] = field(default_factory=dict)
    gate_results: dict[str, bool] = field(default_factory=dict)
    reason: str = ""
    evaluated_at: str = ""
    episode_count: int = 0
    holdout_count: int = 0


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Statistical primitives (pure Python; no numpy dependency)
# --------------------------------------------------------------------------- #
def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _percentile(sorted_xs: Sequence[float], q: float) -> float:
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    idx = q * (len(sorted_xs) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = idx - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def bootstrap_mean_diff_ci(
    candidate: Sequence[float],
    baseline: Sequence[float],
    *,
    resamples: int = 10000,
    confidence: float = 0.95,
    seed: int = 12345,
) -> tuple[float, float, float]:
    """Bootstrap 95 % CI for ``mean(candidate) - mean(baseline)`` (unpaired).

    Returns ``(point_estimate, lower, upper)``.  Uses a fixed ``random.Random``
    seed so the gate decision is reproducible for a given input.
    """
    if not candidate or not baseline:
        return (0.0, 0.0, 0.0)
    ca = [float(x) for x in candidate]
    cb = [float(x) for x in baseline]
    rng = random.Random(seed)
    la, lb = len(ca), len(cb)
    point = _mean(ca) - _mean(cb)
    diffs = []
    for _ in range(resamples):
        ma = sum(ca[rng.randrange(la)] for _ in range(la)) / la
        mb = sum(cb[rng.randrange(lb)] for _ in range(lb)) / lb
        diffs.append(ma - mb)
    diffs.sort()
    alpha = (1.0 - confidence) / 2.0
    return (point, _percentile(diffs, alpha), _percentile(diffs, 1.0 - alpha))


def deflated_sharpe_ratio(returns: Sequence[float], n_trials: int = 1) -> float:
    """Probability the observed Sharpe survives multiple-testing deflation.

    Bailey & López de Prado (2014).  ``returns`` are per-trade (or per-period)
    PnLs; ``n_trials`` is how many candidate strategies were tried.  Returns a
    value in ``[0, 1]``; a higher number means the edge is less likely to be
    luck.  With ``n_trials <= 1`` this reduces to the Probabilistic Sharpe Ratio
    (PSR) against a zero benchmark.
    """
    T = len(returns)
    if T < 3:
        return 0.0
    m = _mean(returns)
    sd = _std(returns)
    if sd == 0.0:
        return 0.0
    sr = m / sd
    # Higher central moments.
    z = [(x - m) / sd for x in returns]
    skew = sum(v ** 3 for v in z) / T
    kurt = sum(v ** 4 for v in z) / T
    sr_var = (1 - skew * sr + (kurt - 1) / 4.0 * sr ** 2) / (T - 1)
    if sr_var <= 0:
        return 0.0
    sr_vol = math.sqrt(sr_var)
    nd = NormalDist()
    if n_trials and n_trials > 1:
        gamma_e = 0.5772156649015329
        e = math.e
        sr0 = sr_vol * (
            (1 - gamma_e) * nd.inv_cdf(1 - 1.0 / n_trials)
            + gamma_e * nd.inv_cdf(1 - 1.0 / (n_trials * e))
        )
    else:
        sr0 = 0.0
    return nd.cdf((sr - sr0) / sr_vol)


def _equity_drawdown(chronological_net_pnls: Iterable[Decimal]) -> Decimal:
    """Peak-to-trough of the cumulative equity built from ordered net PnLs."""
    from paper_trading.metrics import equity_curve, max_drawdown

    curve = equity_curve(list(chronological_net_pnls), starting_equity=Decimal(0))
    return max_drawdown(curve)


class LearningEvaluator:
    """Evaluates strategy candidates against baseline with a statistical gate."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = {**DEFAULT_EVAL_CONFIG, **(config or {})}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        candidate_episodes: list[Episode],
        baseline_episodes: list[Episode],
        initial_capital: Decimal = Decimal(1000),
    ) -> EvaluationResult:
        cfg = self.config
        eval_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        c_ver = candidate_episodes[0].strategy_version if candidate_episodes else ""
        b_ver = baseline_episodes[0].strategy_version if baseline_episodes else ""

        def result(status, *, gates=None, replay=None, holdout=None, reason="",
                   holdout_count=0):
            return EvaluationResult(
                evaluation_id=eval_id, candidate_version=c_ver, baseline_version=b_ver,
                status=status, replay_metrics=replay or {}, holdout_metrics=holdout or {},
                gate_results=gates or {}, reason=reason, evaluated_at=now,
                episode_count=len(candidate_episodes), holdout_count=holdout_count,
            )

        # 1) Sample minimum (total).
        min_eps = cfg["min_episodes"]
        if len(candidate_episodes) < min_eps or len(baseline_episodes) < min_eps:
            return result("insufficient_data", reason=(
                f"need {min_eps} episodes, got candidate={len(candidate_episodes)} "
                f"baseline={len(baseline_episodes)}"))

        c_sorted = sorted(candidate_episodes, key=lambda e: _parse_ts(e.entry_time))
        b_sorted = sorted(baseline_episodes, key=lambda e: _parse_ts(e.entry_time))

        # 2) Comparability (same instruments + near-identical time window).
        ok, why = self._comparable(c_sorted, b_sorted)
        if not ok:
            return result("incomparable", reason=why)

        # 3) Time-based walk-forward split with purge + embargo.
        c_replay, c_holdout = self._temporal_split(c_sorted)
        b_replay, b_holdout = self._temporal_split(b_sorted)

        min_holdout = cfg["min_holdout_episodes"]
        if len(c_holdout) < min_holdout or len(b_holdout) < min_holdout:
            return result("insufficient_data", reason=(
                f"need {min_holdout} holdout episodes, got candidate={len(c_holdout)} "
                f"baseline={len(b_holdout)}"))

        # 4) Trading-day minimum across the whole evaluated span.
        if self._distinct_days(c_sorted + b_sorted) < cfg["min_trading_days"]:
            return result("insufficient_data", reason=(
                f"need {cfg['min_trading_days']} trading days"))

        replay_metrics = self._compute_metrics(c_replay, b_replay)
        holdout_metrics = self._compute_metrics(c_holdout, b_holdout)
        gates: dict[str, bool] = {}

        # --- Bootstrap CI on the holdout mean net-PnL difference ---
        c_hold = [float(e.net_pnl) for e in c_holdout]
        b_hold = [float(e.net_pnl) for e in b_holdout]
        point, lo, hi = bootstrap_mean_diff_ci(
            c_hold, b_hold, resamples=cfg["bootstrap_resamples"],
            confidence=cfg["confidence"], seed=cfg["bootstrap_seed"])
        gates["holdout_significance_ci"] = lo > 0.0
        holdout_metrics["net_pnl_diff"] = point
        holdout_metrics["net_pnl_ci"] = (lo, hi)

        # --- Deflated Sharpe Ratio against the trial count ---
        dsr = deflated_sharpe_ratio(c_hold, n_trials=cfg["n_trials"])
        gates["holdout_deflated_sharpe"] = dsr >= cfg["dsr_threshold"]
        holdout_metrics["dsr"] = dsr

        # --- Risk gates on holdout ---
        gates["holdout_drawdown"] = (
            holdout_metrics["candidate_max_dd"] <= holdout_metrics["baseline_max_dd"])
        gates["holdout_liquidation"] = (
            holdout_metrics["candidate_liq_share"] <= holdout_metrics["baseline_liq_share"])
        gates["holdout_risk"] = (
            holdout_metrics["candidate_risk_violations"]
            <= holdout_metrics["baseline_risk_violations"])
        gates["holdout_reconciliation"] = all(e.reconciliation_passed for e in c_holdout)

        # Replay shown for diagnostics but the promotion decision is holdout-only.
        all_pass = all(gates.values())
        if all_pass:
            status, reason = "approved", "all holdout gates passed"
        else:
            status = "rejected"
            reason = "failed: " + ", ".join(k for k, v in gates.items() if not v)

        return result(status, gates=gates, replay=replay_metrics,
                      holdout=holdout_metrics, reason=reason, holdout_count=len(c_holdout))

    # ------------------------------------------------------------------ #
    # Splitting / comparability helpers
    # ------------------------------------------------------------------ #
    def _temporal_split(self, sorted_eps: list[Episode]) -> tuple[list[Episode], list[Episode]]:
        """Split by *time* with a purge zone before and embargo after the cut."""
        cfg = self.config
        if not sorted_eps:
            return [], []
        start = _parse_ts(sorted_eps[0].entry_time)
        end = max(_parse_ts(e.exit_time) for e in sorted_eps)
        span = (end - start).total_seconds()
        cut = start + timedelta(seconds=span * float(cfg["chronological_split"]))
        purge = timedelta(minutes=cfg["max_lookback_minutes"] + cfg["max_hold_minutes"])
        embargo = timedelta(minutes=cfg["bar_minutes"] * max(1, int(cfg["embargo_bars"])))

        replay: list[Episode] = []
        holdout: list[Episode] = []
        for e in sorted_eps:
            entry = _parse_ts(e.entry_time)
            exit_ = _parse_ts(e.exit_time)
            # An episode leaks into the holdout if its outcome/feature window
            # reaches the cut; drop it entirely (neither side).
            if exit_ <= cut - purge:
                replay.append(e)
            elif entry >= cut + embargo:
                holdout.append(e)
            # else: inside the purge/embargo band — excluded
        return replay, holdout

    def _comparable(self, c: list[Episode], b: list[Episode]) -> tuple[bool, str]:
        ci = {e.instrument for e in c}
        bi = {e.instrument for e in b}
        if ci != bi:
            return False, f"instrument mismatch: {sorted(ci)} vs {sorted(bi)}"
        c0 = _parse_ts(c[0].entry_time)
        c1 = max(_parse_ts(e.exit_time) for e in c)
        b0 = _parse_ts(b[0].entry_time)
        b1 = max(_parse_ts(e.exit_time) for e in b)
        span = max((c1 - c0).total_seconds(), (b1 - b0).total_seconds(), 1.0)
        start_gap = abs((c0 - b0).total_seconds())
        end_gap = abs((c1 - b1).total_seconds())
        tol = float(self.config["window_tolerance"])
        if start_gap / span > tol or end_gap / span > tol:
            return False, "candidate and baseline do not cover the same time window"
        return True, ""

    @staticmethod
    def _distinct_days(episodes: Sequence[Episode]) -> int:
        return len({_parse_ts(e.entry_time).date() for e in episodes})

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def _compute_metrics(self, candidate: list[Episode], baseline: list[Episode]) -> dict[str, Any]:
        def stats(eps: list[Episode]) -> dict[str, Any]:
            if not eps:
                return {"net": Decimal(0), "max_dd": Decimal(0), "wins": 0, "losses": 0,
                        "breakeven": 0, "risk_violations": 0, "liquidations": 0,
                        "liq_share": 0.0, "win_rate": 0.0, "total": 0}
            ordered = sorted(eps, key=lambda e: _parse_ts(e.exit_time))
            net = sum((e.net_pnl for e in eps), Decimal(0))
            wins = sum(1 for e in eps if e.is_win)
            losses = sum(1 for e in eps if e.is_loss)
            breakeven = sum(1 for e in eps if e.is_breakeven)
            equity_dd = _equity_drawdown(e.net_pnl for e in ordered)
            risk_v = sum(e.risk_violations for e in eps)
            liq = sum(1 for e in eps if e.liquidation)
            return {
                "net": net, "max_dd": equity_dd, "wins": wins, "losses": losses,
                "breakeven": breakeven, "risk_violations": risk_v, "liquidations": liq,
                "liq_share": liq / len(eps), "win_rate": wins / len(eps), "total": len(eps),
            }

        c = stats(candidate)
        b = stats(baseline)
        return {
            "candidate_net": c["net"], "baseline_net": b["net"],
            "candidate_max_dd": c["max_dd"], "baseline_max_dd": b["max_dd"],
            "candidate_risk_violations": c["risk_violations"],
            "baseline_risk_violations": b["risk_violations"],
            "candidate_liq_share": c["liq_share"], "baseline_liq_share": b["liq_share"],
            "candidate_wins": c["wins"], "candidate_losses": c["losses"],
            "candidate_breakeven": c["breakeven"], "candidate_liquidations": c["liquidations"],
            "baseline_wins": b["wins"], "baseline_losses": b["losses"],
            "candidate_win_rate": c["win_rate"], "baseline_win_rate": b["win_rate"],
            "candidate_count": c["total"], "baseline_count": b["total"],
        }
