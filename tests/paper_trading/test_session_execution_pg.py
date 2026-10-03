"""P5.2 — session execution against a disposable PostgreSQL (MC-14 / MC-15 / MC-16 PG half).

Runs the *real* :class:`PaperRepository` on a throwaway schema of the disposable
QA database (never ``trading``), and skips cleanly when that database is not
reachable, so the default host run stays green instead of adding a new failure.

Evidence produced:

  * MC-14 — one owner, independent ``manual`` / ``auto`` accounts: separate
    opening deposits, and a full open chain on one account leaves the other
    account's balance, fills and positions untouched.
  * MC-15 — ``accepted → claimed → order → fill → position → close → ledger``:
    idempotent replay of the same key, ``IdempotencyConflict`` on a different
    payload under the same key, a second claim loses, and a second close commit
    is refused with no extra fill or posting.
  * MC-16 — mark-triggered liquidation persists exactly one close fill, a
    ``liquidation``-sourced ledger event and ``status='liquidated'``; a retry
    after the commit is a no-op, a replayed ledger event is refused by the DB
    uniqueness guard, a stale market leaves the position open with zero fills
    until the feed recovers, and the short side is forced out on the upside.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from paper_trading.contracts import (
    AccountKind,
    CommandStatus,
    CommandType,
    DayState,
    Fill,
    Order,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    PositionStatus,
    money,
)
from paper_trading.ledger import build_fill_postings
from paper_trading.liquidation import LiquidationOutcome, LiquidationState
from paper_trading.liquidation_store import (
    build_liquidation_postings,
    evaluate_for_position,
    persist_liquidation,
)
from paper_trading.market import PerpetualSnapshot, RiskTier
from paper_trading.repository import IdempotencyConflict, PaperRepository

#: Opt-in only: the suite never guesses a database.  Point it at the disposable
#: QA container (``wored_qa``) to run the PG half of P5.
DSN = os.getenv("WORED_TEST_DATABASE_URL", "")
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "paper_v2_schema.sql"

INSTRUMENT = "BTC-USDT"
ENTRY = Decimal("10000")
QTY = Decimal("10")
MARGIN = Decimal("100")
ENTRY_FEE = Decimal("6")

#: Buckets one forced exit books: released margin, exit fee, gross P&L.
LIQUIDATION_BUCKETS = {"released_margin", "exit_fee", "realized_gross_pnl"}

#: Domain tables this suite writes; truncated in FK-safe order between tests.
_TRUNCATE_TABLES = (
    "paper_v2_postings", "paper_v2_fills", "paper_v2_positions",
    "paper_v2_orders", "paper_v2_signals", "paper_v2_commands",
    "paper_v2_days", "paper_v2_accounts", "paper_v2_owners",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qa_schema():
    """Create the disposable schema and apply the 467-line DDL exactly once.

    Per-test DDL application dominated the runtime (>12s each); a ``TRUNCATE``
    between tests gives the same clean-slate isolation for milliseconds.  The
    session fixture is synchronous on purpose — it runs its own short-lived
    event loop, so no session-scoped async fixture is requested from a
    function-scoped loop.
    """
    if not DSN:
        pytest.skip("WORED_TEST_DATABASE_URL not set — disposable QA database required")
    if DSN.split("?")[0].rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against production 'trading' database")
    schema = f"sess_v3_{uuid4().hex[:8]}"

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
                await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
                landed = await conn.fetchval(
                    "SELECT to_regclass($1)", f'"{schema}".paper_v2_positions'
                )
                if landed is None:
                    pytest.fail(f"DDL did not land in the disposable schema {schema}")
        finally:
            await pool.close()

    try:
        asyncio.run(_create())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not prepare QA schema: {exc}")
    yield schema
    asyncio.run(_drop_schema(schema))


@pytest.fixture
async def repo(qa_schema):
    """A repository on a fresh pool bound to this test's loop; tables emptied."""
    pool = await asyncpg.create_pool(
        DSN, min_size=2, max_size=4, server_settings={"search_path": qa_schema}
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE " + ", ".join(_TRUNCATE_TABLES) + " CASCADE")
        yield PaperRepository(pool)
    finally:
        await pool.close()


async def _drop_schema(schema: str) -> None:
    conn = await asyncpg.connect(DSN, timeout=5)
    try:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Market + domain helpers
# ---------------------------------------------------------------------------


def _snapshot(*, mark: str, age_seconds: float = 0.0) -> PerpetualSnapshot:
    src = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    now = src.isoformat()
    m = Decimal(mark)
    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code=INSTRUMENT,
        bid=m,
        ask=m + Decimal("0.5"),
        last=m,
        mark=m,
        index=m,
        funding_rate=Decimal("0"),
        next_funding_at=None,
        component_times={"ticker": now, "index": now, "mark": now, "funding": now},
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at=now,
        received_at=now,
        source="fixture",
        quality="live",
        risk_tier=RiskTier(1, 100, Decimal("0.0028"), now, "fixture"),
    )


