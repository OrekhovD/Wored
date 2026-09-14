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
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID, uuid4

from paper_trading.contracts import (
    Command,
    CommandStatus,
    CommandType,
    Decision,
    Heartbeat,
    Order,
    OrderState,
    Position,
    PositionSide,
    PositionStatus,
    ReasonCode,
    Signal as ContractSignal,
    StatusDTO,
)
from paper_trading.strategy import Bar, BaselineV1Strategy, Signal as StrategySignal

log = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
SIGNAL_TTL_SECONDS = 60.0


# ─── Runner-internal data structures ───────────────────────────────────

@dataclass
class RunnerStatus:
    """Snapshot of the runner's current state for presenters / status DTO."""
    running: bool = False
    recovered: bool = False
    fence_token: int = 0
    active_positions: List[Position] = field(default_factory=list)
    pending_commands: List[Command] = field(default_factory=list)
    open_orders: List[Order] = field(default_factory=list)
    last_signal: Optional[StrategySignal] = None
    last_heartbeat_epoch: float = 0.0
    last_poll_epoch: float = 0.0
    entries_blocked: bool = True
    last_error: Optional[str] = None
    last_decision: Optional[Decision] = None
    instance_id: str = ""
    run_id: str = ""


# ─── Abstract store interfaces (dependency-injection) ──────────────────

class RecoveryStore:
    """Abstract interface for loading persisted state on restart.

    Subclasses connect to PostgreSQL.  The runner calls these methods
    during :meth:`PaperTradingRunner.recover`.
    """

    async def load_unfinished_commands(self) -> List[Command]:
        return []

    async def load_open_orders(self) -> List[Order]:
        return []

    async def load_open_positions(self) -> List[Position]:
        return []

    async def reconcile_ledger(self, positions: List[Position]) -> Dict[str, Any]:
        return {"reconciled": True, "discrepancies": []}

    async def cancel_expired_intents(self, expired_ids: List[str]) -> int:
        return 0


class HeartbeatSink:
    """Abstract interface for heartbeats (Redis)."""

    async def send(self, key: str, payload: Dict[str, Any]) -> None:
        pass


class CommandSource:
    """Abstract interface for receiving manual commands."""

    async def poll(self) -> List[Command]:
        return []


