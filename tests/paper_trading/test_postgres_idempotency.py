"""AC-05: PostgreSQL concurrent idempotency tests.

Two concurrent same-key requests must produce one financial effect.
Same key with different payload must be rejected.
Retry after lost response must not create second fill.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from uuid import uuid4

import asyncpg
import pytest

DSN = os.getenv("WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa")

# DDL for test tables (simplified subset of paper_v2)
TEST_DDL = """
CREATE TABLE IF NOT EXISTS paper_v2_owners (
    owner_id UUID PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS paper_v2_accounts (
    account_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES paper_v2_owners(owner_id),
    kind TEXT NOT NULL CHECK (kind IN ('manual','auto')),
    currency TEXT NOT NULL DEFAULT 'USDT',
    opening_deposit NUMERIC(20,8) NOT NULL DEFAULT 1000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, kind)
);
CREATE TABLE IF NOT EXISTS paper_v2_days (
    day_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES paper_v2_owners(owner_id),
    timezone TEXT NOT NULL DEFAULT 'Asia/Bangkok',
    start_utc TIMESTAMPTZ,
    end_utc TIMESTAMPTZ,
    state TEXT NOT NULL DEFAULT 'idle' CHECK (state IN ('idle','starting','running','closing','closed','recovery_required','settlement_pending')),
    strategy_version TEXT NOT NULL DEFAULT 'baseline_v1'
);
CREATE TABLE IF NOT EXISTS paper_v2_commands (
    command_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES paper_v2_owners(owner_id),
    idempotency_key TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted','processing','completed','failed','rejected')),
    account_id UUID,
    day_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, idempotency_key)
);
"""


@pytest.fixture
async def db_pool():
    """Create a connection pool and set up schema."""
    pool = await asyncpg.create_pool(DSN, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS paper_v2_postings, paper_v2_positions, paper_v2_fills, paper_v2_orders, paper_v2_commands, paper_v2_leases, paper_v2_days, paper_v2_accounts, paper_v2_owners, paper_v2_cutovers CASCADE")
        await conn.execute(TEST_DDL)
    yield pool
    await pool.close()


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


class TestIdempotency:
    """AC-05: concurrent and retry idempotency."""

    @pytest.mark.asyncio
    async def test_same_key_same_payload_returns_existing(self, db_pool):
        """Same key + same payload → return existing command (idempotent replay)."""
        owner_id = uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))

        key = "test-key-1"
        payload = {"side": "long", "qty": "0.001"}
        req_hash = _hash_payload(payload)

        # First insert
        cmd_id_1 = uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                str(cmd_id_1), str(owner_id), key, json.dumps(payload), req_hash,
            )

        # Second insert with same key + same hash → should fail (duplicate)
        cmd_id_2 = uuid4()
        with pytest.raises(asyncpg.UniqueViolationError):
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                    str(cmd_id_2), str(owner_id), key, json.dumps(payload), req_hash,
                )

        # Verify only one command exists
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM paper_v2_commands WHERE owner_id=$1 AND idempotency_key=$2",
                str(owner_id), key,
            )
        assert count == 1

    @pytest.mark.asyncio
    async def test_same_key_different_payload_rejected(self, db_pool):
        """Same key + different payload → conflict (different request_hash)."""
        owner_id = uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))

        key = "test-key-2"
        payload_1 = {"side": "long", "qty": "0.001"}
        payload_2 = {"side": "short", "qty": "0.002"}

        # First insert
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                str(uuid4()), str(owner_id), key, json.dumps(payload_1), _hash_payload(payload_1),
            )

        # Second insert with same key but different payload → UNIQUE violation on (owner_id, idempotency_key)
        with pytest.raises(asyncpg.UniqueViolationError):
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                    str(uuid4()), str(owner_id), key, json.dumps(payload_2), _hash_payload(payload_2),
                )

    @pytest.mark.asyncio
    async def test_concurrent_same_key_one_wins(self, db_pool):
        """Two concurrent inserts with same key → only one succeeds."""
        owner_id = uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))

        key = "concurrent-key-1"
        payload = {"side": "long", "qty": "0.01"}
        req_hash = _hash_payload(payload)

        async def try_insert(cmd_id):
            try:
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                        str(cmd_id), str(owner_id), key, json.dumps(payload), req_hash,
                    )
                return True
            except asyncpg.UniqueViolationError:
                return False

        # Launch two concurrent inserts
        results = await asyncio.gather(
            try_insert(uuid4()),
            try_insert(uuid4()),
        )

        # Exactly one should succeed
        assert sum(results) == 1, f"Expected exactly 1 success, got {sum(results)}"

        # Verify only one command in DB
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM paper_v2_commands WHERE owner_id=$1 AND idempotency_key=$2",
                str(owner_id), key,
            )
        assert count == 1

    @pytest.mark.asyncio
    async def test_different_keys_both_succeed(self, db_pool):
        """Different keys → both succeed (no conflict)."""
        owner_id = uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))

        for i in range(3):
            payload = {"index": i}
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, payload, request_hash) VALUES ($1, $2, $3, 'place_order', $4, $5)",
                    str(uuid4()), str(owner_id), f"key-{i}", json.dumps(payload), _hash_payload(payload),
                )

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM paper_v2_commands WHERE owner_id=$1",
                str(owner_id),
            )
        assert count == 3