async def _seed_owner(repo: PaperRepository) -> tuple[UUID, UUID]:
    owner_id, day_id = uuid4(), uuid4()
    await repo.create_owner(owner_id, "p52-session")
    await repo.create_day(day_id, owner_id)
    await repo.update_day_state(day_id, DayState.running, expected_state=DayState.idle)
    return owner_id, day_id


async def _seed_account(
    repo: PaperRepository, owner_id: UUID, day_id: UUID, kind: AccountKind
) -> UUID:
    account_id = uuid4()
    await repo.create_account(account_id, owner_id, kind, opening_deposit=Decimal("1000"))
    return account_id


async def _open_position(
    repo: PaperRepository,
    *,
    account_id: UUID,
    day_id: UUID,
    side: PositionSide = PositionSide.long,
) -> UUID:
    """Open a position through the real chain: order → fill → position → ledger."""
    order_id = uuid4()
    now = datetime.now(timezone.utc)
    open_side = OrderSide.buy if side is PositionSide.long else OrderSide.sell
    await repo.create_order(
        Order(
            order_id=order_id,
            account_id=account_id,
            day_id=day_id,
            origin="user",
            actor="user",
            side=open_side,
            order_type=OrderType.market,
            instrument=INSTRUMENT,
            qty=QTY,
            price=ENTRY,
            stop_loss=Decimal("9000") if side is PositionSide.long else Decimal("11000"),
        )
    )
    open_fill = Fill(
        fill_id=uuid4(),
        order_id=order_id,
        account_id=account_id,
        execution_quote_id=f"open-{order_id}",
        instrument=INSTRUMENT,
        side=open_side,
        price=ENTRY,
        qty=QTY,
        fee=ENTRY_FEE,
        source_timestamp=now,
        receive_timestamp=now,
        execute_timestamp=now,
    )
    await repo.commit_open_fill(
        order_id=order_id,
        fill=open_fill,
        position=Position(
            position_id=order_id,
            account_id=account_id,
            day_id=day_id,
            instrument=INSTRUMENT,
            side=side,
            qty=QTY,
            avg_entry_price=ENTRY,
            isolated_margin=MARGIN,
            stop_loss=Decimal("9000") if side is PositionSide.long else Decimal("11000"),
            entry_fee=ENTRY_FEE,
        ),
        postings=build_fill_postings(
            account_id, ENTRY, QTY, ENTRY_FEE, False, side, day_id,
            str(open_fill.fill_id), reserved_margin=MARGIN,
        ),
    )
    return order_id


async def _counts(repo: PaperRepository, account_id: UUID) -> tuple[int, int, int]:
    """(fills, postings, positions) booked for one account — the no-double-effect probe."""
    async with repo.pool.acquire() as conn:
        fills = await conn.fetchval(
            "SELECT count(*) FROM paper_v2_fills WHERE account_id = $1", str(account_id)
        )
        postings = await conn.fetchval(
            "SELECT count(*) FROM paper_v2_postings WHERE account_id = $1", str(account_id)
        )
        positions = await conn.fetchval(
            "SELECT count(*) FROM paper_v2_positions WHERE account_id = $1", str(account_id)
        )
    return int(fills), int(postings), int(positions)


async def _fetch_close_fills(repo: PaperRepository, position_id: UUID) -> list[dict]:
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT fill_id, execution_quote_id, price, qty, fee "
            "FROM paper_v2_fills WHERE order_id = $1 AND is_close",
            str(position_id),
        )
    return [dict(r) for r in rows]


async def _fetch_liquidation_postings(
    repo: PaperRepository, account_id: UUID
) -> list[dict]:
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT bucket, source_type, source_ref, amount "
            "FROM paper_v2_postings WHERE account_id = $1 AND source_type = 'liquidation'",
            str(account_id),
        )
    return [dict(r) for r in rows]


async def _trigger_mark(
    repo: PaperRepository, position_id: UUID, *, side: PositionSide
) -> Decimal:
    """One unit past the *recorded* liquidation price for this position.

    Same technique as the P5.1 golden tests: probe the price the shared
    ``trading_math`` formula yields, then step through it.  Never a
    hand-guessed mark — an arbitrary ``9000`` sits far above a fully-margined
    position's price and would not trigger.
    """
    probe = evaluate_for_position(await repo.get_position(position_id), _snapshot(mark=str(ENTRY)))
    liq = probe.state.liquidation_price
    assert liq is not None, "the risk formula must yield a liquidation price"
    return liq - Decimal(1) if side is PositionSide.long else liq + Decimal(1)


