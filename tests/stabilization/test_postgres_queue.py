"""Real transaction tests, only against the dedicated disposable QA database."""
import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
from forecast_queue import SCHEMA, enqueue, process_one


@unittest.skipUnless(os.getenv("WORED_TEST_DATABASE_URL"), "Dedicated PostgreSQL QA database not available")
class PostgresQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        dsn = os.environ["WORED_TEST_DATABASE_URL"]
        if urlsplit(dsn).path != "/wored_qa":
            raise RuntimeError("Tests only accept the disposable wored_qa database")
        self.schema = "qa_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4,
                                              server_settings={"search_path": self.schema})
        await self.pool.execute("CREATE TABLE forecast_requests(id SERIAL PRIMARY KEY,status TEXT DEFAULT 'pending',updated_at TIMESTAMP)")
        await self.pool.execute(SCHEMA)
        await self.pool.execute(SCHEMA)
        await self.pool.execute("CREATE TABLE qa_results(request_id INTEGER PRIMARY KEY)")
        async with self.pool.acquire() as connection, connection.transaction():
            self.request_id = await connection.fetchval("INSERT INTO forecast_requests DEFAULT VALUES RETURNING id")
            await enqueue(connection, self.request_id, {"horizon_steps": 4})

    async def asyncTearDown(self):
        await self.pool.close()
        # Only the unique schema created by this test can be removed.
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def save_result(self, request_id, payload, connection):
        await connection.execute("INSERT INTO qa_results VALUES($1)", request_id)
        await connection.execute("UPDATE forecast_requests SET status='active' WHERE id=$1", request_id)

    async def test_success_commits_results_and_ack(self):
        await process_one(self.pool, self.save_result)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM qa_results"), 1)
        self.assertEqual(await self.pool.fetchval("SELECT state FROM forecast_jobs"), "completed")

    async def test_error_rolls_back_partial_results(self):
        async def fail(*args):
            await self.save_result(*args)
            raise ValueError("bad model result")
        await process_one(self.pool, fail)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM qa_results"), 0)
        self.assertEqual(await self.pool.fetchval("SELECT status FROM forecast_requests"), "failed")

    async def test_cancelled_worker_can_be_recovered_without_duplicate_rows(self):
        started = asyncio.Event()
        async def interrupted(*args):
            await self.save_result(*args)
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(process_one(self.pool, interrupted))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM qa_results"), 0)
        self.assertEqual(await self.pool.fetchval("SELECT state FROM forecast_jobs"), "pending")
        await process_one(self.pool, self.save_result)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM qa_results"), 1)

    async def test_two_workers_cannot_run_one_job_concurrently(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def hold(*args):
            started.set()
            await release.wait()
            await self.save_result(*args)
        first = asyncio.create_task(process_one(self.pool, hold))
        await asyncio.wait_for(started.wait(), 3)
        try:
            self.assertFalse(await asyncio.wait_for(process_one(self.pool, self.save_result), 3))
        finally:
            release.set()
            await first
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM qa_results"), 1)
