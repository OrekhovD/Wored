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
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from paper_trading.contracts import (
    SCHEMA_VERSION,
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
    Signal,
    TradingDay,
    money,
)
from paper_trading.ledger import post_entry

log = logging.getLogger(__name__)
ZERO = Decimal(0)

# ---------------------------------------------------------------------------
# Dataclass ↔ row helpers
# ---------------------------------------------------------------------------


def _dc_to_row(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a dict suitable for asyncpg."""
    if not is_dataclass(obj):
        raise TypeError(f"Expected dataclass, got {type(obj)}")
    d: dict[str, Any] = {}
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


def _hash_payload(payload: dict[str, Any]) -> str:
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
    async def create(cls, dsn: str, min_size: int = 2, max_size: int = 10) -> PaperRepository:
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
        telegram_id: int | None = None,
        webui_identity: str | None = None,
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

    async def get_owner(self, owner_id: UUID) -> Owner | None:
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
        opening_deposit: Decimal = Decimal(1000),
    ) -> Account:
        """Create a simulation account and post the opening deposit."""
        now = _now_utc()
        deposit = money(opening_deposit)
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_account(self, account_id: UUID) -> Account | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_accounts WHERE account_id = $1",
                str(account_id),
            )
        return _row_to_account(row) if row else None

    async def get_accounts_by_owner(self, owner_id: UUID) -> list[Account]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM paper_v2_accounts WHERE owner_id = $1 ORDER BY kind",
                str(owner_id),
            )
        return [_row_to_account(r) for r in rows]

    async def get_account_by_kind(
        self,
        owner_id: UUID,
        kind: AccountKind,
    ) -> Account | None:
        """Get the account for an owner with the given kind, or None."""
        kind_val = kind.value if isinstance(kind, AccountKind) else str(kind)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_accounts WHERE owner_id = $1 AND kind = $2",
                str(owner_id),
                kind_val,
            )
        return _row_to_account(row) if row else None

    async def get_account_balance(self, account_id: UUID) -> Decimal:
        """Return available balance from signed financial cashflows."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(amount), 0) AS balance
                FROM paper_v2_postings
                WHERE account_id = $1 AND bucket <> 'realized_net_pnl'
                """,
                str(account_id),
            )
        return Decimal(row["balance"]) if row else Decimal(0)

    # ------------------------------------------------------------------
    # Trading day
    # ------------------------------------------------------------------

    async def create_day(
        self,
        day_id: UUID,
        owner_id: UUID,
        timezone: str = "Asia/Bangkok",
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        strategy_version: str = "baseline_v1",
        settings_snapshot: dict[str, Any] | None = None,
    ) -> TradingDay:
        """Create a new trading day.

        Raises if there is an existing incomplete day for the owner
        (enforced by a partial unique index on non-closed days).
        """
        now = _now_utc()
        settings_json = json.dumps(settings_snapshot or {})
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_day(self, day_id: UUID) -> TradingDay | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_days WHERE day_id = $1",
                str(day_id),
            )
        return _row_to_day(row) if row else None

    async def get_active_day(self, owner_id: UUID) -> TradingDay | None:
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
        expected_state: DayState | None = None,
    ) -> bool:
        """Transition day state with optimistic check.  Returns True if updated."""
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_days_needing_closure(self) -> list[TradingDay]:
        """Days whose close-out is due or was deferred.

        Returns days in ``running`` whose ``end_utc`` has passed, plus every
        ``settlement_pending``/``closing`` day (a deferred close must keep being
        retried).  Selecting these is exactly what the old runner failed to do —
        it only looked at ``running`` so a ``settlement_pending`` day was picked
        up once and then orphaned forever.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM paper_v2_days
                WHERE state IN ('running', 'settlement_pending', 'closing')
                  AND (state <> 'running'
                       OR (end_utc IS NOT NULL AND end_utc < now()))
                ORDER BY owner_id, end_utc NULLS LAST
                """
            )
        return [_row_to_day(r) for r in rows]

    async def get_rollover_candidates(self) -> list[TradingDay]:
        """Most recent ``closed`` day per auto owner with no incomplete successor.

        Only owners that have an ``auto`` account expect unattended rollover, and
        a successor is started only when the owner currently has no non-closed
        day (the ``NOT EXISTS`` mirrors ``uq_days_one_incomplete``).
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (d.owner_id) d.*
                FROM paper_v2_days AS d
                JOIN paper_v2_accounts AS a
                    ON a.owner_id = d.owner_id AND a.kind = 'auto'
                WHERE d.state = 'closed'
                  AND NOT EXISTS (
                      SELECT 1 FROM paper_v2_days AS x
                      WHERE x.owner_id = d.owner_id AND x.state NOT IN ('closed')
                  )
                ORDER BY d.owner_id, d.end_utc DESC NULLS LAST, d.created_at DESC
                """
            )
        return [_row_to_day(r) for r in rows]

    # ------------------------------------------------------------------
    # Command (idempotency)
    # ------------------------------------------------------------------

    async def submit_command(
        self,
        command_id: UUID,
        owner_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        payload: dict[str, Any],
        account_id: UUID | None = None,
        day_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> Command:
        """Submit a command with idempotency.

        - same key + same hash → return existing command (idempotent replay)
        - same key + different hash → raise ``IdempotencyConflict``
        - new key → insert and return accepted command
        """
        request_hash = _hash_payload(payload)
        now = _now_utc()

        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_command(self, command_id: UUID) -> Command | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_commands WHERE command_id = $1",
                str(command_id),
            )
        return _row_to_command(row) if row else None

    async def complete_command(self, command_id: UUID, result: dict | None = None) -> bool:
        """Mark a command as completed so poll() stops returning it."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE paper_v2_commands SET status='completed', result=$1, updated_at=$2 WHERE command_id=$3",
                json.dumps(result) if result else None, _now_utc(), str(command_id),
            )
        return True

    async def update_command_result(
        self,
        command_id: UUID,
        status: CommandStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Update command status/result after processing."""
        now = _now_utc()
        result_json = json.dumps(result) if result else None
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def claim_command(self, command_id: UUID) -> Command | None:
        """Atomically transition an ``accepted`` command to ``processing``.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so two concurrent runners
        can never claim the same command: the loser's row is locked/skipped and
        it receives ``None``.  Only commands currently ``accepted`` are
        claimable — ``processing``/``completed``/``failed``/``conflict`` are
        left for their owner or for ``requeue_stale_processing`` after a crash.

        Returns the freshly-claimed command row, or ``None`` if it was not
        claimable at this instant.
        """
        now = _now_utc()
        async with self._pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval(
                """
                SELECT command_id FROM paper_v2_commands
                WHERE command_id = $1 AND status = 'accepted'
                FOR UPDATE SKIP LOCKED
                """,
                str(command_id),
            )
            if locked is None:
                return None
            row = await conn.fetchrow(
                """
                UPDATE paper_v2_commands
                SET status = 'processing', updated_at = $1
                WHERE command_id = $2
                RETURNING *
                """,
                now,
                str(command_id),
            )
        return _row_to_command(row) if row else None

    async def requeue_command(self, command_id: UUID, error: str | None = None) -> bool:
        """Release a ``processing`` command back to ``accepted`` for retry.

        Used when a command could not be completed this cycle (e.g. a
        finish_day that deferred to ``settlement_pending`` because the market
        snapshot was unavailable).  Only a command still in ``processing`` is
        released, so a command already completed/failed is never resurrected.
        """
        now = _now_utc()
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT status FROM paper_v2_commands WHERE command_id = $1 FOR UPDATE",
                str(command_id),
            )
            if not row or row["status"] != "processing":
                return False
            await conn.execute(
                """
                UPDATE paper_v2_commands
                SET status = 'accepted', error = $1, updated_at = $2
                WHERE command_id = $3
                """,
                error,
                now,
                str(command_id),
            )
        return True

    async def requeue_stale_processing(self, max_age_seconds: int = 60) -> int:
        """Crash recovery: return ``processing`` commands stuck longer than the
        lease window to ``accepted`` so a fresh runner retries them.

        Returns the number of commands requeued.
        """
        now = _now_utc()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE paper_v2_commands
                SET status = 'accepted', updated_at = $1
                WHERE status = 'processing'
                  AND updated_at < $1 - make_interval(secs => $2::int)
                """,
                now,
                int(max_age_seconds),
            )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

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
    ) -> Signal | None:
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
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_order(self, order_id: UUID) -> Order | None:
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
        filled_qty: Decimal | None = None,
    ) -> bool:
        """Update order state and optionally filled_qty."""
        now = _now_utc()
        async with self._pool.acquire() as conn, conn.transaction():
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
        async with self._pool.acquire() as conn, conn.transaction():
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

    async def commit_open_fill(
        self,
        *,
        order_id: UUID,
        fill: Fill,
        position: Position,
        postings: Sequence[JournalPosting],
    ) -> None:
        """Atomically commit an opening fill, position and journal event."""
        if fill.order_id != order_id:
            raise ValueError("fill.order_id must match order_id")
        if position.position_id != order_id:
            raise ValueError("position_id must equal its originating order_id")
        if fill.account_id != position.account_id:
            raise ValueError("fill and position must belong to one account")
        validated = post_entry(position.account_id, postings)
        now = _now_utc()

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM paper_v2_accounts WHERE account_id = $1 FOR UPDATE",
                str(position.account_id),
            )
            balance_row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(amount), 0) AS balance
                FROM paper_v2_postings
                WHERE account_id = $1 AND bucket <> 'realized_net_pnl'
                """,
                str(position.account_id),
            )
            available_balance = Decimal(balance_row["balance"]) if balance_row else ZERO
            event_delta = sum((posting.amount for posting in validated), ZERO)
            if available_balance + event_delta < ZERO:
                raise ValueError("insufficient account balance at commit")
            order_row = await conn.fetchrow(
                "SELECT account_id, state FROM paper_v2_orders WHERE order_id = $1 FOR UPDATE",
                str(order_id),
            )
            if not order_row:
                raise ValueError(f"order not found: {order_id}")
            if str(order_row["account_id"]) != str(position.account_id):
                raise ValueError("order account does not match fill account")
            if order_row["state"] not in (OrderState.pending.value, OrderState.submitted.value):
                raise ValueError(f"order is not fillable: {order_row['state']}")

            await conn.execute(
                """
                UPDATE paper_v2_orders
                SET state = 'filled', filled_qty = $1, updated_at = $2
                WHERE order_id = $3
                """,
                str(fill.qty),
                now,
                str(order_id),
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
                str(fill.fill_id), str(order_id), str(fill.account_id),
                fill.execution_quote_id, fill.instrument, fill.side.value,
                str(fill.price), str(fill.qty), str(fill.fee), str(fill.fee_rate),
                str(fill.slippage_bps), fill.is_close, fill.source_timestamp,
                fill.receive_timestamp, fill.execute_timestamp, SCHEMA_VERSION,
            )
            await conn.execute(
                """
                INSERT INTO paper_v2_positions
                    (position_id, account_id, day_id, instrument, side, qty,
                     avg_entry_price, isolated_margin, stop_loss, take_profit,
                     status, owner_engine_version, opened_at, entry_fee,
                     exit_fee, funding_cashflow, realized_gross_pnl,
                     realized_net_pnl, schema_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        'open', $11, $12, $13, 0, 0, 0, 0, $14)
                """,
                str(position.position_id), str(position.account_id),
                str(position.day_id), position.instrument, position.side.value,
                str(position.qty), str(position.avg_entry_price),
                str(position.isolated_margin),
                str(position.stop_loss) if position.stop_loss is not None else None,
                str(position.take_profit) if position.take_profit is not None else None,
                position.owner_engine_version, position.opened_at or now,
                str(position.entry_fee), SCHEMA_VERSION,
            )
            await self._insert_postings(conn, validated, now)

    async def commit_position_close(
        self,
        *,
        position_id: UUID,
        fill: Fill,
        close_price: Decimal,
        realized_gross_pnl: Decimal,
        realized_net_pnl: Decimal,
        exit_fee: Decimal,
        postings: Sequence[JournalPosting],
    ) -> None:
        """Atomically commit a full close fill, position state and journal."""
        if fill.order_id != position_id:
            raise ValueError("close fill must reference the originating order/position id")
        validated = post_entry(fill.account_id, postings)
        now = _now_utc()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM paper_v2_accounts WHERE account_id = $1 FOR UPDATE",
                str(fill.account_id),
            )
            position_row = await conn.fetchrow(
                "SELECT account_id, status FROM paper_v2_positions WHERE position_id = $1 FOR UPDATE",
                str(position_id),
            )
            if not position_row:
                raise ValueError(f"position not found: {position_id}")
            if str(position_row["account_id"]) != str(fill.account_id):
                raise ValueError("position account does not match fill account")
            if position_row["status"] != PositionStatus.open.value:
                raise ValueError(f"position is not open: {position_row['status']}")

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
                str(fill.fill_id), str(fill.order_id), str(fill.account_id),
                fill.execution_quote_id, fill.instrument, fill.side.value,
                str(fill.price), str(fill.qty), str(fill.fee), str(fill.fee_rate),
                str(fill.slippage_bps), fill.is_close, fill.source_timestamp,
                fill.receive_timestamp, fill.execute_timestamp, SCHEMA_VERSION,
            )
            await conn.execute(
                """
                UPDATE paper_v2_positions
                SET qty = 0, status = 'closed', closed_at = $1,
                    close_price = $2, realized_gross_pnl = $3,
                    realized_net_pnl = $4, exit_fee = $5
                WHERE position_id = $6
                """,
                now, str(money(close_price)), str(money(realized_gross_pnl)),
                str(money(realized_net_pnl)), str(money(exit_fee)),
                str(position_id),
            )
            await self._insert_postings(conn, validated, now)

    @staticmethod
    async def _insert_postings(
        conn: Any,
        postings: Sequence[JournalPosting],
        created_at: datetime,
    ) -> None:
        for p in postings:
            await conn.execute(
                """
                INSERT INTO paper_v2_postings
                    (posting_id, account_id, day_id, event_id, source_type,
                     source_ref, currency, bucket, amount, occurred_at,
                     created_at, schema_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                str(p.posting_id), str(p.account_id),
                str(p.day_id) if p.day_id else None, str(p.event_id),
                p.source_type.value, p.source_ref, p.currency, p.bucket.value,
                str(p.amount), p.occurred_at, created_at, SCHEMA_VERSION,
            )

    # ------------------------------------------------------------------
    # Position
    # ------------------------------------------------------------------

    async def get_position(self, position_id: UUID) -> Position | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_v2_positions WHERE position_id = $1",
                str(position_id),
            )
        return _row_to_position(row) if row else None

    async def get_position_by_id(self, position_id: UUID) -> Position | None:
        """Get a position by its ID (alias for get_position)."""
        return await self.get_position(position_id)

    async def get_open_positions(self, account_id: UUID) -> list[Position]:
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

    async def get_open_positions_by_account(self, account_id: UUID) -> list[Position]:
        """Get all open positions for an account (alias for get_open_positions)."""
        return await self.get_open_positions(account_id)

    async def get_open_positions_by_day(self, day_id: UUID) -> list[Position]:
        """Open positions for a trading day, sourced from the DB.

        Used by finish_day so a close-out is correct after a runner restart
        (the in-memory dict alone is not authoritative across crashes).
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM paper_v2_positions
                WHERE day_id = $1 AND status = 'open'
                ORDER BY opened_at
                """,
                str(day_id),
            )
        return [_row_to_position(r) for r in rows]

    async def get_unfilled_orders_by_day(self, day_id: UUID) -> list[Order]:
        """Entry orders for a day that are still open (pending/submitted).

        Used by finish_day to cancel residual entry intents before close-out.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM paper_v2_orders
                WHERE day_id = $1 AND state IN ('pending', 'submitted')
                ORDER BY created_at
                """,
                str(day_id),
            )
        return [_row_to_order(r) for r in rows]

    async def update_position(
        self,
        position_id: UUID,
        qty: Decimal | None = None,
        avg_entry_price: Decimal | None = None,
        isolated_margin: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        status: PositionStatus | None = None,
        close_price: Decimal | None = None,
        realized_gross_pnl: Decimal | None = None,
        realized_net_pnl: Decimal | None = None,
        exit_fee: Decimal | None = None,
        funding_cashflow: Decimal | None = None,
    ) -> bool:
        """Update a position with row-level lock.

        Only provided fields are updated; ``None`` means "leave unchanged".
        ``stop_loss`` / ``take_profit`` can be set to ``Decimal(0)`` to clear
        only if explicitly passed — but ``None`` means unchanged.
        """
        now = _now_utc()
        sets: list[str] = []
        args: list[Any] = []
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
    ) -> list[JournalPosting]:
        """Persist one validated signed-cashflow event atomically.

        Validates event consistency at the domain level, then inserts
        within a transaction that locks the account row.  The unique
        constraint on ``(source_type, source_ref, bucket)`` prevents
        double-posting of the same financial event.
        """
        validated = post_entry(postings[0].account_id, postings)
        now = _now_utc()

        async with self._pool.acquire() as conn, conn.transaction():
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

    async def get_postings_for_account(self, account_id: UUID) -> list[JournalPosting]:
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


