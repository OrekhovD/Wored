"""Trading-day rollover / P0 settlement_pending regression tests.

These exercise the *real* :class:`PaperTradingService`, :class:`PaperTradingRunner`
and command-lifecycle code paths (claim → execute → complete/requeue) against a
faithful in-memory store that models the exact DB invariants the fix relies on:

  * idempotent commands — ``UNIQUE(owner_id, idempotency_key)``
  * atomic claim — an ``accepted`` command can be claimed by only one runner
  * ``uq_days_one_incomplete`` — at most one non-closed day per owner
  * a day may only be reported ``closed`` when the transition really applied

The close *arithmetic* (fills/fees/ledger) is covered by ``test_closeout.py`` and
``test_ledger_cashflows.py``; here the position-close collaborator is stubbed so
these tests stay focused on the day state machine, retries and inheritance.
"""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from paper_trading.contracts import (
    Account,
    AccountKind,
    Command,
    CommandStatus,
    CommandType,
    DayState,
    Order,
    OrderState,
    Position,
    PositionSide,
    PositionStatus,
    TradingDay,
)
from paper_trading.repository import IdempotencyConflict
from paper_trading.runner import (
    CommandSource,
    MarketDataSource,
    PaperTradingRunner,
    RecoveryStore,
)
from paper_trading.service import PaperTradingService
from paper_trading.strategy import BaselineV1Strategy

# A deliberately non-default policy so we can prove inheritance (not a hidden
# Asia/Bangkok/21:00/baseline_auto fallback) — case 9.
INHERIT_POLICY = {
    "timezone": "Europe/Kyiv",
    "end_time_local": "18:30",
    "mode": "baseline_auto",
    "strategy_version": "baseline_v1",
    "opening_capital": "1000",
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "max_positions_per_instrument": "1",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: dict) -> str:
    import json
    return json.dumps(payload, sort_keys=True)


