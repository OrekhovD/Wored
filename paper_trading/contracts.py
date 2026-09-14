"""
paper_trading.contracts — versioned domain schemas for paper trading.

All money values are ``decimal.Decimal`` in Python and ``NUMERIC(20,8)`` in
PostgreSQL.  When serialised to JSON they become decimal strings (e.g.
``"1000.00000000"``) to preserve exact precision.  IDs are ``uuid.UUID``.

Python 3.9 compatible: ``from __future__ import annotations`` and
``Optional[X]`` instead of ``X | None``.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

#: Contracts/schema version.  Bump when any serialised shape changes.
SCHEMA_VERSION: int = 2

#: Conventional zero-Decimal for comparisons without floating-point error.
ZERO: Decimal = Decimal("0")

#: Decimal quantiser for all monetary values — 8 decimal places.
MONEY_QUANT: Decimal = Decimal("0.00000001")


def money(value: Any) -> Decimal:
    """Coerce *value* to a Decimal quantised to 8 dp (NUMERIC(20,8) parity)."""
    d = Decimal(str(value))
    return d.quantize(MONEY_QUANT)


def new_uuid() -> UUID:
    """Generate a new UUID4."""
    return uuid4()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AccountKind(str, enum.Enum):
    """Two independent simulation accounts per owner."""

    manual = "manual"
    auto = "auto"


class DayState(str, enum.Enum):
    """Trading-day state machine (REQ-08).

    idle → starting → running → closing → closed

    ``recovery_required`` and ``settlement_pending`` are explicit sub-states
    that overlay the main lifecycle.
    """

    idle = "idle"
    starting = "starting"
    running = "running"
    closing = "closing"
    closed = "closed"
    recovery_required = "recovery_required"
    settlement_pending = "settlement_pending"


class AutomationState(str, enum.Enum):
    """Automation lifecycle (separate from day state)."""

    initializing = "initializing"
    armed = "armed"  # waiting_signal
    waiting_signal = "waiting_signal"
    position_open = "position_open"
    paused = "paused"
    cooldown = "cooldown"
    risk_blocked = "risk_blocked"
    data_stale = "data_stale"
    engine_error = "engine_error"


class OrderSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class PositionSide(str, enum.Enum):
    long = "long"
    short = "short"


class OrderType(str, enum.Enum):
    market = "market"
    stop_market = "stop_market"
    market_on_trigger = "market_on_trigger"


class OrderState(str, enum.Enum):
    pending = "pending"
    submitted = "submitted"
    partially_filled = "partially_filled"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"
    expired = "expired"


class PositionStatus(str, enum.Enum):
    open = "open"
    closed = "closed"
    liquidated = "liquidated"


class CommandStatus(str, enum.Enum):
    accepted = "accepted"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    conflict = "conflict"


class CommandType(str, enum.Enum):
    start_day = "start_day"
    finish_day = "finish_day"
    set_automation = "set_automation"
    pause_auto = "pause_auto"
    resume_auto = "resume_auto"
    submit_order = "submit_order"
    close_position = "close_position"
    close_all = "close_all"
    cancel_order = "cancel_order"


class ReasonCode(str, enum.Enum):
    """Reason codes explaining why the engine is waiting or blocked.

    These appear in ``Decision.reason_code`` and ``StatusDTO.reason_code`` so
    that Telegram/WebUI can show a concrete, actionable explanation.
    """

    waiting_regime = "waiting_regime"
    waiting_trigger = "waiting_trigger"
    waiting_data = "waiting_data"
    waiting_warmup = "waiting_warmup"
    no_trade = "no_trade"
    cooldown = "cooldown"
    risk_blocked = "risk_blocked"
    data_stale = "data_stale"
    paused = "paused"
    day_ending = "day_ending"
    day_closed = "day_closed"
    recovery_pending = "recovery_pending"
    settlement_pending = "settlement_pending"
    engine_error = "engine_error"
    ai_timeout = "ai_timeout"
    ai_quota_exhausted = "ai_quota_exhausted"
    ai_invalid_response = "ai_invalid_response"
    expired_plan = "expired_plan"
    insufficient_data = "insufficient_data"
    signal_expired = "signal_expired"


class JournalBucket(str, enum.Enum):
    """Double-entry-ish accounting buckets for the append-only journal."""

    cash = "cash"
    entry_fee = "entry_fee"
    exit_fee = "exit_fee"
    funding = "funding"
    realized_gross_pnl = "realized_gross_pnl"
    realized_net_pnl = "realized_net_pnl"
    reserved_margin = "reserved_margin"
    released_margin = "released_margin"
    liquidation = "liquidation"
    deposit = "deposit"
    withdrawal = "withdrawal"
    adjustment = "adjustment"


class JournalSourceType(str, enum.Enum):
    """What financial event produced the posting."""

    deposit = "deposit"
    fill = "fill"
    funding = "funding"
    liquidation = "liquidation"
    closeout = "closeout"
    reconciliation = "reconciliation"
    manual_adjustment = "manual_adjustment"


# ---------------------------------------------------------------------------
# Owner / Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """A trading owner with a stable UUID.

    ``telegram_id`` and ``webui_identity`` are verified mappings; username
    alone is never an authorisation key.
    """

    owner_id: UUID
    display_name: str
    telegram_id: Optional[int] = None
    webui_identity: Optional[str] = None
    created_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class Account:
    """A simulation account.

    Two kinds: ``manual`` and ``auto``.  Each owner has at most one of each
    (UNIQUE(owner_id, kind)).  Accounts are not zeroed on new day; there are
    no automatic transfers between manual and auto.
    """

    account_id: UUID
    owner_id: UUID
    kind: AccountKind
    currency: str = "USDT"
    opening_deposit: Decimal = field(default_factory=lambda: money("1000"))
    created_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Trading day
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradingDay:
    """A trading day for an owner.

    State machine: idle → starting → running → closing → closed.
    At most one incomplete day per owner.
    """

    day_id: UUID
    owner_id: UUID
    timezone: str = "Asia/Bangkok"
    start_utc: Optional[datetime] = None
    end_utc: Optional[datetime] = None
    state: DayState = DayState.idle
    strategy_version: str = "baseline_v1"
    settings_snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Command (idempotency)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A user/strategy command with idempotency.

    Idempotency rules:
      - same ``idempotency_key`` + same ``request_hash`` → return existing
        result (``status`` / ``result`` / ``error``).
      - same ``idempotency_key`` + different ``request_hash`` → ``conflict``.

    ``expected_revision`` enables optimistic concurrency on the target
    entity (e.g. account/day revision).
    """

    command_id: UUID
    owner_id: UUID
    account_id: Optional[UUID]
    day_id: Optional[UUID]
    command_type: CommandType
    idempotency_key: str
    request_hash: str  # SHA-256 of canonical payload
    expected_revision: Optional[int] = None
    status: CommandStatus = CommandStatus.accepted
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Order / Fill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    """A simulated order.

    Lineage: ``signal_id`` links back to the signal/plan that produced it.
    ``origin`` distinguishes user vs auto; ``actor`` records who placed it
    (user intervention on auto keeps ``origin=auto``, ``actor=user``).
    """

    order_id: UUID
    account_id: UUID
    day_id: UUID
    origin: str  # "user" | "auto"
    actor: str  # "user" | "auto"
    signal_id: Optional[UUID] = None
    side: OrderSide = OrderSide.buy
    order_type: OrderType = OrderType.market
    instrument: str = "BTC-USDT"
    qty: Decimal = field(default_factory=lambda: ZERO)
    price: Optional[Decimal] = None  # limit/stop reference, not fill guarantee
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    state: OrderState = OrderState.pending
    filled_qty: Decimal = field(default_factory=lambda: ZERO)
    execution_engine_version: str = "1"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class Fill:
    """An execution fill.

    Repeating the same command does NOT create a duplicate fill — fills are
    linked to their originating command/order via the idempotency layer.

    ``slippage`` is informational (already included in ``price``); no second
    charge.
    """

    fill_id: UUID
    order_id: UUID
    account_id: UUID
    execution_quote_id: str
    instrument: str = "BTC-USDT"
    side: OrderSide = OrderSide.buy
    price: Decimal = field(default_factory=lambda: ZERO)
    qty: Decimal = field(default_factory=lambda: ZERO)
    fee: Decimal = field(default_factory=lambda: ZERO)
    fee_rate: Decimal = field(default_factory=lambda: money("0.0006"))
    slippage_bps: Decimal = field(default_factory=lambda: ZERO)
    is_close: bool = False  # True if this fill reduces/closes a position
    source_timestamp: Optional[datetime] = None
    receive_timestamp: Optional[datetime] = None
    execute_timestamp: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """An open or closed position.

    Lineage traces to fills.  ``owner_engine_version`` identifies which
    engine version owns this position (prevents dual ownership during
    cutover).
    """

    position_id: UUID
    account_id: UUID
    day_id: UUID
    instrument: str = "BTC-USDT"
    side: PositionSide = PositionSide.long
    qty: Decimal = field(default_factory=lambda: ZERO)
    avg_entry_price: Decimal = field(default_factory=lambda: ZERO)
    isolated_margin: Decimal = field(default_factory=lambda: ZERO)
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    status: PositionStatus = PositionStatus.open
    owner_engine_version: str = "1"
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    close_price: Optional[Decimal] = None
    realized_gross_pnl: Decimal = field(default_factory=lambda: ZERO)
    realized_net_pnl: Decimal = field(default_factory=lambda: ZERO)
    entry_fee: Decimal = field(default_factory=lambda: ZERO)
    exit_fee: Decimal = field(default_factory=lambda: ZERO)
    funding_cashflow: Decimal = field(default_factory=lambda: ZERO)
    schema_version: int = SCHEMA_VERSION

    @property
    def quantity(self) -> Decimal:
        """Alias for qty — used by execution module."""
        return self.qty

    @property
    def direction(self) -> str:
        """Alias for side — returns 'long' or 'short' string."""
        return self.side.value if isinstance(self.side, PositionSide) else str(self.side)

    @property
    def avg_entry(self) -> Decimal:
        """Alias for avg_entry_price."""
        return self.avg_entry_price


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalPosting:
    """An append-only journal entry.

    UNIQUE on the financial source and type prevents double-posting: the same
    fill/funding/closeout cannot produce two postings of the same kind.
    """

    posting_id: UUID
    account_id: UUID
    day_id: Optional[UUID] = None
    event_id: UUID = field(default_factory=new_uuid)
    source_type: JournalSourceType = JournalSourceType.fill
    source_ref: Optional[str] = None  # fill_id / funding_event_id / etc.
    currency: str = "USDT"
    bucket: JournalBucket = JournalBucket.cash
    amount: Decimal = field(default_factory=lambda: ZERO)  # signed
    occurred_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Decision / Heartbeat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """A recorded engine decision (reason for waiting/blocked/acting).

    Long-lived in PostgreSQL so the UI can show a history of decisions.
    """

    decision_id: UUID = field(default_factory=new_uuid)
    run_id: Optional[str] = None
    account_id: Optional[UUID] = None
    day_id: Optional[UUID] = None
    reason_code: ReasonCode = ReasonCode.waiting_trigger
    reason_detail: Optional[str] = None
    required_metrics: Dict[str, Any] = field(default_factory=dict)
    actual_metrics: Dict[str, Any] = field(default_factory=dict)
    next_check: Optional[datetime] = None
    next_transition: Optional[str] = None
    strategy_version: str = "baseline_v1"
    engine_version: str = "1"
    error: Optional[str] = None
    decided_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class Heartbeat:
    """Runtime heartbeat published every PAPER_HEARTBEAT_INTERVAL_SECONDS.

    UI staleness thresholds: age ≤ 15s healthy, 15–30 delayed, > 30 unconfirmed.
    """

    run_id: str
    instance_id: str
    lease_token: str
    account_id: Optional[UUID] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    engine_version: str = "1"
    strategy_version: str = "baseline_v1"
    data_freshness_seconds: Optional[float] = None
    processed_sequence: int = 0
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Signal (uniqueness constraint target)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """A trading signal with uniqueness guarantee.

    UNIQUE(account_id, strategy_version, instrument, closed_bar_time, direction)
    ensures one signal does not create two entries after restart.
    """

    signal_id: UUID = field(default_factory=new_uuid)
    account_id: Optional[UUID] = None
    strategy_version: str = "baseline_v1"
    instrument: str = "BTC-USDT"
    closed_bar_time: Optional[datetime] = None
    direction: PositionSide = PositionSide.long
    entry_ref_price: Decimal = field(default_factory=lambda: ZERO)
    stop_loss: Decimal = field(default_factory=lambda: ZERO)
    take_profit: Decimal = field(default_factory=lambda: ZERO)
    atr_value: Decimal = field(default_factory=lambda: ZERO)
    valid_until: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# StatusDTO — unified status for Telegram and WebUI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusDTO:
    """Unified status DTO consumed by both Telegram and WebUI adapters.

    One DTO, two presentations.  Contains everything needed to render the
    "Сегодня" card, explain zero positions, and show next action.
    """

    owner_id: Optional[UUID] = None
    day_id: Optional[UUID] = None
    account_kind: Optional[AccountKind] = None
    mode: str = "baseline_auto"  # baseline_auto | ai_plan
    day_state: DayState = DayState.idle
    automation_state: AutomationState = AutomationState.initializing
    engine_status: str = "unknown"  # healthy | delayed | unconfirmed | unknown
    engine_age_seconds: Optional[float] = None
    feed_status: str = "unknown"  # fresh | stale | unknown
    feed_age_seconds: Optional[float] = None
    strategy_version: str = "baseline_v1"
    plan_version: Optional[str] = None
    plan_ttl_seconds: Optional[float] = None
    open_positions: int = 0
    pending_orders: int = 0
    closed_trades: int = 0
    last_decision_code: Optional[ReasonCode] = None
    last_decision_at: Optional[datetime] = None
    last_decision_detail: Optional[str] = None
    reason_code: Optional[ReasonCode] = None
    actual_threshold: Optional[str] = None
    required_threshold: Optional[str] = None
    next_check: Optional[datetime] = None
    next_transition: Optional[str] = None
    equity: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    total_fees: Optional[Decimal] = None
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def decimal_to_jsonstr(d: Decimal) -> str:
    """Serialise a Decimal to a decimal string for JSON."""
    return str(d.quantize(MONEY_QUANT))


def jsonstr_to_decimal(s: str) -> Decimal:
    """Deserialise a decimal string from JSON to a Decimal."""
    return Decimal(s).quantize(MONEY_QUANT)


def uuid_to_str(u: Optional[UUID]) -> Optional[str]:
    return str(u) if u is not None else None


def str_to_uuid(s: Optional[str]) -> Optional[UUID]:
    return UUID(s) if s else None