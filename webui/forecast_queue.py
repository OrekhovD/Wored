"""Durable, bounded forecast worker; results and acknowledgement share a transaction.

R01 additions:
- Job queue TTL 30 minutes from creation
- Worker attempt timeout 600 seconds
- Idle cycle 2 seconds
- Cancelled process rolls back uncommitted SQL
- Re-grabbed job gets new attempt_id in ledger
- Heartbeat via Redis key forecast_job:<request_id>:heartbeat, TTL 15s, refresh every 5s
- No infinite retry: provider retry limited by R04; error after exhaustion → failed
- State normalization: queued/running/partial/completed/failed/expired
- New fields: execution_state, evaluation_state, deadline_at, as_of, valid_until, failure_code
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

# ── Normalized states (separate from legacy forecast_requests.status) ──
QUEUED = "queued"
RUNNING = "running"
PARTIAL = "partial"
COMPLETED = "completed"
FAILED = "failed"
EXPIRED = "expired"

VALID_EXECUTION_STATES = frozenset({QUEUED, RUNNING, PARTIAL, COMPLETED, FAILED, EXPIRED})

# ── Queue constants ────────────────────────────────────────────────────
QUEUE_TTL_MINUTES = 30
ATTEMPT_TIMEOUT_SECONDS = 600
IDLE_CYCLE_SECONDS = 2

# ── Heartbeat constants ────────────────────────────────────────────────
HEARTBEAT_KEY_PREFIX = "forecast_job:"
HEARTBEAT_TTL_SECONDS = 15
HEARTBEAT_REFRESH_SECONDS = 5

SCHEMA = """
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS as_of TIMESTAMP;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS execution_state TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS evaluation_state TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS failure_code TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS forecast_jobs (
    request_id INTEGER PRIMARY KEY REFERENCES forecast_requests(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    idempotency_key TEXT,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','completed','failed','expired')),
    attempts INTEGER NOT NULL DEFAULT 0,
    attempt_id TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 minutes',
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS forecast_jobs_pending ON forecast_jobs (created_at) WHERE state='queued';
ALTER TABLE forecast_jobs ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE forecast_jobs ADD COLUMN IF NOT EXISTS attempt_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS forecast_jobs_idempotency ON forecast_jobs(idempotency_key);
"""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _generate_attempt_id() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


async def enqueue(
    connection: Any,
    request_id: int,
    payload: dict,
    idempotency_key: str | None = None,
) -> None:
    """Insert a forecast job into the queue within the caller's transaction."""
    await connection.execute(
        "INSERT INTO forecast_jobs (request_id, payload, idempotency_key) VALUES ($1, $2, $3)",
        request_id, json.dumps(payload, allow_nan=False), idempotency_key,
    )
    # Set normalized execution_state on the request row
    await connection.execute(
        "UPDATE forecast_requests SET execution_state='queued', updated_at=NOW() WHERE id=$1",
        request_id,
    )


async def _heartbeat_loop(redis_client: Any, request_id: int, stop_event: asyncio.Event) -> None:
    """Periodically refresh a Redis heartbeat key while the job is running."""
    if redis_client is None:
        return
    key = f"{HEARTBEAT_KEY_PREFIX}{request_id}:heartbeat"
    while not stop_event.is_set():
        try:
            await redis_client.set(key, str(time.time()), ex=HEARTBEAT_TTL_SECONDS)
        except Exception as exc:
            log.warning("Heartbeat set failed for request %s: %s", request_id, exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_REFRESH_SECONDS)
        except asyncio.TimeoutError:
            pass
        else:
            break  # stop_event was set


async def process_one(
    pool: Any,
    runner: Any,
    timeout: float = ATTEMPT_TIMEOUT_SECONDS,
    redis_client: Any = None,
) -> bool:
    """Pick up one queued job, run it, and commit or fail within a transaction.

    A crash or cancellation releases the row lock and rolls back every result write.
    SKIP LOCKED prevents multiple workers from grabbing the same job.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "SELECT *, deadline_at <= NOW() AS expired FROM forecast_jobs "
                "WHERE state IN ('queued','running') ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            if row is None:
                return False

            request_id = row["request_id"]
            error_code = None
            attempt_id = _generate_attempt_id()

            # Expired job → mark as expired, don't run
            if row["expired"]:
                error_code = "deadline_exceeded"
                await connection.execute(
                    "UPDATE forecast_jobs SET state='expired', attempts=attempts+1, "
                    "error_code=$2, attempt_id=$3, finished_at=NOW() WHERE request_id=$1",
                    request_id, error_code, attempt_id,
                )
                await connection.execute(
                    "UPDATE forecast_requests SET execution_state='expired', "
                    "status='failed', failure_code=$2, updated_at=NOW() WHERE id=$1",
                    request_id, error_code,
                )
                return True

            # Mark as running
            await connection.execute(
                "UPDATE forecast_jobs SET state='running', attempts=attempts+1, attempt_id=$2 "
                "WHERE request_id=$1",
                request_id, attempt_id,
            )
            await connection.execute(
                "UPDATE forecast_requests SET execution_state='running', updated_at=NOW() WHERE id=$1",
                request_id,
            )

            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)

            # Start heartbeat
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(redis_client, request_id, heartbeat_stop)
            )

            try:
                # Savepoint allows partial rollback on errors
                async with connection.transaction():
                    await asyncio.wait_for(
                        runner(request_id, payload, connection), timeout
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_code = type(exc).__name__
                log.warning("Forecast %s failed: %s: %s", request_id, error_code, exc, exc_info=True)

            # Signal heartbeat to stop
            heartbeat_stop.set()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

            if error_code:
                await connection.execute(
                    "UPDATE forecast_jobs SET state='failed', error_code=$2, "
                    "finished_at=NOW() WHERE request_id=$1",
                    request_id, error_code,
                )
                await connection.execute(
                    "UPDATE forecast_requests SET execution_state='failed', "
                    "status='failed', failure_code=$2, updated_at=NOW() WHERE id=$1",
                    request_id, error_code,
                )
            else:
                await connection.execute(
                    "UPDATE forecast_jobs SET state='completed', finished_at=NOW() "
                    "WHERE request_id=$1",
                    request_id,
                )
                # `status` is the lifecycle column and becomes 'completed'. The
                # quality column must not be overwritten here: the runner already
                # declared it (app.py writes PARTIAL when a role of the bundle
                # failed), and forcing 'completed' erased that - requests
                # 127/133/134/137 carry failed runs and still reported a clean
                # completion, while 'partial' never appeared once in 132 requests.
                await connection.execute(
                    "UPDATE forecast_requests SET status='completed', "
                    "execution_state=COALESCE(execution_state, $2), updated_at=NOW() "
                    "WHERE id=$1",
                    request_id, COMPLETED,
                )
    return True


async def work_forecasts(pool: Any, runner: Any, redis_client: Any = None) -> None:
    """Main worker loop: poll for jobs every IDLE_CYCLE_SECONDS."""
    while True:
        try:
            if await process_one(pool, runner, redis_client=redis_client):
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Forecast queue unavailable: %s", type(exc).__name__)
        await asyncio.sleep(IDLE_CYCLE_SECONDS)


async def stop_worker(task: Any) -> None:
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def get_heartbeat_key(request_id: int) -> str:
    """Return the Redis heartbeat key for a given request."""
    return f"{HEARTBEAT_KEY_PREFIX}{request_id}:heartbeat"


def resolve_execution_state(
    db_state: str | None,
    execution_state: str | None,
    heartbeat_alive: bool,
    completed_or_failed_in_db: bool,
) -> str:
    """Determine the normalized execution state.

    Rules:
    - completed/failed in DB have priority over heartbeat
    - heartbeat alive → running (only if not completed/failed in DB)
    - no heartbeat and db state is queued → queued
    - no heartbeat and db state was running → still running (heartbeat loss
      doesn't complete a job; only DB status change does)
    """
    if completed_or_failed_in_db:
        return execution_state or db_state or FAILED
    if heartbeat_alive:
        return RUNNING
    if execution_state in (QUEUED, None):
        return QUEUED
    # Running without heartbeat — heartbeat loss does NOT complete the job
    return execution_state or RUNNING