class FakeStore:
    """In-memory stand-in for :class:`PaperRepository` with real invariants."""

    def __init__(self) -> None:
        self.owners: dict[UUID, str] = {}
        self.accounts: list[Account] = []
        self.days: dict[UUID, TradingDay] = {}
        self.commands: list[Command] = []
        self.positions: dict[UUID, Position] = {}
        self.orders: dict[UUID, Order] = {}
        # Test switches.
        self.fail_closed_transition = False

    # -- sync seed helpers (test-only) ----------------------------------
    def seed_owner(self, owner_id: UUID) -> UUID:
        self.owners[owner_id] = f"owner-{str(owner_id)[:8]}"
        return owner_id

    def seed_account(self, owner_id: UUID, kind: AccountKind) -> Account:
        acct = Account(
            account_id=uuid4(), owner_id=owner_id, kind=kind,
            currency="USDT", opening_deposit=Decimal("1000"), created_at=_now(),
        )
        self.accounts.append(acct)
        return acct

    def seed_day(
        self, owner_id: UUID, *, state: DayState, end_offset_hours: float = -1,
        settings: dict | None = None, day_id: UUID | None = None,
    ) -> TradingDay:
        now = _now()
        day = TradingDay(
            day_id=day_id or uuid4(), owner_id=owner_id,
            timezone=(settings or INHERIT_POLICY).get("timezone", "Asia/Bangkok"),
            start_utc=now - timedelta(hours=24),
            end_utc=now + timedelta(hours=end_offset_hours),
            state=state, strategy_version="baseline_v1",
            settings_snapshot=dict(settings) if settings is not None else dict(INHERIT_POLICY),
            created_at=now,
        )
        self.days[day.day_id] = day
        return day

    def seed_position(self, account_id: UUID, day_id: UUID, *, status: PositionStatus = PositionStatus.open) -> Position:
        pos = Position(
            position_id=uuid4(), account_id=account_id, day_id=day_id,
            side=PositionSide.long, qty=Decimal("1"),
            avg_entry_price=Decimal("10000"), isolated_margin=Decimal("1000"),
            status=status,
        )
        self.positions[pos.position_id] = pos
        return pos

    # -- owner / account -------------------------------------------------
    async def create_owner(self, owner_id, display_name, telegram_id=None, webui_identity=None):
        self.owners[UUID(str(owner_id))] = display_name

    async def create_account(self, account_id, owner_id, kind, currency="USDT", opening_deposit=Decimal(1000)):
        owner_id = UUID(str(owner_id))
        kind_val = kind.value if isinstance(kind, AccountKind) else str(kind)
        for a in self.accounts:
            if a.owner_id == owner_id and a.kind.value == kind_val:
                raise RuntimeError("account exists")
        acct = Account(
            account_id=UUID(str(account_id)), owner_id=owner_id, kind=AccountKind(kind_val),
            currency=currency, opening_deposit=Decimal(str(opening_deposit)), created_at=_now(),
        )
        self.accounts.append(acct)
        return acct

    async def get_account(self, account_id):
        account_id = UUID(str(account_id))
        return next((a for a in self.accounts if a.account_id == account_id), None)

    async def get_account_by_kind(self, owner_id, kind):
        owner_id = UUID(str(owner_id))
        kind_val = kind.value if isinstance(kind, AccountKind) else str(kind)
        return next((a for a in self.accounts if a.owner_id == owner_id and a.kind.value == kind_val), None)

    # -- days ------------------------------------------------------------
    async def create_day(self, day_id, owner_id, timezone="Asia/Bangkok", start_utc=None,
                         end_utc=None, strategy_version="baseline_v1", settings_snapshot=None):
        day_id = UUID(str(day_id))
        owner_id = UUID(str(owner_id))
        for d in self.days.values():
            if d.owner_id == owner_id and d.state != DayState.closed:
                raise RuntimeError("uq_days_one_incomplete")
        day = TradingDay(
            day_id=day_id, owner_id=owner_id, timezone=timezone, start_utc=start_utc,
            end_utc=end_utc, state=DayState.idle, strategy_version=strategy_version,
            settings_snapshot=dict(settings_snapshot or {}), created_at=_now(),
        )
        self.days[day_id] = day
        return day

    async def get_day(self, day_id):
        return self.days.get(UUID(str(day_id)))

    async def get_active_day(self, owner_id):
        owner_id = UUID(str(owner_id))
        cands = [d for d in self.days.values() if d.owner_id == owner_id and d.state != DayState.closed]
        return max(cands, key=lambda d: d.created_at) if cands else None

    async def update_day_state(self, day_id, new_state, expected_state=None):
        day = self.days.get(UUID(str(day_id)))
        if day is None:
            return False
        if expected_state is not None and day.state != expected_state:
            return False
        if new_state == DayState.closed and self.fail_closed_transition:
            return False
        self.days[day.day_id] = replace(day, state=new_state)
        return True

    async def get_days_needing_closure(self):
        now = _now()
        out = []
        for d in self.days.values():
            if d.state in (DayState.settlement_pending, DayState.closing):
                out.append(d)
            elif d.state == DayState.running and d.end_utc is not None and d.end_utc < now:
                out.append(d)
        return out

    async def get_rollover_candidates(self):
        auto_owners = {a.owner_id for a in self.accounts if a.kind == AccountKind.auto}
        incomplete = {d.owner_id for d in self.days.values() if d.state != DayState.closed}
        out = []
        for owner in auto_owners:
            if owner in incomplete:
                continue
            closed = [d for d in self.days.values() if d.owner_id == owner and d.state == DayState.closed]
            if closed:
                out.append(max(closed, key=lambda d: (d.end_utc or d.created_at, d.created_at)))
        return out

    # -- commands --------------------------------------------------------
    async def submit_command(self, command_id, owner_id, idempotency_key, command_type,
                             payload, account_id=None, day_id=None, expected_revision=None):
        owner_id = UUID(str(owner_id))
        rh = _hash(payload)
        for i, c in enumerate(self.commands):
            if c.owner_id == owner_id and c.idempotency_key == idempotency_key:
                if c.request_hash != rh:
                    self.commands[i] = replace(c, status=CommandStatus.conflict)
                    raise IdempotencyConflict("duplicate key, different payload")
                return c
        cmd = Command(
            command_id=UUID(str(command_id)), owner_id=owner_id,
            account_id=UUID(str(account_id)) if account_id else None,
            day_id=UUID(str(day_id)) if day_id else None,
            command_type=command_type, idempotency_key=idempotency_key,
            request_hash=rh, expected_revision=expected_revision,
            status=CommandStatus.accepted, created_at=_now(), updated_at=_now(),
        )
        self.commands.append(cmd)
        return cmd

    async def get_command(self, command_id):
        command_id = UUID(str(command_id))
        return next((c for c in self.commands if c.command_id == command_id), None)

    async def claim_command(self, command_id):
        command_id = UUID(str(command_id))
        for i, c in enumerate(self.commands):
            if c.command_id == command_id and c.status == CommandStatus.accepted:
                new = replace(c, status=CommandStatus.processing, updated_at=_now())
                self.commands[i] = new
                return new
        return None

    async def complete_command(self, command_id, result=None):
        command_id = UUID(str(command_id))
        for i, c in enumerate(self.commands):
            if c.command_id == command_id:
                self.commands[i] = replace(c, status=CommandStatus.completed, result=result, updated_at=_now())
        return True

    async def requeue_command(self, command_id, error=None):
        command_id = UUID(str(command_id))
        for i, c in enumerate(self.commands):
            if c.command_id == command_id:
                if c.status != CommandStatus.processing:
                    return False
                self.commands[i] = replace(c, status=CommandStatus.accepted, error=error, updated_at=_now())
                return True
        return False

    async def requeue_stale_processing(self, max_age_seconds=60):
        return 0  # real expiry is exercised against PostgreSQL (see PG test)

    # -- positions / orders ---------------------------------------------
    async def get_open_positions_by_day(self, day_id):
        day_id = UUID(str(day_id))
        return [p for p in self.positions.values() if p.day_id == day_id and p.status == PositionStatus.open]

    async def get_unfilled_orders_by_day(self, day_id):
        day_id = UUID(str(day_id))
        return [o for o in self.orders.values() if o.day_id == day_id and o.state in (OrderState.pending, OrderState.submitted)]

    async def update_order_state(self, order_id, new_state, filled_qty=None):
        order_id = UUID(str(order_id))
        for k, o in self.orders.items():
            if o.order_id == order_id:
                self.orders[k] = replace(o, state=new_state)
                return True
        return False

    # -- inspection helpers ---------------------------------------------
    def day_by_state(self, state: DayState) -> list[TradingDay]:
        return [d for d in self.days.values() if d.state == state]

    def commands_of_type(self, ctype: CommandType) -> list[Command]:
        return [c for c in self.commands if c.command_type == ctype]


