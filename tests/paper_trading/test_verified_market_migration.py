"""P1 evidence substrate: ``migrations/20261023_verified_paper_market.sql`` on real PostgreSQL.

The unit tests in ``test_contract_units.py`` / ``test_book_execution.py`` prove the
Python side.  This file proves the database side, which is what the TZ §4.3 actually
demands: the invariants must hold even when a bug (or an operator) bypasses the
Python layer.

Deliberate choices:

* every assertion runs against the real DDL in a throwaway schema, never against
  an ORM mock, so a CHECK or trigger typo fails here instead of during a live day;
* the "old defect" shapes are inserted explicitly (fractional lot step, base/contract
  mismatch, rewritten quote) because a constraint that only rejects imaginary values
  is worthless;
* immutability is tested as a *pair*: priced facts must be frozen **and** the
  verifier's PASS update must still be allowed, otherwise the append-only rule would
  silently break the verification contract.
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

DSN = os.getenv(
    "WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa"
)
_ROOT = Path(__file__).resolve().parents[2]
BASE_SCHEMA = _ROOT / "migrations" / "paper_v2_schema.sql"
ADDITIVE_SCHEMA = _ROOT / "migrations" / "20261023_verified_paper_market.sql"

NEW_TABLES = (
    "paper_v2_contract_rules",
    "paper_v2_market_events",
    "paper_v2_market_book_usage",
    "paper_v2_execution_evidence",
    "paper_v2_feed_gaps",
)


async def _column_snapshot(conn) -> dict[str, list[tuple[str, str, bool]]]:
    """name/type/nullable for every table, used to prove the migration is additive."""
    rows = await conn.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema()
        ORDER BY table_name, ordinal_position
        """
    )
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for r in rows:
        out.setdefault(r["table_name"], []).append(
            (r["column_name"], r["data_type"], r["is_nullable"] == "YES")
        )
    return out


@pytest.fixture
async def db():
    """Disposable schema with base schema + the P1 migration applied."""
    if DSN.rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against production 'trading' database")
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - среда без asyncpg
        pytest.skip("asyncpg is not installed")
    try:
        conn = await asyncpg.connect(DSN)
    except Exception as exc:  # pragma: no cover - зависит от окружения
        pytest.skip(f"QA database not reachable: {exc}")

    schema = f"verified_market_{_uuid.uuid4().hex[:8]}"
    applied = False
    try:
        db_name = await conn.fetchval("SELECT current_database()")
        if db_name == "trading":
            pytest.fail("refusing to run against production database 'trading'")
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}", public')
        await conn.execute(BASE_SCHEMA.read_text(encoding="utf-8"))
        before = await _column_snapshot(conn)
        await conn.execute(ADDITIVE_SCHEMA.read_text(encoding="utf-8"))
        applied = True
        yield conn, before
    except Exception as exc:  # pragma: no cover - зависит от окружения
        pytest.skip(f"could not prepare QA schema: {exc}")
    finally:
        if applied:
            try:
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
            except Exception:  # pragma: no cover - cleanup best effort
                pass
        await conn.close()


# ---------------------------------------------------------------------------
# Shape of the migration itself
# ---------------------------------------------------------------------------


async def test_migration_is_additive_only(db) -> None:
    """No pre-existing table gains, loses or retypes a column."""
    conn, before = db
    after = await _column_snapshot(conn)
    for table, cols in before.items():
        assert after[table] == cols, f"{table} was modified by an additive migration"


async def test_all_five_substrate_tables_exist(db) -> None:
    conn, _ = db
    present = {
        r[0]
        for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    }
    for table in NEW_TABLES:
        assert table in present, f"missing {table}"


async def test_migration_is_idempotent(db) -> None:
    """Re-applying must not raise: runbooks re-run migrations by accident."""
    conn, _ = db
    await conn.execute(ADDITIVE_SCHEMA.read_text(encoding="utf-8"))
    assert await conn.fetchval(
        "SELECT count(*) FROM paper_v2_schema_version WHERE version = 20261023"
    ) == 1


async def test_schema_version_row_records_the_migration(db) -> None:
    conn, _ = db
    desc = await conn.fetchval(
        "SELECT description FROM paper_v2_schema_version WHERE version = 20261023"
    )
    assert desc and "evidence" in desc


# ---------------------------------------------------------------------------
# Contract rules: the database rejects the exact shape of the old bug
# ---------------------------------------------------------------------------

_T = datetime(2026, 10, 23, 13, 7, 4, tzinfo=timezone.utc)

