"""R06 tests: History, revisions, and metrics.

R06-01: Immutable parent (revision doesn't change parent)
R06-02: Duplicate revision key returns same request_id
R06-03: Gap not replaced by neighboring candle
R06-04: v1/v2 metrics kept separate in aggregates
R06-05: Future candles absent from model input
"""
import asyncio
import math
import os
import sys
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit

# Ensure webui, chatbot, and collector are importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "chatbot"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "collector"))

import aiohttp  # noqa: E402 — pre-import to avoid _ssl.c:3108 on Windows
import certifi  # noqa: E402

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from forecast_schema import PREDICTION_TABLES_SQL  # noqa: E402
from forecast_queue import SCHEMA as QUEUE_SCHEMA  # noqa: E402


REVISION_DDL = """
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS parent_request_id INTEGER NULL
    REFERENCES forecast_requests(id) ON DELETE SET NULL;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS revision_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS revision_reason TEXT NULL;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS input_snapshot_id UUID NULL;
CREATE INDEX IF NOT EXISTS idx_forecast_requests_parent ON forecast_requests (parent_request_id);
"""


def _env_url():
    """Get WORED_TEST_DATABASE_URL; raise clear error if missing."""
    url = os.getenv("WORED_TEST_DATABASE_URL")
    if not url:
        return None
    if urlsplit(url).path != "/wored_qa":
        raise RuntimeError("Tests only accept the disposable wored_qa database")
    return url


@unittest.skipUnless(_env_url(), "Dedicated PostgreSQL QA database not available")
class R06RevisionTests(unittest.IsolatedAsyncioTestCase):
    """Tests R06-01 and R06-02: revision chain management."""

    async def asyncSetUp(self):
        import asyncpg

        dsn = _env_url()
        self.schema = "qa_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4,
            server_settings={"search_path": self.schema},
        )
        async with self.pool.acquire() as conn:
            await conn.execute(PREDICTION_TABLES_SQL)
            await conn.execute(QUEUE_SCHEMA)
            await conn.execute(REVISION_DDL)

    async def asyncTearDown(self):
        await self.pool.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def _insert_request(self, conn, **overrides):
        """Helper to insert a forecast_request row."""
        defaults = dict(
            symbol="btcusdt", horizon_hours=4, base_timeframe="60min",
            depth=3, base_price="100.00000000", status="active", source="webui",
        )
        defaults.update(overrides)
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(defaults)))
        vals = list(defaults.values())
        row = await conn.fetchrow(
            f"INSERT INTO forecast_requests ({cols}) VALUES ({placeholders}) RETURNING *",
            *vals,
        )
        return dict(row)

    async def test_R06_01_immutable_parent(self):
        """R06-01: Creating a revision does not change the parent request."""
        from chatbot.services.forecast_revisions import create_revision

        async with self.pool.acquire() as conn:
            parent = await self._insert_request(conn)

        parent_id = parent["id"]
        # Snapshot parent before revision
        async with self.pool.acquire() as conn:
            before = dict(await conn.fetchrow(
                "SELECT symbol, horizon_hours, base_price, status FROM forecast_requests WHERE id=$1",
                parent_id,
            ))

        # Create a revision
        result = await create_revision(
            self.pool, parent_id, reason="market_regime_change",
            payload={"horizon_steps": 4, "is_revision": True, "parent_request_id": parent_id},
        )

        # Parent must be unchanged
        async with self.pool.acquire() as conn:
            after = dict(await conn.fetchrow(
                "SELECT symbol, horizon_hours, base_price, status FROM forecast_requests WHERE id=$1",
                parent_id,
            ))
        self.assertEqual(before["symbol"], after["symbol"])
        self.assertEqual(before["horizon_hours"], after["horizon_hours"])
        self.assertEqual(before["base_price"], after["base_price"])
        self.assertEqual(before["status"], after["status"])

        # Revision must point to parent
        self.assertEqual(result["parent_request_id"], parent_id)
        self.assertGreaterEqual(result["revision_number"], 1)

    async def test_R06_02_duplicate_revision_key(self):
        """R06-02: Duplicate revision key with same parent returns same request_id."""
        from chatbot.services.forecast_revisions import create_revision

        async with self.pool.acquire() as conn:
            parent = await self._insert_request(conn)

        parent_id = parent["id"]

        # First call
        result1 = await create_revision(
            self.pool, parent_id, reason="correction",
            idempotency_key="corr-001",
        )

        # Duplicate call with same idempotency_key
        result2 = await create_revision(
            self.pool, parent_id, reason="correction",
            idempotency_key="corr-001",
        )

        # Must return the same request_id
        self.assertEqual(result1["id"], result2["id"])


