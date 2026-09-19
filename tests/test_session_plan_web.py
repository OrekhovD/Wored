"""WebUI uses the same persisted plan version; controls cannot invent new versions."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webui"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_session_plan_contract as plan_fixtures


@unittest.skipUnless(os.getenv("WORED_TEST_DATABASE_URL"), "Dedicated wored_qa database required")
class WebPlanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = plan_fixtures.PlanPostgresTests()
        await self.db.asyncSetUp()
        await self.db.publish()
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pg_pool=self.db.pool)))
        self.auth = patch("app.require_api_auth")
        self.auth.start()
        self.redis = patch("redis.asyncio.from_url", return_value=AsyncMock())
        self.redis.start()

    async def asyncTearDown(self):
        self.redis.stop()
        self.auth.stop()
        await self.db.asyncTearDown()

    async def test_snapshot_and_controls_share_validated_version(self):
        from app import api_daily_session_active, api_daily_session_revision
        before = await api_daily_session_active(self.request)
        result = await api_daily_session_revision(self.request, {"session_id": self.db.sid, "command": "pause"})
        self.assertTrue(result["ok"])
        result = await api_daily_session_revision(self.request, {"session_id": self.db.sid, "command": "continue"})
        self.assertTrue(result["ok"])
        after = await api_daily_session_active(self.request)
        self.assertEqual(before["plan"]["version"], after["plan"]["version"])
        self.assertEqual(after["session"]["activeplanversion"], after["plan"]["version"])
        self.assertEqual(after["plan"]["details"]["validation_status"], "accepted")
        self.assertEqual(len(after["plan"]["entries"]), 1)

    async def test_missing_version_is_not_replaced_by_latest(self):
        from app import api_daily_session_active, api_daily_session_revision
        await self.db.pool.execute("UPDATE trading_sessions SET active_plan_version=2,status='paused'")
        result = await api_daily_session_active(self.request)
        self.assertIsNone(result["plan"])
        response = await api_daily_session_revision(self.request, {"session_id": self.db.sid, "command": "continue"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(await self.db.pool.fetchval("SELECT count(*) FROM session_revisions"), 0)

    async def test_expired_session_controls_do_not_advance_version(self):
        from app import api_daily_session_revision
        await self.db.pool.execute("UPDATE trading_sessions SET session_end=NOW()-INTERVAL '1 second'")
        response = await api_daily_session_revision(self.request, {"session_id": self.db.sid, "command": "continue"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(await self.db.pool.fetchval("SELECT active_plan_version FROM trading_sessions"), 1)
