"""Persistent state and append-only audit ledger for paper trading days."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request


PAPER_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS paper_trading_runtime (
    owner_key TEXT PRIMARY KEY,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    state JSONB,
    revision BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_trading_ledger (
    id BIGSERIAL PRIMARY KEY,
    owner_key TEXT NOT NULL,
    day_id TEXT,
    account_kind TEXT,
    event_type TEXT NOT NULL,
    amount NUMERIC(24, 10),
    idempotency_key TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT paper_ledger_account_kind_check
        CHECK (account_kind IS NULL OR account_kind IN ('manual', 'auto')),
    CONSTRAINT paper_ledger_owner_idempotency_unique
        UNIQUE (owner_key, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_paper_ledger_owner_time
    ON paper_trading_ledger (owner_key, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_ledger_day_time
    ON paper_trading_ledger (day_id, occurred_at, id);

CREATE TABLE IF NOT EXISTS paper_agent_runs (
    run_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    day_id TEXT,
    agent_role TEXT NOT NULL,
    provider TEXT,
    model_id TEXT,
    snapshot_id TEXT NOT NULL,
    strategy_version INT NOT NULL,
    status TEXT NOT NULL,
    input_payload JSONB NOT NULL,
    output_payload JSONB,
    usage_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT paper_agent_run_status_check
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_paper_agent_runs_day_role
    ON paper_agent_runs (owner_key, day_id, agent_role, created_at DESC);

CREATE TABLE IF NOT EXISTS paper_strategy_versions (
    owner_key TEXT NOT NULL,
    version INT NOT NULL,
    parent_version INT,
    status TEXT NOT NULL,
    rules JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_metrics JSONB,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    PRIMARY KEY (owner_key, version),
    CONSTRAINT paper_strategy_status_check
        CHECK (status IN ('candidate', 'validating', 'approved', 'active', 'rejected', 'rolled_back'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_strategy_one_active
    ON paper_strategy_versions (owner_key)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS paper_learning_reviews (
    owner_key TEXT NOT NULL,
    day_id TEXT NOT NULL,
    review_version INT NOT NULL,
    status TEXT NOT NULL,
    sample_size INT NOT NULL,
    input_digest TEXT NOT NULL,
    findings JSONB NOT NULL,
    candidate_strategy_version INT,
    model_runs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_key, day_id, review_version),
    CONSTRAINT paper_learning_status_check
        CHECK (status IN ('insufficient_data', 'candidate', 'validating', 'accepted', 'rejected'))
);
"""


@dataclass(frozen=True)
class StoredPaperState:
    settings: dict[str, Any]
    state: dict[str, Any] | None
    revision: int


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


