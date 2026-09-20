"""Phase 7: Migration rehearsal for trader_v1_schema.sql on disposable QA.

Applies the schema to the QA database resolved from WORED_TEST_DATABASE_URL,
verifies trader_v1_* tables and their FK constraints to paper_v2_*, and checks
that a repeated application is idempotent. Refuses to run against the
production 'trading' database.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import asyncpg
import pytest

DSN = os.getenv("WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa")

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "trader_v1_schema.sql"
PAPER_V2_PATH = Path(__file__).resolve().parents[2] / "migrations" / "paper_v2_schema.sql"


@pytest.fixture
async def db_conn():
    dsn = DSN
    if dsn.split("?")[0].rstrip("/").endswith("/trading"):
        pytest.fail(f"refusing to rehearse migration against production database: {dsn}")
    conn = await asyncpg.connect(dsn)
    try:
        db = await conn.fetchval("SELECT current_database()")
        if db == "trading":
            pytest.fail("refusing to rehearse migration against production database 'trading'")
        # Isolate from other suites that share the disposable QA database:
        # run the rehearsal in a temporary schema so the real migrations apply
        # against real table definitions, not stripped fixture tables.
        rehearsal_schema = f"trader_v1_rehearsal_{uuid.uuid4().hex[:8]}"
        await conn.execute(f'CREATE SCHEMA "{rehearsal_schema}"')
        await conn.execute(f'SET search_path TO "{rehearsal_schema}", public')
        # trader_v1_* has FKs into paper_v2_* (single source of truth);
        # ensure the base schema exists in the disposable QA DB.
        await conn.execute(PAPER_V2_PATH.read_text(encoding="utf-8"))
        yield conn
        await conn.execute(f'DROP SCHEMA "{rehearsal_schema}" CASCADE')
    finally:
        await conn.close()


class TestTraderV1Migration:
    async def test_schema_applies_and_creates_tables(self, db_conn):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        await db_conn.execute(schema)
        rows = await db_conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname=(SELECT current_schema()) "
            "AND tablename LIKE 'trader_v1_%' ORDER BY tablename"
        )
        tables = {r["tablename"] for r in rows}
        assert tables, "trader_v1_schema.sql created no trader_v1_* tables"

    async def test_fk_constraints_present(self, db_conn):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        await db_conn.execute(schema)
        fks = await db_conn.fetch(
            "SELECT tc.table_name, kcu.column_name, ccu.table_name AS fk_table "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.table_name LIKE 'trader_v1_%' AND tc.constraint_type = 'FOREIGN KEY' "
            "AND tc.table_schema = (SELECT current_schema())"
        )
        assert len(fks) > 0, "no FK constraints found on trader_v1_* tables"
        referenced = {r["fk_table"] for r in fks}
        assert any(t.startswith("paper_v2_") for t in referenced), (
            f"trader_v1_* must reference paper_v2_* source of truth, got: {referenced}"
        )

    async def test_migration_is_idempotent(self, db_conn):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        await db_conn.execute(schema)
        before = await db_conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname=(SELECT current_schema()) "
            "AND tablename LIKE 'trader_v1_%' ORDER BY tablename"
        )
        # second application must not fail and must not duplicate tables
        await db_conn.execute(schema)
        after = await db_conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname=(SELECT current_schema()) "
            "AND tablename LIKE 'trader_v1_%' ORDER BY tablename"
        )
        assert [r["tablename"] for r in before] == [r["tablename"] for r in after]
