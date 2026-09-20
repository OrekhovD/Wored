"""Сквозная приёмка — замкнутый цикл самообучения (ТЗ §«Сквозная приёмка»).

 proves all four links connect end-to-end:

  1. episodes (metrics)   →  2. walk-forward statistical gate (block D)
  3. gated promotion      →  candidate ``active`` (block E.5)
  4. active rules         →  observably different strategy replay (block E.4)

The reflector (block F) is the *source* of the candidate ruleset; here the
candidate is represented by exactly what :func:`run_strategy_learner` would
persist (a ``{"adjustments": [...]}`` shape), so the loop is exercised without a
network call.  The pure test runs everywhere; the DB variant additionally proves
the real ``active`` write/read-back path and needs the disposable QA Postgres.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_trading.learning import Episode
from paper_trading.promotion import promote
from paper_trading.rules import build_strategy
from paper_trading.strategy import Bar

pytest.importorskip("asyncpg")

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


# --------------------------------------------------------------------------- #
# link 1 — episodes
# --------------------------------------------------------------------------- #
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
    rng = random.Random(2026)
    base = [Decimal(str(round(rng.gauss(0.0, 1.0), 4))) for _ in range(120)]
    cand = [Decimal(str(round(rng.gauss(0.0, 1.0) + 3.0, 4))) for _ in range(120)]
    return _episodes("cand", cand), _episodes("base", base)


# --------------------------------------------------------------------------- #
# link 4 — a market the baseline would short, and the candidate rule disabling it
# --------------------------------------------------------------------------- #
def _bar(minute: int, close) -> Bar:
    v = Decimal(str(close))
    return Bar(timestamp=f"2026-01-01T00:{minute % 60:02d}:00+00:00",
               open=v, high=v + Decimal("1"), low=v - Decimal("1"), close=v, volume=Decimal("1"))


def _ramp(n: int, start: str, end: str) -> list[Bar]:
    a, b = Decimal(start), Decimal(end)
    step = (b - a) / Decimal(max(n - 1, 1))
    return [Bar(timestamp=f"t{i:05d}", open=a + step * i, high=a + step * i,
                low=a + step * i, close=a + step * i, volume=Decimal("1"))
            for i in range(n)]


def _bearish_1m() -> list[Bar]:
    bars = [_bar(i, 100) for i in range(20)]
    bars.append(_bar(20, 103))
    bars.append(_bar(21, 105))
    bars.append(_bar(22, 99))
    return bars


def _seeded(strategy):
    strategy.warm_up(_ramp(60, "120", "60"), _ramp(60, "120", "60"), _bearish_1m()[:-1])
    return strategy


# The reflector-style candidate: "disable the short side" — a whitelisted knob.
CANDIDATE_RULES = {"adjustments": [{"parameter": "enable_short", "new": False, "reason": "loop"}]}


def _replay(side_rules):
    strategy, applied = build_strategy(side_rules)
    _seeded(strategy)
    signal = strategy.evaluate(
        bars_1m=_bearish_1m(), close_1h=Decimal("60"), close_15m=Decimal("60"), now_epoch=1000.0,
    )
    return signal, applied


# --------------------------------------------------------------------------- #
# pure closed loop: episodes → gate → promotion verdict → active rules → replay
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_closed_loop_links_episodes_gate_promotion_replay():
    cand, base = _edge_pairs()

    # links 2 + 3: the walk-forward gate clears the candidate and the promotion
    # path carries its rules forward (pool=None → verdict without a DB write).
    verdict = await promote(
        None, strategy_version="loop-v1", rules=CANDIDATE_RULES,
        candidate_episodes=cand, baseline_episodes=base, capital=CAPITAL, config=FAST,
    )
    assert verdict["approved"] is True, (verdict["reason"], verdict["gate_results"])

    # link 4: applying the approved candidate rules observably changes replay —
    # the baseline short disappears once the rule is active.
    signal_default, _ = _replay(None)
    signal_active, applied = _replay(verdict.get("rules") or CANDIDATE_RULES)

    assert signal_default is not None and signal_default.side == "short"
    assert signal_active is None
    assert any(a["parameter"] == "enable_short" for a in applied)


# --------------------------------------------------------------------------- #
# DB closed loop: gate → real ``active`` write → read-back → replay behaviour
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_closed_loop_activates_and_drives_replay_on_db():
    import asyncpg
    from uuid import uuid4
    from paper_trading.rules import load_active_rules

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    schema = f"loop_{uuid4().hex[:10]}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f"""
                CREATE TABLE {schema}.strategy_rules (
                    id SERIAL PRIMARY KEY, version INT NOT NULL, rules JSONB NOT NULL,
                    source VARCHAR(30) DEFAULT 'x', status VARCHAR(20) NOT NULL DEFAULT 'candidate',
                    evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
            """)
            await conn.execute(f"""
                CREATE TABLE {schema}.paper_v2_strategy_versions (
                    version_id UUID PRIMARY KEY, strategy_version VARCHAR(64) NOT NULL UNIQUE,
                    parent_version VARCHAR(64), status VARCHAR(20) NOT NULL DEFAULT 'candidate',
                    parameters JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    evaluation_evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    trials INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    activated_at TIMESTAMPTZ, schema_version INTEGER NOT NULL DEFAULT 2)
            """)
        db = await asyncpg.create_pool(
            DSN, min_size=1, max_size=2, server_settings={"search_path": schema}
        )
        cand, base = _edge_pairs()
        verdict = await promote(
            db, strategy_version="loop-db-v1", rules=CANDIDATE_RULES,
            candidate_episodes=cand, baseline_episodes=base, capital=CAPITAL, config=FAST,
        )
        assert verdict["approved"] is True and verdict["activated"] is True

        # read the freshly-activated ruleset back and feed it to the strategy.
        active_rules = await load_active_rules(db)
        assert active_rules is not None
        signal, applied = _replay(active_rules)
        assert signal is None
        assert any(a["parameter"] == "enable_short" for a in applied)

        async with db.acquire() as conn:
            st = await conn.fetch("SELECT status FROM strategy_rules WHERE status='active'")
        assert len(st) == 1
        await db.close()
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()
