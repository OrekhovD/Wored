"""P6.2 — immutable report persistence against a disposable PostgreSQL.

Covers the PG half of the P6 acceptance clauses, and skips cleanly when the
throwaway QA database is not reachable so the default host run stays green:

  * MC-18 — the report is *derived from the ledger* (its net P&L equals the sum
    of the recorded ``realized_net_pnl`` rows) and becomes ``is_final`` only
    after settlement, reconciliation and no open position; a saved snapshot
    round-trips byte-for-byte with a stable canonical hash.
  * MC-19 — an already-saved report is not mutated by a later session: writing
    new closed positions and saving a second session leaves the first row's hash
    and bytes unchanged, and re-finalising the same slot is refused by the
    insert-only guard (the first frozen bytes stay authoritative).

Rows are seeded with raw SQL (owner / accounts / positions) rather than the full
order→fill chain, because this suite is about report derivation + persistence,
not execution — that path is already covered by ``test_session_execution_pg.py``.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from paper_trading import report_store
from paper_trading.session_report import canonical_hash

#: Opt-in only: never guesses a database (same contract as the P5.2 PG suite).
DSN = os.getenv("WORED_TEST_DATABASE_URL", "")
_ROOT = Path(__file__).resolve().parents[2]
LEDGER_SCHEMA = _ROOT / "migrations" / "paper_v2_schema.sql"
REPORT_SCHEMA = _ROOT / "db" / "migrations" / "20260930_simulation_reports.sql"

INSTRUMENT = "BTC-USDT"
_T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

_TRUNCATE = (
    "simulation_reports",
    "paper_v2_postings", "paper_v2_fills", "paper_v2_positions",
    "paper_v2_orders", "paper_v2_signals", "paper_v2_commands",
    "paper_v2_days", "paper_v2_accounts", "paper_v2_owners",
)


# ---------------------------------------------------------------------------
# schema bootstrap
# ---------------------------------------------------------------------------


def _dsn_ok() -> bool:
    if not DSN:
        return False
    if DSN.split("?")[0].rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against production 'trading' database")
    return True


@pytest.fixture(scope="session")
def qa_schema():
    if not _dsn_ok():
        pytest.skip("WORED_TEST_DATABASE_URL not set — disposable QA database required")
    schema = f"report_v3_{uuid4().hex[:8]}"

    async def _create() -> None:
        admin = await asyncpg.connect(DSN, timeout=5)
        try:
            db = await admin.fetchval("SELECT current_database()")
            if db == "trading":
                pytest.fail("refusing to run against production database 'trading'")
            await admin.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            await admin.close()
        pool = await asyncpg.create_pool(
            DSN, min_size=1, max_size=1, server_settings={"search_path": schema}
        )
        try:
            async with pool.acquire() as conn:
                await conn.execute(LEDGER_SCHEMA.read_text(encoding="utf-8"))
                await conn.execute(REPORT_SCHEMA.read_text(encoding="utf-8"))
                if await conn.fetchval("SELECT to_regclass($1)",
                                       f'"{schema}".simulation_reports') is None:
                    pytest.fail("simulation_reports DDL did not land")
        finally:
            await pool.close()

    try:
        asyncio.run(_create())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not prepare QA schema: {exc}")
    yield schema
    asyncio.run(_drop_schema(schema))


async def _drop_schema(schema: str) -> None:
    conn = await asyncpg.connect(DSN, timeout=5)
    try:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
async def conn(qa_schema):
    """One fresh connection per test with all domain tables emptied."""
    pool = await asyncpg.create_pool(
        DSN, min_size=1, max_size=2, server_settings={"search_path": qa_schema}
    )
    try:
        async with pool.acquire() as c:
            await c.execute("TRUNCATE " + ", ".join(_TRUNCATE) + " CASCADE")
            yield c
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# raw-SQL seed helpers
# ---------------------------------------------------------------------------


async def _seed_owner(conn) -> tuple[str, str, dict[str, str]]:
    """Insert an owner, one day and manual/auto accounts; return their ids."""
    owner_id, day_id = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO paper_v2_owners (owner_id, display_name, webui_identity) "
        "VALUES ($1, 'p62-report', $2)",
        owner_id, f"p62-{owner_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO paper_v2_days (day_id, owner_id, state) VALUES ($1, $2, 'closed')",
        day_id, owner_id,
    )
    accounts: dict[str, str] = {}
    for kind in ("manual", "auto"):
        aid = uuid4()
        await conn.execute(
            "INSERT INTO paper_v2_accounts (account_id, owner_id, kind, currency, "
            "opening_deposit) VALUES ($1, $2, $3, 'USDT', $4)",
            aid, owner_id, kind, Decimal("1000"),
        )
        accounts[kind] = str(aid)
    return str(owner_id), str(day_id), accounts


async def _insert_position(
    conn, account_id: str, day_id: str, *,
    net: Decimal, gross: Decimal, entry_fee: Decimal, exit_fee: Decimal,
    funding: Decimal, status: str = "closed", offset_minutes: int = 0,
    qty: Decimal = Decimal("1"), entry: Decimal = Decimal("10000"),
    margin: Decimal = Decimal("100"),
) -> str:
    pid = uuid4()
    opened = _T0 + timedelta(minutes=offset_minutes)
    closed = opened + timedelta(minutes=30)
    await conn.execute(
        """
        INSERT INTO paper_v2_positions (
            position_id, account_id, day_id, instrument, side, qty, avg_entry_price,
            isolated_margin, status, opened_at, closed_at, close_price,
            realized_gross_pnl, realized_net_pnl, entry_fee, exit_fee, funding_cashflow
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, 'long', $5, $6, $7, $8, $9, $10, $6,
            $11, $12, $13, $14, $15
        )
        """,
        pid, account_id, day_id, INSTRUMENT, qty, entry, margin, status,
        opened, closed if status != "open" else None,
        gross, net, entry_fee, exit_fee, funding,
    )
    return str(pid)


# ---------------------------------------------------------------------------
# MC-18 — derivation from the ledger + final gating
# ---------------------------------------------------------------------------


async def test_report_derived_from_ledger_MC18(conn):
    owner_id, day_id, accts = await _seed_owner(conn)
    await _insert_position(conn, accts["manual"], day_id,
                           net=Decimal("10"), gross=Decimal("12"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("1"),
                           funding=Decimal("0"), offset_minutes=0)
    await _insert_position(conn, accts["manual"], day_id,
                           net=Decimal("-4"), gross=Decimal("-3"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("-1"), offset_minutes=60)

    report = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=str(uuid4()),
        settlement_complete=False, reconciliation_ok=True,
    )
    manual = report.accounts["manual"]
    # net P&L is exactly the recorded ledger sum — no re-derivation from prices.
    # NUMERIC(20,8) columns come back scaled, so compare as Decimal not string.
    assert Decimal(manual.net_pnl) == Decimal("6")
    assert manual.trades_closed == 2
    assert Decimal(manual.entry_fees) == Decimal("2")
    assert Decimal(manual.funding_cashflow) == Decimal("-1")
    # below MIN_TRADES the sample metric is null, not a misleading point estimate
    assert manual.win_rate is None
    assert manual.reasons["win_rate"] == "insufficient_sample"
    # unsettled session must not publish a final PASS (ТЗ §83)
    assert report.is_final is False
    assert report.not_final_reason == "settlement_pending"


async def test_report_final_only_after_settle_reconcile_flat_MC18(conn):
    owner_id, day_id, accts = await _seed_owner(conn)
    await _insert_position(conn, accts["auto"], day_id,
                           net=Decimal("5"), gross=Decimal("6"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"), offset_minutes=0)
    # a still-open row keeps the report provisional via the ledger count
    await _insert_position(conn, accts["auto"], day_id,
                           net=Decimal("0"), gross=Decimal("0"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"), status="open", offset_minutes=90)

    sid = str(uuid4())
    with_open = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid,
        settlement_complete=True, reconciliation_ok=True,
    )
    assert with_open.is_final is False
    assert with_open.not_final_reason == "open_positions_remain"

    # closing the open position flips it to final
    await conn.execute(
        "UPDATE paper_v2_positions SET status='closed', closed_at=$2, "
        "realized_net_pnl=2, realized_gross_pnl=2 WHERE account_id=$1::uuid "
        "AND status='open'",
        accts["auto"], _T0 + timedelta(minutes=120),
    )
    settled = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid,
        settlement_complete=True, reconciliation_ok=True,
    )
    assert settled.is_final is True
    assert settled.not_final_reason is None
    assert settled.open_positions == 0


async def test_save_fetch_roundtrip_hash_stable_MC18(conn):
    owner_id, day_id, accts = await _seed_owner(conn)
    await _insert_position(conn, accts["manual"], day_id,
                           net=Decimal("7"), gross=Decimal("8"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"))
    sid = str(uuid4())
    report = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid,
        settlement_complete=True, reconciliation_ok=True,
    )
    saved = await report_store.save_report(conn, owner_id=owner_id, report=report)
    assert saved["stored"] is True
    assert saved["hash"] == canonical_hash(report)

    row = await report_store.fetch_report(conn, session_id=sid, scope="session")
    assert row is not None
    assert row["report_hash"] == canonical_hash(report)
    # the stored JSON is the model's own to_dict — proof the snapshot is faithful
    assert row["report"] == report.to_dict()
    assert row["is_final"] is True


# ---------------------------------------------------------------------------
# MC-19 — an old report is never mutated by a later session
# ---------------------------------------------------------------------------


async def test_old_report_unchanged_after_new_session_MC19(conn):
    owner_id, day_id, accts = await _seed_owner(conn)
    await _insert_position(conn, accts["manual"], day_id,
                           net=Decimal("10"), gross=Decimal("11"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"))
    sid_a = str(uuid4())
    report_a = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid_a,
        settlement_complete=True, reconciliation_ok=True,
    )
    await report_store.save_report(conn, owner_id=owner_id, report=report_a)
    before = await report_store.fetch_report(conn, session_id=sid_a, scope="session")
    hash_a = before["report_hash"]

    # a *new* session writes more closed positions to the same owner's accounts
    await _insert_position(conn, accts["manual"], day_id,
                           net=Decimal("-3"), gross=Decimal("-2"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("-1"), offset_minutes=180)
    sid_b = str(uuid4())
    report_b = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid_b,
        settlement_complete=True, reconciliation_ok=True,
    )
    await report_store.save_report(conn, owner_id=owner_id, report=report_b)
    assert Decimal(report_b.accounts["manual"].net_pnl) == Decimal("7")  # 10 + (-3)

    # session A's frozen row is byte-for-byte unchanged
    after = await report_store.fetch_report(conn, session_id=sid_a, scope="session")
    assert after["report_hash"] == hash_a
    assert after["report"] == before["report"]

    # and A's snapshot genuinely differs from B's (they are separate facts)
    assert hash_a != canonical_hash(report_b)


async def test_refinalise_same_slot_does_not_mutate_MC19(conn):
    owner_id, day_id, accts = await _seed_owner(conn)
    await _insert_position(conn, accts["auto"], day_id,
                           net=Decimal("4"), gross=Decimal("5"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"))
    sid = str(uuid4())
    first = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid,
        settlement_complete=True, reconciliation_ok=True,
    )
    res1 = await report_store.save_report(conn, owner_id=owner_id, report=first)
    hash_first = res1["hash"]

    # ledger moves, a new (different-bytes) report is built for the same slot
    await _insert_position(conn, accts["auto"], day_id,
                           net=Decimal("9"), gross=Decimal("10"),
                           entry_fee=Decimal("1"), exit_fee=Decimal("0"),
                           funding=Decimal("0"), offset_minutes=200)
    second = await report_store.build_report_from_ledger(
        conn, owner_id=owner_id, session_id=sid,
        settlement_complete=True, reconciliation_ok=True,
    )
    assert Decimal(second.accounts["auto"].net_pnl) == Decimal("13")
    assert canonical_hash(second) != hash_first

    res2 = await report_store.save_report(conn, owner_id=owner_id, report=second)
    # the insert-only guard keeps the original; nothing is overwritten
    assert res2["stored"] is False
    assert res2["hash"] == hash_first

    row = await report_store.fetch_report(conn, session_id=sid, scope="session")
    assert row["report_hash"] == hash_first
    assert row["report"] == first.to_dict()