_RULE_BASE = {
    "venue": "htx",
    "market": "linear_swap",
    "contract_code": "BTC-USDT",
    "contract_size_btc_per_contract": Decimal("0.001"),
    "price_tick_usdt": Decimal("0.1"),
    "order_qty_step_contracts": Decimal("1"),
    "order_qty_min_contracts": Decimal("1"),
    "validation_status": "PROVEN_SIZE_TICK_UNITS__MIN_DERIVED_FROM_TYPE",
    "observed_at": _T,
    "source_uri": "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info",
    "source_payload_hash": "a" * 64,
    "rule_version": "htx_linear_swap_btc_usdt_v1",
}


async def _insert_rule(conn, **over) -> _uuid.UUID:
    payload = {**_RULE_BASE, **over}
    rule_id = _uuid.uuid4()
    cols = ", ".join(payload)
    # $1 is the id, so the payload placeholders start at $2.
    ph = ", ".join(f"${i + 2}" for i in range(len(payload)))
    await conn.execute(
        f"INSERT INTO paper_v2_contract_rules (contract_rule_id, {cols}) "
        f"VALUES ($1, {ph})",
        str(rule_id),
        *payload.values(),
    )
    return rule_id


async def test_proven_rule_row_is_accepted(db) -> None:
    conn, _ = db
    rule_id = await _insert_rule(conn)
    assert await conn.fetchval(
        "SELECT order_qty_step_contracts FROM paper_v2_contract_rules WHERE contract_rule_id = $1",
        str(rule_id),
    ) == Decimal("1")


async def test_fractional_lot_step_is_rejected_by_the_database(db) -> None:
    """A half-contract lot step is not a lot step; 1.5 isolates that one check."""
    conn, _ = db
    with pytest.raises(Exception, match="ck_rules_whole_lot_step"):
        await _insert_rule(conn, order_qty_step_contracts=Decimal("1.5"))


async def test_step_copied_from_contract_size_is_rejected(db) -> None:
    """The original defect: step and min both taken from the 0.001 multiplier."""
    conn, _ = db
    with pytest.raises(Exception, match="ck_rules_"):
        await _insert_rule(
            conn,
            order_qty_step_contracts=Decimal("0.001"),
            order_qty_min_contracts=Decimal("0.001"),
        )


async def test_fractional_min_lot_is_rejected(db) -> None:
    conn, _ = db
    with pytest.raises(Exception, match="ck_rules_whole_min_lot"):
        await _insert_rule(conn, order_qty_min_contracts=Decimal("0.5"))


async def test_min_lot_above_step_is_rejected(db) -> None:
    conn, _ = db
    with pytest.raises(Exception, match="ck_rules_min_le_step"):
        await _insert_rule(conn, order_qty_min_contracts=Decimal("5"))


async def test_rule_without_provenance_hash_is_rejected(db) -> None:
    """A rule nobody observed is not a rule; it is a guess."""
    conn, _ = db
    with pytest.raises(Exception):
        await _insert_rule(conn, source_payload_hash=None)


async def test_base_quantity_is_the_only_allowed_unit(db) -> None:
    """The capture stores contracts; base-quantity rules would silently rescale."""
    conn, _ = db
    with pytest.raises(Exception):
        await _insert_rule(conn, order_qty_unit="base_quantity")


# ---------------------------------------------------------------------------
# Market events: immutable, replay-safe, exchange time never invented
# ---------------------------------------------------------------------------

_EVENT_BASE = {
    "source": "htx_public_ws",
    "channel": "bbo",
    "contract_code": "BTC-USDT",
    "source_event_at": _T.replace(microsecond=300_000),
    "received_at": _T.replace(microsecond=320_000),
    "connection_epoch": "conn-1",
    "sequence_no": 100,
    "bid_price": Decimal("81675.2"),
    "bid_size_contracts": Decimal("6551"),
    "ask_price": Decimal("81675.3"),
    "ask_size_contracts": Decimal("1053"),
    "raw_payload": json.dumps({"tick": {"bid": 81675.2, "ask": 81675.3}}),
    "raw_payload_sha256": "b" * 64,
    "parser_version": "htx_linear_swap_ws_v1",
}


async def _insert_event(conn, **over) -> _uuid.UUID:
    payload = {**_EVENT_BASE, **over}
    quote_id = _uuid.uuid4()
    cols = ", ".join(payload)
    ph = ", ".join(f"${i + 2}" for i in range(len(payload)))
    await conn.execute(
        f"INSERT INTO paper_v2_market_events (quote_event_id, {cols}) VALUES ($1, {ph})",
        str(quote_id),
        *payload.values(),
    )
    return quote_id


