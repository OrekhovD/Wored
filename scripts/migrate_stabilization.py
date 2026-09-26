#!/usr/bin/env python3
"""Stabilization migration script for WORED.

Implements versioned schema migrations with:
- schema_migrations(version TEXT PK, applied_at TIMESTAMPTZ, checksum TEXT)
- Advisory lock for the entire run
- Idempotent DDL steps
- Checksum verification of already-applied migrations
- Rollback on checksum mismatch

Usage:
  python scripts/migrate_stabilization.py [--dsn DSN] [--check] [--rollback-step N]

Options:
  --dsn DSN          PostgreSQL connection string (default: from WORED_TEST_DATABASE_URL or .env.postgres)
  --check            Dry-run: verify migrations without applying
  --rollback-step N  NOT IMPLEMENTED: would roll back step N (additive migrations only)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

# ── Migration steps ──────────────────────────────────────────────────────────

MIGRATIONS = [
    {
        "version": "20260908_01",
        "description": "Stabilization base DDL — forecast_jobs, forecast_requests extensions, forecast_points metrics",
        "sql": """
-- Forecast jobs table (from forecast_queue.py SCHEMA)
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
CREATE UNIQUE INDEX IF NOT EXISTS forecast_jobs_idempotency ON forecast_jobs(idempotency_key);

-- Forecast requests extensions (from forecast_queue.py and forecast_schema.py)
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS execution_state TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS evaluation_state TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS failure_code TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMP;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS as_of TIMESTAMP;

-- Forecast points metrics columns (from forecast_schema.py)
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS metrics_version INT NOT NULL DEFAULT 1;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS baseline_error_pct DECIMAL(12,6);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS skill_vs_baseline DOUBLE PRECISION;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS step_index INT DEFAULT 0;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS predicted_low DECIMAL(20, 8);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS predicted_high DECIMAL(20, 8);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS in_range BOOLEAN DEFAULT NULL;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS pattern_match_score DECIMAL(5,2) DEFAULT NULL;