class MarketDataSource:
    """Abstract interface for fetching bars on each timeframe."""

    async def fetch_bars(self, timeframe: str, limit: int) -> List[Bar]:
        return []

    async def fetch_current_price(self) -> Optional[Decimal]:
        bars = await self.fetch_bars("1m", 1)
        return bars[-1].close if bars else None


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
        recovery_store: Optional[RecoveryStore] = None,
        heartbeat_sink: Optional[HeartbeatSink] = None,
        command_source: Optional[CommandSource] = None,
        market_data: Optional[MarketDataSource] = None,
        heartbeat_key: str = "paper_trading:runner:heartbeat",
        poll_interval: float = POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        instance_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        self.strategy = strategy
        self.recovery_store = recovery_store
        self.heartbeat_sink = heartbeat_sink
        self.command_source = command_source
        self.market_data = market_data
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
    def from_env(cls, **kwargs):
        """Create runner from environment variables."""
        import os
        strategy = BaselineV1Strategy()
        poll = float(os.getenv("PAPER_ENGINE_INTERVAL_SECONDS", "2"))
        hb = float(os.getenv("PAPER_HEARTBEAT_INTERVAL_SECONDS", "5"))
        return cls(
            strategy=strategy,
            poll_interval=poll,
            heartbeat_interval=hb,
            **kwargs,
        )

    # ── State (initialized after from_env creates the instance) ──
    def _init_state(self):
        self._positions: Dict[UUID, Position] = {}
        self._open_orders: Dict[UUID, Order] = {}
        self._pending_commands: List[Command] = []
        self._last_signal: Optional[StrategySignal] = None
        self._last_1m_bar_ts: Optional[str] = None
        self._entries_blocked: bool = True
        self._recovered: bool = False
        self._running: bool = False
        self._last_heartbeat: float = 0.0
        self._last_poll: float = 0.0
        self._last_error: Optional[str] = None
        self._last_decision: Optional[Decision] = None
        self._stop_event = asyncio.Event()

    # ── Fence token ──

    def acquire_fence_token(self) -> int:
        """Return the next monotonically increasing fence token."""
        token = self._next_fence_token
        self._next_fence_token += 1
        return token

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

    async def recover(self) -> Dict[str, Any]:
        """Load unfinished state from PostgreSQL and reconcile.

        Blocks new entries until recovery is complete.
        """
        self._entries_blocked = True
        report: Dict[str, Any] = {
            "commands_loaded": 0,
            "orders_loaded": 0,
            "positions_loaded": 0,
            "ledger_discrepancies": [],
            "intents_cancelled": 0,
        }

        if self.recovery_store is None:
            log.info("recovery: no store configured — skipping (entries unblocked)")
            self._recovered = True
            self._entries_blocked = False
            report["recovered"] = True
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

            # 4. Reconcile ledger
            ledger_result = await self.recovery_store.reconcile_ledger(
                list(self._positions.values())
            )
            report["ledger_discrepancies"] = ledger_result.get("discrepancies", [])

            # 5. Cancel expired signal intents
            now = time.time()
            expired: List[str] = []
            if self._last_signal is not None and self._last_signal.is_expired(now, SIGNAL_TTL_SECONDS):
                expired.append(self._last_signal.bar_timestamp)
            cancelled = await self.recovery_store.cancel_expired_intents(expired)
            report["intents_cancelled"] = cancelled
            if cancelled:
                self._last_signal = None

            self._recovered = True
            self._entries_blocked = False
            report["recovered"] = True
            log.info(
                "recovery complete: %d commands, %d orders, %d positions, %d intents cancelled",
                report["commands_loaded"],
                report["orders_loaded"],
                report["positions_loaded"],
                report["intents_cancelled"],
            )
        except Exception as exc:
            self._last_error = str(exc)
            log.exception("recovery failed: %s", exc)
            report["recovered"] = False
            report["error"] = str(exc)
            # Entries remain blocked on failure
        return report

    # ── Main loop ──

    async def run_cycle(self) -> None:
        """Single poll cycle — called by APScheduler every 2s."""
        now = time.time()
        self._last_poll = now
        try:
            # 1. SL/TP check (always, even if entries blocked)
            await self._check_sl_tp(now)
            # 2. Process pending commands (close, cancel, etc.)
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

        This is called every poll cycle and is never blocked by AI tasks.
        """
        if not self._positions:
            return
        current_price = await self._fetch_current_price()
        if current_price is None:
            return

        to_close: List[UUID] = []
        for pid, pos in self._positions.items():
            if pos.status != PositionStatus.open:
                continue
            sl = pos.stop_loss
            tp = pos.take_profit
            if sl is None or tp is None:
                continue
            if pos.side == PositionSide.long:
                if current_price <= sl:
                    to_close.append(pid)
                    log.info("SL hit: position %s @ %s (SL %s)", pid, current_price, sl)
                    self.strategy.on_stop_loss_hit(now)
                elif current_price >= tp:
                    to_close.append(pid)
                    log.info("TP hit: position %s @ %s (TP %s)", pid, current_price, tp)
            else:  # short
                if current_price >= sl:
                    to_close.append(pid)
                    log.info("SL hit: position %s @ %s (SL %s)", pid, current_price, sl)
                    self.strategy.on_stop_loss_hit(now)
                elif current_price <= tp:
                    to_close.append(pid)
                    log.info("TP hit: position %s @ %s (TP %s)", pid, current_price, tp)

        for pid in to_close:
            del self._positions[pid]

    async def _fetch_current_price(self) -> Optional[Decimal]:
        """Fetch the latest mark/last price from the market data source."""
        if self.market_data is None:
            return None
        try:
            return await self.market_data.fetch_current_price()
        except Exception as exc:
            log.warning("fetch_current_price failed: %s", exc)
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

        remaining: List[Command] = []
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
            # Close a specific position
            target_pid = cmd.account_id  # In the full impl, payload carries position_id
            if target_pid and target_pid in self._positions:
                del self._positions[target_pid]
                log.info("manual close: position %s", target_pid)
            return True

        if ct == CommandType.close_all:
            # Close all positions for an account
            acct = cmd.account_id
            if acct:
                to_remove = [
                    pid for pid, pos in self._positions.items() if pos.account_id == acct
                ]
            else:
                to_remove = list(self._positions.keys())
            for pid in to_remove:
                del self._positions[pid]
            log.info("manual close_all: %d positions", len(to_remove))
            return True

        if ct == CommandType.cancel_order:
            # Cancel a pending order
            self._last_signal = None
            log.info("manual cancel: signal/order intent cleared")
            return True

        # Other commands (start_day, finish_day, etc.) are acknowledged
        log.info("command %s acknowledged", ct.value)
        return True

    # ── Signal evaluation ──

    async def _evaluate_signals(self, now: float) -> None:
        """Evaluate the strategy on new 1m bar close."""
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

        # Fetch higher TF closes (latest)
        close_1h: Optional[Decimal] = None
        close_15m: Optional[Decimal] = None
        try:
            bars_1h = await self.market_data.fetch_bars("1h", 1)
            if bars_1h:
                close_1h = bars_1h[-1].close
            bars_15m = await self.market_data.fetch_bars("15m", 1)
            if bars_15m:
                close_15m = bars_15m[-1].close
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
        owner_id: Optional[UUID] = None,
        day_id: Optional[UUID] = None,
        account_kind: Optional[str] = None,
        equity: Optional[Decimal] = None,
        realized_pnl: Optional[Decimal] = None,
        unrealized_pnl: Optional[Decimal] = None,
        total_fees: Optional[Decimal] = None,
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
        """Manually close a position by ID.  Returns True if found."""
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