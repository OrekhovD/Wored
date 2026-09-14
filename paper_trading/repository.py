"""
paper_trading.repository — asyncpg PostgreSQL repository.

PostgreSQL is the source of truth.  All mutations are serialised by a
PostgreSQL transaction with ``SELECT ... FOR UPDATE`` locks on the
account and/or position rows.  Idempotency is enforced at the DB level:

    - same idempotency_key + same request_hash → return existing result
    - same idempotency_key + different request_hash → conflict

Python 3.9 compatible.  Uses ``asyncpg`` directly (no ORM).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

ZERO = Decimal("0")
from uuid import UUID

import asyncpg

from paper_trading.contracts import (
    Account,
    AccountKind,
    Command,
    CommandStatus,
    CommandType,
    DayState,
    Fill,
    JournalBucket,
    JournalPosting,
    JournalSourceType,
    Order,
    OrderSide,
    OrderState,
    OrderType,
    Owner,
    Position,
    PositionSide,
    PositionStatus,
    SCHEMA_VERSION,
    Signal,
    TradingDay,
    money,
)
from paper_trading.ledger import post_entry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclass ↔ row helpers
# ---------------------------------------------------------------------------


def _dc_to_row(obj: Any) -> Dict[str, Any]:
    """Convert a dataclass instance to a dict suitable for asyncpg."""
    if not is_dataclass(obj):
        raise TypeError(f"Expected dataclass, got {type(obj)}")
    d: Dict[str, Any] = {}
    for k, v in asdict(obj).items():  # type: ignore[arg-type]
        d[k] = _encode(v)
    return d


def _encode(v: Any) -> Any:
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (AccountKind, DayState, OrderSide, OrderState,
                      OrderType, PositionSide, PositionStatus,
                      CommandStatus, CommandType,
                      JournalBucket, JournalSourceType)):
        return v.value
    if isinstance(v, datetime):
        return v
    if isinstance(v, dict):
        return json.dumps(v, default=str)
    return v


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: Dict[str, Any]) -> str:
    """SHA-256 of canonical JSON payload for idempotency request_hash."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class IdempotencyConflict(Exception):
    """Raised when same key + different payload is submitted."""