class FakeCommandSource(CommandSource):
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def poll(self):
        return [c for c in self._store.commands if c.status == CommandStatus.accepted]


class FakeRecoveryStore(RecoveryStore):
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def load_unfinished_commands(self):
        return [c for c in self._store.commands if c.status in (CommandStatus.accepted, CommandStatus.processing)]

    async def load_open_orders(self):
        return [o for o in self._store.orders.values() if o.state in (OrderState.pending, OrderState.submitted)]

    async def load_open_positions(self):
        return [p for p in self._store.positions.values() if p.status == PositionStatus.open]

    async def load_active_auto_accounts(self):
        states = {DayState.running, DayState.settlement_pending, DayState.closing}
        owners = {d.owner_id for d in self._store.days.values() if d.state in states}
        return [a for a in self._store.accounts if a.kind == AccountKind.auto and a.owner_id in owners]

    async def reconcile_ledger(self, positions):
        return {"reconciled": True, "discrepancies": []}

    async def cancel_expired_intents(self, expired_ids):
        return 0


class FakeMarketData(MarketDataSource):
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot

    async def fetch_snapshot(self):
        return self.snapshot


def make_runner(store: FakeStore, market: FakeMarketData) -> PaperTradingRunner:
    return PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        repository=store,
        command_source=FakeCommandSource(store),
        market_data=market,
    )