async def _trigger_snapshot(
    repo: PaperRepository, position_id: UUID, *, side: PositionSide, age_seconds: float = 0.0
) -> PerpetualSnapshot:
    return _snapshot(
        mark=str(await _trigger_mark(repo, position_id, side=side)),
        age_seconds=age_seconds,
    )


# ---------------------------------------------------------------------------
# MC-14 — manual and auto accounts are independent
# ---------------------------------------------------------------------------


async def test_manual_and_auto_accounts_are_independent(repo) -> None:
    """MC-14: two accounts per owner never share balance, fills or positions."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    auto_id = await _seed_account(repo, owner_id, day_id, AccountKind.auto)

    assert await repo.get_account_balance(manual_id) == Decimal("1000")
    assert await repo.get_account_balance(auto_id) == Decimal("1000")
    by_kind = {a.kind: a for a in await repo.get_accounts_by_owner(owner_id)}
    assert set(by_kind) == {AccountKind.manual, AccountKind.auto}
    assert by_kind[AccountKind.manual].account_id == manual_id

    await _open_position(repo, account_id=manual_id, day_id=day_id)

    assert await repo.get_account_balance(manual_id) == Decimal("894.00000000")
    assert await repo.get_account_balance(auto_id) == Decimal("1000.00000000")
    assert len(await repo.get_open_positions(manual_id)) == 1
    assert await repo.get_open_positions(auto_id) == []
    assert await _counts(repo, auto_id) == (0, 1, 0)


async def test_no_implicit_transfer_between_account_kinds(repo) -> None:
    """MC-14: an auto-account chain never moves the manual account's money."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    auto_id = await _seed_account(repo, owner_id, day_id, AccountKind.auto)

    await _open_position(repo, account_id=auto_id, day_id=day_id, side=PositionSide.short)

    assert await repo.get_account_balance(manual_id) == Decimal("1000.00000000")
    assert await repo.get_account_balance(auto_id) == Decimal("894.00000000")
    assert (await _counts(repo, manual_id))[2] == 0
    assert len(await repo.get_open_positions(auto_id)) == 1


# ---------------------------------------------------------------------------
# MC-15 — lifecycle, duplicated and lost responses
# ---------------------------------------------------------------------------


async def test_command_idempotency_replay_and_conflict(repo) -> None:
    """MC-15: same key+payload replays; same key+different payload conflicts."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    payload = {"side": "long", "qty": "10", "stop_loss": "9000"}

    first = await repo.submit_command(
        uuid4(), owner_id, "ord-1", CommandType.submit_order, payload,
        account_id=manual_id, day_id=day_id,
    )
    assert first.status is CommandStatus.accepted

    replay = await repo.submit_command(
        uuid4(), owner_id, "ord-1", CommandType.submit_order, payload,
        account_id=manual_id, day_id=day_id,
    )
    assert replay.command_id == first.command_id, "a lost response replays the same command"

    with pytest.raises(IdempotencyConflict):
        await repo.submit_command(
            uuid4(), owner_id, "ord-1", CommandType.submit_order,
            {"side": "short", "qty": "10", "stop_loss": "11000"},
            account_id=manual_id, day_id=day_id,
        )
    conflicted = await repo.get_command(first.command_id)
    assert conflicted is not None and conflicted.status is CommandStatus.conflict


async def test_command_claim_is_atomic_across_runners(repo) -> None:
    """MC-15: two concurrent claims of one command — exactly one wins."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    command_id = uuid4()
    await repo.submit_command(
        command_id, owner_id, "claim-1", CommandType.submit_order,
        {"side": "long"}, account_id=manual_id, day_id=day_id,
    )

    winner, loser = await asyncio.gather(
        repo.claim_command(command_id), repo.claim_command(command_id)
    )
    claimed = [c for c in (winner, loser) if c is not None]
    assert len(claimed) == 1
    assert claimed[0].status is CommandStatus.processing
    assert await repo.claim_command(command_id) is None, "processing is not re-claimable"