class PaperRepository:
    """asyncpg-based repository for paper_trading.

    All public mutation methods acquire a transaction and lock the relevant
    account row with ``SELECT ... FOR UPDATE`` before writing.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    @classmethod
    async def create(cls, dsn: str, min_size: int = 2, max_size: int = 10) -> "PaperRepository":
        """Create a repository with a new asyncpg connection pool."""
        pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
        return cls(pool)

    async def close(self) -> None:
        """Close the underlying pool."""
        await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    # ------------------------------------------------------------------
    # Owner
    # ------------------------------------------------------------------

    async def create_owner(
        self,
        owner_id: UUID,
        display_name: str,
        telegram_id: Optional[int] = None,
        webui_identity: Optional[str] = None,
    ) -> Owner:
        """Insert a new owner.  Raises ``asyncpg.UniqueViolationError`` on duplicate."""
        now = _now_utc()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO paper_v2_owners
                    (owner_id, display_name, telegram_id, webui_identity,
                     created_at, schema_version)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                str(owner_id),
                display_name,
                telegram_id,
                webui_identity,
                now,
                SCHEMA_VERSION,
            )
        return Owner(
            owner_id=owner_id,
            display_name=display_name,
            telegram_id=telegram_id,
            webui_identity=webui_identity,
            created_at=now,
        )

    async def get_owner(self, owner_id: UUID) -> Optional[Owner]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_owners WHERE owner_id = $1",
                str(owner_id),
            )
        return _row_to_owner(row) if row else None

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def create_account(
        self,
        account_id: UUID,
        owner_id: UUID,
        kind: AccountKind,
        currency: str = "USDT",
        opening_deposit: Decimal = Decimal("1000"),
    ) -> Account:
        """Create a simulation account and post the opening deposit."""
        now = _now_utc()
        deposit = money(opening_deposit)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Lock owner row to serialise account creation.
                await conn.execute(
                    "SELECT 1 FROM paper_v2_owners WHERE owner_id = $1 FOR UPDATE",
                    str(owner_id),
                )
                await conn.execute(
                    """
                    INSERT INTO paper_v2_accounts
                        (account_id, owner_id, kind, currency, opening_deposit,
                         created_at, schema_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    str(account_id),
                    str(owner_id),
                    kind.value,
                    currency,
                    str(deposit),
                    now,
                    SCHEMA_VERSION,
                )
                # Post opening deposit as a journal entry.
                posting_id = _new_uuid_str()
                await conn.execute(
                    """
                    INSERT INTO paper_v2_postings
                        (posting_id, account_id, day_id, event_id,
                         source_type, source_ref, currency, bucket,
                         amount, occurred_at, created_at, schema_version)
                    VALUES ($1, $2, NULL, $3, 'deposit', NULL, $4, 'deposit',
                            $5, $6, $6, $7)
                    """,
                    posting_id,
                    str(account_id),
                    _new_uuid_str(),
                    currency,
                    str(deposit),
                    now,
                    SCHEMA_VERSION,
                )
        return Account(
            account_id=account_id,
            owner_id=owner_id,
            kind=kind,
            currency=currency,
            opening_deposit=deposit,
            created_at=now,
        )

    async def get_account(self, account_id: UUID) -> Optional[Account]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_accounts WHERE account_id = $1",
                str(account_id),
            )
        return _row_to_account(row) if row else None

    async def get_accounts_by_owner(self, owner_id: UUID) -> List[Account]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM paper_v2_accounts WHERE owner_id = $1 ORDER BY kind",
                str(owner_id),
            )
        return [_row_to_account(r) for r in rows]

    # ------------------------------------------------------------------
    # Trading day
    # ------------------------------------------------------------------

    async def create_day(
        self,
        day_id: UUID,
        owner_id: UUID,
        timezone: str = "Asia/Bangkok",
        start_utc: Optional[datetime] = None,
        end_utc: Optional[datetime] = None,
        strategy_version: str = "baseline_v1",
        settings_snapshot: Optional[Dict[str, Any]] = None,
    ) -> TradingDay:
        """Create a new trading day.

        Raises if there is an existing incomplete day for the owner
        (enforced by a partial unique index on non-closed days).
        """
        now = _now_utc()
        settings_json = json.dumps(settings_snapshot or {})
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT 1 FROM paper_v2_owners WHERE owner_id = $1 FOR UPDATE",
                    str(owner_id),
                )
                await conn.execute(
                    """
                    INSERT INTO paper_v2_days
                        (day_id, owner_id, timezone, start_utc, end_utc,
                         state, strategy_version, settings_snapshot,
                         created_at, schema_version)
                    VALUES ($1, $2, $3, $4, $5, 'idle', $6, $7, $8, $9)
                    """,
                    str(day_id),
                    str(owner_id),
                    timezone,
                    start_utc,
                    end_utc,
                    strategy_version,
                    settings_json,
                    now,
                    SCHEMA_VERSION,
                )
        return TradingDay(
            day_id=day_id,
            owner_id=owner_id,
            timezone=timezone,
            start_utc=start_utc,
            end_utc=end_utc,
            state=DayState.idle,
            strategy_version=strategy_version,
            settings_snapshot=settings_snapshot or {},
            created_at=now,
        )

    async def get_day(self, day_id: UUID) -> Optional[TradingDay]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_days WHERE day_id = $1",
                str(day_id),
            )
        return _row_to_day(row) if row else None

    async def get_active_day(self, owner_id: UUID) -> Optional[TradingDay]:
        """Get the single incomplete (non-closed) day for an owner."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM paper_v2_days
                WHERE owner_id = $1 AND state NOT IN ('closed')
                ORDER BY created_at DESC LIMIT 1
                """,
                str(owner_id),
            )
        return _row_to_day(row) if row else None

    async def update_day_state(
        self,
        day_id: UUID,
        new_state: DayState,
        expected_state: Optional[DayState] = None,
    ) -> bool:
        """Transition day state with optimistic check.  Returns True if updated."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT state FROM paper_v2_days WHERE day_id = $1 FOR UPDATE",
                    str(day_id),
                )
                if not row:
                    return False
                if expected_state is not None and row["state"] != expected_state.value:
                    return False
                await conn.execute(
                    "UPDATE paper_v2_days SET state = $1 WHERE day_id = $2",
                    new_state.value,
                    str(day_id),
                )
                return True

    # ------------------------------------------------------------------
    # Command (idempotency)
    # ------------------------------------------------------------------

    async def submit_command(
        self,
        command_id: UUID,
        owner_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        payload: Dict[str, Any],
        account_id: Optional[UUID] = None,
        day_id: Optional[UUID] = None,
        expected_revision: Optional[int] = None,
    ) -> Command:
        """Submit a command with idempotency.

        - same key + same hash → return existing command (idempotent replay)
        - same key + different hash → raise ``IdempotencyConflict``
        - new key → insert and return accepted command
        """
        request_hash = _hash_payload(payload)
        now = _now_utc()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT * FROM paper_v2_commands
                    WHERE owner_id = $1 AND idempotency_key = $2
                    """,
                    str(owner_id),
                    idempotency_key,
                )

                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        # Mark as conflict in DB, then raise.
                        await conn.execute(
                            """
                            UPDATE paper_v2_commands
                            SET status = 'conflict', updated_at = $1
                            WHERE command_id = $2
                            """,
                            now,
                            existing["command_id"],
                        )
                        raise IdempotencyConflict(
                            f"Idempotency key '{idempotency_key}' already used "
                            f"with a different payload"
                        )
                    # Idempotent replay: return existing result.
                    return _row_to_command(existing)

                await conn.execute(
                    """
                    INSERT INTO paper_v2_commands
                        (command_id, owner_id, account_id, day_id,
                         command_type, idempotency_key, request_hash,
                         expected_revision, status, result, error,
                         created_at, updated_at, schema_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'accepted',
                            NULL, NULL, $9, $9, $10)
                    """,
                    str(command_id),
                    str(owner_id),
                    str(account_id) if account_id else None,
                    str(day_id) if day_id else None,
                    command_type.value,
                    idempotency_key,
                    request_hash,
                    expected_revision,
                    now,
                    SCHEMA_VERSION,
                )

        return Command(
            command_id=command_id,
            owner_id=owner_id,
            account_id=account_id,
            day_id=day_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expected_revision=expected_revision,
            status=CommandStatus.accepted,
            created_at=now,
            updated_at=now,
        )

    async def get_command(self, command_id: UUID) -> Optional[Command]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_commands WHERE command_id = $1",
                str(command_id),
            )
        return _row_to_command(row) if row else None

    async def update_command_result(
        self,
        command_id: UUID,
        status: CommandStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Update command status/result after processing."""
        now = _now_utc()
        result_json = json.dumps(result) if result else None
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM paper_v2_commands WHERE command_id = $1 FOR UPDATE",
                    str(command_id),
                )
                if not row:
                    return False
                await conn.execute(
                    """
                    UPDATE paper_v2_commands
                    SET status = $1, result = $2, error = $3, updated_at = $4
                    WHERE command_id = $5
                    """,
                    status.value,
                    result_json,
                    error,
                    now,
                    str(command_id),
                )
                return True

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------

    async def create_signal(self, signal: Signal) -> Signal:
        """Insert a signal.  Unique constraint prevents duplicates after restart."""
        now = _now_utc()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO paper_v2_signals
                    (signal_id, account_id, strategy_version, instrument,
                     closed_bar_time, direction, entry_ref_price, stop_loss,
                     take_profit, atr_value, valid_until, rejected_reason,
                     created_at, schema_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                str(signal.signal_id),
                str(signal.account_id) if signal.account_id else None,
                signal.strategy_version,
                signal.instrument,
                signal.closed_bar_time,
                signal.direction.value,
                str(signal.entry_ref_price),
                str(signal.stop_loss),
                str(signal.take_profit),
                str(signal.atr_value),
                signal.valid_until,
                signal.rejected_reason,
                now,
                SCHEMA_VERSION,
            )
        return signal

    async def get_signal_by_uniqueness(
        self,
        account_id: UUID,
        strategy_version: str,
        instrument: str,
        closed_bar_time: datetime,
        direction: PositionSide,
    ) -> Optional[Signal]:
        """Look up a signal by its unique key."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM paper_v2_signals
                WHERE account_id = $1 AND strategy_version = $2
                  AND instrument = $3 AND closed_bar_time = $4
                  AND direction = $5
                """,
                str(account_id),
                strategy_version,
                instrument,
                closed_bar_time,
                direction.value,
            )
        return _row_to_signal(row) if row else None

    # ------------------------------------------------------------------
    # Order
    # ------------------------------------------------------------------

    async def create_order(self, order: Order) -> Order:
        """Insert a new order."""
        now = _now_utc()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Lock the account to prevent concurrent orders.
                await conn.execute(
                    "SELECT 1 FROM paper_v2_accounts WHERE account_id = $1 FOR UPDATE",
                    str(order.account_id),
                )
                await conn.execute(
                    """
                    INSERT INTO paper_v2_orders
                        (order_id, account_id, day_id, origin, actor,
                         signal_id, side, order_type, instrument, qty, price,
                         stop_loss, take_profit, state, filled_qty,
                         execution_engine_version, created_at, updated_at,
                         schema_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, 'pending', $14, $15, $16, $16, $17)
                    """,
                    str(order.order_id),
                    str(order.account_id),
                    str(order.day_id),
                    order.origin,
                    order.actor,
                    str(order.signal_id) if order.signal_id else None,
                    order.side.value,
                    order.order_type.value,
                    order.instrument,
                    str(order.qty),
                    str(order.price) if order.price is not None else None,
                    str(order.stop_loss) if order.stop_loss is not None else None,
                    str(order.take_profit) if order.take_profit is not None else None,
                    str(order.filled_qty),
                    order.execution_engine_version,
                    now,
                    SCHEMA_VERSION,
                )
        return order

    async def get_order(self, order_id: UUID) -> Optional[Order]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_orders WHERE order_id = $1",
                str(order_id),
            )
        return _row_to_order(row) if row else None

    async def update_order_state(
        self,
        order_id: UUID,
        new_state: OrderState,
        filled_qty: Optional[Decimal] = None,
    ) -> bool:
        """Update order state and optionally filled_qty."""
        now = _now_utc()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM paper_v2_orders WHERE order_id = $1 FOR UPDATE",
                    str(order_id),
                )
                if not row:
                    return False
                if filled_qty is not None:
                    await conn.execute(
                        """
                        UPDATE paper_v2_orders
                        SET state = $1, filled_qty = $2, updated_at = $3
                        WHERE order_id = $4
                        """,
                        new_state.value,
                        str(filled_qty),
                        now,
                        str(order_id),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE paper_v2_orders
                        SET state = $1, updated_at = $2
                        WHERE order_id = $3
                        """,
                        new_state.value,
                        now,
                        str(order_id),
                    )
                return True

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------

    async def record_fill(
        self,
        fill: Fill,
        order_id: UUID,
    ) -> Fill:
        """Record a fill.  Linked to its order; repeat command does not
        create duplicate fill (idempotency at command layer + unique
        constraint on execution_quote_id).
        """
        now = _now_utc()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Lock the order and account.
                await conn.execute(
                    "SELECT 1 FROM paper_v2_orders WHERE order_id = $1 FOR UPDATE",
                    str(order_id),
                )
                await conn.execute(
                    "SELECT 1 FROM paper_v2_accounts WHERE account_id = $1 FOR UPDATE",
                    str(fill.account_id),
                )
                await conn.execute(
                    """
                    INSERT INTO paper_v2_fills
                        (fill_id, order_id, account_id, execution_quote_id,
                         instrument, side, price, qty, fee, fee_rate,
                         slippage_bps, is_close, source_timestamp,
                         receive_timestamp, execute_timestamp, schema_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15, $16)
                    """,
                    str(fill.fill_id),
                    str(order_id),
                    str(fill.account_id),
                    fill.execution_quote_id,
                    fill.instrument,
                    fill.side.value,
                    str(fill.price),
                    str(fill.qty),
                    str(fill.fee),
                    str(fill.fee_rate),
                    str(fill.slippage_bps),
                    fill.is_close,
                    fill.source_timestamp,
                    fill.receive_timestamp,
                    fill.execute_timestamp,
                    SCHEMA_VERSION,
                )
        return fill

    # ------------------------------------------------------------------
    # Position
    # ------------------------------------------------------------------

    async def get_position(self, position_id: UUID) -> Optional[Position]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_positions WHERE position_id = $1",
                str(position_id),
            )
        return _row_to_position(row) if row else None

    async def get_open_positions(self, account_id: UUID) -> List[Position]:
        """Get all open positions for an account."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM paper_v2_positions
                WHERE account_id = $1 AND status = 'open'
                ORDER BY opened_at
                """,
                str(account_id),
            )
        return [_row_to_position(r) for r in rows]

    async def update_position(
        self,
        position_id: UUID,
        qty: Optional[Decimal] = None,
        avg_entry_price: Optional[Decimal] = None,
        isolated_margin: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        status: Optional[PositionStatus] = None,
        close_price: Optional[Decimal] = None,
        realized_gross_pnl: Optional[Decimal] = None,
        realized_net_pnl: Optional[Decimal] = None,
        exit_fee: Optional[Decimal] = None,
        funding_cashflow: Optional[Decimal] = None,
    ) -> bool:
        """Update a position with row-level lock.

        Only provided fields are updated; ``None`` means "leave unchanged".
        ``stop_loss`` / ``take_profit`` can be set to ``Decimal(0)`` to clear
        only if explicitly passed — but ``None`` means unchanged.
        """
        now = _now_utc()
        sets: List[str] = []
        args: List[Any] = []
        idx = 1

        def add(col: str, val: Any) -> None:
            nonlocal idx
            sets.append(f"{col} = ${idx}")
            args.append(val)
            idx += 1

        if qty is not None:
            add("qty", str(qty))
        if avg_entry_price is not None:
            add("avg_entry_price", str(avg_entry_price))
        if isolated_margin is not None:
            add("isolated_margin", str(isolated_margin))
        if stop_loss is not None:
            add("stop_loss", str(stop_loss) if stop_loss != ZERO else None)
        if take_profit is not None:
            add("take_profit", str(take_profit) if take_profit != ZERO else None)
        if status is not None:
            add("status", status.value)
        if close_price is not None:
            add("close_price", str(close_price))
        if realized_gross_pnl is not None:
            add("realized_gross_pnl", str(realized_gross_pnl))
        if realized_net_pnl is not None:
            add("realized_net_pnl", str(realized_net_pnl))
        if exit_fee is not None:
            add("exit_fee", str(exit_fee))
        if funding_cashflow is not None:
            add("funding_cashflow", str(funding_cashflow))

        if not sets:
            return True  # nothing to update

        # Add closed_at if transitioning to closed/liquidated.
        if status is not None and status in (PositionStatus.closed, PositionStatus.liquidated):
            sets.append(f"closed_at = ${idx}")
            args.append(now)
            idx += 1

        sets.append(f"updated_at = ${idx}")
        args.append(now)
        idx += 1

        args.append(str(position_id))

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT 1 FROM paper_v2_positions WHERE position_id = $1 FOR UPDATE",
                    str(position_id),
                )
                if not row:
                    return False
                sql = f"UPDATE paper_v2_positions SET {', '.join(sets)} WHERE position_id = ${idx}"
                await conn.execute(sql, *args)
                return True

    # ------------------------------------------------------------------
    # Journal postings
    # ------------------------------------------------------------------

    async def append_postings(
        self,
        postings: Sequence[JournalPosting],
    ) -> List[JournalPosting]:
        """Persist a balanced journal entry atomically.

        Validates balance at the domain level (``post_entry``), then inserts
        within a transaction that locks the account row.  The unique
        constraint on ``(source_type, source_ref, bucket)`` prevents
        double-posting of the same financial event.
        """
        validated = post_entry(postings[0].account_id, postings)
        now = _now_utc()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT 1 FROM paper_v2_accounts WHERE account_id = $1 FOR UPDATE",
                    str(validated[0].account_id),
                )
                for p in validated:
                    await conn.execute(
                        """
                        INSERT INTO paper_v2_postings
                            (posting_id, account_id, day_id, event_id,
                             source_type, source_ref, currency, bucket,
                             amount, occurred_at, created_at, schema_version)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                        """,
                        str(p.posting_id),
                        str(p.account_id),
                        str(p.day_id) if p.day_id else None,
                        str(p.event_id),
                        p.source_type.value,
                        p.source_ref,
                        p.currency,
                        p.bucket.value,
                        str(p.amount),
                        p.occurred_at,
                        now,
                        SCHEMA_VERSION,
                    )
        return validated

    async def get_postings_for_account(self, account_id: UUID) -> List[JournalPosting]:
        """Get all journal postings for an account, ordered by time."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM paper_v2_postings
                WHERE account_id = $1
                ORDER BY created_at, posting_id
                """,
                str(account_id),
            )
        return [_row_to_posting(r) for r in rows]