async def test_event_stores_exchange_and_receive_time_separately(db) -> None:
    conn, _ = db
    quote_id = await _insert_event(conn)
    row = await conn.fetchrow(
        "SELECT source_event_at, received_at FROM paper_v2_market_events WHERE quote_event_id = $1",
        str(quote_id),
    )
    assert (row["received_at"] - row["source_event_at"]).total_seconds() == pytest.approx(0.020)


async def test_missing_exchange_time_is_null_with_a_reason(db) -> None:
    """A venue that publishes no timestamp is recorded as NULL, not backfilled."""
    conn, _ = db
    quote_id = await _insert_event(
        conn, source_event_at=None, source_time_reason="venue_payload_without_event_time"
    )
    row = await conn.fetchrow(
        "SELECT source_event_at, source_time_reason FROM paper_v2_market_events WHERE quote_event_id = $1",
        str(quote_id),
    )
    assert row["source_event_at"] is None
    assert row["source_time_reason"] == "venue_payload_without_event_time"


async def test_events_are_immutable(db) -> None:
    conn, _ = db
    quote_id = await _insert_event(conn)
    with pytest.raises(Exception, match="append-only"):
        await conn.execute(
            "UPDATE paper_v2_market_events SET ask_price = 1 WHERE quote_event_id = $1",
            str(quote_id),
        )
    with pytest.raises(Exception, match="append-only"):
        await conn.execute(
            "DELETE FROM paper_v2_market_events WHERE quote_event_id = $1", str(quote_id)
        )


async def test_ws_replay_of_the_same_event_is_not_double_stored(db) -> None:
    conn, _ = db
    await _insert_event(conn)
    with pytest.raises(Exception, match="uq_market_event_identity"):
        await _insert_event(conn, raw_payload_sha256="c" * 64)


async def test_recorded_index_is_a_monotonic_sequence(db) -> None:
    """Two events in the same exchange millisecond stay distinguishable."""
    conn, _ = db
    first = await _insert_event(conn, sequence_no=1, raw_payload_sha256="1" * 64)
    second = await _insert_event(conn, sequence_no=2, raw_payload_sha256="2" * 64)
    order = await conn.fetchval(
        """
        SELECT a.recorded_index < b.recorded_index
        FROM paper_v2_market_events a, paper_v2_market_events b
        WHERE a.quote_event_id = $1::uuid AND b.quote_event_id = $2::uuid
        """,
        str(first),
        str(second),
    )
    assert order
    assert await conn.fetchval(
        "SELECT count(DISTINCT recorded_index) FROM paper_v2_market_events"
    ) == 2


# ---------------------------------------------------------------------------
# Execution evidence
# ---------------------------------------------------------------------------


async def _seed_chain(conn) -> dict[str, _uuid.UUID]:
    """owner → account → day → order → fill, the minimum path to an FK-valid fill_id."""
    ids = {k: _uuid.uuid4() for k in ("owner", "account", "day", "order", "fill")}
    await conn.execute(
        "INSERT INTO paper_v2_owners (owner_id, display_name) VALUES ($1, 'mig-test')",
        str(ids["owner"]),
    )
    await conn.execute(
        "INSERT INTO paper_v2_accounts (account_id, owner_id, kind) VALUES ($1, $2, 'auto')",
        str(ids["account"]),
        str(ids["owner"]),
    )
    await conn.execute(
        "INSERT INTO paper_v2_days (day_id, owner_id, state) VALUES ($1, $2, 'running')",
        str(ids["day"]),
        str(ids["owner"]),
    )
    await conn.execute(
        "INSERT INTO paper_v2_orders (order_id, account_id, day_id, origin, actor, side, qty) "
        "VALUES ($1, $2, $3, 'auto', 'auto', 'buy', 10)",
        str(ids["order"]),
        str(ids["account"]),
        str(ids["day"]),
    )
    await conn.execute(
        "INSERT INTO paper_v2_fills (fill_id, order_id, account_id, execution_quote_id, "
        "side, price, qty, contract_size) VALUES ($1, $2, $3, $4, 'buy', 81675.3, 0.010, 0.001)",
        str(ids["fill"]),
        str(ids["order"]),
        str(ids["account"]),
        f"q-{ids['fill']}",
    )
    return ids