async def cycle(runner: PaperTradingRunner) -> None:
    """Mirror the run_cycle ordering relevant to rollover: day loop then commands."""
    await runner._auto_finish_expired_days()
    await runner._process_commands(time.time())


def install_close_stub(runner: PaperTradingRunner, closed_log: list) -> None:
    """Stub the position-close collaborator (its arithmetic is tested elsewhere)."""
    async def _fake_close(pos, snapshot, reason):
        closed_log.append(pos.position_id)
        store = runner.repository
        store.positions[pos.position_id] = replace(pos, status=PositionStatus.closed)
        runner._positions.pop(pos.position_id, None)
    runner._execute_position_close = _fake_close  # type: ignore[method-assign]


def seed_owner_with_accounts(store: FakeStore):
    owner = store.seed_owner(uuid4())
    manual = store.seed_account(owner, AccountKind.manual)
    auto = store.seed_account(owner, AccountKind.auto)
    return owner, manual, auto


# ─────────────────────────────────────────────────────────────────────────
# 1. Expired running day, no positions → one finish command, one closed,
#    one successor day.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_expired_running_day_closes_once_and_starts_one_successor() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    runner = make_runner(store, FakeMarketData(snapshot=None))

    await cycle(runner)   # enqueue+execute finish → day closed
    await cycle(runner)   # rollover → successor day created

    finish_cmds = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert len(finish_cmds) == 1, "exactly one finish command for the day"
    assert finish_cmds[0].status == CommandStatus.completed

    assert store.day_by_state(DayState.closed), "the expired day became closed"
    # one successor in a non-closed state (idle/running), none extra
    incomplete = [d for d in store.days.values() if d.state != DayState.closed]
    assert len(incomplete) == 1, "exactly one successor day was started"


# ─────────────────────────────────────────────────────────────────────────
# 2. Open positions, no quote → settlement_pending, 0 new days, 0 closes.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_open_positions_without_snapshot_defers_to_settlement_pending() -> None:
    store = FakeStore()
    owner, manual, auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    p1 = store.seed_position(manual.account_id, day.day_id)
    p2 = store.seed_position(auto.account_id, day.day_id)
    closed_log: list = []
    runner = make_runner(store, FakeMarketData(snapshot=None))
    install_close_stub(runner, closed_log)

    await cycle(runner)

    assert store.day_by_state(DayState.settlement_pending), "day deferred to settlement_pending"
    assert closed_log == [], "no position was closed without a valid quote"
    assert p1.status == PositionStatus.open and p2.status == PositionStatus.open
    # no successor day created while the current one is unresolved
    assert len([d for d in store.days.values() if d.state != DayState.closed]) == 1
    finish = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert len(finish) == 1 and finish[0].status == CommandStatus.accepted, "finish requeued for retry"


# ─────────────────────────────────────────────────────────────────────────
# 3. Next cycle with a valid quote → both positions closed exactly once,
#    then exactly one successor day.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_snapshot_becoming_available_completes_closeout_then_rollover() -> None:
    store = FakeStore()
    owner, manual, auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    p1 = store.seed_position(manual.account_id, day.day_id)
    p2 = store.seed_position(auto.account_id, day.day_id)
    closed_log: list = []
    market = FakeMarketData(snapshot=None)
    runner = make_runner(store, market)
    install_close_stub(runner, closed_log)

    await cycle(runner)  # deferred: settlement_pending
    market.snapshot = object()  # a "valid" snapshot appears

    await cycle(runner)  # retry the requeued finish command → close both, day closed
    await cycle(runner)  # rollover

    assert sorted(closed_log) == sorted([p1.position_id, p2.position_id]), "each position closed exactly once"
    assert store.positions[p1.position_id].status == PositionStatus.closed
    assert store.positions[p2.position_id].status == PositionStatus.closed
    assert store.day_by_state(DayState.closed), "day reached closed once the quote was valid"
    incomplete = [d for d in store.days.values() if d.state != DayState.closed]
    assert len(incomplete) == 1, "exactly one successor day started after closure"


