"""paper_trading.report_store — derive and persist immutable V3 reports (P6.2).

Two responsibilities, kept separate from the pure model in
:mod:`paper_trading.session_report`:

  1. **Read side (no fabrication).** A report is built *from the ledger*: the
     closed/liquidated rows of ``paper_v2_positions`` for each of an owner's
     accounts become :class:`Trade` records, and the P6.1 builder aggregates
     them. Nothing here invents a number — every monetary field is a column the
     engine already wrote, parsed as ``Decimal`` straight from Postgres.

  2. **Persistence side (immutability, MC-19).** ``simulation_reports`` rows are
     insert-only. We never ``UPDATE``; ``save_report`` uses ``ON CONFLICT DO
     NOTHING`` so re-finalising a slot keeps the *first* frozen bytes rather
     than mutating history, and stores a canonical sha256 so a later read can
     prove the snapshot is byte-for-byte the one that was saved.

This module talks to an :mod:`asyncpg` connection directly (the same raw-SQL
style as :mod:`paper_trading.repository`) and is only exercised against a real
database by the P6.2 golden tests; the pure model above is what the host suite
covers. Python 3.9 compatible.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from paper_trading.session_report import (
    SCHEMA_VERSION,
    AccountReportV1,
    SessionReportV1,
    Trade,
    build_account_report,
    build_session_report,
    canonical_hash,
)

__all__ = [
    "load_account_trades",
    "load_owner_accounts",
    "build_report_from_ledger",
    "save_report",
    "fetch_report",
    "fetch_reports_for_session",
]


# ---------------------------------------------------------------------------
# Read side: ledger → Trade
# ---------------------------------------------------------------------------

# Only terminal positions carry a recorded realized result; open rows are
# deliberately excluded so a report is never computed on an unrealized number.
_ACCOUNT_TRADES_SQL = """
SELECT position_id::text               AS position_id,
       realized_net_pnl,
       realized_gross_pnl,
       entry_fee,
       exit_fee,
       funding_cashflow,
       (status = 'liquidated')         AS is_liquidated,
       opened_at,
       closed_at,
       isolated_margin,
       avg_entry_price,
       qty
FROM paper_v2_positions
WHERE account_id = $1::uuid
  AND status IN ('closed', 'liquidated')
ORDER BY closed_at ASC NULLS LAST, opened_at ASC
"""

_OWNER_ACCOUNTS_SQL = """
SELECT account_id::text AS account_id,
       kind,
       currency,
       opening_deposit
FROM paper_v2_accounts
WHERE owner_id = $1::uuid
ORDER BY kind
"""


def _row_to_trade(row: Any) -> Trade:
    """Map a ledger position row onto a :class:`Trade`.

    ``planned_risk`` is not a stored column, so it is passed as ``None``: the
    report will honestly omit an R-based expectancy rather than assume a risk.
    Notional is the recorded entry exposure (``avg_entry_price * qty``), a
    derived-from-ledgers product, not a fresh number.
    """
    qty = row["qty"]
    entry = row["avg_entry_price"]
    notional = None
    if isinstance(qty, Decimal) and isinstance(entry, Decimal):
        notional = qty * entry
    return Trade(
        position_id=str(row["position_id"]),
        realized_net=row["realized_net_pnl"],
        realized_gross=row["realized_gross_pnl"],
        entry_fee=row["entry_fee"],
        exit_fee=row["exit_fee"],
        funding_cashflow=row["funding_cashflow"],
        is_liquidated=bool(row["is_liquidated"]),
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        planned_risk=None,
        notional=notional,
    )


async def load_account_trades(conn, account_id) -> list[Trade]:
    """Return every terminal (closed/liquidated) position of an account as a Trade."""
    rows = await conn.fetch(_ACCOUNT_TRADES_SQL, str(account_id))
    return [_row_to_trade(r) for r in rows]


async def load_owner_accounts(conn, owner_id) -> list[dict[str, Any]]:
    """Return an owner's accounts (id / kind / currency / opening budget)."""
    rows = await conn.fetch(_OWNER_ACCOUNTS_SQL, str(owner_id))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Build a report straight from the ledger
# ---------------------------------------------------------------------------


