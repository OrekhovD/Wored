"""PaperTradingRunner — orchestration loop for paper-trading execution.

Responsibilities:
  * 2-second poll cycle: check SL/TP, process manual commands, evaluate signals
    on new 1m close.
  * Lease fencing token: every mutation carries a monotonically increasing
    token; stale mutations from a previous runner instance are rejected.
  * Recovery from PostgreSQL on restart: load unfinished commands, open orders,
    open positions; reconcile the ledger; cancel expired signal intents; block
    new entries until recovery is complete.
  * Heartbeat to Redis every 5 seconds.
  * Long-running AI agent calls do NOT block SL/TP checks, funding, or manual
    close operations (they run in a separate task group).
  * Signal→order→fill→ledger chain: signal found → check_order_risk →
    create_order → pre-fill recheck → execute_market_order → atomically commit
    fill + position + ledger.
  * SL/TP: check_stop_trigger → execute_close → atomically commit close fill,
    position state and ledger.  Remove from dict only after DB commit.
  * close_position: use position_id (not account_id) to find position,
    verify ownership, execute close.
  * start_day: transition day state idle→running in DB, set
    _entries_blocked=False for auto account.
  * finish_day: cancel pending entries, close all positions, transition
    day→closed.

Uses the domain contracts from ``paper_trading.contracts`` for Position,
Command, Signal, Heartbeat, Decision, and StatusDTO.

Python 3.9 compatible.  All money Decimal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from paper_trading.contracts import (
    ZERO,
    Account,
    AccountKind,
    Command,
    CommandType,
    DayState,
    Decision,
    Fill,
    Order,
    OrderSide,
    OrderState,
    OrderType,
    Position,
    PositionSide,
    PositionStatus,
    ReasonCode,
    StatusDTO,
    money,
)
from paper_trading.execution import (
    FillResult,
    Position as ExecutionPosition,
    check_stop_trigger,
    execute_close,
    execute_market_order,
)
from paper_trading.ledger import build_fill_postings
from paper_trading.market import PerpetualSnapshot
from paper_trading.risk import (
    OrderRequest,
    PositionInfo,
    RiskCheckResult,
    RiskSettings,
    check_fill_risk,
    check_order_risk,
)
from paper_trading.strategy import Bar, BaselineV1Strategy
from paper_trading.strategy import Signal as StrategySignal

if TYPE_CHECKING:
    from paper_trading.repository import PaperRepository

log = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
SIGNAL_TTL_SECONDS = 60.0
INSTRUMENT = "BTC-USDT"
LEVERAGE = 10


# ─── Runner-internal data structures ───────────────────────────────────

@dataclass
class RunnerStatus:
    """Snapshot of the runner's current state for presenters / status DTO."""

    running: bool = False
    recovered: bool = False
    fence_token: int = 0
    active_positions: list[Position] = field(default_factory=list)
    pending_commands: list[Command] = field(default_factory=list)
    open_orders: list[Order] = field(default_factory=list)
    last_signal: StrategySignal | None = None
    last_heartbeat_epoch: float = 0.0
    last_poll_epoch: float = 0.0
    entries_blocked: bool = True
    last_error: str | None = None
    last_decision: Decision | None = None
    instance_id: str = ""
    run_id: str = ""


# ─── Abstract store interfaces (dependency-injection) ──────────────────

class RecoveryStore:
    """Abstract interface for loading persisted state on restart.

    Subclasses connect to PostgreSQL.  The runner calls these methods
    during :meth:`PaperTradingRunner.recover`.
    """

    async def load_unfinished_commands(self) -> list[Command]:
        return []

    async def load_open_orders(self) -> list[Order]:
        return []

    async def load_open_positions(self) -> list[Position]:
        return []

    async def load_active_auto_accounts(self) -> list[Account]:
        return []

    async def reconcile_ledger(self, positions: list[Position]) -> dict[str, Any]:
        return {"reconciled": True, "discrepancies": []}

    async def cancel_expired_intents(self, expired_ids: list[str]) -> int:
        return 0


class HeartbeatSink:
    """Abstract interface for heartbeats (Redis)."""

    async def send(self, key: str, payload: dict[str, Any]) -> None:
        pass


class CommandSource:
    """Abstract interface for receiving manual commands."""

    async def poll(self) -> list[Command]:
        return []


class MarketDataSource:
    """Abstract interface for fetching bars on each timeframe."""

    async def fetch_bars(self, timeframe: str, limit: int) -> list[Bar]:
        return []

    async def fetch_current_price(self) -> Decimal | None:
        bars = await self.fetch_bars("1m", 1)
        return bars[-1].close if bars else None

    async def fetch_snapshot(self) -> PerpetualSnapshot | None:
        """Fetch a validated market snapshot for execution/risk checks."""
        return None


# ─── Concrete adapter implementations ──────────────────────────────────

class PgRecoveryStore(RecoveryStore):
    """PostgreSQL-backed recovery store using PaperRepository."""

    def __init__(self, repo: PaperRepository) -> None:
        self._repo = repo

    async def load_unfinished_commands(self) -> list[Command]:
        """Load accepted/processing commands that were not completed."""
        try:
            async with self._repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM paper_v2_commands
                    WHERE status IN ('accepted', 'processing')
                    ORDER BY created_at
                    """
                )
            from paper_trading.repository import _row_to_command
            return [_row_to_command(r) for r in rows]
        except Exception as exc:
            log.warning("load_unfinished_commands failed: %s", exc)
            return []

    async def load_open_orders(self) -> list[Order]:
        """Load pending/partially_filled orders."""
        try:
            async with self._repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM paper_v2_orders
                    WHERE state IN ('pending', 'submitted', 'partially_filled')
                    ORDER BY created_at
                    """
                )
            from paper_trading.repository import _row_to_order
            return [_row_to_order(r) for r in rows]
        except Exception as exc:
            log.warning("load_open_orders failed: %s", exc)
            return []

    async def load_open_positions(self) -> list[Position]:
        """Load all open positions across all accounts."""
        try:
            async with self._repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM paper_v2_positions
                    WHERE status = 'open'
                    ORDER BY opened_at
                    """
                )
            from paper_trading.repository import _row_to_position
            return [_row_to_position(r) for r in rows]
        except Exception as exc:
            log.warning("load_open_positions failed: %s", exc)
            return []

    async def load_active_auto_accounts(self) -> list[Account]:
        """Load auto accounts whose owner currently has a running day."""
        try:
            async with self._repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT a.*
                    FROM paper_v2_accounts AS a
                    JOIN paper_v2_days AS d ON d.owner_id = a.owner_id
                    WHERE a.kind = 'auto' AND d.state = 'running'
                    ORDER BY a.created_at
                    """
                )
            from paper_trading.repository import _row_to_account
            return [_row_to_account(row) for row in rows]
        except Exception as exc:
            log.warning("load_active_auto_accounts failed: %s", exc)
            return []

    async def reconcile_ledger(self, positions: list[Position]) -> dict[str, Any]:
        """Reconcile ledger postings against position state."""
        discrepancies: list[str] = []
        for pos in positions:
            try:
                postings = await self._repo.get_postings_for_account(pos.account_id)
                from paper_trading.ledger import get_account_balance
                balance = get_account_balance(postings, pos.account_id)
                if balance < ZERO:
                    discrepancies.append(
                        f"account {pos.account_id}: negative balance {balance}"
                    )
            except Exception as exc:
                discrepancies.append(f"account {pos.account_id}: reconcile error: {exc}")
        return {"reconciled": len(discrepancies) == 0, "discrepancies": discrepancies}

    async def cancel_expired_intents(self, expired_ids: list[str]) -> int:
        """Cancel expired signal intents in DB."""
        if not expired_ids:
            return 0
        try:
            async with self._repo.pool.acquire() as conn:
                count = 0
                for bid in expired_ids:
                    result = await conn.execute(
                        """
                        UPDATE paper_v2_signals
                        SET rejected_reason = 'expired'
                        WHERE closed_bar_time = $1 AND rejected_reason IS NULL
                        """,
                        bid,
                    )
                    if result and result != "UPDATE 0":
                        count += 1
                return count
        except Exception as exc:
            log.warning("cancel_expired_intents failed: %s", exc)
            return 0


