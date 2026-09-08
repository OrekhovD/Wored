"""Session transaction regressions on a real, disposable PostgreSQL schema."""
import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "chatbot"))


@unittest.skipUnless(os.getenv("WORED_TEST_DATABASE_URL"), "Dedicated PostgreSQL QA database not available")
class PostgresSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        from services.pipeline_schema import ensure_pipeline_tables
        dsn = os.environ["WORED_TEST_DATABASE_URL"]
        if urlsplit(dsn).path != "/wored_qa":
            raise RuntimeError("Tests only accept the disposable wored_qa database")
        self.schema = "qa_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4,
            server_settings={"search_path": self.schema})
        self.binding = patch("storage.postgres_client.get_pool", AsyncMock(return_value=self.pool))
        self.binding.start()
        await ensure_pipeline_tables()
        await ensure_pipeline_tables()
        self.sid = await self.pool.fetchval("INSERT INTO trading_sessions(user_id,symbol,session_start,"
            "session_end,initial_budget_usdt,status,cost_filter_enabled) "
            "VALUES(42,'btcusdt',NOW(),NOW()+INTERVAL '8 hours',100,'armed',false) RETURNING id")
        self.entry = dict(await self.pool.fetchrow("INSERT INTO planned_entries(session_id,plan_version,side,"
            "entry_zone_from,entry_zone_to,invalidation_price,stop_loss,take_profit_json,"
            "recommended_leverage,budget_share_pct,confirmation_rule,reason_code) "
            "VALUES($1,1,'long',99,101,90,95,'[110]',10,15,'any','qa') RETURNING *", self.sid))

    async def asyncTearDown(self):
        self.binding.stop()
        await self.pool.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def open_trade(self):
        from services.session_manager import execute_entry
        return await execute_entry(str(self.sid), self.entry,
            {"open": 100, "high": 101, "low": 99, "close": 100}, 100)

    async def test_concurrent_entries_create_one_trade_and_event(self):
        results = await asyncio.gather(self.open_trade(), self.open_trade())
        self.assertEqual(sum(bool(result.get("executed")) for result in results), 1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM executed_trades"), 1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM execution_events WHERE event_type='position_opened'"), 1)
        self.assertEqual(await self.pool.fetchval("SELECT calculation_version FROM executed_trades"), 2)

    async def test_concurrent_close_settles_once(self):
        from services.session_manager import execute_exit
        opened = await self.open_trade()
        self.assertTrue(opened.get("executed"), opened)
        results = await asyncio.gather(*(execute_exit(str(self.sid), opened["trade_id"], 105) for _ in range(2)))
        self.assertEqual(sum("error" not in result for result in results), 1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM execution_events WHERE event_type='position_closed'"), 1)

    async def test_failed_event_insert_rolls_back_entire_entry(self):
        import asyncpg
        await self.pool.execute("ALTER TABLE execution_events ADD CONSTRAINT qa_reject CHECK(event_type <> 'position_opened')")
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.open_trade()
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM executed_trades"), 0)
        self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "armed")
        self.assertEqual(await self.pool.fetchval("SELECT status FROM planned_entries"), "planned")

    async def test_protective_close_preserves_pause_and_liquidation_stops(self):
        from services.session_manager import execute_exit
        opened = await self.open_trade()
        self.assertTrue(opened.get("executed"), opened)
        await self.pool.execute("UPDATE trading_sessions SET status='paused'")
        closed = await execute_exit(str(self.sid), opened["trade_id"], 95, "stop_loss")
        self.assertEqual(closed["state_after"], "paused")
        await self.pool.execute("UPDATE trading_sessions SET status='armed'")
        await self.pool.execute("UPDATE planned_entries SET status='planned'")
        opened = await self.open_trade()
        closed = await execute_exit(str(self.sid), opened["trade_id"], 90, "liquidation")
        self.assertEqual(closed["state_after"], "stopped")