async def build_report_from_ledger(
    conn,
    *,
    owner_id,
    session_id: str,
    day_id: str = "",
    instrument_key: str = "BTC-USDT",
    as_of: datetime | None = None,
    settlement_complete: bool,
    reconciliation_ok: bool,
    open_positions: int | None = None,
    plan_hash: str | None = None,
    replay_data_hash: str | None = None,
    rejected_entries: int = 0,
    min_trades: int = 10,
) -> SessionReportV1:
    """Construct a :class:`SessionReportV1` from ``paper_v2_*`` rows only.

    If ``open_positions`` is not supplied we count the account's still-open rows
    from the ledger so ``is_final`` reflects the real position set (MC-18: the
    report becomes final only once nothing is open *and* settlement/reconciliation
    have passed).  ``owner_id`` drives which accounts are read; it is not a field
    of the report model itself (the model keys on ``session_id``), so it stays a
    query argument rather than leaking into the snapshot.
    """
    accounts = await load_owner_accounts(conn, owner_id)
    built: dict[str, AccountReportV1] = {}
    counted_open = 0

    for acct in accounts:
        trades = await load_account_trades(conn, acct["account_id"])
        built[str(acct["kind"])] = build_account_report(
            kind=str(acct["kind"]),
            trades=trades,
            initial_budget=acct["opening_deposit"],
            currency=str(acct["currency"]),
            slippage_included=True,
            rejected_entries=rejected_entries,
            min_trades=min_trades,
        )
        if open_positions is None:
            counted_open += await conn.fetchval(
                "SELECT count(*) FROM paper_v2_positions "
                "WHERE account_id = $1::uuid AND status = 'open'",
                str(acct["account_id"]),
            )

    if open_positions is None:
        open_positions = int(counted_open)
    if as_of is None:
        as_of = datetime.now(timezone.utc)

    return build_session_report(
        session_id=session_id,
        day_id=day_id,
        instrument_key=instrument_key,
        as_of=as_of,
        accounts=built,
        settlement_complete=settlement_complete,
        reconciliation_ok=reconciliation_ok,
        open_positions=open_positions,
        plan_hash=plan_hash,
        replay_data_hash=replay_data_hash,
    )


# ---------------------------------------------------------------------------
# Persistence: insert-only, canonical hash
# ---------------------------------------------------------------------------

async def save_report(
    conn,
    *,
    owner_id: str,
    report: SessionReportV1,
    scope: str = "session",
    day_id: str | None = None,
    engine_version: str | None = None,
    simulator_version: str | None = None,
    report_id: UUID | None = None,
) -> dict[str, Any]:
    """Insert one frozen report snapshot; never mutate an existing slot.

    Returns ``{"report_id", "hash", "stored"}``. ``stored`` is ``False`` when the
    slot already held a report — the original bytes stay authoritative (MC-19),
    and the hash of the *stored* row is what we hand back after re-reading it.
    ``engine_version`` / ``simulator_version`` are DB-level provenance columns
    (the report model itself does not carry them), so they are explicit args.
    """
    if scope not in ("day", "session"):
        raise ValueError(f"scope must be 'day' or 'session', got {scope!r}")
    payload = report.to_dict()
    digest = canonical_hash(report)
    rid = str(report_id or uuid4())
    slot_day = day_id if day_id is not None else (report.day_id or None)
    result = await conn.execute(
        _insert_sql(),
        rid,
        str(owner_id),
        str(report.session_id),
        slot_day,
        scope,
        json.dumps(payload, sort_keys=True, default=str),
        digest,
        bool(report.is_final),
        int(SCHEMA_VERSION),
        engine_version,
        simulator_version,
        report.plan_hash,
        report.replay_data_hash,
    )
    inserted = result.endswith("1")
    row = await fetch_report(
        conn, session_id=str(report.session_id), scope=scope, day_id=slot_day
    )
    # The authoritative hash is whatever is now in the slot (the first insert
    # wins under ON CONFLICT DO NOTHING), so a refused re-save still reports the
    # original, unchanged digest.
    return {
        "report_id": row["id"] if row else rid,
        "hash": row["report_hash"] if row else digest,
        "stored": inserted,
    }


def _insert_sql() -> str:
    """INSERT with the slot conflict resolved via the unique index name.

    The guard is the ``uq_report_slot`` index, so the ON CONFLICT target is that
    index expression rather than a named constraint.
    """
    return """
INSERT INTO simulation_reports (
    id, owner_id, session_id, day_id, scope, report, report_hash, is_final,
    schema_version, engine_version, simulator_version, plan_hash, replay_data_hash
)
VALUES (
    $1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13
)
ON CONFLICT (session_id, scope, COALESCE(day_id, ''), schema_version) DO NOTHING
"""


_FETCH_REPORT_SQL = """
SELECT id::text AS id, owner_id, session_id, day_id, scope, report, report_hash,
       is_final, schema_version, engine_version, simulator_version, plan_hash,
       replay_data_hash, created_at
FROM simulation_reports
WHERE session_id = $1 AND scope = $2
  AND COALESCE(day_id, '') = COALESCE($3, '')
ORDER BY created_at ASC
LIMIT 1
"""

_FETCH_SESSION_SQL = """
SELECT id::text AS id, owner_id, session_id, day_id, scope, report, report_hash,
       is_final, schema_version, created_at
FROM simulation_reports
WHERE session_id = $1
ORDER BY scope, day_id NULLS FIRST, created_at
"""


def _decode_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("report"), str):
        d["report"] = json.loads(d["report"])
    return d


async def fetch_report(
    conn,
    *,
    session_id: str,
    scope: str = "session",
    day_id: str | None = None,
) -> dict[str, Any] | None:
    """Read one stored snapshot (report JSON decoded) or ``None`` if absent."""
    row = await conn.fetchrow(_FETCH_REPORT_SQL, str(session_id), scope, day_id)
    return _decode_row(row) if row else None


async def fetch_reports_for_session(conn, session_id: str) -> list[dict[str, Any]]:
    """List every snapshot materialised for a session (day + session scope)."""
    rows = await conn.fetch(_FETCH_SESSION_SQL, str(session_id))
    return [_decode_row(r) for r in rows]
