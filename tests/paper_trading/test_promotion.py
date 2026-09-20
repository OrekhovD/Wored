"""Block E.5 — gated promotion (``paper_trading.promotion``) + active-write guard.

The invariant under test: a candidate reaches ``active`` **only** through the
block-D gate.  Pure gate/guard tests run everywhere; the end-to-end activation
test needs the disposable QA Postgres (``WORED_TEST_DATABASE_URL``) and is
skipped otherwise.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_trading.learning import Episode
from paper_trading.promotion import promote, run_gate

pytest.importorskip("asyncpg")  # postgres_client / DB test need the driver

CAPITAL = Decimal(1000)
DSN = os.getenv(
    "WORED_TEST_DATABASE_URL",
    "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa",
)

FAST = {
    "min_episodes": 60, "min_holdout_episodes": 24, "min_trading_days": 10,
    "chronological_split": 0.70, "max_lookback_minutes": 0, "max_hold_minutes": 0,
    "bar_minutes": 1, "embargo_bars": 1, "bootstrap_resamples": 400,
    "dsr_threshold": 0.95,
}


def _ts(day: int, minute: int = 0) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day, minutes=minute)).isoformat()


def _episodes(version: str, nets) -> list[Episode]:
    n = len(nets)
    out = []
    for i, net in enumerate(nets):
        day = i * 60 // max(1, n)
        out.append(Episode(
            episode_id=f"{version}-{i}", strategy_version=version, instrument="BTC-USDT",
            interval="1m", entry_time=_ts(day), exit_time=_ts(day, 5), side="long",
            entry_price=Decimal("100"), exit_price=Decimal("100") + net, qty=Decimal("1"),
            gross_pnl=net + Decimal("0.12"), entry_fee=Decimal("0.06"), exit_fee=Decimal("0.06"),
            funding_cashflow=Decimal("0"), net_pnl=net, max_drawdown=Decimal("0.5"),
            duration_minutes=5,
        ))
    return out


def _edge_pairs():
    rng = random.Random(4242)
    base = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(120)]
    cand = [Decimal(str(round(rng.gauss(0.0, 1.0) + 3.0, 4))) for _ in range(120)]
    return _episodes("cand", cand), _episodes("base", base)


# --------------------------------------------------------------------------- #
# pure gate logic (no DB)
# --------------------------------------------------------------------------- #
def test_gate_approves_genuine_edge_in_promotion():
    cand, base = _edge_pairs()
    result = run_gate(cand, base, capital=CAPITAL, config=FAST)
    assert result.status == "approved", (result.reason, result.gate_results)


@pytest.mark.asyncio
async def test_promote_without_pool_does_not_activate():
    cand, base = _edge_pairs()
    verdict = await promote(
        None, strategy_version="v1", rules={"adjustments": []},
        candidate_episodes=cand, baseline_episodes=base, capital=CAPITAL, config=FAST,
    )
    assert verdict["approved"] is True
    assert verdict["activated"] is False  # no pool → no DB write


@pytest.mark.asyncio
async def test_promote_rejects_noise_and_does_not_activate():
    rng = random.Random(7)
    base = _episodes("base", [Decimal(str(round(rng.gauss(0, 1), 4))) for _ in range(120)])
    cand = _episodes("cand", [Decimal(str(round(rng.gauss(0, 1), 4))) for _ in range(120)])
    verdict = await promote(
        None, strategy_version="v-noise", rules={"adjustments": []},
        candidate_episodes=cand, baseline_episodes=base, capital=CAPITAL, config=FAST,
    )
    assert verdict["approved"] is False
    assert verdict["activated"] is False


@pytest.mark.asyncio
async def test_active_status_guard_rejects_direct_write():
    from chatbot.storage.postgres_client import save_strategy_rules
    with pytest.raises(ValueError):
        await save_strategy_rules({"adjustments": []}, version=1, status="active")


# --------------------------------------------------------------------------- #
# end-to-end gated activation (needs disposable QA Postgres)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_promote_activates_through_gate_on_db():
    import asyncpg
    from uuid import uuid4

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    schema = f"promo_{uuid4().hex[:10]}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}"')
            await conn.execute("""
                CREATE TABLE strategy_rules (
                    id SERIAL PRIMARY KEY, version INT NOT NULL, rules JSONB NOT NULL,
                    source VARCHAR(30) DEFAULT 'x', status VARCHAR(20) NOT NULL DEFAULT 'candidate',
                    evidence JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
            """)
            await conn.execute("""
                CREATE TABLE paper_v2_strategy_versions (
                    version_id UUID PRIMARY KEY, strategy_version VARCHAR(64) NOT NULL UNIQUE,
                    parent_version VARCHAR(64), status VARCHAR(20) NOT NULL DEFAULT 'candidate',
                    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
                    evaluation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
                    trials INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    activated_at TIMESTAMPTZ, schema_version INTEGER NOT NULL DEFAULT 2)
            """)
        # asyncpg pools reuse connections; pin the schema on every checkout.
        pool_schema = await asyncpg.create_pool(
            DSN, min_size=1, max_size=2, server_settings={"search_path": schema}
        )
        cand, base = _edge_pairs()
        verdict = await promote(
            pool_schema, strategy_version="v-edge", rules={"adjustments": [{"parameter": "tp_rr", "new": 2.5}]},
            candidate_episodes=cand, baseline_episodes=base, capital=CAPITAL, config=FAST,
        )
        assert verdict["approved"] is True and verdict["activated"] is True
        async with pool_schema.acquire() as conn:
            rows = await conn.fetch("SELECT status FROM strategy_rules ORDER BY id")
            vrow = await conn.fetchrow(
                "SELECT status, trials FROM paper_v2_strategy_versions WHERE strategy_version='v-edge'")
        assert [r["status"] for r in rows] == ["active"]
        assert vrow["status"] == "active" and vrow["trials"] >= 1
        await pool_schema.close()
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()