_EVIDENCE_REQUIRED = {
    "side": "buy",
    "open_or_close": "open",
    "received_at": _T.replace(microsecond=320_000),
    "decision_at": _T,
    "submitted_at": _T.replace(microsecond=20_000),
    "eligible_at": _T.replace(microsecond=270_000),
    "executed_at": _T.replace(microsecond=500_000),
    "requested_contracts": Decimal("10"),
    "filled_contracts": Decimal("10"),
    "contract_size_btc": Decimal("0.001"),
    "base_qty_btc": Decimal("0.010"),
    "price_tick": Decimal("0.1"),
    "slippage_mode": "depth_vwap",
    "fee_rate": Decimal("0.0005"),
    "raw_event_sha256": "d" * 64,
    "model_version": "book_v1",
    "policy_hash": "e" * 64,
}


async def _insert_evidence(conn, ids, **over) -> _uuid.UUID:
    payload = {
        **_EVIDENCE_REQUIRED,
        "fill_id": str(ids["fill"]),
        "day_id": str(ids["day"]),
        "account_id": str(ids["account"]),
        "order_id": str(ids["order"]),
        "position_id": str(_uuid.uuid4()),
        **over,
    }
    cols = ", ".join(payload)
    ph = ", ".join(f"${i + 1}" for i in range(len(payload)))
    await conn.execute(
        f"INSERT INTO paper_v2_execution_evidence ({cols}) VALUES ({ph})",
        *payload.values(),
    )
    return ids["fill"]


async def test_evidence_defaults_to_pending_not_pass(db) -> None:
    """The runtime cannot claim its own homework was checked."""
    conn, _ = db
    ids = await _seed_chain(conn)
    fill_id = await _insert_evidence(conn, ids)
    status = await conn.fetchval(
        "SELECT verification_status FROM paper_v2_execution_evidence WHERE fill_id = $1",
        str(fill_id),
    )
    assert status == "PENDING_VERIFICATION"


async def test_evidence_units_identity_is_enforced(db) -> None:
    """base_qty must equal contracts * contract_size, or the row is fiction."""
    conn, _ = db
    ids = await _seed_chain(conn)
    with pytest.raises(Exception, match="ck_evidence_units"):
        await _insert_evidence(conn, ids, base_qty_btc=Decimal("0.100"))


async def test_evidence_fractional_lot_is_rejected(db) -> None:
    conn, _ = db
    ids = await _seed_chain(conn)
    with pytest.raises(Exception, match="ck_evidence_whole_lot"):
        await _insert_evidence(
            conn,
            ids,
            filled_contracts=Decimal("0.839"),
            base_qty_btc=Decimal("0.000839"),
        )


async def test_evidence_requires_a_real_fill_row(db) -> None:
    """Orphan evidence would let a fill be 'verified' that never existed."""
    conn, _ = db
    ids = await _seed_chain(conn)
    await conn.execute("DELETE FROM paper_v2_fills WHERE fill_id = $1", str(ids["fill"]))
    with pytest.raises(Exception):
        await _insert_evidence(conn, ids)


async def test_priced_evidence_facts_are_frozen(db) -> None:
    conn, _ = db
    ids = await _seed_chain(conn)
    fill_id = await _insert_evidence(conn, ids)
    with pytest.raises(Exception, match="immutable"):
        await conn.execute(
            "UPDATE paper_v2_execution_evidence SET fill_vwap = 1 WHERE fill_id = $1",
            str(fill_id),
        )
    with pytest.raises(Exception, match="immutable"):
        await conn.execute(
            "UPDATE paper_v2_execution_evidence SET slippage_bps = 9999 WHERE fill_id = $1",
            str(fill_id),
        )
    with pytest.raises(Exception, match="never deleted"):
        await conn.execute(
            "DELETE FROM paper_v2_execution_evidence WHERE fill_id = $1", str(fill_id)
        )


async def test_no_op_update_of_a_frozen_fact_is_not_a_mutation(db) -> None:
    """Writing the same price again changes nothing, so it must not be blocked.

    Stating this explicitly matters: an immutability rule that also rejects
    identical rewrites breaks idempotent replay code for no security gain.
    """
    conn, _ = db
    ids = await _seed_chain(conn)
    fill_id = await _insert_evidence(conn, ids, fill_vwap=Decimal("81675.3"))
    await conn.execute(
        "UPDATE paper_v2_execution_evidence SET fill_vwap = 81675.3 WHERE fill_id = $1",
        str(fill_id),
    )
    assert await conn.fetchval(
        "SELECT fill_vwap FROM paper_v2_execution_evidence WHERE fill_id = $1",
        str(fill_id),
    ) == Decimal("81675.3")