def _to_uuid(val: Any) -> UUID:
    """Convert asyncpg UUID or string to Python UUID."""
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


def _row_to_owner(row: asyncpg.Record) -> Owner:
    return Owner(
        owner_id=_to_uuid(row["owner_id"]),
        display_name=row["display_name"],
        telegram_id=row["telegram_id"],
        webui_identity=row["webui_identity"],
        created_at=row["created_at"],
    )


def _row_to_account(row: asyncpg.Record) -> Account:
    return Account(
        account_id=_to_uuid(row["account_id"]),
        owner_id=_to_uuid(row["owner_id"]),
        kind=AccountKind(row["kind"]),
        currency=row["currency"],
        opening_deposit=Decimal(str(row["opening_deposit"])),
        created_at=row["created_at"],
    )


def _row_to_day(row: asyncpg.Record) -> TradingDay:
    settings = row["settings_snapshot"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    return TradingDay(
        day_id=_to_uuid(row["day_id"]),
        owner_id=_to_uuid(row["owner_id"]),
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
        command_id=_to_uuid(row["command_id"]),
        owner_id=_to_uuid(row["owner_id"]),
        account_id=_to_uuid(row["account_id"]) if row["account_id"] else None,
        day_id=_to_uuid(row["day_id"]) if row["day_id"] else None,
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
        order_id=_to_uuid(row["order_id"]),
        account_id=_to_uuid(row["account_id"]),
        day_id=_to_uuid(row["day_id"]),
        origin=row["origin"],
        actor=row["actor"],
        signal_id=_to_uuid(row["signal_id"]) if row["signal_id"] else None,
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
        fill_id=_to_uuid(row["fill_id"]),
        order_id=_to_uuid(row["order_id"]),
        account_id=_to_uuid(row["account_id"]),
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
        position_id=_to_uuid(row["position_id"]),
        account_id=_to_uuid(row["account_id"]),
        day_id=_to_uuid(row["day_id"]),
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
        posting_id=_to_uuid(row["posting_id"]),
        account_id=_to_uuid(row["account_id"]),
        day_id=_to_uuid(row["day_id"]) if row["day_id"] else None,
        event_id=_to_uuid(row["event_id"]),
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
        signal_id=_to_uuid(row["signal_id"]),
        account_id=_to_uuid(row["account_id"]) if row["account_id"] else None,
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