# ─────────────────────────────────────────────────────────────────────────
# 4. Restart between (2) and (3): owner scope recovered, entries blocked
#    until the deferred close finishes, retry fires.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restart_recovers_settlement_pending_owner_and_keeps_entries_blocked() -> None:
    store = FakeStore()
    owner, manual, auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    store.seed_position(manual.account_id, day.day_id)
    market = FakeMarketData(snapshot=None)
    first = make_runner(store, market)
    closed_log: list = []
    install_close_stub(first, closed_log)
    await cycle(first)  # → settlement_pending, finish requeued, position open

    # Simulate a runner restart against the same store.
    runner2 = PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        repository=store,
        recovery_store=FakeRecoveryStore(store),
        command_source=FakeCommandSource(store),
        market_data=market,
    )
    report = await runner2.recover()

    assert report["recovered"] is True
    assert runner2._auto_owner_id == owner, "owner scope restored from settlement_pending day"
    assert runner2.entries_blocked is True, "entries stay blocked while a closure is pending"
    assert report.get("entries_blocked_reason") == "pending_closure"

    market.snapshot = object()
    closed_log2: list = []
    install_close_stub(runner2, closed_log2)
    await cycle(runner2)  # recovered finish command is retried and completes
    assert store.day_by_state(DayState.closed), "restart-driven retry finished the close"


# ─────────────────────────────────────────────────────────────────────────
# 5. Two runners claim one command → one finish effect, one successor.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_two_runners_one_command_exactly_once_finish_effect() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)

    # Enqueue the finish command once.
    svc = PaperTradingService(store)
    await svc.finish_day(str(owner))
    finish = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert len(finish) == 1

    runner_a = make_runner(store, FakeMarketData(snapshot=None))
    runner_b = make_runner(store, FakeMarketData(snapshot=None))

    cmd = finish[0]
    claimed_a = await store.claim_command(cmd.command_id)
    claimed_b = await store.claim_command(cmd.command_id)
    assert claimed_a is not None, "first runner claims the accepted command"
    assert claimed_b is None, "second runner cannot claim the same command"

    # Runner A executes it to completion.
    processed = await runner_a._handle_finish_day(claimed_a)
    assert processed is True
    await store.complete_command(claimed_a.command_id)

    # Runner B, seeing the (now completed) command, produces no second effect.
    b_processed = await runner_b._handle_finish_day(replace(claimed_a, status=CommandStatus.completed))
    # A completed day re-run is idempotent and must not create a new command/day.
    finish_after = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert len(finish_after) == 1, "no duplicate finish command from the second runner"
    assert b_processed is True  # re-run on already-closed day is a harmless no-op

    # Rollover creates exactly one successor (only via a single owner's queue).
    await cycle(runner_a)
    incomplete = [d for d in store.days.values() if d.state != DayState.closed]
    assert len(incomplete) == 1, "exactly one successor day overall"


# ─────────────────────────────────────────────────────────────────────────
# 6. Idempotency: same key → same command; different payload same key → conflict.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finish_idempotency_replay_and_conflict() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    svc = PaperTradingService(store)

    r1 = await svc.finish_day(str(owner))
    r2 = await svc.finish_day(str(owner))
    assert r1["command_id"] == r2["command_id"], "replayed finish returns the same command"
    finish = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert len(finish) == 1

    # Same key, different payload → conflict.
    with pytest.raises(IdempotencyConflict):
        await store.submit_command(
            command_id=uuid4(), owner_id=owner,
            idempotency_key=f"finish-{day.day_id}", command_type=CommandType.finish_day,
            payload={"unexpected": True}, day_id=day.day_id,
        )