async def test_full_lifecycle_open_close_ledger(repo) -> None:
    """MC-15: command→order→fill→position→close→ledger with exact cashflows."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    command_id = uuid4()
    await repo.submit_command(
        command_id, owner_id, "lifecycle-1", CommandType.submit_order,
        {"side": "long", "qty": str(QTY)}, account_id=manual_id, day_id=day_id,
    )
    claimed = await repo.claim_command(command_id)
    assert claimed is not None and claimed.status is CommandStatus.processing

    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)
    assert (await repo.get_position(position_id)).status is PositionStatus.open

    now = datetime.now(timezone.utc)
    close_fill = Fill(
        fill_id=uuid4(), order_id=position_id, account_id=manual_id,
        execution_quote_id=f"close-{position_id}", instrument=INSTRUMENT,
        side=OrderSide.sell, price=Decimal("10100"), qty=QTY,
        fee=Decimal("6.06"), is_close=True,
        source_timestamp=now, receive_timestamp=now, execute_timestamp=now,
    )
    close_postings = build_fill_postings(
        manual_id, Decimal("10100"), QTY, Decimal("6.06"), True,
        PositionSide.long, day_id, str(close_fill.fill_id),
        realized_gross=Decimal("100"), realized_net=Decimal("87.94"),
        released_margin=MARGIN,
    )
    await repo.commit_position_close(
        position_id=position_id,
        fill=close_fill,
        close_price=Decimal("10100"),
        realized_gross_pnl=Decimal("100"),
        realized_net_pnl=Decimal("87.94"),
        exit_fee=Decimal("6.06"),
        postings=close_postings,
    )

    closed = await repo.get_position(position_id)
    assert closed is not None and closed.status is PositionStatus.closed
    # deposit 1000 − reserved margin 100 − entry fee 6 + released margin 100
    # − exit fee 6.06 + gross pnl 100  (realized_net is a projection, not cash)
    assert await repo.get_account_balance(manual_id) == Decimal("1087.94000000")

    await repo.complete_command(command_id, {"position_id": str(position_id)})
    completed = await repo.get_command(command_id)
    assert completed is not None and completed.status is CommandStatus.completed

    # A duplicated close response must not double-count anything.
    with pytest.raises(ValueError, match="position is not open"):
        await repo.commit_position_close(
            position_id=position_id,
            fill=close_fill,
            close_price=Decimal("10100"),
            realized_gross_pnl=Decimal("100"),
            realized_net_pnl=Decimal("87.94"),
            exit_fee=Decimal("6.06"),
            postings=close_postings,
        )
    # 2 fills; 6 postings = deposit + (reserved margin, entry fee)
    #                    + (released margin, exit fee, realized gross)
    assert await _counts(repo, manual_id) == (2, 6, 1)
    assert await repo.get_account_balance(manual_id) == Decimal("1087.94000000")


async def test_close_commit_refuses_non_terminal_status(repo) -> None:
    """MC-16 guard: a close commit may only stamp a terminal position status."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="terminal status"):
        await repo.commit_position_close(
            position_id=position_id,
            fill=Fill(
                fill_id=uuid4(), order_id=position_id, account_id=manual_id,
                execution_quote_id="bad-status", side=OrderSide.sell,
                price=Decimal("10100"), qty=QTY, fee=Decimal("6.06"),
                is_close=True, source_timestamp=now, receive_timestamp=now,
                execute_timestamp=now,
            ),
            close_price=Decimal("10100"),
            realized_gross_pnl=Decimal("100"),
            realized_net_pnl=Decimal("87.94"),
            exit_fee=Decimal("6.06"),
            postings=build_fill_postings(
                manual_id, Decimal("10100"), QTY, Decimal("6.06"), True,
                PositionSide.long, day_id, "bad-status", realized_gross=Decimal("100"),
            ),
            status=PositionStatus.open,
        )
    assert (await repo.get_position(position_id)).status is PositionStatus.open
    assert await _counts(repo, manual_id) == (1, 3, 1)


# ---------------------------------------------------------------------------
# MC-16 — liquidation persistence (PG half)
# ---------------------------------------------------------------------------


