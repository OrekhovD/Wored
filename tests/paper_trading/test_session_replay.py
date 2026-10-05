"""P6.4-C — executable multi-window replay (MC-21) + ledger bridge on PG.

MC-21 asks that a replay of several windows is *reproducible* by data hash,
keeps **train** out of the **holdout** verdict and compares against no-trade /
buy-hold with the **same costs**. The host half below runs
:mod:`paper_trading.replay` (which drives the real ``execution`` engine) with
fixed synthetic bars — no clock, no RNG, no network — and asserts those
properties. The PG half is opt-in via ``WORED_TEST_DATABASE_URL`` and skips
cleanly otherwise (same contract as the P6.2 report suite); it replays windows,
writes the realised window nets into ``paper_v2_positions`` and rebuilds the
report via ``report_store`` to prove the replay → ledger → report chain keeps
identical numbers.

Money comparisons are Decimal (NUMERIC(20,8) comes back scaled).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from paper_trading.replay import ReplayWindow, replay, run_window
from paper_trading.strategy import Bar

# ---------------------------------------------------------------------------
# bar helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _ts(minute: int) -> str:
    return (_T0 + timedelta(minutes=minute)).isoformat()


def _bar(minute: int, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(
        timestamp=_ts(minute),
        open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c),
    )


def _window(window_id: str, split: str, bars: list[Bar]):
    return ReplayWindow(window_id=window_id, split=split, bars=bars)


# rising window: bullish closes, wide enough range to keep the run going
_RISING = [
    _bar(0, "9990", "10010", "9980", "10000"),
    _bar(1, "10010", "10030", "10000", "10020"),
    _bar(2, "10030", "10050", "10020", "10040"),
    _bar(3, "10050", "10070", "10040", "10060"),
]
# bearish, flat endpoint window: every close < open → the probe never enters,
# yet first and last close are equal, so any buy-hold net is pure cost.
_FLAT_BEARISH = [
    _bar(0, "10010", "10010", "9990", "10000"),
    _bar(1, "10010", "10010", "9990", "10000"),
    _bar(2, "10010", "10010", "9990", "10000"),
]
# second, disjoint window on a later clock block
_RISING_2 = [
    _bar(10, "10090", "10110", "10080", "10100"),
    _bar(11, "10110", "10130", "10100", "10120"),
    _bar(12, "10130", "10150", "10120", "10140"),
]
# same shapes as above on further clock blocks (windows must not overlap)
_FLAT_BEARISH_B = [
    _bar(10, "10010", "10010", "9990", "10000"),
    _bar(11, "10010", "10010", "9990", "10000"),
    _bar(12, "10010", "10010", "9990", "10000"),
]
_RISING_B = [
    _bar(30, "9990", "10010", "9980", "10000"),
    _bar(31, "10010", "10030", "10000", "10020"),
    _bar(32, "10030", "10050", "10020", "10040"),
    _bar(33, "10050", "10070", "10040", "10060"),
]

_BUDGET = Decimal("10000")


# ---------------------------------------------------------------------------
# MC-21 — reproducibility
# ---------------------------------------------------------------------------


def test_replay_is_reproducible_identical_runs_match():
    wins = [
        _window("w1", "train", _RISING),
        _window("w2", "holdout", _RISING_2),
    ]
    r1 = replay(wins, budget=_BUDGET)
    r2 = replay(wins, budget=_BUDGET)
    assert r1.data_hash == r2.data_hash
    assert r1.data_hash.startswith("sha256:")
    assert [o.net_pnl for o in r1.outcomes] == [o.net_pnl for o in r2.outcomes]
    assert r1.to_dict() == r2.to_dict()


def test_replay_perturbed_bar_changes_data_hash():
    base = [_window("w1", "holdout", _RISING)]
    perturbed_bar = list(_RISING)
    perturbed_bar[2] = _bar(2, "10030", "10050", "10020", "10041")  # one tick
    perturbed = [_window("w1", "holdout", perturbed_bar)]
    assert replay(base, budget=_BUDGET).data_hash != replay(
        perturbed, budget=_BUDGET
    ).data_hash


# ---------------------------------------------------------------------------
# MC-21 — non-overlap enforcement
# ---------------------------------------------------------------------------


def test_replay_rejects_overlapping_windows():
    # w1 spans minutes 0..3, w2 intrudes at minute 2 — bars would be double-counted
    overlapping = [
        _window("w1", "train", _RISING),
        _window("w2", "holdout", [_bar(2, "10090", "10110", "10080", "10100"),
                                  _bar(5, "10110", "10130", "10100", "10120")]),
    ]
    with pytest.raises(ValueError, match="overlap"):
        replay(overlapping, budget=_BUDGET)


def test_adjacent_windows_are_allowed():
    wins = [
        _window("w1", "train", _RISING),       # ends at minute 3 bar
        _window("w2", "holdout", _RISING_2),    # starts at minute 10 bar
    ]
    r = replay(wins, budget=_BUDGET)
    assert r.windows == 2


# ---------------------------------------------------------------------------
# MC-21 — train stays out of the holdout verdict
# ---------------------------------------------------------------------------


def test_train_and_holdout_are_not_mixed():
    wins = [
        _window("w1", "train", _FLAT_BEARISH),     # never trades → net 0
        _window("w2", "train", _FLAT_BEARISH_B),
        _window("w3", "holdout", _RISING_B),
    ]
    r = replay(wins, budget=_BUDGET)
    assert r.train_windows == 2
    assert r.holdout_windows == 1
    # train windows are both flat-bearish: their net sum is exactly zero
    assert Decimal(r.train["sum"]) == Decimal("0")
    # the holdout verdict must not fold those zeros in as a second sample
    assert r.holdout["count"] == 1
    assert Decimal(r.holdout["sum"]) == r.outcomes[2].net_pnl


def test_single_holdout_reports_na_not_point_estimate():
    wins = [
        _window("w1", "train", _RISING),
        _window("w2", "holdout", _RISING_2),
    ]
    r = replay(wins, budget=_BUDGET)
    assert r.holdout["stdev"] is None
    assert r.holdout["reasons"]["stdev"] == "insufficient_sample"
    assert r.benchmarks["windows"] == 1
    assert r.benchmarks["reasons"]["delta"] == "insufficient_sample"


# ---------------------------------------------------------------------------
# MC-21 — same-cost benchmarks and real engine P&L
# ---------------------------------------------------------------------------


def test_flat_window_no_entry_and_buyhold_pays_costs():
    w = _window("w1", "holdout", _FLAT_BEARISH)
    oc = run_window(w, budget=_BUDGET)
    assert oc.net_pnl == Decimal(0)          # never entered → no-trade line
    # flat prices, but buy-hold still pays fee + slippage on both legs
    assert oc.buy_hold_net < Decimal(0)


def test_single_entry_window_matches_engine_golden():
    # open on the first bullish close, force-close at the last bar; every
    # number below is what execution.execute_market_order/execute_close book.
    bars = [
        _bar(0, "9990", "10000", "9980", "10000"),   # bullish → entry at 10000
        _bar(1, "10050", "10100", "10000", "10100"),  # inside stop/tp, session end
    ]
    oc = run_window(_window("w1", "holdout", bars), budget=_BUDGET)
    # entry fill 10000*1.0002=10002; entry fee 10002*0.0005=5.001
    # exit  10100*0.9998=10097.98; close fee 10097.98*0.0005=5.04899
    # net   (10097.98-10002) - 5.04899 - 5.001 = 85.93001
    assert oc.net_pnl == Decimal("85.93001")
    assert oc.data_hash.startswith("sha256:")


def test_no_trade_mean_is_zero_explicitly():
    wins = [
        _window("w1", "holdout", _FLAT_BEARISH),
        _window("w2", "holdout", _FLAT_BEARISH_B),
    ]
    r = replay(wins, budget=_BUDGET)
    assert Decimal(r.benchmarks["no_trade_mean"]) == Decimal("0")
    # with no entries the strategy also nets exactly zero on both windows
    assert Decimal(r.holdout["sum"]) == Decimal("0")


# ---------------------------------------------------------------------------
# PG bridge (opt-in): replay → ledger rows → report keeps identical numbers
# ---------------------------------------------------------------------------

DSN = os.getenv("WORED_TEST_DATABASE_URL", "")
_ROOT = Path(__file__).resolve().parents[2]
LEDGER_SCHEMA = _ROOT / "migrations" / "paper_v2_schema.sql"
INSTRUMENT = "BTC-USDT"


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
    schema = f"replay_v3_{uuid4().hex[:8]}"

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
    pool = await asyncpg.create_pool(
        DSN, min_size=1, max_size=2, server_settings={"search_path": qa_schema}
    )
    try:
        async with pool.acquire() as c:
            await c.execute(
                "TRUNCATE paper_v2_postings, paper_v2_fills, paper_v2_positions, "
                "paper_v2_orders, paper_v2_signals, paper_v2_commands, "
                "paper_v2_days, paper_v2_accounts, paper_v2_owners CASCADE"
            )
            yield c
    finally:
        await pool.close()


async def test_replay_ledger_report_bridge(conn):
    """Replayed window nets written to the ledger rebuild an identical report net."""
    from paper_trading import report_store

    owner_id, day_id = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO paper_v2_owners (owner_id, display_name, webui_identity) "
        "VALUES ($1, 'p64-replay', $2)",
        owner_id, f"p64-{owner_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO paper_v2_days (day_id, owner_id, state) VALUES ($1, $2, 'closed')",
        day_id, owner_id,
    )
    account_id = uuid4()
    await conn.execute(
        "INSERT INTO paper_v2_accounts (account_id, owner_id, kind, currency, "
        "opening_deposit) VALUES ($1, $2, 'manual', 'USDT', $3)",
        account_id, owner_id, Decimal("10000"),
    )

    wins = [
        _window("w1", "holdout", _RISING),
        _window("w2", "holdout", _RISING_2),
    ]
    result = replay(wins, budget=_BUDGET)

    total = Decimal(0)
    for n, oc in enumerate(result.outcomes):
        total += oc.net_pnl
        opened = _T0 + timedelta(minutes=10 * n)
        await conn.execute(
            """
            INSERT INTO paper_v2_positions (
                position_id, account_id, day_id, instrument, side, qty,
                avg_entry_price, isolated_margin, status, opened_at, closed_at,
                close_price, realized_gross_pnl, realized_net_pnl, entry_fee,
                exit_fee, funding_cashflow
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, 'long', $5, $6, $7, 'closed',
                $8, $9, $6, $10, $11, $12, $13, $14
            )
            """,
            uuid4(), account_id, day_id, INSTRUMENT,
            Decimal("1"), Decimal("10000"), Decimal("100"),
            opened, opened + timedelta(minutes=30),
            # gross/fees are the window aggregates; the ledger row records the
            # engine-produced net verbatim (NUMERIC keeps its scale)
            oc.net_pnl, oc.net_pnl, Decimal("0"), Decimal("0"), Decimal("0"),
        )

    report = await report_store.build_report_from_ledger(
        conn, owner_id=str(owner_id), session_id=str(uuid4()),
        settlement_complete=True, reconciliation_ok=True,
        replay_data_hash=result.data_hash,
    )
    manual = report.accounts["manual"]
    # identical numbers end-to-end, at the ledger's declared NUMERIC(20,8) scale:
    # each engine net carries >8 decimals and Postgres rounds per row on store, so
    # the expectation is the sum of the *stored* (rounded) values — never a silent
    # re-derivation from prices.
    stored_total = sum(
        (oc.net_pnl.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
         for oc in result.outcomes),
        Decimal(0),
    )
    assert Decimal(manual.net_pnl) == stored_total
    assert abs(Decimal(manual.net_pnl) - total) <= Decimal("0.00000001") * len(result.outcomes)
    assert manual.trades_closed == len(result.outcomes)
    assert report.replay_data_hash == result.data_hash  # reproducibility anchor stored
    assert report.is_final is True