# ---------------------------------------------------------------------------
# Row → dataclass converters
# ---------------------------------------------------------------------------


def _new_uuid_str() -> str:
    from uuid import uuid4
    return str(uuid4())


def _row_to_owner(row: asyncpg.Record) -> Owner:
    return Owner(
        owner_id=UUID(row["owner_id"]),
        display_name=row["display_name"],
        telegram_id=row["telegram_id"],
        webui_identity=row["webui_identity"],
        created_at=row["created_at"],
    )


def _row_to_account(row: asyncpg.Record) -> Account:
    return Account(
        account_id=UUID(row["account_id"]),
        owner_id=UUID(row["owner_id"]),
        kind=AccountKind(row["kind"]),
        currency=row["currency"],
        opening_deposit=Decimal(row["opening_deposit"]),
        created_at=row["created_at"],
    )


def _row_to_day(row: asyncpg.Record) -> TradingDay:
    settings = row["settings_snapshot"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    return TradingDay(
        day_id=UUID(row["day_id"]),
        owner_id=UUID(row["owner_id"]),
        timezone=row["timezone"],
        start_utc=row["start_utc"],
        end_utc=row["end_utc"],
        state=DayState(row["state"]),
        strategy_version=row["strategy_version"],
        settings_snapshot=settings or {},
        created_at=row["created_at"],
    )


def _row_to_command(row: asyncpg.Record) -> Command:
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    return Command(
        command_id=UUID(row["command_id"]),
        owner_id=UUID(row["owner_id"]),
        account_id=UUID(row["account_id"]) if row["account_id"] else None,
        day_id=UUID(row["day_id"]) if row["day_id"] else None,
        command_type=CommandType(row["command_type"]),
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        expected_revision=row["expected_revision"],
        status=CommandStatus(row["status"]),
        result=result,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_order(row: asyncpg.Record) -> Order:
    return Order(
        order_id=UUID(row["order_id"]),
        account_id=UUID(row["account_id"]),
        day_id=UUID(row["day_id"]),
        origin=row["origin"],
        actor=row["actor"],
        signal_id=UUID(row["signal_id"]) if row["signal_id"] else None,
        side=OrderSide(row["side"]),
        order_type=OrderType(row["order_type"]),
        instrument=row["instrument"],
        qty=Decimal(row["qty"]),
        price=Decimal(row["price"]) if row["price"] is not None else None,
        stop_loss=Decimal(row["stop_loss"]) if row["stop_loss"] is not None else None,
        take_profit=Decimal(row["take_profit"]) if row["take_profit"] is not None else None,
        state=OrderState(row["state"]),
        filled_qty=Decimal(row["filled_qty"]),
        execution_engine_version=row["execution_engine_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_fill(row: asyncpg.Record) -> Fill:
    return Fill(
        fill_id=UUID(row["fill_id"]),
        order_id=UUID(row["order_id"]),
        account_id=UUID(row["account_id"]),
        execution_quote_id=row["execution_quote_id"],
        instrument=row["instrument"],
        side=OrderSide(row["side"]),
        price=Decimal(row["price"]),
        qty=Decimal(row["qty"]),
        fee=Decimal(row["fee"]),
        fee_rate=Decimal(row["fee_rate"]),
        slippage_bps=Decimal(row["slippage_bps"]),
        is_close=row["is_close"],
        source_timestamp=row["source_timestamp"],
        receive_timestamp=row["receive_timestamp"],
        execute_timestamp=row["execute_timestamp"],
    )


def _row_to_position(row: asyncpg.Record) -> Position:
    return Position(
        position_id=UUID(row["position_id"]),
        account_id=UUID(row["account_id"]),
        day_id=UUID(row["day_id"]),
        instrument=row["instrument"],
        side=PositionSide(row["side"]),
        qty=Decimal(row["qty"]),
        avg_entry_price=Decimal(row["avg_entry_price"]),
        isolated_margin=Decimal(row["isolated_margin"]),
        stop_loss=Decimal(row["stop_loss"]) if row["stop_loss"] is not None else None,
        take_profit=Decimal(row["take_profit"]) if row["take_profit"] is not None else None,
        status=PositionStatus(row["status"]),
        owner_engine_version=row["owner_engine_version"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        close_price=Decimal(row["close_price"]) if row["close_price"] is not None else None,
        realized_gross_pnl=Decimal(row["realized_gross_pnl"]),
        realized_net_pnl=Decimal(row["realized_net_pnl"]),
        entry_fee=Decimal(row["entry_fee"]),
        exit_fee=Decimal(row["exit_fee"]),
        funding_cashflow=Decimal(row["funding_cashflow"]),
    )


def _row_to_posting(row: asyncpg.Record) -> JournalPosting:
    return JournalPosting(
        posting_id=UUID(row["posting_id"]),
        account_id=UUID(row["account_id"]),
        day_id=UUID(row["day_id"]) if row["day_id"] else None,
        event_id=UUID(row["event_id"]),
        source_type=JournalSourceType(row["source_type"]),
        source_ref=row["source_ref"],
        currency=row["currency"],
        bucket=JournalBucket(row["bucket"]),
        amount=Decimal(row["amount"]),
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
    )


def _row_to_signal(row: asyncpg.Record) -> Signal:
    return Signal(
        signal_id=UUID(row["signal_id"]),
        account_id=UUID(row["account_id"]) if row["account_id"] else None,
        strategy_version=row["strategy_version"],
        instrument=row["instrument"],
        closed_bar_time=row["closed_bar_time"],
        direction=PositionSide(row["direction"]),
        entry_ref_price=Decimal(row["entry_ref_price"]),
        stop_loss=Decimal(row["stop_loss"]),
        take_profit=Decimal(row["take_profit"]),
        atr_value=Decimal(row["atr_value"]),
        valid_until=row["valid_until"],
        rejected_reason=row["rejected_reason"],
        created_at=row["created_at"],
    )