async def test_mark_trigger_liquidates_once_with_ledger(repo) -> None:
    """MC-16: forced full close → one fill, liquidation ledger, status, reason."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)

    snap = await _trigger_snapshot(repo, position_id, side=PositionSide.long)
    decision = evaluate_for_position(await repo.get_position(position_id), snap)
    assert decision.state.state is LiquidationState.liquidated
    assert decision.state.reason_code == "liquidated"
    assert decision.outcome is LiquidationOutcome.closed
    assert decision.state.event_key == f"liq:{position_id}"

    assert await persist_liquidation(repo, decision) is True

    position = await repo.get_position(position_id)
    assert position is not None and position.status is PositionStatus.liquidated
    assert position.qty == Decimal("0")
    assert position.closed_at is not None
    assert position.realized_gross_pnl == money(decision.state.close.gross_pnl)
    assert position.exit_fee == money(decision.state.close.close_fee)

    close_fills = await _fetch_close_fills(repo, position_id)
    assert len(close_fills) == 1, "exactly one forced close fill"
    assert close_fills[0]["execution_quote_id"] == f"liq:{position_id}"

    liq_postings = await _fetch_liquidation_postings(repo, manual_id)
    assert {p["bucket"] for p in liq_postings} == LIQUIDATION_BUCKETS
    assert {p["source_ref"] for p in liq_postings} == {f"liq:{position_id}"}
    assert {p["source_type"] for p in liq_postings} == {"liquidation"}

    close = decision.state.close
    expected = (
        Decimal("1000") - MARGIN - ENTRY_FEE
        + MARGIN - money(close.close_fee) + money(close.gross_pnl)
    )
    assert await repo.get_account_balance(manual_id) == money(expected)


async def test_retry_after_liquidation_commit_is_noop(repo) -> None:
    """MC-16 crash/retry: re-evaluating the persisted row never fills twice."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)

    snap = await _trigger_snapshot(repo, position_id, side=PositionSide.long)
    decision = evaluate_for_position(await repo.get_position(position_id), snap)
    assert await persist_liquidation(repo, decision) is True
    before = await _counts(repo, manual_id)

    replay = evaluate_for_position(await repo.get_position(position_id), snap)
    assert replay.state.reason_code == "already_liquidated"
    assert replay.outcome is LiquidationOutcome.noop
    assert replay.should_persist is False
    assert await persist_liquidation(repo, replay) is False

    assert await _counts(repo, manual_id) == before, "retry produced no second effect"
    assert len(await _fetch_liquidation_postings(repo, manual_id)) == len(LIQUIDATION_BUCKETS)


async def test_ledger_unique_guard_blocks_replayed_event(repo) -> None:
    """MC-16: a replayed liquidation event is refused by DB uniqueness, not by luck."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)

    snap = await _trigger_snapshot(repo, position_id, side=PositionSide.long)
    decision = evaluate_for_position(await repo.get_position(position_id), snap)
    assert await persist_liquidation(repo, decision) is True

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.append_postings(build_liquidation_postings(decision))

    assert len(await _fetch_liquidation_postings(repo, manual_id)) == len(LIQUIDATION_BUCKETS)


async def test_stale_market_blocks_fill_until_feed_recovers(repo) -> None:
    """MC-16 negative half: stale mark → pending/blocked, position stays open."""
    owner_id, day_id = await _seed_owner(repo)
    manual_id = await _seed_account(repo, owner_id, day_id, AccountKind.manual)
    position_id = await _open_position(repo, account_id=manual_id, day_id=day_id)
    before = await _counts(repo, manual_id)
    mark = str(await _trigger_mark(repo, position_id, side=PositionSide.long))

    stale = evaluate_for_position(
        await repo.get_position(position_id), _snapshot(mark=mark, age_seconds=30.0)
    )
    assert stale.state.state is LiquidationState.pending
    assert stale.state.reason_code == "stale_market"
    assert stale.outcome is LiquidationOutcome.blocked_stale_market
    assert stale.should_persist is False
    assert await persist_liquidation(repo, stale) is False

    assert await _counts(repo, manual_id) == before, "no fill or posting against a stale quote"
    assert (await repo.get_position(position_id)).status is PositionStatus.open

    recovered = evaluate_for_position(
        await repo.get_position(position_id), _snapshot(mark=mark)
    )
    assert await persist_liquidation(repo, recovered) is True
    assert (await repo.get_position(position_id)).status is PositionStatus.liquidated
    # 2 fills; deposit + open pair + the three liquidation buckets
    assert await _counts(repo, manual_id) == (2, 6, 1)


async def test_short_position_liquidates_on_upside_mark(repo) -> None:
    """MC-16 symmetry: a short is forced out when mark rises through the price."""
    owner_id, day_id = await _seed_owner(repo)
    auto_id = await _seed_account(repo, owner_id, day_id, AccountKind.auto)
    position_id = await _open_position(
        repo, account_id=auto_id, day_id=day_id, side=PositionSide.short
    )

    snap = await _trigger_snapshot(repo, position_id, side=PositionSide.short)
    decision = evaluate_for_position(await repo.get_position(position_id), snap)
    assert decision.state.state is LiquidationState.liquidated
    assert decision.state.close.gross_pnl < 0
    assert await persist_liquidation(repo, decision) is True

    assert (await repo.get_position(position_id)).status is PositionStatus.liquidated
    assert len(await _fetch_liquidation_postings(repo, auto_id)) == len(LIQUIDATION_BUCKETS)