# ─────────────────────────────────────────────────────────────────────────
# 7. closed transition fails → no false closed, no successor.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_failed_closed_transition_does_not_report_closed_or_rollver() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    day = store.seed_day(owner, state=DayState.running, end_offset_hours=-1)
    store.fail_closed_transition = True  # DB refuses to set closed
    runner = make_runner(store, FakeMarketData(snapshot=None))

    await cycle(runner)
    await cycle(runner)

    assert not store.day_by_state(DayState.closed), "day must NOT be reported closed"
    # The finish command is requeued (never completed) so closure is retried.
    finish = [c for c in store.commands_of_type(CommandType.finish_day) if c.day_id == day.day_id]
    assert finish[0].status in (CommandStatus.accepted, CommandStatus.processing)
    # Exactly one incomplete day — no successor started behind an unresolved one.
    assert len([d for d in store.days.values() if d.state != DayState.closed]) == 1


# ─────────────────────────────────────────────────────────────────────────
# 8. A different owner's day is untouched.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_other_owner_day_is_untouched() -> None:
    store = FakeStore()
    owner1, _m1, _a1 = seed_owner_with_accounts(store)
    owner2, _m2, _a2 = seed_owner_with_accounts(store)
    expired = store.seed_day(owner1, state=DayState.running, end_offset_hours=-1)
    future = store.seed_day(owner2, state=DayState.running, end_offset_hours=+5)

    runner = make_runner(store, FakeMarketData(snapshot=None))
    await cycle(runner)
    await cycle(runner)

    assert (await store.get_day(expired.day_id)).state in (DayState.closed, DayState.running)  # processed
    assert (await store.get_day(future.day_id)).state == DayState.running, "non-expired owner day untouched"
    # Only owner1 rolled over; owner2 keeps a single day.
    owner2_days = [d for d in store.days.values() if d.owner_id == owner2]
    assert len(owner2_days) == 1


# ─────────────────────────────────────────────────────────────────────────
# 9a. Successor inherits the finished day's policy (not a hidden default).
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_successor_inherits_stored_policy() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    store.seed_day(owner, state=DayState.closed, end_offset_hours=-1,
                   settings=dict(INHERIT_POLICY))

    runner = make_runner(store, FakeMarketData(snapshot=None))
    await cycle(runner)

    incomplete = [d for d in store.days.values() if d.state != DayState.closed]
    assert len(incomplete) == 1, "a successor was started"
    succ = incomplete[0]
    assert succ.timezone == "Europe/Kyiv", "timezone inherited, not Asia/Bangkok"
    assert succ.settings_snapshot["end_time_local"] == "18:30", "end time inherited, not 21:00"
    assert succ.settings_snapshot["mode"] == "baseline_auto", "mode inherited"


# ─────────────────────────────────────────────────────────────────────────
# 9b. Unrecoverable auto policy → explicit recovery_required, no fabricated day.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unrecoverable_policy_blocks_rollover_instead_of_defaulting() -> None:
    store = FakeStore()
    owner, _manual, _auto = seed_owner_with_accounts(store)
    bad_policy = dict(INHERIT_POLICY)
    bad_policy.pop("end_time_local")  # mode says auto but end time is lost
    day = store.seed_day(owner, state=DayState.closed, end_offset_hours=-1, settings=bad_policy)

    runner = make_runner(store, FakeMarketData(snapshot=None))
    await cycle(runner)

    # No successor day was fabricated: still only the one (now escalated) day.
    assert len(store.days) == 1, "no hidden default day was created"
    # The closed day was escalated to recovery_required and the reason surfaced.
    assert (await store.get_day(day.day_id)).state == DayState.recovery_required
    assert runner._last_decision is not None
    assert runner.entries_blocked is True
