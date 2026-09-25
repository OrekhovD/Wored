"""PostgreSQL command-claim concurrency + idempotency (Plan Phase 1, cases 5 & 6, real DB).

Applies ``migrations/paper_v2_schema.sql`` to a disposable schema on the QA
database (never the production ``trading`` DB) and exercises the *real*
:class:`PaperRepository`:

  * ``submit_command`` idempotency — replay returns the same command; a different
    payload under the same key raises :class:`IdempotencyConflict`.
  * ``claim_command`` atomicity across two independent connections — exactly one
    caller transitions an ``accepted`` command to ``processing``.

Skips gracefully when no QA database is reachable, so it runs in the isolated
``wored-qa`` compose environment (Phase 2) and does not require a local server.
"""
from __future__ import annotations

import asyncio
import os
import uuid as _uuid
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from paper_trading.contracts import CommandStatus, CommandType
from paper_trading.repository import IdempotencyConflict, PaperRepository

DSN = os.getenv("WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa")
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "paper_v2_schema.sql"


@pytest.fixture
async def repo():
    if DSN.rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against production 'trading' database")
    schema = f"cmd_claim_{_uuid.uuid4().hex[:8]}"
    admin = None
    try:
        admin = await asyncpg.connect(DSN)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"QA database not reachable: {exc}")
    try:
        db = await admin.fetchval("SELECT current_database()")
        if db == "trading":
            pytest.fail("refusing to run against production database 'trading'")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}", public')
        await admin.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not prepare QA schema: {exc}")
    finally:
        await admin.close()

    try:
        pool = await asyncpg.create_pool(
            DSN, min_size=2, max_size=4, server_settings={"search_path": schema}
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not build QA pool: {exc}")
    try:
        yield PaperRepository(pool)
    finally:
        await pool.close()
        conn = await asyncpg.connect(DSN)
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def _seed_owner(repo: PaperRepository):
    owner_id = uuid4()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO paper_v2_owners (owner_id, display_name, created_at, schema_version) "
            "VALUES ($1, $2, NOW(), 2)",
            str(owner_id), f"owner-{str(owner_id)[:8]}",
        )
    return owner_id


async def _seed_day(repo: PaperRepository, owner_id):
    """Create a real day row so a finish command's day_id FK is satisfied."""
    day_id = uuid4()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO paper_v2_days (day_id, owner_id, state, created_at, schema_version) "
            "VALUES ($1, $2, 'running', NOW(), 2)",
            str(day_id), str(owner_id),
        )
    return day_id


class TestCommandIdempotencyRealRepo:
    async def test_same_key_replay_returns_same_command(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        day_id = await _seed_day(repo, owner_id)
        key = f"finish-{day_id}"
        first = await repo.submit_command(
            command_id=uuid4(), owner_id=owner_id, idempotency_key=key,
            command_type=CommandType.finish_day, payload={}, day_id=day_id,
        )
        second = await repo.submit_command(
            command_id=uuid4(), owner_id=owner_id, idempotency_key=key,
            command_type=CommandType.finish_day, payload={}, day_id=day_id,
        )
        assert second.command_id == first.command_id, "replay returns the same command"
        assert second.status == first.status

    async def test_same_key_different_payload_conflicts(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        key = f"finish-{uuid4()}"
        await repo.submit_command(
            command_id=uuid4(), owner_id=owner_id, idempotency_key=key,
            command_type=CommandType.finish_day, payload={},
        )
        with pytest.raises(IdempotencyConflict):
            await repo.submit_command(
                command_id=uuid4(), owner_id=owner_id, idempotency_key=key,
                command_type=CommandType.finish_day, payload={"unexpected": True},
            )

    async def test_claim_is_atomic_across_two_connections(self, repo) -> None:
        """Exactly one of two concurrent claims wins (SKIP LOCKED + status filter)."""
        owner_id = await _seed_owner(repo)
        cmd = await repo.submit_command(
            command_id=uuid4(), owner_id=owner_id, idempotency_key=f"finish-{uuid4()}",
            command_type=CommandType.finish_day, payload={},
        )

        results = await asyncio.gather(
            repo.claim_command(cmd.command_id),
            repo.claim_command(cmd.command_id),
        )
        winners = [r for r in results if r is not None]
        assert len(winners) == 1, "exactly one runner claims the accepted command"

        # The command is now processing; a third claim (already processing) is refused.
        again = await repo.claim_command(cmd.command_id)
        assert again is None, "a processing command cannot be re-claimed"

        # Completing it makes it terminal; requeue only releases a processing row.
        await repo.complete_command(cmd.command_id, {"ok": True})
        requeued = await repo.requeue_command(cmd.command_id)
        assert requeued is False, "a completed command is never resurrected"
        final = await repo.get_command(cmd.command_id)
        assert final is not None and final.status == CommandStatus.completed

    async def test_requeue_releases_processing_for_retry(self, repo) -> None:
        """A deferred close requeues its claim so it is retried next cycle."""
        owner_id = await _seed_owner(repo)
        cmd = await repo.submit_command(
            command_id=uuid4(), owner_id=owner_id, idempotency_key=f"finish-{uuid4()}",
            command_type=CommandType.finish_day, payload={},
        )
        claimed = await repo.claim_command(cmd.command_id)
        assert claimed is not None and claimed.status == CommandStatus.processing
        released = await repo.requeue_command(cmd.command_id)
        assert released is True
        after = await repo.get_command(cmd.command_id)
        assert after is not None and after.status == CommandStatus.accepted