class PgCommandSource(CommandSource):
    """PostgreSQL-backed command source using PaperRepository."""

    def __init__(self, repo: PaperRepository) -> None:
        self._repo = repo

    async def poll(self) -> list[Command]:
        """Poll for accepted commands that need processing."""
        try:
            async with self._repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM paper_v2_commands
                    WHERE status = 'accepted'
                    ORDER BY created_at
                    LIMIT 50
                    """
                )
            from paper_trading.repository import _row_to_command
            return [_row_to_command(r) for r in rows]
        except Exception as exc:
            log.warning("command poll from DB failed: %s", exc)
            return []


class RedisMarketDataSource(MarketDataSource):
    """Redis-backed market data source."""

    def __init__(self, redis_client: Any, contract_code: str = INSTRUMENT) -> None:
        self._redis = redis_client
        self._contract = contract_code

    @staticmethod
    def _aggregate_15m(bars_1m: list[Bar]) -> list[Bar]:
        """Build complete 15-minute bars from closed one-minute bars.

        HTX candle ``close_time`` values are UTC ISO timestamps.  Subtracting
        one second before choosing the bucket keeps the candle closing at
        ``00:15`` inside the ``00:00..00:15`` interval.  Incomplete buckets
        are discarded so the strategy never evaluates a still-forming bar.
        """
        buckets: dict[int, list[Bar]] = {}
        for bar in bars_1m:
            try:
                close_at = datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            bucket = (int(close_at.timestamp()) - 1) // (15 * 60)
            buckets.setdefault(bucket, []).append(bar)

        result: list[Bar] = []
        for bucket_bars in buckets.values():
            ordered = sorted(bucket_bars, key=lambda item: item.timestamp)
            if len(ordered) != 15:
                continue
            result.append(
                Bar(
                    timestamp=ordered[-1].timestamp,
                    open=ordered[0].open,
                    high=max(item.high for item in ordered),
                    low=min(item.low for item in ordered),
                    close=ordered[-1].close,
                    volume=sum((item.volume for item in ordered), ZERO),
                )
            )
        return sorted(result, key=lambda item: item.timestamp)

    async def fetch_bars(self, timeframe: str, limit: int) -> list[Bar]:
        """Fetch bars from Redis.

        Closed candle history is published by ``collector.htx.history_loader``
        under the same perpetual-market namespace as the execution snapshot.
        A missing or incomplete history returns no bars.  It must not be
        replaced by synthetic prices because that could create a false signal.
        """
        try:
            import json

            redis_timeframe = {
                "1m": "1min",
                "15m": "1min",
                "1h": "60min",
            }.get(timeframe)
            if redis_timeframe is None:
                raise ValueError(f"unsupported candle timeframe: {timeframe}")
            raw = await self._redis.get(
                f"market:perpetual:htx:candles:{self._contract}:{redis_timeframe}"
            )
            if raw is not None:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                rows = json.loads(raw)
                if not isinstance(rows, list):
                    raise ValueError("candle cache is not a list")
                bars = [
                    Bar(
                        timestamp=str(item["close_time"]),
                        open=Decimal(str(item["open"])),
                        high=Decimal(str(item["high"])),
                        low=Decimal(str(item["low"])),
                        close=Decimal(str(item["close"])),
                        volume=Decimal(str(item.get("volume", "0"))),
                    )
                    for item in rows
                ]
                if timeframe == "15m":
                    bars = self._aggregate_15m(bars)
                if len(bars) >= limit:
                    return bars[-limit:]
        except Exception as exc:
            log.warning("fetch_bars from Redis failed: %s", exc)
        return []

    async def fetch_current_price(self) -> Decimal | None:
        """Fetch the latest mark price from Redis snapshot."""
        try:
            from paper_trading.market import read_snapshot_from_redis
            snap = await read_snapshot_from_redis(self._redis, self._contract)
            return snap.mark
        except Exception as exc:
            log.warning("fetch_current_price from Redis failed: %s", exc)
            return None

    async def fetch_snapshot(self) -> PerpetualSnapshot | None:
        """Fetch a validated market snapshot from Redis."""
        try:
            from paper_trading.market import read_snapshot_from_redis
            return await read_snapshot_from_redis(self._redis, self._contract)
        except Exception as exc:
            log.warning("fetch_snapshot from Redis failed: %s", exc)
            return None


# ─── Runner ────────────────────────────────────────────────────────────

class PaperTradingRunner:
    """Async runner that orchestrates the paper-trading loop.

    Usage::

        runner = PaperTradingRunner(
            strategy=BaselineV1Strategy(),
            recovery_store=PgRecoveryStore(dsn=...),
            heartbeat_sink=RedisHeartbeat(redis=...),
            command_source=RedisCommandSource(redis=...),
            market_data=MarketDataAdapter(dsn=...),
        )
        await runner.recover()
        await runner.run()
    """

    def __init__(
        self,
        *,
        strategy: BaselineV1Strategy,
        recovery_store: RecoveryStore | None = None,
        heartbeat_sink: HeartbeatSink | None = None,
        command_source: CommandSource | None = None,
        market_data: MarketDataSource | None = None,
        repository: PaperRepository | None = None,
        risk_settings: RiskSettings | None = None,
        heartbeat_key: str = "paper_trading:runner:heartbeat",
        poll_interval: float = POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        instance_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.strategy = strategy
        self.recovery_store = recovery_store
        self.heartbeat_sink = heartbeat_sink
        self.command_source = command_source
        self.market_data = market_data
        self.repository = repository
        # Automatic entries are fail-closed when live HTX risk-tier metadata
        # is absent or stale.  Callers may inject a different setting only
        # for an explicit isolated test/replay.
        self.risk_settings = risk_settings or RiskSettings(require_risk_tier=True)
        self.heartbeat_key = heartbeat_key
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.instance_id = instance_id or str(uuid4())
        self.run_id = run_id or str(uuid4())

        # Lease fencing token — monotonically increasing
        self._fence_token: int = 0
        self._next_fence_token: int = 1
        self._init_state()

    @classmethod
    def from_env(cls, **kwargs: Any) -> PaperTradingRunner:
        """Create runner from environment variables.

        Wires real dependencies (market_data, command_source, recovery_store,
        repository) from Redis and PostgreSQL when available.
        """
        import os

        strategy = BaselineV1Strategy()
        poll = float(os.getenv("PAPER_ENGINE_INTERVAL_SECONDS", "2"))
        hb = float(os.getenv("PAPER_HEARTBEAT_INTERVAL_SECONDS", "5"))

        # Extract factory references (not passed to __init__)
        pg_pool_factory = None
        redis_factory = None
        try:
            from storage.postgres_client import get_pool as _get_pg_pool
            pg_pool_factory = _get_pg_pool
        except ImportError:
            log.debug("postgres_client not available — no DB dependencies")

        try:
            from storage.redis_client import get_redis as _get_redis
            redis_factory = _get_redis
        except ImportError:
            log.debug("redis_client not available — no market data source")

        # Remove any factory kwargs that shouldn't go to __init__
        kwargs.pop("_pg_pool_factory", None)
        kwargs.pop("_redis_factory", None)

        instance = cls(
            strategy=strategy,
            poll_interval=poll,
            heartbeat_interval=hb,
            **kwargs,
        )
        # Store factories on instance for _wire_dependencies to use
        instance._pg_pool_factory = pg_pool_factory  # type: ignore[attr-defined]
        instance._redis_factory = redis_factory  # type: ignore[attr-defined]
        return instance

    def _wire_dependencies(
        self,
        *,
        pg_pool: Any = None,
        redis_client: Any = None,
    ) -> None:
        """Wire concrete dependency instances after from_env.

        Called by adapter.register_runner() after async resources are
        available.  This connects market_data from Redis, command_source
        from PostgreSQL, recovery_store from PostgreSQL, and the
        repository for the signal→order→fill→ledger chain.
        """
        if pg_pool is not None and self.repository is None:
            from paper_trading.repository import PaperRepository
            self.repository = PaperRepository(pg_pool)

        if self.repository is not None:
            if self.recovery_store is None:
                self.recovery_store = PgRecoveryStore(self.repository)
            if self.command_source is None:
                self.command_source = PgCommandSource(self.repository)

        if redis_client is not None and self.market_data is None:
            self.market_data = RedisMarketDataSource(redis_client)

    # ── State (initialized after from_env creates the instance) ──
    def _init_state(self) -> None:
        self._positions: dict[UUID, Position] = {}
        self._open_orders: dict[UUID, Order] = {}
        self._pending_commands: list[Command] = []
        self._last_signal: StrategySignal | None = None
        self._last_1m_bar_ts: str | None = None
        self._entries_blocked: bool = True
        self._recovered: bool = False
        self._running: bool = False
        self._last_heartbeat: float = 0.0
        self._last_poll: float = 0.0
        self._last_error: str | None = None
        self._last_decision: Decision | None = None
        self._auto_account_id: UUID | None = None
        self._auto_owner_id: UUID | None = None
        self._stop_event = asyncio.Event()

    # ── Fence token ──

    def acquire_fence_token(self) -> int:
        """Return the next monotonically increasing fence token (local)."""
        token = self._next_fence_token
        self._next_fence_token += 1
        return token

    async def acquire_lease(self) -> int:
        """Acquire a PostgreSQL-backed lease with fencing token.

        Uses paper_v2_leases table for persistent fencing.
        Falls back to local counter if no repository.
        """
        if self.repository is None:
            return self.acquire_fence_token()
        try:
            async with self.repository.pool.acquire() as conn:
                # Insert new lease with fencing token
                row = await conn.fetchrow(
                    """
                    INSERT INTO paper_v2_leases (lease_id, instance_id, fence_token, acquired_at, expires_at)
                    VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '60 seconds')
                    ON CONFLICT (instance_id) DO UPDATE
                    SET fence_token = EXCLUDED.fence_token,
                        acquired_at = EXCLUDED.acquired_at,
                        expires_at = EXCLUDED.expires_at
                    RETURNING fence_token
                    """,
                    str(uuid4()), self.instance_id, self._next_fence_token,
                )
                token = int(row["fence_token"])
                self._next_fence_token += 1
                return token
        except Exception as exc:
            log.warning("lease acquire failed, using local: %s", exc)
            return self.acquire_fence_token()

    def current_fence_token(self) -> int:
        return self._fence_token

    def commit_fence_token(self, token: int) -> bool:
        """Attempt to advance the committed fence token.

        Returns ``True`` if *token* > current (i.e. not stale), else ``False``.
        """
        if token <= self._fence_token:
            log.warning("stale fence token %d (current %d) — rejected", token, self._fence_token)
            return False
        self._fence_token = token
        return True

    # ── Recovery ──

    async def recover(self) -> dict[str, Any]:
        """Load unfinished state from PostgreSQL and reconcile.

        Blocks new entries until recovery is complete.
        """
        self._entries_blocked = True
        report: dict[str, Any] = {
            "commands_loaded": 0,
            "orders_loaded": 0,
            "positions_loaded": 0,
            "ledger_discrepancies": [],
            "intents_cancelled": 0,
            "active_auto_accounts": 0,
        }

        if self.recovery_store is None:
            log.warning("recovery: no store configured — entries remain blocked")
            self._recovered = True
            self._entries_blocked = True
            report["recovered"] = True
            report["entries_blocked_reason"] = "recovery_store_unavailable"
            return report

        try:
            # 1. Load unfinished commands
            commands = await self.recovery_store.load_unfinished_commands()
            self._pending_commands.extend(commands)
            report["commands_loaded"] = len(commands)

            # 2. Load open orders
            orders = await self.recovery_store.load_open_orders()
            for order in orders:
                self._open_orders[order.order_id] = order
            report["orders_loaded"] = len(orders)

            # 3. Load open positions
            positions = await self.recovery_store.load_open_positions()
            for pos in positions:
                self._positions[pos.position_id] = pos
            report["positions_loaded"] = len(positions)

            # Restore the auto-account scope.  This runner intentionally owns
            # one personal auto account; zero or multiple candidates are
            # ambiguous and must keep new entries blocked.
            auto_accounts = await self.recovery_store.load_active_auto_accounts()
            report["active_auto_accounts"] = len(auto_accounts)
            if len(auto_accounts) == 1:
                self._auto_account_id = auto_accounts[0].account_id
                self._auto_owner_id = auto_accounts[0].owner_id
            else:
                self._auto_account_id = None
                self._auto_owner_id = None

            # 4. Reconcile ledger
            ledger_result = await self.recovery_store.reconcile_ledger(
                list(self._positions.values())
            )
            report["ledger_discrepancies"] = ledger_result.get("discrepancies", [])

            # 5. Cancel expired signal intents
            now = time.time()
            expired: list[str] = []
            if self._last_signal is not None and self._last_signal.is_expired(now, SIGNAL_TTL_SECONDS):
                expired.append(self._last_signal.bar_timestamp)
            cancelled = await self.recovery_store.cancel_expired_intents(expired)
            report["intents_cancelled"] = cancelled
            if cancelled:
                self._last_signal = None

            # 6. Check discrepancies — fail-closed: keep entries blocked if discrepancies exist
            discrepancies = ledger_result.get("discrepancies", [])
            if discrepancies:
                self._recovered = True
                self._entries_blocked = True  # Keep blocked — reconciliation failed
                report["recovered"] = True
                report["entries_blocked_reason"] = "ledger_discrepancies"
                log.warning(
                    "recovery complete with %d discrepancies — entries BLOCKED: %s",
                    len(discrepancies), discrepancies[:3],
                )
            elif len(auto_accounts) != 1:
                self._recovered = True
                self._entries_blocked = True
                report["recovered"] = True
                report["entries_blocked_reason"] = (
                    "no_active_auto_account"
                    if not auto_accounts
                    else "ambiguous_active_auto_accounts"
                )
                log.warning(
                    "recovery complete but found %d active auto accounts — entries BLOCKED",
                    len(auto_accounts),
                )
            else:
                self._recovered = True
                self._entries_blocked = False
                report["recovered"] = True
                log.info(
                    "recovery complete: %d commands, %d orders, %d positions, %d intents cancelled — entries unblocked",
                    report["commands_loaded"],
                    report["orders_loaded"],
                    report["positions_loaded"],
                    report["intents_cancelled"],
                )
        except Exception:
            self._last_error = "recovery failed"
            log.exception("recovery failed")
            report["recovered"] = False
            report["error"] = self._last_error
            # Entries remain blocked on failure
        return report

    # ── Main loop ──

    async def run_cycle(self) -> None:
        """Single poll cycle — called by APScheduler every 2s."""
        now = time.time()
        self._last_poll = now
        try:
            # 0. Recovery if not yet recovered
            if not self._recovered and self.recovery_store is not None:
                await self.recover()

            # 1. SL/TP check (always, even if entries blocked)
            await self._check_sl_tp(now)
            # 2. Process pending commands (close, cancel, start/finish day, etc.)
            await self._process_commands(now)
            # 3. Evaluate signals on new 1m close (only if not blocked)
            if not self._entries_blocked and self._recovered:
                await self._evaluate_signals(now)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Runner cycle error: %s", exc)

    async def run(self) -> None:
        """Main poll loop.  Runs until :meth:`stop` is called.

        SL/TP checks, funding, and manual close operations run every cycle
        and are never blocked by long-running AI tasks.  Signal evaluation
        runs on new 1m bar close only when entries are not blocked.
        """
        self._running = True
        self._stop_event.clear()
        last_heartbeat = 0.0

        try:
            while not self._stop_event.is_set():
                now = time.time()
                await self.run_cycle()
                # Heartbeat
                if (now - last_heartbeat) >= self.heartbeat_interval:
                    await self._send_heartbeat(now)
                    last_heartbeat = now

                # 5. Sleep until next poll
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the main loop to stop."""
        self._stop_event.set()

    # ── SL/TP ──

    async def _check_sl_tp(self, now: float) -> None:
        """Check all open positions against current price for SL/TP hits.

        Uses check_stop_trigger → execute_close → record_fill →
        update_position → post ledger.  Removes from dict only after
        DB commit.

        This is called every poll cycle and is never blocked by AI tasks.
        """
        if not self._positions:
            return

        # Need a snapshot for execution
        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            self._last_decision = Decision(
                reason_code=ReasonCode.waiting_data,
                reason_detail="Protective exit deferred: validated bid/ask snapshot unavailable",
                decided_at=datetime.now(timezone.utc),
            )
            return

        to_close: list[tuple[UUID, str]] = []  # (position_id, exit_reason)
        for pid, pos in self._positions.items():
            if pos.status != PositionStatus.open:
                continue
            sl = pos.stop_loss
            tp = pos.take_profit
            if sl is None or tp is None:
                continue

            # Check stop trigger (SL)
            trigger = check_stop_trigger(self._to_execution_position(pos), snapshot)
            if trigger.triggered:
                to_close.append((pid, "stop_loss"))
                log.info("SL hit: position %s @ %s (SL %s)", pid, trigger.trigger_price, sl)
                self.strategy.on_stop_loss_hit(now)
                continue

            # Check take profit
            mark = snapshot.mark
            if pos.side == PositionSide.long:
                if mark >= tp:
                    to_close.append((pid, "take_profit"))
                    log.info("TP hit: position %s @ %s (TP %s)", pid, mark, tp)
            else:  # short
                if mark <= tp:
                    to_close.append((pid, "take_profit"))
                    log.info("TP hit: position %s @ %s (TP %s)", pid, mark, tp)

        # Execute closes with DB persistence
        for pid, exit_reason in to_close:
            position_to_close = self._positions.get(pid)
            if position_to_close is None:
                continue
            try:
                await self._execute_position_close(position_to_close, snapshot, exit_reason)
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("SL/TP close failed for position %s: %s", pid, exc)

    async def _check_sl_tp_price_only(self, current_price: Decimal, now: float) -> None:
        """Retained compatibility hook; exits require a validated snapshot."""
        self._last_decision = Decision(
            reason_code=ReasonCode.waiting_data,
            reason_detail="Protective exit deferred: execution snapshot unavailable",
            actual_metrics={"mark_price": str(current_price)},
            decided_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _to_execution_position(pos: Position) -> ExecutionPosition:
        """Translate the persisted position contract into execution input."""
        return ExecutionPosition(
            position_id=str(pos.position_id),
            account_id=str(pos.account_id),
            instrument=pos.instrument,
            direction=pos.side.value,
            entry_price=pos.avg_entry_price,
            quantity=pos.qty,
            leverage=LEVERAGE,
            stop_price=pos.stop_loss or ZERO,
            take_profit=pos.take_profit,
            reserved_margin=pos.isolated_margin,
            entry_fee=pos.entry_fee,
            opened_at=pos.opened_at.isoformat() if pos.opened_at else "",
            status=pos.status.value,
        )

    async def _execute_position_close(
        self,
        pos: Position,
        snapshot: PerpetualSnapshot,
        exit_reason: str,
    ) -> None:
        """Execute a full position close with DB persistence and ledger.

        Chain: execute_close → record_fill → update_position → post ledger.
        Removes from _positions dict only after DB commit.
        """
        close_result = execute_close(
            self._to_execution_position(pos),
            snapshot,
            close_quantity=pos.qty,
            exit_reason=exit_reason,
        )
        if not close_result.closed:
            log.warning("close failed for position %s: %s", pos.position_id, close_result.exit_reason)
            return

        if self.repository is None:
            # No DB — just remove from dict
            self._positions.pop(pos.position_id, None)
            return

        # Build the closing fill and its signed cashflows, then commit the
        # fill, position transition and ledger event in one transaction.
        fill = Fill(
            fill_id=uuid4(),
            order_id=pos.position_id,  # link to position's originating order
            account_id=pos.account_id,
            execution_quote_id=f"close-{pos.position_id}-{int(time.time())}",
            instrument=pos.instrument,
            side=OrderSide.sell if pos.side == PositionSide.long else OrderSide.buy,
            price=money(close_result.close_price),
            qty=money(close_result.closed_quantity),
            fee=money(close_result.close_fee),
            is_close=True,
            source_timestamp=datetime.now(timezone.utc),
            receive_timestamp=datetime.now(timezone.utc),
            execute_timestamp=datetime.now(timezone.utc),
        )
        postings = build_fill_postings(
            account_id=pos.account_id,
            fill_price=close_result.close_price,
            fill_qty=close_result.closed_quantity,
            fee=close_result.close_fee,
            is_close=True,
            side=pos.side,
            day_id=pos.day_id,
            source_ref=str(fill.fill_id),
            realized_gross=money(close_result.gross_pnl),
            realized_net=money(close_result.realized_net),
            released_margin=money(pos.isolated_margin),
        )
        await self.repository.commit_position_close(
            position_id=pos.position_id,
            fill=fill,
            close_price=close_result.close_price,
            realized_gross_pnl=close_result.gross_pnl,
            realized_net_pnl=close_result.realized_net,
            exit_fee=close_result.close_fee,
            postings=postings,
        )

        # 4. Remove from in-memory dict only after DB commit
        self._positions.pop(pos.position_id, None)
        log.info(
            "position %s closed: reason=%s, gross=%s, net=%s",
            pos.position_id, exit_reason, close_result.gross_pnl, close_result.realized_net,
        )

    async def _fetch_current_price(self) -> Decimal | None:
        """Fetch the latest mark/last price from the market data source."""
        if self.market_data is None:
            return None
        try:
            return await self.market_data.fetch_current_price()
        except Exception as exc:
            log.warning("fetch_current_price failed: %s", exc)
        return None

    async def _fetch_snapshot(self) -> PerpetualSnapshot | None:
        """Fetch a validated market snapshot for execution/risk checks."""
        if self.market_data is None:
            return None
        try:
            return await self.market_data.fetch_snapshot()
        except Exception as exc:
            log.warning("fetch_snapshot failed: %s", exc)
        return None

    # ── Commands ──

    async def _process_commands(self, now: float) -> None:
        """Poll for and process manual commands."""
        # Fetch new commands from the source
        if self.command_source is not None:
            try:
                new_commands = await self.command_source.poll()
                self._pending_commands.extend(new_commands)
            except Exception as exc:
                log.warning("command poll failed: %s", exc)

        if not self._pending_commands:
            return

        remaining: list[Command] = []
        for cmd in self._pending_commands:
            # Fence check: reject stale commands (using expected_revision as fence)
            if cmd.expected_revision is not None and cmd.expected_revision <= self._fence_token:
                log.warning(
                    "stale command %s (fence %d <= current %d)",
                    cmd.command_id, cmd.expected_revision, self._fence_token,
                )
                continue

            processed = await self._execute_command(cmd, now)
            if not processed:
                remaining.append(cmd)
        self._pending_commands = remaining

    async def _execute_command(self, cmd: Command, now: float) -> bool:
        """Execute a single command.  Returns True if fully processed."""
        token = self.acquire_fence_token()
        if not self.commit_fence_token(token):
            return False

        ct = cmd.command_type
        if ct == CommandType.close_position:
            # Close a specific position — use position_id from command payload
            position_id = self._extract_position_id(cmd)
            if position_id is None:
                log.warning("close_position command %s: no position_id", cmd.command_id)
                return True

            pos = self._positions.get(position_id)
            if pos is None:
                log.warning("close_position: position %s not found", position_id)
                return True

            # Verify ownership: command's account must match position's account
            if cmd.account_id is not None and pos.account_id != cmd.account_id:
                log.warning(
                    "close_position: ownership mismatch — command account %s vs position account %s",
                    cmd.account_id, pos.account_id,
                )
                return True

            # Execute close with DB persistence
            snapshot = await self._fetch_snapshot()
            if snapshot is not None:
                try:
                    await self._execute_position_close(pos, snapshot, "manual_close")
                except Exception as exc:
                    self._last_error = str(exc)
                    log.warning("manual close failed for position %s: %s", position_id, exc)
            else:
                self._last_decision = Decision(
                    reason_code=ReasonCode.waiting_data,
                    reason_detail="Close deferred: validated market snapshot unavailable",
                    decided_at=datetime.now(timezone.utc),
                )
                return False

            log.info("manual close: position %s", position_id)
            return True

        if ct == CommandType.close_all:
            # Close all positions for an account
            acct = cmd.account_id
            if acct:
                to_close = [
                    (pid, pos) for pid, pos in self._positions.items()
                    if pos.account_id == acct and pos.status == PositionStatus.open
                ]
            else:
                to_close = [
                    (pid, pos) for pid, pos in self._positions.items()
                    if pos.status == PositionStatus.open
                ]

            snapshot = await self._fetch_snapshot()
            if snapshot is None and to_close:
                self._last_decision = Decision(
                    reason_code=ReasonCode.waiting_data,
                    reason_detail="Close-all deferred: validated market snapshot unavailable",
                    decided_at=datetime.now(timezone.utc),
                )
                return False
            if snapshot is None:
                return True
            for pid, pos in to_close:
                try:
                    await self._execute_position_close(pos, snapshot, "close_all")
                except Exception as exc:
                    log.warning("close_all: failed for position %s: %s", pid, exc)

            log.info("manual close_all: %d positions", len(to_close))
            return True

        if ct == CommandType.cancel_order:
            # Cancel a pending order
            self._last_signal = None
            log.info("manual cancel: signal/order intent cleared")
            return True

        if ct == CommandType.start_day:
            return await self._handle_start_day(cmd)

        if ct == CommandType.finish_day:
            return await self._handle_finish_day(cmd)

        if ct == CommandType.pause_auto:
            self._entries_blocked = True
            log.info("auto trading paused")
            return True

        if ct == CommandType.resume_auto:
            if self._recovered:
                self._entries_blocked = False
                log.info("auto trading resumed")
            else:
                log.warning("resume_auto: cannot resume — not recovered")
            return True

        # Unknown commands are acknowledged
        log.info("command %s acknowledged", ct.value)
        return True

    def _extract_position_id(self, cmd: Command) -> UUID | None:
        """Extract position_id from a command.

        The command may carry position_id in:
        - cmd.result payload (if already processed)
        - cmd.day_id field (misused in some flows)
        - A convention where account_id carries the position_id for close commands

        For close_position commands, we look at account_id as a fallback
        (this is the existing behavior) but prefer an explicit position_id
        from the command payload if available.
        """
        # Try to get position_id from command result (for replayed commands)
        if cmd.result and isinstance(cmd.result, dict):
            pid_str = cmd.result.get("position_id")
            if pid_str:
                try:
                    return UUID(str(pid_str))
                except (ValueError, TypeError):
                    pass

        # For close_position, account_id may actually be the position_id
        # (legacy convention from the adapter)
        if cmd.account_id is not None:
            # Check if account_id is actually a position_id by looking it up
            if cmd.account_id in self._positions:
                return cmd.account_id

        return None

    async def _handle_start_day(self, cmd: Command) -> bool:
        """Handle start_day command: transition day idle→running in DB."""
        if self.repository is None:
            log.info("start_day acknowledged (no repository)")
            return True

        if cmd.day_id is None:
            log.warning("start_day: no day_id in command")
            return True

        account = None
        if cmd.account_id is not None:
            try:
                account = await self.repository.get_account(cmd.account_id)
            except Exception as exc:
                log.warning("start_day: cannot resolve account %s: %s", cmd.account_id, exc)
                return True
        if account is None:
            log.warning("start_day: account is required to determine manual/auto ownership")
            return True

        try:
            # Transition day state: idle → running
            updated = await self.repository.update_day_state(
                cmd.day_id,
                DayState.running,
                expected_state=DayState.idle,
            )
            if not updated:
                # Day may already be running — try without expected_state
                updated = await self.repository.update_day_state(
                    cmd.day_id,
                    DayState.running,
                )

            if updated:
                if account.kind == AccountKind.auto:
                    self._auto_account_id = account.account_id
                    self._auto_owner_id = account.owner_id
                    self._entries_blocked = False
                    log.info("start_day: auto account %s enabled for day %s", account.account_id, cmd.day_id)
                else:
                    log.info("start_day: manual account %s acknowledged for day %s", account.account_id, cmd.day_id)
            else:
                log.warning("start_day: could not transition day %s to running", cmd.day_id)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("start_day failed: %s", exc)

        return True

    async def _handle_finish_day(self, cmd: Command) -> bool:
        """Handle finish_day command: cancel entries, close all, day→closed."""
        if self.repository is None:
            log.info("finish_day acknowledged (no repository)")
            return True

        if cmd.day_id is None:
            log.warning("finish_day: no day_id in command")
            return True

        # 1. Block new entries
        self._entries_blocked = True

        # 2. Close all open positions
        snapshot = await self._fetch_snapshot()
        positions_to_close = [
            (pid, pos) for pid, pos in self._positions.items()
            if pos.status == PositionStatus.open and pos.day_id == cmd.day_id
        ]
        if snapshot is None and positions_to_close:
            await self.repository.update_day_state(
                cmd.day_id,
                DayState.settlement_pending,
            )
            self._last_decision = Decision(
                reason_code=ReasonCode.settlement_pending,
                reason_detail="Finish-day deferred: execution snapshot unavailable",
                day_id=cmd.day_id,
                decided_at=datetime.now(timezone.utc),
            )
            return False

        close_failed = False
        for pid, pos in positions_to_close:
            try:
                if snapshot is None:
                    raise RuntimeError("execution snapshot unavailable")
                await self._execute_position_close(pos, snapshot, "day_close")
            except Exception as exc:
                close_failed = True
                log.warning("finish_day: close position %s failed: %s", pid, exc)

        remaining = any(
            pos.status == PositionStatus.open and pos.day_id == cmd.day_id
            for pos in self._positions.values()
        )
        if close_failed or remaining:
            await self.repository.update_day_state(
                cmd.day_id,
                DayState.settlement_pending,
            )
            self._last_decision = Decision(
                reason_code=ReasonCode.settlement_pending,
                reason_detail="Finish-day deferred: one or more positions remain open",
                day_id=cmd.day_id,
                decided_at=datetime.now(timezone.utc),
            )
            return False

        # 3. Transition day state → closed
        try:
            updated = await self.repository.update_day_state(
                cmd.day_id,
                DayState.closed,
            )
            if updated:
                log.info("finish_day: day %s transitioned to closed", cmd.day_id)
            else:
                log.warning("finish_day: could not transition day %s to closed", cmd.day_id)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("finish_day failed: %s", exc)

        return True

    # ── Signal evaluation ──

    async def _evaluate_signals(self, now: float) -> None:
        """Evaluate the strategy on new 1m bar close.

        Signal→order→fill→ledger chain:
        signal found → order risk → create pending order → pre-fill risk →
        execute at bid/ask → atomic fill + position + ledger commit.
        """
        if self.market_data is None:
            return

        try:
            bars_1m = await self.market_data.fetch_bars("1m", self.strategy.cfg.warmup_bars_1m)
        except Exception as exc:
            log.warning("fetch 1m bars failed: %s", exc)
            return

        if len(bars_1m) < 2:
            return

        last_bar = bars_1m[-1]
        # Only evaluate on a new bar close
        if self._last_1m_bar_ts == last_bar.timestamp:
            return
        self._last_1m_bar_ts = last_bar.timestamp

        # Fetch enough closed higher-TF bars to initialise EMA20/EMA50.  The
        # strategy de-duplicates overlapping windows by candle timestamp.
        close_1h: Decimal | None = None
        close_15m: Decimal | None = None
        try:
            bars_1h = await self.market_data.fetch_bars("1h", self.strategy.cfg.warmup_bars_1h)
            if bars_1h:
                close_1h = bars_1h[-1].close
            bars_15m = await self.market_data.fetch_bars("15m", self.strategy.cfg.warmup_bars_15m)
            if bars_15m:
                close_15m = bars_15m[-1].close
            self.strategy.update_higher_timeframes(bars_1h, bars_15m)
        except Exception as exc:
            log.warning("fetch higher TF bars failed: %s", exc)

        # Expire stale signal
        if self._last_signal is not None and self._last_signal.is_expired(now, SIGNAL_TTL_SECONDS):
            log.debug("signal expired (bar %s)", self._last_signal.bar_timestamp)
            self._last_signal = None
            self._last_decision = Decision(
                reason_code=ReasonCode.signal_expired,
                reason_detail=f"Signal expired after {SIGNAL_TTL_SECONDS}s (bar {self._last_1m_bar_ts})",
                decided_at=datetime.now(timezone.utc),
            )

        # Evaluate
        signal = self.strategy.evaluate(
            bars_1m=bars_1m,
            close_1h=close_1h,
            close_15m=close_15m,
            now_epoch=now,
            day_end_epoch=None,  # set by caller if day boundary is known
        )
        if signal is not None:
            self._last_signal = signal
            self._last_decision = Decision(
                reason_code=ReasonCode.waiting_trigger,
                reason_detail=f"Signal {signal.side} @ {signal.close_price}, "
                              f"SL={signal.stop_loss}, TP={signal.take_profit}, netRR={signal.net_rr}",
                decided_at=datetime.now(timezone.utc),
            )
            log.info(
                "signal generated: %s @ %s SL=%s TP=%s netRR=%s",
                signal.side, signal.close_price, signal.stop_loss, signal.take_profit, signal.net_rr,
            )

            # Execute the signal→order→fill→ledger chain
            await self._execute_signal(signal, now)

    async def _execute_signal(self, signal: StrategySignal, now: float) -> None:
        """Execute the full signal→order→fill→ledger chain.

        1. Get market snapshot for execution
        2. Build OrderRequest and check_order_risk
        3. Calculate position size
        4. Create Order in DB
        5. Execute market order (get fill price)
        6. Record Fill in DB
        7. Create/Update Position in DB
        8. Post ledger entries
        """
        if self.repository is None:
            log.debug("signal→order chain skipped: no repository")
            return

        # 1. Get snapshot for execution
        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            log.warning("signal→order chain: no snapshot available")
            self._last_decision = Decision(
                reason_code=ReasonCode.waiting_data,
                reason_detail="No market snapshot available for execution",
                decided_at=datetime.now(timezone.utc),
            )
            return

        # Determine account for auto trading
        # For v1, we use the first account found or a configured one
        account_id = self._get_auto_account_id()
        if account_id is None:
            log.warning("signal→order chain: no auto account configured")
            return

        if self._auto_owner_id is None:
            log.warning("signal→order chain: auto account owner is unknown")
            return

        # Get active day for the owner of the configured auto account.
        day = await self.repository.get_active_day(self._auto_owner_id)
        if day is None:
            log.warning("signal→order chain: no active trading day")
            return

        # A missing balance must never be interpreted as unlimited margin.
        try:
            available_margin = await self.repository.get_account_balance(account_id)
            persisted_positions = await self.repository.get_open_positions(account_id)
        except Exception as exc:
            log.warning("signal→order chain: account risk state unavailable: %s", exc)
            self._last_decision = Decision(
                reason_code=ReasonCode.waiting_data,
                reason_detail="Account balance or positions unavailable for risk check",
                decided_at=datetime.now(timezone.utc),
            )
            return

        if any(position.instrument == INSTRUMENT for position in persisted_positions):
            log.info("signal→order chain: existing %s position blocks a second entry", INSTRUMENT)
            return

        open_positions = [
            PositionInfo(
                position_id=str(position.position_id),
                direction=position.direction,
                entry_price=position.avg_entry_price,
                quantity=position.qty,
                stop_price=position.stop_loss or ZERO,
                leverage=LEVERAGE,
                reserved_margin=position.isolated_margin,
                unrealized_pnl=ZERO,
            )
            for position in persisted_positions
        ]

        # 2. Build OrderRequest and check risk
        direction = signal.side  # "long" or "short"
        stop_price = signal.stop_loss
        take_profit = signal.take_profit

        order_request = OrderRequest(
            instrument=INSTRUMENT,
            direction=direction,
            order_type="market",
            risk_amount=self.risk_settings.max_risk_per_order,
            stop_price=stop_price,
            take_profit=take_profit,
            leverage=LEVERAGE,
            quantity=None,  # will be calculated by risk check
            created_at=datetime.now(timezone.utc),
            account_id=str(account_id),
        )

        risk_result: RiskCheckResult = check_order_risk(
            order_request,
            snapshot,
            self.risk_settings,
            open_positions=open_positions,
            available_margin=available_margin,
            is_day_active=(day.state == DayState.running),
        )

        if not risk_result.allowed:
            reasons = ", ".join(risk_result.reasons)
            log.info("signal→order chain: risk check failed: %s", reasons)
            self._last_decision = Decision(
                reason_code=ReasonCode.risk_blocked,
                reason_detail=f"Risk check failed: {reasons}",
                decided_at=datetime.now(timezone.utc),
            )
            return

        # 3. Get calculated quantity
        qty = risk_result.quantity
        if qty is None or qty <= 0:
            log.warning("signal→order chain: no quantity from risk check")
            return

        # 4. Create Order in DB
        order_id = uuid4()
        signal_id = uuid4()  # link order to signal

        # First, create the signal record in DB
        from paper_trading.contracts import Signal as ContractSignal
        try:
            closed_bar_time = datetime.fromisoformat(
                signal.bar_timestamp.replace("Z", "+00:00")
            )
            if closed_bar_time.tzinfo is None:
                closed_bar_time = closed_bar_time.replace(tzinfo=timezone.utc)
        except ValueError:
            self._last_decision = Decision(
                reason_code=ReasonCode.engine_error,
                reason_detail="Signal rejected: invalid closed-bar timestamp",
                decided_at=datetime.now(timezone.utc),
            )
            return

        contract_signal = ContractSignal(
            signal_id=signal_id,
            account_id=account_id,
            strategy_version=self.strategy.VERSION,
            instrument=INSTRUMENT,
            closed_bar_time=closed_bar_time,
            direction=PositionSide.long if direction == "long" else PositionSide.short,
            entry_ref_price=signal.close_price,
            stop_loss=stop_price,
            take_profit=take_profit,
            atr_value=signal.atr_at_trigger,
        )
        try:
            await self.repository.create_signal(contract_signal)
        except Exception as exc:
            self._last_error = str(exc)
            self._last_decision = Decision(
                reason_code=ReasonCode.engine_error,
                reason_detail="Signal persistence failed; order was not created",
                error=str(exc),
                decided_at=datetime.now(timezone.utc),
            )
            log.warning("signal→order chain: create_signal failed: %s", exc)
            return

        order = Order(
            order_id=order_id,
            account_id=account_id,
            day_id=day.day_id,
            origin="auto",
            actor="auto",
            signal_id=signal_id,
            side=OrderSide.buy if direction == "long" else OrderSide.sell,
            order_type=OrderType.market,
            instrument=INSTRUMENT,
            qty=money(qty),
            stop_loss=stop_price,
            take_profit=take_profit,
            state=OrderState.pending,
        )
        await self.repository.create_order(order)
        log.info("signal→order chain: order %s created (qty=%s, side=%s)", order_id, qty, direction)

        # 5. Refresh the snapshot and re-check mutable fill conditions.
        fill_snapshot = await self._fetch_snapshot()
        if fill_snapshot is None:
            await self.repository.update_order_state(order_id, OrderState.rejected)
            self._last_decision = Decision(
                reason_code=ReasonCode.waiting_data,
                reason_detail="Order rejected before fill: fresh snapshot unavailable",
                decided_at=datetime.now(timezone.utc),
            )
            return
        fill_risk = check_fill_risk(
            order_request,
            fill_snapshot,
            self.risk_settings,
            reserved_quantity=qty,
            reserved_entry_price=risk_result.entry_price,
            available_margin=available_margin,
        )
        if not fill_risk.allowed:
            await self.repository.update_order_state(order_id, OrderState.rejected)
            reasons = ", ".join(fill_risk.reasons)
            self._last_decision = Decision(
                reason_code=ReasonCode.risk_blocked,
                reason_detail=f"Pre-fill risk check failed: {reasons}",
                decided_at=datetime.now(timezone.utc),
            )
            return

        # 6. Execute market order against the revalidated quote.
        fill_result: FillResult = execute_market_order(
            snapshot=fill_snapshot,
            direction=direction,
            requested_quantity=qty,
            leverage=LEVERAGE,
            stop_price=stop_price,
            take_profit=take_profit,
            position_id=str(order_id),
            account_id=str(account_id),
            instrument=INSTRUMENT,
        )

        if not fill_result.filled:
            log.warning(
                "signal→order chain: order not filled: %s", fill_result.reason,
            )
            await self.repository.update_order_state(
                order_id, OrderState.rejected,
            )
            return

        # Build all persisted objects before the single financial commit.
        fill = Fill(
            fill_id=uuid4(),
            order_id=order_id,
            account_id=account_id,
            execution_quote_id=f"fill-{order_id}-{int(time.time())}",
            instrument=INSTRUMENT,
            side=OrderSide.buy if direction == "long" else OrderSide.sell,
            price=money(fill_result.fill_price),
            qty=money(fill_result.filled_quantity),
            fee=money(fill_result.entry_fee),
            is_close=False,
            source_timestamp=datetime.now(timezone.utc),
            receive_timestamp=datetime.now(timezone.utc),
            execute_timestamp=datetime.now(timezone.utc),
        )
        position_id = order_id
        position = Position(
            position_id=position_id,
            account_id=account_id,
            day_id=day.day_id,
            instrument=INSTRUMENT,
            side=PositionSide.long if direction == "long" else PositionSide.short,
            qty=money(fill_result.filled_quantity),
            avg_entry_price=money(fill_result.fill_price),
            isolated_margin=money(fill_result.reserved_margin),
            stop_loss=stop_price,
            take_profit=take_profit,
            status=PositionStatus.open,
            entry_fee=money(fill_result.entry_fee),
        )

        postings = build_fill_postings(
            account_id=account_id,
            fill_price=fill_result.fill_price,
            fill_qty=fill_result.filled_quantity,
            fee=fill_result.entry_fee,
            is_close=False,
            side=PositionSide.long if direction == "long" else PositionSide.short,
            day_id=day.day_id,
            source_ref=str(fill.fill_id),
            reserved_margin=money(fill_result.reserved_margin),
        )
        try:
            await self.repository.commit_open_fill(
                order_id=order_id,
                fill=fill,
                position=position,
                postings=postings,
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._last_decision = Decision(
                reason_code=ReasonCode.engine_error,
                reason_detail="Financial commit failed; no partial fill was committed",
                error=str(exc),
                decided_at=datetime.now(timezone.utc),
            )
            log.exception("signal→order chain: atomic commit failed")
            return

        self._positions[position_id] = position
        log.info(
            "signal→order chain: atomic fill committed (price=%s, qty=%s, postings=%d)",
            fill_result.fill_price, fill_result.filled_quantity, len(postings),
        )

        self._last_decision = Decision(
            reason_code=ReasonCode.waiting_trigger,
            reason_detail=f"Order filled: {direction} {fill_result.filled_quantity} @ {fill_result.fill_price}",
            decided_at=datetime.now(timezone.utc),
        )
        log.info(
            "signal→order chain complete: position %s, entry=%s, qty=%s",
            position_id, fill_result.fill_price, fill_result.filled_quantity,
        )

    def _get_auto_account_id(self) -> UUID | None:
        """Get the auto account ID for signal execution.

        The auto account is learned from its ``start_day`` command.  It is
        deliberately not inferred from a manual position: the two accounts
        have independent balances and ownership.
        """
        return self._auto_account_id

    # ── Heartbeat ──

    async def _send_heartbeat(self, now: float) -> None:
        """Send a heartbeat to Redis."""
        self._last_heartbeat = now
        if self.heartbeat_sink is None:
            return
        payload = {
            "run_id": self.run_id,
            "instance_id": self.instance_id,
            "lease_token": str(self._fence_token),
            "timestamp": now,
            "fence_token": self._fence_token,
            "positions": len(self._positions),
            "open_orders": len(self._open_orders),
            "pending_commands": len(self._pending_commands),
            "entries_blocked": self._entries_blocked,
            "recovered": self._recovered,
            "last_error": self._last_error,
        }
        try:
            await self.heartbeat_sink.send(self.heartbeat_key, payload)
        except Exception as exc:
            log.warning("heartbeat failed: %s", exc)

    # ── Status / DTO ──

    def status(self) -> RunnerStatus:
        """Return a snapshot of the runner's current state."""
        return RunnerStatus(
            running=self._running,
            recovered=self._recovered,
            fence_token=self._fence_token,
            active_positions=list(self._positions.values()),
            pending_commands=list(self._pending_commands),
            open_orders=list(self._open_orders.values()),
            last_signal=self._last_signal,
            last_heartbeat_epoch=self._last_heartbeat,
            last_poll_epoch=self._last_poll,
            entries_blocked=self._entries_blocked,
            last_error=self._last_error,
            last_decision=self._last_decision,
            instance_id=self.instance_id,
            run_id=self.run_id,
        )

    def build_status_dto(
        self,
        *,
        owner_id: UUID | None = None,
        day_id: UUID | None = None,
        account_kind: str | None = None,
        equity: Decimal | None = None,
        realized_pnl: Decimal | None = None,
        unrealized_pnl: Decimal | None = None,
        total_fees: Decimal | None = None,
    ) -> StatusDTO:
        """Build a :class:`StatusDTO` from the current runner state."""
        last_decision = self._last_decision
        return StatusDTO(
            owner_id=owner_id,
            day_id=day_id,
            account_kind=account_kind,  # type: ignore[arg-type]
            strategy_version=self.strategy.VERSION,
            open_positions=len(self._positions),
            pending_orders=len(self._open_orders),
            closed_trades=0,
            engine_status="healthy" if self._running else "unknown",
            engine_age_seconds=(time.time() - self._last_heartbeat) if self._last_heartbeat else None,
            reason_code=last_decision.reason_code if last_decision else ReasonCode.waiting_trigger,
            last_decision_code=last_decision.reason_code if last_decision else None,
            last_decision_detail=last_decision.reason_detail if last_decision else None,
            equity=equity,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_fees=total_fees,
        )

    # ── Manual position management (for testing / recovery) ──

    def add_position(self, pos: Position) -> None:
        """Manually add a position (used in tests/recovery)."""
        self._positions[pos.position_id] = pos

    def close_position(self, position_id: UUID) -> bool:
        """Manually close a position by ID.  Returns True if found.

        This is the sync test/recovery helper.  It removes the position
        from the in-memory dict.  For a real close with DB persistence,
        use the async close via command.
        """
        if position_id in self._positions:
            del self._positions[position_id]
            return True
        return False

    @property
    def entries_blocked(self) -> bool:
        return self._entries_blocked

    @property
    def recovered(self) -> bool:
        return self._recovered

    @property
    def position_count(self) -> int:
        return len(self._positions)
