"""Observable entry decisions and cooldown recovery without external infrastructure."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatbot"))
from services.execution_status import entry_observation, render


class StatusTests(unittest.TestCase):
    def test_missing_and_stale_heartbeats_never_claim_working(self):
        self.assertIn("нет подтверждения", render(None))
        old = {"checked_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()}
        self.assertIn("устарел", render(old))

    def test_zone_distance_explains_zero_positions(self):
        e = {"id": "a", "side": "long", "entry_zone_from": 99, "entry_zone_to": 100}
        decision = entry_observation(e, {"close": 105, "high": 106, "low": 104}, {"reason": "entry_not_confirmed"})
        self.assertEqual(decision["reason"], "zone_not_reached")
        self.assertAlmostEqual(decision["distance_pct"], 5 / 105 * 100)
        output = render({"checked_at": datetime.now(timezone.utc).isoformat(), "pending_count": 1,
                         "reason": decision["reason"], "entries": [decision]})
        self.assertIn("до зоны", output)
        self.assertIn("не достигла", output)


class WatchLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime.now(timezone.utc)
        self.session = {"id": "s", "symbol": "BTCUSDT", "status": "armed", "active_plan_version": 1,
                        "initial_budget_usdt": 100, "risk_mode": "balanced", "session_start": self.now - timedelta(hours=1),
                        "session_end": self.now + timedelta(hours=1)}
        self.plan = {"schema_version": 2, "version": 1, "validation_status": "accepted",
                     "valid_until": (self.now + timedelta(minutes=30)).isoformat()}
        self.entries = [{"id": "e", "side": "long", "entry_zone_from": 99, "entry_zone_to": 100}]
        self.conn = AsyncMock()
        self.conn.transaction = MagicMock()
        self.conn.transaction.return_value.__aenter__ = AsyncMock()
        self.conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        self.conn.fetchrow.side_effect = self.fetchrow
        self.conn.fetch.side_effect = self.fetch
        self.conn.fetchval.return_value = self.now - timedelta(minutes=25)
        self.pool = MagicMock()
        self.pool.acquire.return_value.__aenter__ = AsyncMock(return_value=self.conn)
        self.pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        self.redis = AsyncMock()
        self.redis.get.return_value = json.dumps({"price": 101, "timestamp": self.now.isoformat()})
        self.locked_status = None

    async def fetchrow(self, sql, *args):
        if "FROM session_plans" in sql:
            return {"plan_json": json.dumps(self.plan)}
        if "FOR UPDATE" in sql:
            return {**self.session, "status": self.locked_status or self.session["status"]}
        return None

    async def fetch(self, sql, *args):
        return self.entries if "planned_entries" in sql else []

    async def run_loop(self):
        from services.session_manager import execution_watch_loop
        response = MagicMock()
        response.json.return_value = {"data": [{"id": int(self.now.timestamp()) - 70,
                                                "open": 99, "high": 101, "low": 99, "close": 100.1}]}
        http = MagicMock()
        http.__aenter__ = AsyncMock(return_value=SimpleNamespace(get=AsyncMock(return_value=response)))
        http.__aexit__ = AsyncMock(return_value=False)
        with patch("services.session_manager.get_session", AsyncMock(return_value=self.session)), \
             patch("storage.postgres_client.get_pool", AsyncMock(return_value=self.pool)), \
             patch("storage.redis_client.get_redis", return_value=self.redis), \
             patch("httpx.AsyncClient", return_value=http), \
             patch("services.session_manager.build_market_context", AsyncMock(return_value={"quality": "ready", "timeframes": {"1m": {"rsi": 55}}})), \
             patch.dict(sys.modules, {"services.breakout_detector": SimpleNamespace(get_breakout_signal_for_session=AsyncMock(return_value=None))}), \
             patch("services.session_manager.execute_entry", AsyncMock(return_value={"executed": False, "reason": "entry_not_confirmed"})) as execute:
            result = await execution_watch_loop("s")
        return result, execute

    async def test_wait_reason_and_fresh_fill_price_are_published(self):
        result, execute = await self.run_loop()
        execute.assert_awaited_once()
        self.assertEqual(execute.call_args.args[2]["execution_price"], 101)
        self.assertEqual(execute.call_args.args[2]["close"], 100.1)
        self.assertEqual(result["reason"], "entry_not_confirmed")
        self.redis.set.assert_awaited_once()
        saved = json.loads(self.redis.set.call_args.args[1])
        self.assertEqual(saved["pending_count"], 1)
        self.assertEqual(saved["entries"][0]["current_price"], 101)

    async def test_expired_plan_has_reason_and_no_execution(self):
        self.plan["valid_until"] = (self.now - timedelta(seconds=1)).isoformat()
        result, execute = await self.run_loop()
        execute.assert_not_awaited()
        self.assertEqual(result["reason"], "plan_expired")

    async def test_elapsed_cooldown_resumes_valid_plan(self):
        self.session["status"] = "cooldown"
        result, execute = await self.run_loop()
        execute.assert_awaited_once()
        self.assertTrue(any("status='armed'" in c.args[0] for c in self.conn.execute.call_args_list))
        self.assertNotEqual(result["reason"], "cooldown")

    async def test_current_cooldown_does_not_resume_early(self):
        self.session["status"] = "cooldown"
        self.conn.fetchval.return_value = self.now - timedelta(minutes=1)
        result, execute = await self.run_loop()
        execute.assert_not_awaited()
        self.assertEqual(result["reason"], "cooldown")

    async def test_concurrent_user_pause_wins_over_cooldown(self):
        self.session["status"] = "cooldown"
        self.locked_status = "paused"
        _, execute = await self.run_loop()
        execute.assert_not_awaited()
        self.conn.execute.assert_not_awaited()

    async def test_no_orders_is_visible(self):
        self.entries = []
        result, execute = await self.run_loop()
        execute.assert_not_awaited()
        self.assertEqual(result["reason"], "no_pending_entries")

    async def test_exception_is_reported_without_false_healthy_status(self):
        from services.session_manager import execution_watch_loop
        with patch("services.session_manager._execution_watch_loop", AsyncMock(side_effect=RuntimeError("qa"))), \
             patch("storage.redis_client.get_redis", return_value=self.redis):
            result = await execution_watch_loop("s")
        self.assertEqual(result["reason"], "engine_error")
        self.redis.set.assert_awaited_once()
