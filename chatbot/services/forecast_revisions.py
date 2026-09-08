"""Forecast revision chain management.

R06 — History, revisions, and metrics.
- forecast_requests gets parent_request_id, revision_number, revision_reason, input_snapshot_id
- Creating a revision uses the same forecast queue; parent stays immutable
- Idempotency scope includes parent ID
- Automatic hourly correction stays disabled; admin command for linked forecast is OK
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

log = logging.getLogger(__name__)


async def create_revision(
    pool: asyncpg.Pool,
    parent_request_id: int,
    reason: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create a revision of an existing forecast request.

    The parent request stays immutable. A new forecast_request row is inserted with
    parent_request_id pointing to the parent, revision_number = parent.revision_number + 1,
    and the revision reason is recorded.

    The same forecast_jobs queue is used; the idempotency scope includes the parent ID
    so that a duplicate call for the same (parent, key) pair returns the existing request_id.

    Returns dict with request_id, parent_request_id, revision_number, status.
    """
    async with pool.acquire() as conn, conn.transaction():
        # Fetch the parent request (must exist)
        parent = await conn.fetchrow(
            "SELECT id, symbol, horizon_hours, base_timeframe, depth, base_price, status "
            "FROM forecast_requests WHERE id = $1 FOR UPDATE",
            parent_request_id,
        )
        if parent is None:
            raise ValueError(f"Parent request {parent_request_id} not found")

        # Idempotency: if an idempotency_key scoped to parent is provided,
        # check if a job with that key already exists.
        scoped_key = None
        if idempotency_key:
            scoped_key = f"rev-{parent_request_id}-{idempotency_key}"
            existing = await conn.fetchrow(
                "SELECT request_id FROM forecast_jobs WHERE idempotency_key = $1",
                scoped_key,
            )
            if existing:
                # Return the existing request
                req = await conn.fetchrow(
                    "SELECT id, parent_request_id, revision_number, status "
                    "FROM forecast_requests WHERE id = $1",
                    existing["request_id"],
                )
                return dict(req)

        # Calculate revision_number from existing revisions of this parent
        max_rev = await conn.fetchval(
            "SELECT COALESCE(MAX(revision_number), 0) FROM forecast_requests "
            "WHERE parent_request_id = $1",
            parent_request_id,
        )
        revision_number = max_rev + 1

        # Insert new forecast_request as a revision
        snapshot_id = uuid.uuid4() if payload else None
        row = await conn.fetchrow(
            """
            INSERT INTO forecast_requests
                (symbol, horizon_hours, base_timeframe, depth, base_price,
                 status, source, parent_request_id, revision_number,
                 revision_reason, input_snapshot_id)
            VALUES ($1, $2, $3, $4, $5, 'pending', 'revision', $6, $7, $8, $9)
            RETURNING id, parent_request_id, revision_number, status
            """,
            parent["symbol"],
            parent["horizon_hours"],
            parent["base_timeframe"],
            parent["depth"],
            parent["base_price"],
            parent_request_id,
            revision_number,
            reason,
            snapshot_id,
        )

        # Enqueue using the same forecast_jobs queue
        job_payload = payload or {
            "horizon_steps": parent["horizon_hours"],
            "is_revision": True,
            "parent_request_id": parent_request_id,
        }
        await conn.execute(
            """
            INSERT INTO forecast_jobs (request_id, payload, idempotency_key)
            VALUES ($1, $2, $3)
            """,
            row["id"],
            json.dumps(job_payload, allow_nan=False),
            scoped_key,
        )

        return dict(row)


async def get_revision_chain(pool: asyncpg.Pool, request_id: int) -> list[dict[str, Any]]:
    """Return the full revision chain for a request (parent + all revisions)."""
    async with pool.acquire() as conn:
        # Find the root parent by walking up the parent chain
        root_id = await conn.fetchval(
            """
            WITH RECURSIVE ancestors AS (
                SELECT id, parent_request_id FROM forecast_requests WHERE id = $1
                UNION ALL
                SELECT fr.id, fr.parent_request_id FROM forecast_requests fr
                JOIN ancestors a ON fr.id = a.parent_request_id
            )
            SELECT id FROM ancestors WHERE parent_request_id IS NULL
            """,
            request_id,
        )

        if root_id is None:
            root_id = request_id

        # Get the parent and all revisions
        rows = await conn.fetch(
            """
            SELECT id, parent_request_id, revision_number, revision_reason, status, created_at
            FROM forecast_requests
            WHERE id = $1 OR parent_request_id = $1
            ORDER BY revision_number NULLS FIRST
            """,
            root_id,
        )
        return [dict(r) for r in rows]


async def cleanup_legacy_pending(pool: asyncpg.Pool, max_age_minutes: int = 20) -> int:
    """Mark pending jobs older than max_age_minutes as failed with reason legacy_missing_payload.

    Before changing status, backs up IDs/status/created_at into a
    forecast_jobs_legacy_backup table (created idempotently).
    Returns the number of rows affected.
    """
    async with pool.acquire() as conn, conn.transaction():
        # Create backup table idempotently
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS forecast_jobs_legacy_backup (
                request_id INTEGER NOT NULL,
                old_state TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                backup_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Backup IDs, status, created_at of rows that will be updated (idempotent)
        await conn.execute("""
            INSERT INTO forecast_jobs_legacy_backup (request_id, old_state, created_at)
            SELECT fj.request_id, fj.state, fj.created_at
            FROM forecast_jobs fj
            WHERE fj.state IN ('queued', 'pending')
              AND fj.deadline_at <= NOW()
              AND NOT EXISTS (
                  SELECT 1 FROM forecast_jobs_legacy_backup b
                  WHERE b.request_id = fj.request_id AND b.old_state = fj.state
              )
        """)

        # Mark stale pending jobs as failed
        result = await conn.execute(
            """
            UPDATE forecast_jobs
            SET state = 'failed',
                error_code = 'legacy_missing_payload',
                finished_at = NOW()
            WHERE state IN ('queued', 'pending')
              AND deadline_at <= NOW()
            """
        )
        count = int(result.split()[-1]) if result else 0

        # Also mark the corresponding forecast_requests as failed
        if count > 0:
            await conn.execute(
                """
                UPDATE forecast_requests fr
                SET status = 'failed', updated_at = NOW()
                FROM forecast_jobs fj
                WHERE fj.request_id = fr.id
                  AND fj.state = 'failed'
                  AND fj.error_code = 'legacy_missing_payload'
                  AND fr.status IN ('pending', 'active')
                """
            )

        return count