class MemoryPaperStore:
    """Process-local adapter used only when the application has no Postgres pool."""

    def __init__(self) -> None:
        self._rows: dict[str, StoredPaperState] = {}
        self._results: dict[tuple[str, str], dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, owner_key: str) -> asyncio.Lock:
        return self._locks.setdefault(owner_key, asyncio.Lock())

    async def load(self, owner_key: str) -> StoredPaperState:
        row = self._rows.get(owner_key, StoredPaperState({}, None, 0))
        return StoredPaperState(deepcopy(row.settings), deepcopy(row.state), row.revision)

    async def find_result(self, owner_key: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        result = self._results.get((owner_key, key))
        return deepcopy(result) if result is not None else None

    async def save(
        self,
        owner_key: str,
        *,
        settings: dict[str, Any],
        state: dict[str, Any] | None,
        expected_revision: int,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        current = self._rows.get(owner_key, StoredPaperState({}, None, 0))
        if current.revision != expected_revision:
            if idempotency_key:
                repeated = self._results.get((owner_key, idempotency_key))
                if repeated is not None:
                    return deepcopy(repeated)
            raise HTTPException(status_code=409, detail="Состояние изменилось; обновите страницу")
        revision = expected_revision + 1
        self._rows[owner_key] = StoredPaperState(
            deepcopy(settings), deepcopy(state), revision
        )
        if idempotency_key:
            self._results[(owner_key, idempotency_key)] = deepcopy(payload)
        return None


class PostgresPaperStore:
    """Optimistic snapshot store with an append-only event ledger."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def lock(self, owner_key: str) -> asyncio.Lock:
        # Postgres revision checks provide cross-process serialization.
        return _NOOP_LOCK

    async def load(self, owner_key: str) -> StoredPaperState:
        row = await self.pool.fetchrow(
            "SELECT settings, state, revision FROM paper_trading_runtime WHERE owner_key=$1",
            owner_key,
        )
        if row is None:
            return StoredPaperState({}, None, 0)
        return StoredPaperState(
            _json_object(row["settings"]),
            _json_object(row["state"]) if row["state"] is not None else None,
            int(row["revision"]),
        )

    async def find_result(self, owner_key: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        payload = await self.pool.fetchval(
            """SELECT payload FROM paper_trading_ledger
               WHERE owner_key=$1 AND idempotency_key=$2""",
            owner_key,
            key,
        )
        return _json_object(payload) if payload is not None else None

    async def save(
        self,
        owner_key: str,
        *,
        settings: dict[str, Any],
        state: dict[str, Any] | None,
        expected_revision: int,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        day_id = (state or {}).get("day", {}).get("id")
        account_kind = payload.get("account_kind")
        amount = payload.get("amount")
        async with self.pool.acquire() as connection, connection.transaction():
            if idempotency_key:
                existing = await connection.fetchval(
                    """SELECT payload FROM paper_trading_ledger
                       WHERE owner_key=$1 AND idempotency_key=$2""",
                    owner_key,
                    idempotency_key,
                )
                if existing is not None:
                    return _json_object(existing)

            if expected_revision == 0:
                changed = await connection.fetchval(
                    """INSERT INTO paper_trading_runtime
                           (owner_key, settings, state, revision)
                       VALUES ($1, $2::jsonb, $3::jsonb, 1)
                       ON CONFLICT (owner_key) DO NOTHING
                       RETURNING revision""",
                    owner_key,
                    json.dumps(settings),
                    json.dumps(state) if state is not None else None,
                )
            else:
                changed = await connection.fetchval(
                    """UPDATE paper_trading_runtime
                       SET settings=$2::jsonb, state=$3::jsonb,
                           revision=revision+1, updated_at=NOW()
                       WHERE owner_key=$1 AND revision=$4
                       RETURNING revision""",
                    owner_key,
                    json.dumps(settings),
                    json.dumps(state) if state is not None else None,
                    expected_revision,
                )
            if changed is None:
                if idempotency_key:
                    existing = await connection.fetchval(
                        """SELECT payload FROM paper_trading_ledger
                           WHERE owner_key=$1 AND idempotency_key=$2""",
                        owner_key,
                        idempotency_key,
                    )
                    if existing is not None:
                        return _json_object(existing)
                raise HTTPException(status_code=409, detail="Состояние изменилось; обновите страницу")
            await connection.execute(
                """INSERT INTO paper_trading_ledger
                       (owner_key, day_id, account_kind, event_type, amount,
                        idempotency_key, payload)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
                owner_key,
                day_id,
                account_kind,
                event_type,
                amount,
                idempotency_key,
                json.dumps(payload),
            )
            return None


class _NoopLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


_NOOP_LOCK = _NoopLock()
MEMORY_STORE = MemoryPaperStore()


def paper_owner_key(request: Request) -> str:
    session = request.session
    if session.get("auth_type") == "telegram":
        user = session.get("telegram_user") or {}
        if isinstance(user.get("user_id"), int):
            return f"telegram:{user['user_id']}"
    if session.get("auth_type") == "password" and session.get("username"):
        return f"password:{session['username']}"
    identifier = session.get("paper_demo_id")
    if not identifier:
        from uuid import uuid4
        identifier = str(uuid4())
        session["paper_demo_id"] = identifier
    return f"browser:{identifier}"


def get_paper_store(request: Request) -> MemoryPaperStore | PostgresPaperStore:
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        return MEMORY_STORE
    return PostgresPaperStore(pool)
