"""Block A tests — the perpetual candle/funding data layer.

Two kinds of test live here:

* Fast, dependency-free unit tests for the deterministic logic (idempotent
  primary keys, funding extraction, timeframe topology, and a source scan that
  keeps the perpetual learning path off the spot host).
* An opt-in Postgres integration test that exercises the real writer, the
  derived-timeframe aggregation, gap counting, and the stable dataset hash.  It
  is skipped automatically when ``DATABASE_URL`` is not configured.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from collector.htx import history_loader
from collector.htx.funding import extract_funding
from collector.htx.history_loader import (
    DEFAULT_BASE_URL,
    NETWORK_PERIOD,
    PERIODS,
    HistoryLoadError,
    PerpetualCandle,
    parse_closed_candles,
)
from collector.storage import perp_candles

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Timeframe topology
# --------------------------------------------------------------------------- #
def test_only_1min_is_a_network_timeframe() -> None:
    assert NETWORK_PERIOD == "1min"
    assert "1min" in PERIODS


def test_higher_timeframes_are_declared_and_derivable() -> None:
    # 15m/1h/4h exist for parsing/interval math but are rebuilt from 1m, never
    # fetched: they must be in the derived set exposed by the store.
    for tf in ("15min", "60min", "4hour"):
        assert tf in PERIODS
        assert tf in perp_candles.DERIVED_TIMEFRAMES


def test_aggregate_rejects_unknown_derived_timeframe() -> None:
    with pytest.raises(ValueError, match="unsupported derived timeframe"):
        # Raises before touching the pool argument, so a dummy pool is fine.
        _run(perp_candles.aggregate_timeframe(object(), "BTC-USDT", "7min"))


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Idempotent keys
# --------------------------------------------------------------------------- #
def test_candle_uuid_is_deterministic_and_key_scoped() -> None:
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    a = perp_candles.candle_uuid("BTC-USDT", "1min", t)
    b = perp_candles.candle_uuid("BTC-USDT", "1min", t)
    assert a == b
    assert a != perp_candles.candle_uuid("BTC-USDT", "60min", t)
    assert a != perp_candles.candle_uuid("ETH-USDT", "1min", t)


def test_candle_uuid_ignores_tz_representation() -> None:
    utc = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    shifted = utc.astimezone(timezone(timedelta(hours=5)))
    assert perp_candles.candle_uuid("BTC-USDT", "1min", utc) == perp_candles.candle_uuid(
        "BTC-USDT", "1min", shifted
    )


# --------------------------------------------------------------------------- #
# parse_closed_candles period message and validation still holds
# --------------------------------------------------------------------------- #
def test_parse_rejects_unknown_period_with_new_message() -> None:
    with pytest.raises(HistoryLoadError, match="expected one of"):
        parse_closed_candles(
            {"status": "ok", "data": []},
            contract_code="BTC-USDT",
            period="7min",
            received_at=datetime.now(timezone.utc),
        )


# --------------------------------------------------------------------------- #
# Funding extraction
# --------------------------------------------------------------------------- #
def test_extract_funding_returns_utc_time_and_rate() -> None:
    snapshot = {
        "funding_rate": "0.0001",
        "next_funding_at": "2026-01-01T16:00:00+00:00",
    }
    when, rate = extract_funding(snapshot)
    assert when.tzinfo is not None and when.utcoffset() == timedelta(0)
    assert rate == Decimal("0.0001")


def test_extract_funding_allows_negative_rate() -> None:
    snapshot = {"funding_rate": "-0.0002", "next_funding_at": "2026-01-01T16:00:00Z"}
    _, rate = extract_funding(snapshot)
    assert rate == Decimal("-0.0002")


def test_extract_funding_missing_or_malformed_is_none() -> None:
    assert extract_funding({}) is None
    assert extract_funding({"funding_rate": "x", "next_funding_at": "2026-01-01T16:00:00Z"}) is None
    assert extract_funding({"funding_rate": "0.1", "next_funding_at": "not-a-time"}) is None


# --------------------------------------------------------------------------- #
# Source scan: the perpetual learning path must never read the spot host
# --------------------------------------------------------------------------- #
_PERP_PATHS = [
    REPO_ROOT / "collector" / "htx" / "history_loader.py",
    REPO_ROOT / "collector" / "htx" / "funding.py",
    REPO_ROOT / "collector" / "htx" / "gap_monitor.py",
    REPO_ROOT / "collector" / "storage" / "perp_candles.py",
    REPO_ROOT / "scripts" / "backfill_perp_candles.py",
]


def test_perp_learning_modules_reference_no_spot_host() -> None:
    for path in _PERP_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "api.huobi.pro" not in text, f"{path.name} must not read the spot host"


def test_default_base_url_is_linear_swap() -> None:
    assert DEFAULT_BASE_URL == "https://api.hbdm.com"


def test_persist_and_derive_is_noop_without_persistence() -> None:
    # A missing DB layer must never crash the collector refresh loop.
    _run(perp_candles.bulk_upsert_candles(_NullPool(), []))  # empty batch short-circuits
    _run(history_loader._persist_and_derive(None, "BTC-USDT", []))


class _NullPool:
    async def acquire(self):  # pragma: no cover - never reached for empty input
        raise AssertionError("pool must not be touched for empty candle batch")


# --------------------------------------------------------------------------- #
# Postgres integration (opt-in)
# --------------------------------------------------------------------------- #
_HAS_DB = bool(os.getenv("DATABASE_URL") or os.getenv("WORED_TEST_DATABASE_URL"))
_CONTRACT = "QA-PERP-TEST"


async def _make_pool():
    import asyncpg

    dsn = os.getenv("DATABASE_URL") or os.getenv("WORED_TEST_DATABASE_URL", "")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    if dsn.split("?")[0].rstrip("/").endswith("/trading"):
        pytest.skip("refusing to write perpetual test rows to the production database")
    return await asyncpg.create_pool(dsn=dsn)


@pytest.mark.skipif(not _HAS_DB, reason="DATABASE_URL not configured")
async def test_persist_derive_and_hash_against_real_postgres() -> None:
    pool = await _make_pool()
    try:
        await perp_candles.ensure_perp_schema(pool)
        base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        one_min = []
        for i in range(60):
            o = base + timedelta(minutes=i)
            one_min.append(
                PerpetualCandle(
                    contract_code=_CONTRACT,
                    period="1min",
                    open_time=o,
                    close_time=o + timedelta(minutes=1),
                    open=Decimal(100 + i),
                    high=Decimal(101 + i),
                    low=Decimal(99),
                    close=Decimal(100 + i),
                    volume=Decimal(2),
                )
            )
        inserted = await perp_candles.bulk_upsert_candles(pool, one_min)
        assert inserted == 60
        # Re-ingest must be a no-op, never a duplicate.
        assert await perp_candles.bulk_upsert_candles(pool, one_min) == 0

        buckets = await perp_candles.aggregate_timeframe(pool, _CONTRACT, "60min")
        assert buckets == 1
        rows = await perp_candles.load_candles(pool, _CONTRACT, "60min", base - timedelta(days=1), base + timedelta(days=1))
        assert len(rows) == 1
        bucket = rows[0]
        assert bucket["open"] == Decimal(100)
        assert bucket["high"] == Decimal(160)  # max of (101+i) for i in 0..59
        assert bucket["low"] == Decimal(99)
        assert bucket["close"] == Decimal(159)  # last minute close
        assert bucket["volume"] == Decimal(120)  # 60 * 2

        h1 = await perp_candles.dataset_sha256(pool, _CONTRACT, "1min", base - timedelta(minutes=1), base + timedelta(minutes=120))
        h2 = await perp_candles.dataset_sha256(pool, _CONTRACT, "1min", base - timedelta(minutes=1), base + timedelta(minutes=120))
        assert re.fullmatch(r"[0-9a-f]{64}", h1)
        assert h1 == h2
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM trader_v1_perp_candles WHERE contract_code = $1", _CONTRACT)
        await pool.close()