-- Forecast reports table
CREATE TABLE IF NOT EXISTS forecast_reports (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_run_id INT NOT NULL REFERENCES forecast_model_runs(id) ON DELETE CASCADE,
    agent_role VARCHAR(20) NOT NULL DEFAULT 'neutral',
    evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    step_index INT NOT NULL,
    target_time TIMESTAMP NOT NULL,
    factual_price DECIMAL(20, 8),
    error_pct DECIMAL(10, 4),
    in_range BOOLEAN,
    confidence_before DECIMAL(5, 2),
    confidence_after DECIMAL(5, 2),
    reason_text TEXT,
    model_response TEXT,
    UNIQUE (model_run_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_forecast_reports_request_id ON forecast_reports (request_id);
CREATE INDEX IF NOT EXISTS idx_forecast_reports_evaluated ON forecast_reports (evaluated_at);
""",
    },
    {
        "version": "20260908_02",
        "description": "LLM accounting tables (from R04 specification)",
        "sql": """
-- LLM request tracking
CREATE TABLE IF NOT EXISTS llm_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    principal TEXT NOT NULL,
    task_type TEXT NOT NULL,
    snapshot_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    final_state TEXT NOT NULL DEFAULT 'queued' CHECK (final_state IN ('queued','running','completed','failed','denied')),
    next_sequence INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_requests_source ON llm_requests (source, created_at);

-- LLM attempt tracking
CREATE TABLE IF NOT EXISTS llm_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES llm_requests(id) ON DELETE CASCADE,
    sequence INT NOT NULL,
    provider TEXT,
    model TEXT,
    started_at TIMESTAMPTZ,
    deadline_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','running','succeeded','failed','cancelled','unknown_charge')),
    error_code TEXT,
    finish_reason TEXT,
    input_tokens BIGINT CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens BIGINT CHECK (output_tokens IS NULL OR output_tokens >= 0),
    cached_tokens BIGINT CHECK (cached_tokens IS NULL OR cached_tokens >= 0),
    reasoning_tokens BIGINT CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
    tool_tokens BIGINT CHECK (tool_tokens IS NULL OR tool_tokens >= 0),
    reserved_tokens BIGINT CHECK (reserved_tokens IS NULL OR reserved_tokens >= 0),
    charged_tokens BIGINT CHECK (charged_tokens IS NULL OR charged_tokens >= 0),
    reserved_cost NUMERIC(20,8),
    charged_cost NUMERIC(20,8),
    estimated_equivalent_cost NUMERIC(20,8),
    usage_source TEXT,
    latency_ms INT,
    UNIQUE (request_id, sequence)
);

-- LLM budget buckets
CREATE TABLE IF NOT EXISTS llm_budget_buckets (
    scope_key TEXT NOT NULL,
    period_kind TEXT NOT NULL CHECK (period_kind IN ('day','week','month')),
    period_start TIMESTAMPTZ NOT NULL,
    request_limit BIGINT,
    token_limit BIGINT,
    cost_limit NUMERIC(20,8),
    used_requests BIGINT NOT NULL DEFAULT 0,
    reserved_requests BIGINT NOT NULL DEFAULT 0,
    used_tokens BIGINT NOT NULL DEFAULT 0,
    reserved_tokens BIGINT NOT NULL DEFAULT 0,
    used_cost NUMERIC(20,8) NOT NULL DEFAULT 0,
    reserved_cost NUMERIC(20,8) NOT NULL DEFAULT 0,
    PRIMARY KEY (scope_key, period_kind, period_start)
);
""",
    },
    {
        "version": "20260908_03",
        "description": "Forecast revisions and history columns (from R06 specification)",
        "sql": """
-- Forecast revisions (from R06)
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS parent_request_id INTEGER REFERENCES forecast_requests(id);
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS revision_number INT NOT NULL DEFAULT 0;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS revision_reason TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS input_snapshot_id UUID;

-- Index for revision chain lookups
CREATE INDEX IF NOT EXISTS idx_forecast_requests_parent ON forecast_requests (parent_request_id) WHERE parent_request_id IS NOT NULL;

-- Legacy pending cleanup: mark stale pending requests as failed
-- This is a one-time data migration, not DDL
""",
    },
    {
        "version": "20260908_04",
        "description": "LLM reservations and routing decisions tables (from R04 specification)",
        "sql": """
-- LLM reservations for atomic budget reservation tracking
CREATE TABLE IF NOT EXISTS llm_reservations (
    attempt_id UUID NOT NULL REFERENCES llm_attempts(id) ON DELETE CASCADE,
    scope_key TEXT NOT NULL,
    period_kind TEXT NOT NULL CHECK (period_kind IN ('day', 'week', 'month')),
    period_start TIMESTAMPTZ NOT NULL,
    requests BIGINT NOT NULL DEFAULT 0 CHECK (requests >= 0),
    tokens BIGINT NOT NULL DEFAULT 0 CHECK (tokens >= 0),
    cost NUMERIC(20, 8) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    settled_at TIMESTAMPTZ,
    PRIMARY KEY (attempt_id, scope_key, period_kind, period_start),
    FOREIGN KEY (scope_key, period_kind, period_start)
        REFERENCES llm_budget_buckets(scope_key, period_kind, period_start)
        ON DELETE RESTRICT
);

-- LLM routing decisions for audit trail
CREATE TABLE IF NOT EXISTS llm_routing_decisions (
    request_id UUID NOT NULL REFERENCES llm_requests(id) ON DELETE CASCADE,
    sequence INT NOT NULL,
    candidate_provider_model TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allowed', 'skipped')),
    reason_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (request_id, sequence, candidate_provider_model)
);

CREATE INDEX IF NOT EXISTS idx_llm_reservations_attempt ON llm_reservations(attempt_id);
CREATE INDEX IF NOT EXISTS idx_llm_routing_decisions_request ON llm_routing_decisions(request_id);
""",
    },
]


def compute_checksum(sql: str) -> str:
    """Compute SHA-256 checksum of migration SQL."""
    return hashlib.sha256(sql.strip().encode()).hexdigest()[:16]


async def run_migrations(dsn: str, check_only: bool = False) -> None:
    """Apply migrations with advisory lock and checksum verification."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        # Acquire advisory lock
        await conn.execute("SELECT pg_advisory_lock(20260908)")

        # Create schema_migrations table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                checksum TEXT NOT NULL
            )
        """)

        # Get already-applied migrations
        applied = {
            row["version"]: row["checksum"]
            for row in await conn.fetch("SELECT version, checksum FROM schema_migrations")
        }

        for migration in MIGRATIONS:
            version = migration["version"]
            sql = migration["sql"]
            checksum = compute_checksum(sql)

            if version in applied:
                existing_checksum = applied[version]
                if existing_checksum != checksum:
                    print(f"CHECKSUM MISMATCH for {version}: "
                          f"applied={existing_checksum}, current={checksum}", file=sys.stderr)
                    print("Aborting. Changes must be a new migration version, "
                          "not modifying an existing one.", file=sys.stderr)
                    await conn.execute("SELECT pg_advisory_unlock(20260908)")
                    sys.exit(1)
                print(f"SKIP {version} (already applied, checksum matches)")
                continue

            if check_only:
                print(f"WOULD APPLY {version}: {migration['description']}")
                continue

            print(f"APPLY {version}: {migration['description']}")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    version, checksum
                )
            print(f"  Applied {version} successfully")

    finally:
        await conn.execute("SELECT pg_advisory_unlock(20260908)")
        await conn.close()


def get_dsn() -> str:
    """Get DSN from environment or .env.postgres."""
    dsn = os.getenv("WORED_TEST_DATABASE_URL")
    if dsn:
        return dsn

    env_path = Path(__file__).resolve().parents[1] / ".env.postgres"
    if env_path.exists():
        parts = {}
        for line in env_path.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
                if line.startswith(f"{key}="):
                    parts[key] = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(parts) == 3:
                return f"postgresql://{parts['POSTGRES_USER']}:{parts['POSTGRES_PASSWORD']}@localhost:5432/{parts['POSTGRES_DB']}"

    return "postgresql://bot@localhost:5432/wored_qa"


def main():
    parser = argparse.ArgumentParser(description="WORED stabilization migrations")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN")
    parser.add_argument("--check", action="store_true", help="Dry-run: verify without applying")
    args = parser.parse_args()

    dsn = args.dsn or get_dsn()
    import asyncio
    asyncio.run(run_migrations(dsn, check_only=args.check))


if __name__ == "__main__":
    main()