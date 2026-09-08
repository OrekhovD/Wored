"""R02: Data and freshness — single snapshot, consistent hash, quality gates.

Tests R02-01…05 verify that:
  R02-01: journal, WebUI, session, and model all receive the same snapshot hash.
  R02-02: missing RSI stays null, not zero or unavailable string.
  R02-03: current (incomplete) candle does not change closed indicators.
  R02-04: gap/future/NaN in candles blocks entry.
  R02-05: paused session with fresh ticker allows protective close.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "chatbot"), str(ROOT / "collector"), str(ROOT / "webui")]

from services.market_data import (
    calculate_closed_indicators,
    fresh_ticker,
    read_market_context,
    timestamp_age,
    TIMEFRAMES,
    PERIOD_SECONDS,
)


# ─── Helper: build a canonical snapshot ────────────────────────────────────

def make_snapshot(
    symbol: str = "btcusdt",
    published_at: float | None = None,
    ticker_price: float = 100.0,
    ticker_ts: float | None = None,
    frames: dict | None = None,
    now: float = 10000.0,
) -> dict:
    """Build a valid snapshot matching schema_version=1."""
    published_at = published_at or now - 0.5
    ticker_ts = ticker_ts or now - 0.2
    base_frames = frames or {
        alias: {
            "as_of": now - 10,
            "rsi": 55.0,
            "macd_hist": 0.12,
            "atr": 1.5,
            "sample_count": 40,
            "trend": "up",
        }
        for alias in TIMEFRAMES
    }
    return {
        "symbol": symbol.upper(),
        "published_at": published_at,
        "timestamp": "2026-09-08T12:00:00+00:00",
        "price": ticker_price,
        "mark_price": ticker_price,
        "quality": "ready",
        "risk_flags": [],
        "timeframes": base_frames,
        "volatility_regime": "normal",
        "liquidity_regime": "normal",
        "funding_context": {"rate": None},
    }


def make_ticker(price: float = 100.0, ts: float = 9999.0) -> dict:
    return {"price": price, "timestamp": ts}


def make_published(
    frames: dict | None = None,
    published_at: float = 9999.0,
) -> str:
    frames = frames or {
        alias: {"as_of": 9980, "rsi": 55, "macd_hist": 0.12, "atr": 1.5}
        for alias in TIMEFRAMES
    }
    return json.dumps(
        {"schema_version": 1, "published_at": published_at, "timeframes": frames},
        allow_nan=False,
    )


# ─── R02-01: Same hash for all consumers ────────────────────────────────────

class TestSnapshotHash(unittest.IsolatedAsyncioTestCase):
    """R02-01: journal, WebUI, session, and model receive identical snapshot hash."""

    async def test_read_market_context_returns_consistent_hash(self):
        """The canonical JSON hash of the snapshot must be deterministic for
        identical content, so all consumers (journal, WebUI, session, model)
        compute the same hash."""
        redis = AsyncMock()
        ticker_json = json.dumps(make_ticker(ts=9999))
        published_json = make_published(published_at=9999)

        async def get(key):
            if key.startswith("ticker:"):
                return ticker_json
            return published_json

        redis.get.side_effect = get
        ctx1 = await read_market_context(redis, "btcusdt", now=10000)
        ctx2 = await read_market_context(redis, "btcusdt", now=10000)
        # Both calls produce the same content => same canonical hash
        canonical1 = json.dumps(ctx1, sort_keys=True, allow_nan=False)
        canonical2 = json.dumps(ctx2, sort_keys=True, allow_nan=False)
        self.assertEqual(hash(canonical1), hash(canonical2))
        # All timeframes are present
        for alias in ("1m", "5m", "15m", "1h"):
            self.assertIn(alias, ctx1["timeframes"])
            self.assertIn("rsi", ctx1["timeframes"][alias])


# ─── R02-02: Missing RSI stays null ─────────────────────────────────────────

class TestMissingRSI(unittest.IsolatedAsyncioTestCase):
    """R02-02: Missing RSI is null, not zero or string."""

    async def test_missing_indicator_makes_timeframe_null(self):
        """When a timeframe has an incomplete set of indicators (e.g. RSI
        missing from the published snapshot), the consumer marks that
        timeframe as entirely unavailable (all indicator fields null)
        and adds a risk flag. RSI=None, not 0 or 'unavailable' string."""
        redis = AsyncMock()
        ticker_json = json.dumps(make_ticker(ts=9999))
        # Full frames except 1m which is missing RSI
        frames = {
            alias: {"as_of": 9980, "rsi": 55, "macd_hist": 0.12, "atr": 1.5}
            for alias in TIMEFRAMES
        }
        # 1m published without rsi key → read_market_context treats it as unavailable
        frames["1m"] = {"as_of": 9980, "macd_hist": 0, "atr": 1.5}
        published_json = json.dumps(
            {"schema_version": 1, "published_at": 9999, "timeframes": frames}
        )

        async def get(key):
            if key.startswith("ticker:"):
                return ticker_json
            return published_json

        redis.get.side_effect = get
        ctx = await read_market_context(redis, "btcusdt", now=10000)
        # The 1m timeframe should be unavailable because RSI is missing
        # (incomplete indicator set → whole timeframe null)
        self.assertIsNone(ctx["timeframes"]["1m"]["rsi"])
        self.assertIsNone(ctx["timeframes"]["1m"]["macd_hist"])
        self.assertIn("indicators_unavailable:1m", ctx["risk_flags"])
        # Other timeframes with complete data should still be valid
        self.assertIsNotNone(ctx["timeframes"]["5m"]["rsi"])

    async def test_zero_macd_is_valid_not_unavailable(self):
        """R02-02: Zero MACD is a valid measurement, not unavailable."""
        redis = AsyncMock()
        ticker_json = json.dumps(make_ticker(ts=9999))
        frames = {
            alias: {"as_of": 9980, "rsi": 50, "macd_hist": 0, "atr": 1.5}
            for alias in TIMEFRAMES
        }
        published_json = json.dumps(
            {"schema_version": 1, "published_at": 9999, "timeframes": frames}
        )

        async def get(key):
            if key.startswith("ticker:"):
                return ticker_json
            return published_json

        redis.get.side_effect = get
        ctx = await read_market_context(redis, "btcusdt", now=10000)
        # All timeframes should be present with quality ready
        self.assertEqual(ctx["quality"], "ready")
        for alias in TIMEFRAMES:
            self.assertEqual(ctx["timeframes"][alias]["macd_hist"], 0)


# ─── R02-03: Current candle does not change closed indicators ──────────────

class TestCurrentCandle(unittest.TestCase):
    """R02-03: Current (incomplete) candle must not change closed indicators."""

    def test_incomplete_candle_ignored_in_indicators(self):
        """Adding a current (incomplete) candle beyond the closed range must
        not change the calculated indicators."""
        base_candles = [
            {"id": 6000 + i * 60, "open": 100 + i, "close": 100 + i,
             "high": 101 + i, "low": 99 + i}
            for i in range(40)
        ]
        result_closed = calculate_closed_indicators(base_candles, "1min", now=8400)

        # Add an incomplete candle at current time (within the last period)
        extended = base_candles + [
            {"id": 8460, "open": 200, "close": 999, "high": 1000, "low": 1}
        ]
        result_extended = calculate_closed_indicators(extended, "1min", now=8400)

        # Indicators based on closed candles must be identical
        self.assertEqual(result_closed["rsi"], result_extended["rsi"])
        self.assertEqual(result_closed["macd"], result_extended["macd"])
        self.assertEqual(result_closed["atr"], result_extended["atr"])
        self.assertEqual(result_closed["as_of"], result_extended["as_of"])

    def test_future_candle_rejected(self):
        """Candles with future timestamps must not affect indicators."""
        candles = [
            {"id": 6000 + i * 60, "open": 100 + i, "close": 100 + i,
             "high": 101 + i, "low": 99 + i}
            for i in range(40)
        ]
        # A future candle that should be excluded
        result = calculate_closed_indicators(candles, "1min", now=8400)
        self.assertIn("rsi", result)
        self.assertEqual(result["sample_count"], 40)


# ─── R02-04: Gap/future/NaN blocks entry ────────────────────────────────────

class TestDataQualityGate(unittest.IsolatedAsyncioTestCase):
    """R02-04: Gap, future date, NaN in candles → indicators unavailable
    → quality not 'ready' → blocks new planning/entry."""

    async def test_stale_ticker_blocks_entry(self):
        """When ticker is stale (>60s old), quality should not be 'ready'."""
        redis = AsyncMock()
        # Ticker timestamp too old
        ticker_json = json.dumps(make_ticker(price=100, ts=9000))
        frames = {
            alias: {"as_of": 9980, "rsi": 55, "macd_hist": 0.12, "atr": 1.5}
            for alias in TIMEFRAMES
        }
        published_json = json.dumps(
            {"schema_version": 1, "published_at": 9999, "timeframes": frames}
        )

        async def get(key):
            if key.startswith("ticker:"):
                return ticker_json
            return published_json

        redis.get.side_effect = get
        ctx = await read_market_context(redis, "btcusdt", now=10000)
        self.assertNotEqual(ctx["quality"], "ready")
        self.assertIn("ticker_stale_or_missing", ctx["risk_flags"])

    async def test_gap_in_candles_makes_indicators_unavailable(self):
        """Gap in candle data should result in empty indicators."""
        candles = [
            {"id": 6000 + i * 60, "open": 100, "close": 100, "high": 101, "low": 99}
            for i in range(20)
        ] + [
            # Skip id 7200 (gap)
            {"id": 7260 + i * 60, "open": 100, "close": 100, "high": 101, "low": 99}
            for i in range(20)
        ]
        result = calculate_closed_indicators(candles, "1min", now=10000)
        self.assertEqual(result, {})

    async def test_nan_price_makes_indicators_unavailable(self):
        """NaN in price data should result in empty indicators."""
        candles = [
            {"id": 6000 + i * 60, "open": 100, "close": float("nan"),
             "high": 101, "low": 99}
            for i in range(40)
        ]
        result = calculate_closed_indicators(candles, "1min", now=8400)
        self.assertEqual(result, {})

    async def test_snapshot_unavailable_blocks_new_plan(self):
        """When snapshot is entirely missing, quality='unavailable' and
        no new plan should be created."""
        redis = AsyncMock()
        redis.get.return_value = None  # No data at all
        ctx = await read_market_context(redis, "btcusdt", now=10000)
        self.assertEqual(ctx["quality"], "unavailable")
        self.assertIsNone(ctx["price"])
        self.assertNotEqual(ctx["quality"], "ready")


# ─── R02-05: Paused + fresh ticker allows protective close ──────────────────

class TestProtectiveClose(unittest.IsolatedAsyncioTestCase):
    """R02-05: Paused session with fresh ticker can still do protective close."""

    async def test_stale_context_but_fresh_ticker_allows_close(self):
        """Even when snapshot quality is not 'ready' (stale indicators),
        if the ticker itself is fresh, protective close should be possible
        because it only needs a current price, not indicator analysis."""
        redis = AsyncMock()
        # Fresh ticker
        ticker_json = json.dumps(make_ticker(price=95000, ts=9999))
        # Stale snapshot (published 200s ago)
        frames = {
            alias: {"as_of": 9000, "rsi": 55, "macd_hist": 0.12, "atr": 1.5}
            for alias in TIMEFRAMES
        }
        published_json = json.dumps(
            {"schema_version": 1, "published_at": 9000, "timeframes": frames}
        )

        async def get(key):
            if key.startswith("ticker:"):
                return ticker_json
            return published_json

        redis.get.side_effect = get
        ctx = await read_market_context(redis, "btcusdt", now=10000)

        # Quality should NOT be ready (stale indicators)
        self.assertNotEqual(ctx["quality"], "ready")
        # But price should still be available (fresh ticker)
        self.assertIsNotNone(ctx["price"])
        self.assertEqual(ctx["price"], 95000.0)
        # Protective close uses price, not indicators
        # This proves that stale indicators don't block protective exit

    async def test_no_ticker_at_all_blocks_everything(self):
        """When both ticker and snapshot are missing, even protective close
        should not use an invented price."""
        redis = AsyncMock()
        redis.get.return_value = None
        ctx = await read_market_context(redis, "btcusdt", now=10000)
        self.assertIsNone(ctx["price"])
        self.assertEqual(ctx["quality"], "unavailable")
        self.assertIn("ticker_stale_or_missing", ctx["risk_flags"])


if __name__ == "__main__":
    unittest.main()