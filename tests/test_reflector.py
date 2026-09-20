"""Block F — slow-ring reflector (``ai.reflector``).

Offline unit tests cover the pure control flow (env-driven model resolution,
input-hash dedup, budget fallback, run accounting) by monkeypatching the I/O
helpers.  The DB end-to-end (persistence + real repeat-call dedup) runs only
against the disposable QA Postgres and is skipped otherwise.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "chatbot"), str(ROOT)]

from ai import reflector  # noqa: E402


DSN = os.getenv(
    "WORED_TEST_DATABASE_URL",
    "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa",
)


def _evaluation() -> dict:
    return {
        "evaluation_run_id": "eval-1", "total": 12, "winrate": 35,
        "avg_pnl": -2.5, "max_drawdown": 18, "liquidation_rate": 8,
        "details": {"wins": 4, "losses": 8, "best_pnl": 10, "worst_pnl": -12},
    }


# --------------------------------------------------------------------------- #
# F1 — env-driven model resolution, no hardcoded slugs
# --------------------------------------------------------------------------- #
def test_reflector_candidates_from_env(monkeypatch):
    monkeypatch.setenv("REFLECTOR_MODEL", "custom/deep-x,plain-y")
    monkeypatch.setenv("REFLECTOR_PROVIDER", "fallback-provider")
    keys = reflector.resolve_reflector_candidates()
    assert keys == ["custom/deep-x", "fallback-provider/plain-y"]


def test_reflector_candidates_defer_when_env_empty(monkeypatch):
    monkeypatch.delenv("REFLECTOR_MODEL", raising=False)
    with patch("ai.strategy_learner._candidate_keys", return_value=["ollama-cloud/glm-5.2"]):
        keys = reflector.resolve_reflector_candidates()
    assert keys == ["ollama-cloud/glm-5.2"]


# --------------------------------------------------------------------------- #
# F3 — input hash stability / sensitivity
# --------------------------------------------------------------------------- #
def test_input_hash_stable_and_sensitive():
    h1 = reflector.compute_input_hash(_evaluation(), {"adjustments": []})
    h2 = reflector.compute_input_hash(_evaluation(), {"adjustments": []})
    h3 = reflector.compute_input_hash(_evaluation(), {"adjustments": [{"parameter": "tp_rr"}]})
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


# --------------------------------------------------------------------------- #
# F3 — dedup short-circuits a paid call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dedup_returns_cached_without_paid_call(monkeypatch):
    monkeypatch.setattr(reflector, "_fetch_active_rules", AsyncMock(return_value=None))
    cached = {"adjustments": [], "summary": "cached", "confidence": "low", "status": "candidate"}
    monkeypatch.setattr(reflector, "_lookup_prior_run", AsyncMock(return_value=cached))

    async def _boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("run_strategy_learner must not be called on dedup hit")
    monkeypatch.setattr("ai.strategy_learner.run_strategy_learner", _boom)

    result = await reflector.run_reflector(_evaluation(), pool=object())
    assert result["paid"] is False and result["deduped"] is True
    assert result["candidate"] is cached


# --------------------------------------------------------------------------- #
# F4 — budget exhaustion → heuristic, still unpaid
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_budget_blocked_uses_heuristic_unpaid(monkeypatch):
    monkeypatch.setattr(reflector, "_fetch_active_rules", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_lookup_prior_run", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_budget_allows", AsyncMock(return_value=False))
    inserted = []
    monkeypatch.setattr(
        reflector, "_insert_run",
        AsyncMock(side_effect=lambda pool, **kw: inserted.append(kw),
    ))

    fake_pg = types.SimpleNamespace(
        get_latest_strategy_rules=AsyncMock(return_value=None),
        save_strategy_rules=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(reflector, "_storage", lambda: fake_pg)
    monkeypatch.setattr(
        "ai.strategy_learner._heuristic_candidate",
        lambda ev: {"adjustments": [], "summary": "heuristic", "confidence": "low"},
    )

    result = await reflector.run_reflector(_evaluation(), pool=object())
    assert result["paid"] is False
    assert result["status"] == "budget_blocked"
    assert result["candidate"]["status"] == "candidate"
    assert inserted and inserted[0]["status"] == "budget_blocked"
    assert fake_pg.save_strategy_rules.await_args.kwargs["status"] == "candidate"


# --------------------------------------------------------------------------- #
# F2 — fresh reflection is paid and books a completed run
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fresh_reflection_paid_and_records_completed(monkeypatch):
    monkeypatch.setattr(reflector, "_fetch_active_rules", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_lookup_prior_run", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_budget_allows", AsyncMock(return_value=True))
    inserted = []
    monkeypatch.setattr(
        reflector, "_insert_run",
        AsyncMock(side_effect=lambda pool, **kw: inserted.append(kw),
    ))

    async def _fake_learner(evaluation, *, gateway=None, run_meta=None):
        if run_meta is not None:
            run_meta.update({
                "provider": "ollama-cloud", "model": "glm-5.2", "request_id": "r-1",
                "input_tokens": 100, "output_tokens": 20, "error_code": None,
            })
        return {"adjustments": [], "summary": "llm", "confidence": "medium", "status": "candidate"}
    monkeypatch.setattr("ai.strategy_learner.run_strategy_learner", _fake_learner)

    result = await reflector.run_reflector(_evaluation(), pool=object())
    assert result["paid"] is True and result["status"] == "completed"
    assert inserted[0]["status"] == "completed"
    assert inserted[0]["provider"] == "ollama-cloud"
    assert inserted[0]["input_tokens"] == 100


@pytest.mark.asyncio
async def test_gateway_error_books_failed_run(monkeypatch):
    monkeypatch.setattr(reflector, "_fetch_active_rules", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_lookup_prior_run", AsyncMock(return_value=None))
    monkeypatch.setattr(reflector, "_budget_allows", AsyncMock(return_value=True))
    inserted = []
    monkeypatch.setattr(
        reflector, "_insert_run",
        AsyncMock(side_effect=lambda pool, **kw: inserted.append(kw),
    ))

    async def _fake_learner(evaluation, *, gateway=None, run_meta=None):
        if run_meta is not None:
            run_meta.update({"provider": "ollama-cloud", "model": "glm-5.2",
                             "input_tokens": 0, "output_tokens": 0,
                             "error_code": "provider_unavailable"})
        return {"adjustments": [], "summary": "heuristic", "confidence": "low", "status": "candidate"}
    monkeypatch.setattr("ai.strategy_learner.run_strategy_learner", _fake_learner)

    result = await reflector.run_reflector(_evaluation(), pool=object())
    assert result["status"] == "failed"
    assert inserted[0]["status"] == "failed"


# --------------------------------------------------------------------------- #
# end-to-end dedup + persistence (needs disposable QA Postgres)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reflector_persists_and_dedups_on_db():
    import asyncpg
    from uuid import uuid4

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    schema = f"refl_{uuid4().hex[:10]}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f"""
                CREATE TABLE {schema}.trader_v1_agent_runs (
                    agent_run_id UUID PRIMARY KEY,
                    role VARCHAR(32) NOT NULL,
                    input_hash VARCHAR(64) NOT NULL,
                    output_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    status VARCHAR(16) NOT NULL,
                    provider VARCHAR(64), model VARCHAR(128),
                    input_tokens INTEGER, output_tokens INTEGER,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    completed_at TIMESTAMPTZ)
            """)
        db = await asyncpg.create_pool(
            DSN, min_size=1, max_size=2, server_settings={"search_path": schema}
        )
        ev = _evaluation()
        with patch.object(reflector, "_fetch_active_rules", AsyncMock(return_value=None)), \
             patch.object(reflector, "_budget_allows", AsyncMock(return_value=True)), \
             patch("ai.strategy_learner.run_strategy_learner") as learner:
            async def _fake(evaluation, *, gateway=None, run_meta=None):
                if run_meta is not None:
                    run_meta.update({"provider": "ollama-cloud", "model": "glm-5.2",
                                     "request_id": "r-db", "input_tokens": 50,
                                     "output_tokens": 10, "error_code": None})
                return {"adjustments": [], "summary": "llm", "confidence": "medium", "status": "candidate"}
            learner.side_effect = _fake

            first = await reflector.run_reflector(ev, pool=db)
            second = await reflector.run_reflector(ev, pool=db)

        assert first["paid"] is True and first["deduped"] is False
        assert second["paid"] is False and second["deduped"] is True
        assert learner.await_count == 1  # only the fresh reflection cost a call
        async with db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status FROM trader_v1_agent_runs ORDER BY started_at")
        assert [r["status"] for r in rows] == ["completed"]
        await db.close()
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()
