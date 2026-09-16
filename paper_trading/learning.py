"""Paper Trading Learning — candidate evaluation, holdout, promotion gate.

Evaluates strategy candidates against baseline using chronological replay
and separate holdout. Gate checks: net P&L candidate - baseline >= threshold,
drawdown not worse, risk violations not more, reconciliation pass.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

# Default evaluation config (from REQ-10)
DEFAULT_EVAL_CONFIG = {
    "min_episodes": 20,           # minimum comparable episodes
    "chronological_split": 0.70,  # 70% replay, 30% holdout
    "min_holdout_episodes": 6,    # minimum holdout episodes
    "purge_gap": "max_lookback + hold_time",
    "net_pnl_threshold": "0.001",  # fraction of initial capital
    "episode_boundaries": "fixed_before_comparison",
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
    status: str  # "approved" | "rejected" | "insufficient_data" | "deferred"
    replay_metrics: dict[str, Any] = field(default_factory=dict)
    holdout_metrics: dict[str, Any] = field(default_factory=dict)
    gate_results: dict[str, bool] = field(default_factory=dict)
    reason: str = ""
    evaluated_at: str = ""
    episode_count: int = 0
    holdout_count: int = 0


class LearningEvaluator:
    """Evaluates strategy candidates against baseline."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = {**DEFAULT_EVAL_CONFIG, **(config or {})}

    def evaluate(
        self,
        candidate_episodes: list[Episode],
        baseline_episodes: list[Episode],
        initial_capital: Decimal = Decimal(1000),
    ) -> EvaluationResult:
        """Evaluate candidate vs baseline using chronological split."""
        eval_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Check minimum episodes
        min_eps = self.config["min_episodes"]
        if len(candidate_episodes) < min_eps or len(baseline_episodes) < min_eps:
            return EvaluationResult(
                evaluation_id=eval_id,
                candidate_version=candidate_episodes[0].strategy_version if candidate_episodes else "",
                baseline_version=baseline_episodes[0].strategy_version if baseline_episodes else "",
                status="insufficient_data",
                reason=f"need {min_eps} episodes, got candidate={len(candidate_episodes)} baseline={len(baseline_episodes)}",
                evaluated_at=now,
                episode_count=len(candidate_episodes),
                holdout_count=0,
            )

        # Sort by entry_time (chronological)
        candidate_sorted = sorted(candidate_episodes, key=lambda e: e.entry_time)
        baseline_sorted = sorted(baseline_episodes, key=lambda e: e.entry_time)

        # Purge gap: remove episodes within max_lookback + hold_time of the split point
        # to prevent information leakage between replay and holdout
        split = self.config["chronological_split"]
        c_replay_end = int(len(candidate_sorted) * split)
        b_replay_end = int(len(baseline_sorted) * split)

        # Apply purge gap: remove episodes near the split boundary
        # (within 1 episode of the boundary on each side — prevents look-ahead)
        purge_count = max(1, int(len(candidate_sorted) * 0.05))  # 5% purge zone
        c_replay_end_purged = max(0, c_replay_end - purge_count)
        b_replay_end_purged = max(0, b_replay_end - purge_count)

        c_replay = candidate_sorted[:c_replay_end_purged]
        c_holdout = candidate_sorted[c_replay_end:]
        b_replay = baseline_sorted[:b_replay_end_purged]
        b_holdout = baseline_sorted[b_replay_end:]

        # Check holdout minimum
        min_holdout = self.config["min_holdout_episodes"]
        if len(c_holdout) < min_holdout or len(b_holdout) < min_holdout:
            return EvaluationResult(
                evaluation_id=eval_id,
                candidate_version=candidate_episodes[0].strategy_version,
                baseline_version=baseline_episodes[0].strategy_version,
                status="insufficient_data",
                reason=f"need {min_holdout} holdout episodes, got candidate={len(c_holdout)} baseline={len(b_holdout)}",
                evaluated_at=now,
                episode_count=len(candidate_episodes),
                holdout_count=len(c_holdout),
            )

        # Compute metrics
        replay_metrics = self._compute_metrics(c_replay, b_replay, initial_capital)
        holdout_metrics = self._compute_metrics(c_holdout, b_holdout, initial_capital)

        # Gate checks (separate on replay and holdout)
        gates: dict[str, bool] = {}
        threshold = Decimal(self.config["net_pnl_threshold"]) * initial_capital

        # Replay gate: net P&L candidate - baseline >= threshold
        gates["replay_net_pnl"] = replay_metrics["candidate_net"] - replay_metrics["baseline_net"] >= threshold

        # Replay gate: drawdown not worse
        gates["replay_drawdown"] = replay_metrics["candidate_max_dd"] <= replay_metrics["baseline_max_dd"]

        # Replay gate: risk violations not more
        gates["replay_risk"] = replay_metrics["candidate_risk_violations"] <= replay_metrics["baseline_risk_violations"]

        # Replay gate: reconciliation pass
        gates["replay_reconciliation"] = all(e.reconciliation_passed for e in c_replay)

        # Holdout gate: same checks
        gates["holdout_net_pnl"] = holdout_metrics["candidate_net"] - holdout_metrics["baseline_net"] >= threshold
        gates["holdout_drawdown"] = holdout_metrics["candidate_max_dd"] <= holdout_metrics["baseline_max_dd"]
        gates["holdout_risk"] = holdout_metrics["candidate_risk_violations"] <= holdout_metrics["baseline_risk_violations"]
        gates["holdout_reconciliation"] = all(e.reconciliation_passed for e in c_holdout)

        # All gates must pass
        all_pass = all(gates.values())

        status = "approved" if all_pass else "rejected"
        failed_gates = [k for k, v in gates.items() if not v]
        reason = "all gates passed" if all_pass else f"failed: {', '.join(failed_gates)}"

        return EvaluationResult(
            evaluation_id=eval_id,
            candidate_version=candidate_episodes[0].strategy_version,
            baseline_version=baseline_episodes[0].strategy_version,
            status=status,
            replay_metrics=replay_metrics,
            holdout_metrics=holdout_metrics,
            gate_results=gates,
            reason=reason,
            evaluated_at=now,
            episode_count=len(candidate_episodes),
            holdout_count=len(c_holdout),
        )

    def _compute_metrics(
        self,
        candidate: list[Episode],
        baseline: list[Episode],
        initial_capital: Decimal,
    ) -> dict[str, Any]:
        """Compute comparison metrics for a set of episodes."""
        def stats(eps: list[Episode]) -> dict[str, Any]:
            if not eps:
                return {"net": Decimal(0), "max_dd": Decimal(0), "wins": 0, "losses": 0, "breakeven": 0, "risk_violations": 0, "liquidations": 0}
            net = sum((e.net_pnl for e in eps), Decimal(0))
            wins = sum(1 for e in eps if e.is_win)
            losses = sum(1 for e in eps if e.is_loss)
            breakeven = sum(1 for e in eps if e.is_breakeven)
            max_dd = max((e.max_drawdown for e in eps), default=Decimal(0))
            risk_v = sum(e.risk_violations for e in eps)
            liq = sum(1 for e in eps if e.liquidation)
            return {
                "net": net,
                "max_dd": max_dd,
                "wins": wins,
                "losses": losses,
                "breakeven": breakeven,
                "risk_violations": risk_v,
                "liquidations": liq,
                "win_rate": wins / len(eps) if eps else 0,
                "total": len(eps),
            }

        c_stats = stats(candidate)
        b_stats = stats(baseline)

        return {
            "candidate_net": c_stats["net"],
            "baseline_net": b_stats["net"],
            "candidate_max_dd": c_stats["max_dd"],
            "baseline_max_dd": b_stats["max_dd"],
            "candidate_risk_violations": c_stats["risk_violations"],
            "baseline_risk_violations": b_stats["risk_violations"],
            "candidate_wins": c_stats["wins"],
            "candidate_losses": c_stats["losses"],
            "candidate_breakeven": c_stats["breakeven"],
            "candidate_liquidations": c_stats["liquidations"],
            "baseline_wins": b_stats["wins"],
            "baseline_losses": b_stats["losses"],
            "candidate_win_rate": c_stats["win_rate"],
            "baseline_win_rate": b_stats["win_rate"],
        }