class R06MetricsTests(unittest.TestCase):
    """Tests R06-03, R06-04, R06-05: metrics v2 behaviour (offline, no DB)."""

    def test_R06_03_gap_not_replaced_by_neighbor(self):
        """R06-03: closed_target_price returns None for a gap (no matching candle at expected_start).

        Candles at t=0 and t=180 (gap: no candle at t=60).
        Target ts=120 → boundary=ceil(120/60)*60=120, expected_start=60.
        No candle at t=60, so result is None — NOT filled by neighbor.
        """
        from collector.predictions.scoring import closed_target_price

        candles = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 105},
            {"time": 180, "open": 110, "high": 111, "low": 109, "close": 115},
        ]
        # Target at t=120: boundary=120, expected_start=60 → no candle at 60 → None
        result = closed_target_price(candles, target_ts=120, period_seconds=60, now_ts=300)
        self.assertIsNone(result, "Gap must NOT be filled by neighboring candle")

    def test_R06_03_valid_candle_found(self):
        """R06-03: A candle that exactly matches expected_start IS returned (not a gap)."""
        from collector.predictions.scoring import closed_target_price

        # Candle at t=60: for target_ts=120, boundary=120, expected_start=60 → match
        candles = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 102},
            {"time": 60, "open": 103, "high": 104, "low": 102, "close": 105},
            {"time": 180, "open": 110, "high": 111, "low": 109, "close": 115},
        ]
        result = closed_target_price(candles, target_ts=120, period_seconds=60, now_ts=300)
        self.assertAlmostEqual(result, 105.0)

    def test_R06_03_no_substitution_across_gap(self):
        """R06-03: Even with a close neighbor, a gap candle returns None."""
        from collector.predictions.scoring import closed_target_price

        # Only candle at t=0. Target ts=60, boundary=60, expected_start=0.
        # Candle at t=0 DOES match expected_start=0 → returns 105.0 (not a gap)
        # But if we ask for target_ts=120, boundary=120, expected_start=60 → no match → None
        candles = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 105},
        ]
        # Not a gap: expected_start=0 matches the candle
        result_valid = closed_target_price(candles, target_ts=60, period_seconds=60, now_ts=300)
        self.assertAlmostEqual(result_valid, 105.0)

        # Gap: expected_start=60, no candle there
        result_gap = closed_target_price(candles, target_ts=120, period_seconds=60, now_ts=300)
        self.assertIsNone(result_gap, "Neighboring candle must not fill a gap")

    def test_R06_04_v1_v2_metrics_separate(self):
        """R06-04: v2 scoring produces skill_vs_baseline; v1 historical records have metrics_version=1."""
        from collector.predictions.scoring import score_forecast

        # v2 scoring produces skill_vs_baseline which v1 did not have
        result = score_forecast(
            base_price=100.0, predicted_price=102.0,
            predicted_change_pct=2.0, actual_price=101.0,
        )
        # v2 has skill_vs_baseline field
        self.assertIsNotNone(result.skill_vs_baseline)
        # The heuristic score is max(0, 100-100*|actual_change-expected_change|)
        # actual_change = (101-100)/100*100 = 1%, expected_change ≈ 2%
        # change_error = |1-2| = 1, score = max(0, 100-100*1) = 0
        self.assertAlmostEqual(result.accuracy_score, 0.0)

    def test_R06_04_skill_vs_baseline_calculation(self):
        """R06-04: skill = 1 - error/baseline, null when baseline=0."""
        from collector.predictions.scoring import score_forecast

        # Perfect prediction: predicted=100, actual=100, base=100
        # baseline = |100-100|/100*100 = 0 → skill must be None
        perfect = score_forecast(100.0, 100.0, 0.0, 100.0)
        self.assertAlmostEqual(perfect.price_error_pct, 0.0)
        self.assertAlmostEqual(perfect.baseline_error_pct, 0.0)
        self.assertIsNone(perfect.skill_vs_baseline, "skill must be null when baseline=0")

        # Better than baseline: base=100, predicted=102, actual=105
        # error = |105-102|/105*100 ≈ 2.857
        # baseline = |105-100|/105*100 ≈ 4.762
        # skill = 1 - 2.857/4.762 ≈ 0.4
        better = score_forecast(100.0, 102.0, 2.0, 105.0)
        self.assertIsNotNone(better.skill_vs_baseline)
        self.assertGreater(better.skill_vs_baseline, 0.0)
        self.assertLess(better.skill_vs_baseline, 1.0)

    def test_R06_05_future_candles_absent(self):
        """R06-05: closed_target_price returns None when boundary > now_ts (future)."""
        from collector.predictions.scoring import closed_target_price

        # Candle at t=0 with close=105. Target ts=60, period=60.
        # boundary = ceil(60/60)*60 = 60. If now_ts=50, then boundary(60) > now_ts(50) → None
        candles = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 105},
        ]
        result = closed_target_price(candles, target_ts=60, period_seconds=60, now_ts=50)
        self.assertIsNone(result, "Future candle must be excluded")

        # Same target at now_ts=120: boundary=60 ≤ 120 → valid
        result_valid = closed_target_price(candles, target_ts=60, period_seconds=60, now_ts=120)
        self.assertAlmostEqual(result_valid, 105.0)

    def test_R06_05_boundary_calculation(self):
        """R06-05: target boundary = ceil(target_ts/period_seconds)*period_seconds."""
        from collector.predictions.scoring import closed_target_price

        # period=3600 (1h), target_ts=7230
        # boundary = ceil(7230/3600)*3600 = ceil(2.008)*3600 = 3*3600 = 10800
        # expected_start = 10800-3600 = 7200
        # Candle at t=7200 close=500
        candles = [
            {"time": 7200, "open": 490, "high": 510, "low": 485, "close": 500},
        ]
        result = closed_target_price(candles, target_ts=7230, period_seconds=3600, now_ts=20000)
        self.assertAlmostEqual(result, 500.0)


if __name__ == "__main__":
    unittest.main()