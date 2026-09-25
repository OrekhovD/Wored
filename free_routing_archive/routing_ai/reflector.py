"""Slow-ring daily reflector (ТЗ block F).

The reflector is the *only* place an LLM is allowed to touch the learning loop,
and even then strictly in advisory mode: it proposes a ``candidate`` ruleset that
must still clear the block-D statistical gate before it can ever become
``active`` (see :mod:`paper_trading.promotion`).  It never executes trades and
never sets risk directly (ADR-03).

Responsibilities added on top of :func:`ai.strategy_learner.run_strategy_learner`:

* **Env-driven model resolution** — the model slug comes from ``REFLECTOR_MODEL``
  (or the learner's own Ollama chain); nothing is hardcoded here.
* **Input-hash dedup** — one reflection per (metrics, active-rules) shape per
  day: a repeat of the same ``input_hash`` reuses the stored run and issues **0
  paid provider calls** (ТЗ §F.3).
* **Budget gate** — daily quota via :mod:`ai.quota`; on exhaustion it falls back
  to the deterministic :func:`_heuristic_candidate` without a paid call.
* **Run accounting** — every attempt is booked in ``trader_v1_agent_runs`` with
  its ``input_hash``, provider/model, tokens and terminal status.

Every collaborator that performs I/O is resolved lazily so the pure control
flow is unit-testable offline; the end-to-end DB path is exercised under Docker.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

ROLE = "reflector"
__all__ = [
    "resolve_reflector_candidates",
    "compute_input_hash",
    "run_reflector",
]


# --------------------------------------------------------------------------- #
# F1 — env-driven model resolution (no hardcoded slugs)
# --------------------------------------------------------------------------- #
def resolve_reflector_candidates() -> list[str]:
    """Provider/model keys for the reflector, entirely from ``REFLECTOR_MODEL``.

    Accepts a comma-separated list; bare slugs are grouped under the primary
    ``ollama-cloud`` provider (the active WORED stack).  When the env var is
    unset/empty we defer to the learner's own env-driven chain so there is a
    single source of truth for defaults and no slug is baked into this module.
    """
    raw = os.getenv("REFLECTOR_MODEL", "").strip()
    if not raw:
        from ai.strategy_learner import _candidate_keys
        return _candidate_keys()

    default_provider = os.getenv("REFLECTOR_PROVIDER", "ollama-cloud").strip() or "ollama-cloud"
    keys: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        keys.append(token if "/" in token else f"{default_provider}/{token}")
    return list(dict.fromkeys(keys))


# --------------------------------------------------------------------------- #
# F3 — deterministic input hash over (daily metrics + active rules)
# --------------------------------------------------------------------------- #
def compute_input_hash(evaluation: dict, active_rules: Any) -> str:
    """SHA-256 of the canonical reflector input (metrics + active rules)."""
    payload = {
        "evaluation": evaluation,
        "active_rules": active_rules,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# I/O helpers — resolved lazily; patchable for offline unit tests
# --------------------------------------------------------------------------- #
def _storage():
    try:  # container PYTHONPATH=/app
        from storage import postgres_client  # type: ignore
    except ModuleNotFoundError:  # repository test imports
        from chatbot.storage import postgres_client  # type: ignore
    return postgres_client


async def _fetch_active_rules() -> Optional[dict]:
    postgres = _storage()
    current = await postgres.get_latest_strategy_rules(status="active")
    return current.get("rules") if current else None


async def _lookup_prior_run(pool: Any, input_hash: str, *, role: str = ROLE) -> Optional[dict]:
    """Return ``output_json`` of a completed same-input-hash run booked today, else None."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT output_json FROM trader_v1_agent_runs
                WHERE role = $1 AND input_hash = $2 AND status = 'completed'
                  AND started_at >= date_trunc('day', now() AT TIME ZONE 'utc')
                ORDER BY started_at DESC LIMIT 1
                """,
                role, input_hash,
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("reflector: prior-run lookup failed: %s", exc)
        return None
    if not row:
        return None
    raw = row["output_json"]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _insert_run(
    pool: Any,
    *,
    role: str,
    input_hash: str,
    status: str,
    output: dict[str, Any],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trader_v1_agent_runs
                    (agent_run_id, role, input_hash, output_json, status,
                     provider, model, input_tokens, output_tokens,
                     started_at, completed_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, now(), now())
                """,
                str(uuid4()), role, input_hash, json.dumps(output, default=str),
                status, provider, model, input_tokens, output_tokens,
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("reflector: run insert failed: %s", exc)


async def _budget_allows(user_id: int) -> bool:
    from ai.quota import check_quota
    status = await check_quota(user_id, ROLE)
    return bool(status.get("allowed", True))


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
async def run_reflector(
    evaluation: dict,
    *,
    pool: Any = None,
    user_id: int = 0,
    gateway: Any = None,
) -> dict[str, Any]:
    """Run the daily reflection once, with dedup + budget + run accounting.

    Returns ``{candidate, paid, deduped, status, input_hash}``.  A ``paid=False``
    result means no provider request was issued this call (cache hit or budget
    fallback).  The returned ``candidate`` is always ``status='candidate'`` —
    activation is exclusively the gate's decision.
    """
    active_rules = await _fetch_active_rules()
    input_hash = compute_input_hash(evaluation, active_rules)

    # F3 — repeat input for the same day is free.
    prior = await _lookup_prior_run(pool, input_hash)
    if prior is not None:
        return {
            "candidate": prior, "paid": False, "deduped": True,
            "status": prior.get("status", "candidate"), "input_hash": input_hash,
        }

    # F4 — budget exhausted → deterministic heuristic, still no paid call.
    if not await _budget_allows(user_id):
        from ai.strategy_learner import _heuristic_candidate
        postgres = _storage()
        current = await postgres.get_latest_strategy_rules(status="active")
        version = (current["version"] + 1) if current else 1
        candidate = _heuristic_candidate(evaluation)
        candidate.update({
            "status": "candidate",
            "version": version,
            "validation_required": ["minimum_sample", "replay", "out_of_sample", "risk_limits"],
        })
        await postgres.save_strategy_rules(
            candidate, version=version, source="reflector_heuristic_budget",
            status="candidate", evidence={"input_hash": input_hash, "budget": "exhausted"},
        )
        await _insert_run(pool, role=ROLE, input_hash=input_hash,
                          status="budget_blocked", output=candidate)
        return {
            "candidate": candidate, "paid": False, "deduped": False,
            "status": "budget_blocked", "input_hash": input_hash,
        }

    # F2 — fresh reflection through the learner (schema-gated LLM + heuristic
    # fallback on any gateway/validation error; the out-of-schema LLM text is
    # never persisted — only a validated/heuristic candidate is).
    from ai.strategy_learner import run_strategy_learner
    run_meta: dict[str, Any] = {}
    candidate = await run_strategy_learner(evaluation, gateway=gateway, run_meta=run_meta)
    booked_status = "failed" if run_meta.get("error_code") else "completed"
    await _insert_run(
        pool, role=ROLE, input_hash=input_hash, status=booked_status,
        output=candidate, provider=run_meta.get("provider"), model=run_meta.get("model"),
        input_tokens=run_meta.get("input_tokens"), output_tokens=run_meta.get("output_tokens"),
    )
    return {
        "candidate": candidate, "paid": True, "deduped": False,
        "status": booked_status, "input_hash": input_hash,
    }