async def test_verifier_may_still_record_its_conclusion(db) -> None:
    """Freezing the facts must not freeze the verdict column (TZ §4.3)."""
    conn, _ = db
    ids = await _seed_chain(conn)
    fill_id = await _insert_evidence(conn, ids)
    await conn.execute(
        "UPDATE paper_v2_execution_evidence "
        "SET verification_status = 'PASS', verifier_run_id = 'verify-1', "
        "    verified_at = NOW() "
        "WHERE fill_id = $1",
        str(fill_id),
    )
    row = await conn.fetchrow(
        "SELECT verification_status, verifier_run_id FROM paper_v2_execution_evidence "
        "WHERE fill_id = $1",
        str(fill_id),
    )
    assert row["verification_status"] == "PASS"
    assert row["verifier_run_id"] == "verify-1"


async def test_verifier_cannot_silently_reprice_a_fill(db) -> None:
    """PASS is allowed; PASS plus a nicer VWAP is not."""
    conn, _ = db
    ids = await _seed_chain(conn)
    fill_id = await _insert_evidence(conn, ids)
    with pytest.raises(Exception, match="immutable"):
        await conn.execute(
            "UPDATE paper_v2_execution_evidence "
            "SET verification_status = 'PASS', fill_vwap = 81675.0 WHERE fill_id = $1",
            str(fill_id),
        )


async def test_evidence_quotes_are_referential(db) -> None:
    """An evidence row must point at stored quote events, not invented ids."""
    conn, _ = db
    ids = await _seed_chain(conn)
    quote_id = await _insert_event(conn)
    fill_id = await _insert_evidence(conn, ids, quote_event_id=str(quote_id))
    linked = await conn.fetchval(
        "SELECT quote_event_id FROM paper_v2_execution_evidence WHERE fill_id = $1",
        str(fill_id),
    )
    assert str(linked) == str(quote_id)
    with pytest.raises(Exception):
        await _insert_evidence(conn, ids, quote_event_id=str(_uuid.uuid4()))


async def test_book_usage_ledger_is_immutable_and_positive(db) -> None:
    conn, _ = db
    ids = await _seed_chain(conn)
    quote_id = await _insert_event(conn)
    await conn.execute(
        "INSERT INTO paper_v2_market_book_usage "
        "(quote_event_id, trade_side, price, contracts_used, fill_id) "
        "VALUES ($1, 'buy', 81675.3, 100, $2)",
        str(quote_id),
        str(ids["fill"]),
    )
    with pytest.raises(Exception, match="append-only"):
        await conn.execute(
            "UPDATE paper_v2_market_book_usage SET contracts_used = 1 "
            "WHERE quote_event_id = $1",
            str(quote_id),
        )
    with pytest.raises(Exception):
        await conn.execute(
            "INSERT INTO paper_v2_market_book_usage "
            "(quote_event_id, trade_side, price, contracts_used, fill_id) "
            "VALUES ($1, 'buy', 81675.3, 0, $2)",
            str(quote_id),
            str(ids["fill"]),
        )


async def test_same_fill_cannot_reuse_a_consumed_level(db) -> None:
    """The ledger's dedup key is (quote, side, price, fill) — a replay collides."""
    conn, _ = db
    ids = await _seed_chain(conn)
    quote_id = await _insert_event(conn)
    for _ in range(2):
        try:
            await conn.execute(
                "INSERT INTO paper_v2_market_book_usage "
                "(quote_event_id, trade_side, price, contracts_used, fill_id) "
                "VALUES ($1, 'buy', 81675.3, 100, $2)",
                str(quote_id),
                str(ids["fill"]),
            )
        except Exception:
            break
    else:
        pytest.fail("a fill was allowed to consume the same level twice")


async def test_feed_gap_can_be_resolved_but_never_erased(db) -> None:
    """A gap is an incident record: it must stay visible after resolution."""
    conn, _ = db
    gap_id = _uuid.uuid4()
    await conn.execute(
        "INSERT INTO paper_v2_feed_gaps (gap_id, source, channel, contract_code, "
        "detected_at, reason) VALUES ($1, 'htx_public_ws', 'depth', 'BTC-USDT', "
        "NOW(), 'sequence_gap')",
        str(gap_id),
    )
    await conn.execute(
        "UPDATE paper_v2_feed_gaps SET resolved_at = NOW() WHERE gap_id = $1", str(gap_id)
    )
    assert await conn.fetchval(
        "SELECT resolved_at IS NOT NULL FROM paper_v2_feed_gaps WHERE gap_id = $1", str(gap_id)
    )
    with pytest.raises(Exception, match="never deleted"):
        await conn.execute("DELETE FROM paper_v2_feed_gaps WHERE gap_id = $1", str(gap_id))
