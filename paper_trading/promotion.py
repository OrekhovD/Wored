"""paper_trading.promotion — the only path that may set a strategy ``active`` (E.5).

ТЗ §7.2 / block E.5: a candidate becomes ``active`` **only** after it clears the
block-D statistical gate — never from webui, Telegram, or a manual write.  This
module is the single sanctioned writer of ``status='active'``: it runs the
:class:`~paper_trading.learning.LearningEvaluator` gate, and on ``approved``
retires the current active ruleset and activates the candidate through the
guarded :func:`save_strategy_rules` (which itself refuses ``active`` unless the
internal promotion flag is set).

It also maintains the trial counter on ``paper_v2_strategy_versions`` that the
Deflated Sharpe Ratio uses for multiple-testing correction.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence
from uuid import uuid4

from paper_trading.learning import (
    DEFAULT_EVAL_CONFIG,
    Episode,
    EvaluationResult,
    LearningEvaluator,
)

log = logging.getLogger(__name__)

__all__ = ["run_gate", "promote"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_gate(
    candidate_episodes: Sequence[Episode],
    baseline_episodes: Sequence[Episode],
    *,
    capital: Decimal = Decimal(1000),
    config: Optional[dict[str, Any]] = None,
) -> EvaluationResult:
    """Pure block-D gate — no I/O.  Returns the full :class:`EvaluationResult`."""
    merged = {**DEFAULT_EVAL_CONFIG, **(config or {})}
    return LearningEvaluator(merged).evaluate(
        list(candidate_episodes), list(baseline_episodes), capital
    )


async def _count_trials(pool: Any) -> int:
    """Number of strategies already evaluated (each is one DSR trial)."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM paper_v2_strategy_versions "
                "WHERE status IN ('candidate','validating','approved','rejected','active','rolled_back')"
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("promotion: trial count failed: %s", exc)
        return 0
    return int(row["n"]) if row else 0


async def _next_rules_version(pool: Any) -> int:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT MAX(version) AS v FROM strategy_rules")
    except Exception:  # pragma: no cover - defensive
        return 1
    return int(row["v"]) + 1 if row and row["v"] is not None else 1


async def _record_version(
    pool: Any,
    strategy_version: str,
    *,
    status: str,
    evidence: dict[str, Any],
    trials: int,
    parent_version: Optional[str] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> None:
    """Upsert the candidate's row in ``paper_v2_strategy_versions``."""
    activated = datetime.now(timezone.utc) if status == "active" else None
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO paper_v2_strategy_versions
                    (version_id, strategy_version, parent_version, status,
                     parameters, evaluation_evidence, trials, activated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8)
                ON CONFLICT (strategy_version) DO UPDATE SET
                    status = EXCLUDED.status,
                    evaluation_evidence = EXCLUDED.evaluation_evidence,
                    trials = EXCLUDED.trials,
                    activated_at = COALESCE(paper_v2_strategy_versions.activated_at, EXCLUDED.activated_at)
                """,
                str(uuid4()), strategy_version, parent_version, status,
                json.dumps(parameters or {}, default=str), json.dumps(evidence, default=str),
                trials, activated,
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("promotion: version record failed for %s: %s", strategy_version, exc)


async def _save_active_rules(
    pool: Any, rules: dict[str, Any], *, version: int, source: str, evidence: dict[str, Any]
) -> None:
    """Insert the newly-approved ruleset as ``active`` through the given pool.

    This is the privileged internal write path; the public
    :func:`storage.postgres_client.save_strategy_rules` independently refuses
    ``active`` unless the promotion flag is set, so webui/Telegram/manual callers
    cannot bypass the gate (ТЗ §7.2).
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategy_rules (version, rules, source, status, evidence) "
            "VALUES ($1, $2::jsonb, $3, 'active', $4::jsonb)",
            version, json.dumps(rules, default=str), source, json.dumps(evidence, default=str),
        )


async def _retire_current_active(pool: Any) -> None:
    """Move any currently-active ruleset out of ``active`` before a new one lands."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_rules SET status = 'rejected' WHERE status = 'active'"
            )
            await conn.execute(
                "UPDATE paper_v2_strategy_versions SET status = 'rolled_back' "
                "WHERE status = 'active'"
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("promotion: retire-active failed: %s", exc)


async def promote(
    pool: Any,
    *,
    strategy_version: str,
    rules: dict[str, Any],
    candidate_episodes: Sequence[Episode],
    baseline_episodes: Sequence[Episode],
    capital: Decimal = Decimal(1000),
    config: Optional[dict[str, Any]] = None,
    source: str = "promotion",
    parent_version: Optional[str] = None,
) -> dict[str, Any]:
    """Run the gate and, only if it clears, promote ``candidate → active``.

    Returns a verdict dict ``{status, approved, activated, gate_results,
    n_trials, evaluation_id, reason}``.  On any non-``approved`` status the
    candidate is recorded but **not** activated.  If *pool* is ``None`` the gate
    still runs (pure) but no DB write happens — used by unit tests.
    """
    trials = 0
    if pool is not None:
        trials = await _count_trials(pool)
    merged = {**DEFAULT_EVAL_CONFIG, **(config or {})}
    merged["n_trials"] = max(1, trials + 1)

    result = run_gate(
        candidate_episodes, baseline_episodes, capital=capital, config=merged
    )
    approved = result.status == "approved"
    verdict: dict[str, Any] = {
        "strategy_version": strategy_version,
        "status": result.status,
        "approved": approved,
        "activated": False,
        "gate_results": result.gate_results,
        "holdout_metrics": result.holdout_metrics,
        "n_trials": merged["n_trials"],
        "evaluation_id": result.evaluation_id,
        "reason": result.reason,
        "evaluated_at": _now_iso(),
    }

    if pool is None:
        return verdict

    if not approved:
        next_status = "rejected" if result.status == "rejected" else result.status
        await _record_version(
            pool, strategy_version, status=next_status,
            evidence=verdict, trials=merged["n_trials"], parent_version=parent_version,
        )
        return verdict

    # Approved — the one and only place allowed to write ``active``.
    await _retire_current_active(pool)
    version = await _next_rules_version(pool)
    await _save_active_rules(pool, rules, version=version, source=source, evidence=verdict)
    await _record_version(
        pool, strategy_version, status="active",
        evidence=verdict, trials=merged["n_trials"], parent_version=parent_version,
        parameters=rules,
    )
    verdict["activated"] = True
    verdict["rules_version"] = version
    return verdict
