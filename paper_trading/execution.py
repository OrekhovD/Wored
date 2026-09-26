"""Simulated execution engine for paper trading.

Models IOC market orders with partial fills, stop-market triggers, partial
and full closes, funding payments, and mark-based unrealized PnL.

All monetary values are :class:`~decimal.Decimal`.

HTX BTC-USDT USDT-margined isolated perpetual:
  contract multiplier: 0.001
  price tick: 0.1
  quantity step: 0.001
  taker fee: 0.0006 (0.06%)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from paper_trading.market import (
    DEFAULT_SLIPPAGE_BPS,
    PerpetualSnapshot,
    apply_slippage,
    available_quantity,
    get_execution_price,
)

__all__ = [
    "FEE_RATE",
    "CloseResult",
    "FillResult",
    "FundingResult",
    "MarginAdjustResult",
    "Position",
    "ReverseResult",
    "StopTriggerResult",
    "adjust_margin",
    "apply_funding",
    "calculate_unrealized",
    "check_stop_trigger",
    "estimate_equity",
    "execute_close",
    "execute_market_order",
    "execute_reverse",
    "execute_stop_market",
]

FEE_RATE = Decimal("0.0006")  # 0.06% taker


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """An open paper-trading position."""

    position_id: str
    account_id: str
    instrument: str
    direction: str  # "long" or "short"
    entry_price: Decimal
    quantity: Decimal
    leverage: int
    stop_price: Decimal
    take_profit: Decimal | None = None
    reserved_margin: Decimal = Decimal(0)
    entry_fee: Decimal = Decimal(0)
    allocated_entry_fee: Decimal = Decimal(0)  # portion of entry fee already allocated on close
    opened_at: str = ""
    status: str = "open"


@dataclass
class FillResult:
    """Result of executing a market order (IOC with possible partial fill)."""

    filled: bool
    fill_price: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    entry_fee: Decimal
    notional: Decimal
    reserved_margin: Decimal
    position: Position | None = None
    reason: str = ""


@dataclass
class StopTriggerResult:
    """Result of checking whether a stop-loss should trigger."""

    triggered: bool
    trigger_price: Decimal
    reason: str = ""


@dataclass
class CloseResult:
    """Result of closing a position (full or partial)."""

    closed: bool
    close_price: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal
    gross_pnl: Decimal
    close_fee: Decimal
    allocated_entry_fee: Decimal
    net_pnl: Decimal  # net of entry fee portion + close fee
    realized_net: Decimal  # net_pnl including already-paid entry fee
    exit_reason: str = ""
    remaining_position: Position | None = None


@dataclass
class FundingResult:
    """Result of applying a funding payment."""

    funding_amount: Decimal  # positive = received, negative = paid
    rate: Decimal
    mark_price: Decimal
    quantity: Decimal


# ---------------------------------------------------------------------------
# Execute market order (IOC, partial fill)
# ---------------------------------------------------------------------------


def execute_market_order(
    snapshot: PerpetualSnapshot,
    direction: str,
    requested_quantity: Decimal,
    leverage: int,
    stop_price: Decimal,
    *,
    position_id: str = "",
    account_id: str = "proto-manual",
    instrument: str = "BTC-USDT",
    take_profit: Decimal | None = None,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    available_fraction: Decimal = Decimal("0.1"),
    now: datetime | None = None,
) -> FillResult:
    """Execute a market order with IOC semantics and partial-fill modeling.

    The exchange fills up to ``available_quantity`` at the top of book
    (with adverse slippage applied). Any unfilled remainder is cancelled
    (Immediate-or-Cancel).

    Parameters
    ----------
    snapshot
        Validated market snapshot.
    direction
        ``"long"`` or ``"short"``.
    requested_quantity
        Quantity in contracts.
    leverage
        Leverage multiplier (1–100).
    stop_price
        Stop-loss price.
    available_fraction
        Fraction of top-of-book depth available for fill (default 0.1).

    Returns a :class:`FillResult` describing the fill.
    """
    now = now or datetime.now(timezone.utc)
    direction = direction.lower()
    if direction not in ("long", "short"):
        raise ValueError(f"direction: expected 'long' or 'short', got {direction!r}")
    if requested_quantity <= 0:
        return FillResult(
            filled=False,
            fill_price=Decimal(0),
            filled_quantity=Decimal(0),
            remaining_quantity=requested_quantity,
            entry_fee=Decimal(0),
            notional=Decimal(0),
            reserved_margin=Decimal(0),
            reason="invalid_quantity",
        )

    # Raw execution price (ask for long open, bid for short open)
    raw_price = get_execution_price(snapshot, direction, "open")
    fill_price = apply_slippage(raw_price, direction, "open", bps=slippage_bps)

    # Available quantity at top of book
    max_available = available_quantity(snapshot, fraction=available_fraction)
    filled_quantity = min(requested_quantity, max_available)
    remaining_quantity = requested_quantity - filled_quantity

    # Quantize filled quantity to step
    step = snapshot.quantity_step
    if step > 0 and filled_quantity > 0:
        filled_quantity = (filled_quantity / step).to_integral_value(
            rounding="ROUND_DOWN"
        ) * step

    if filled_quantity <= 0:
        return FillResult(
            filled=False,
            fill_price=fill_price,
            filled_quantity=Decimal(0),
            remaining_quantity=requested_quantity,
            entry_fee=Decimal(0),
            notional=Decimal(0),
            reserved_margin=Decimal(0),
            reason="no_liquidity",
        )

    # Notional = filled_quantity (in contracts) * contract_size * fill_price
    # For HTX BTC-USDT: contract_size=0.001 BTC, so notional = qty * 0.001 * price
    contract_size = getattr(snapshot, "contract_size", Decimal("0.001"))
    notional = filled_quantity * contract_size * fill_price
    entry_fee = notional * FEE_RATE
    reserved_margin = notional / Decimal(leverage) if leverage >= 1 else notional

    position = Position(
        position_id=position_id,
        account_id=account_id,
        instrument=instrument,
        direction=direction,
        entry_price=fill_price,
        quantity=filled_quantity,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        reserved_margin=reserved_margin,
        entry_fee=entry_fee,
        allocated_entry_fee=Decimal(0),
        opened_at=now.isoformat(),
        status="open",
    )

    return FillResult(
        filled=True,
        fill_price=fill_price,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        entry_fee=entry_fee,
        notional=notional,
        reserved_margin=reserved_margin,
        position=position,
        reason="",
    )


# ---------------------------------------------------------------------------
# Stop trigger check
# ---------------------------------------------------------------------------


def check_stop_trigger(
    position: Position,
    snapshot: PerpetualSnapshot,
) -> StopTriggerResult:
    """Check whether a position's stop-loss should trigger based on mark price.

    For a long position, the stop triggers when ``mark <= stop_price``.
    For a short position, the stop triggers when ``mark >= stop_price``.
    """
    mark = snapshot.mark
    if position.direction == "long":
        if mark <= position.stop_price:
            return StopTriggerResult(
                triggered=True,
                trigger_price=mark,
                reason="mark_crossed_below_stop",
            )
    elif position.direction == "short":
        if mark >= position.stop_price:
            return StopTriggerResult(
                triggered=True,
                trigger_price=mark,
                reason="mark_crossed_above_stop",
            )
    return StopTriggerResult(
        triggered=False,
        trigger_price=mark,
        reason="",
    )


# ---------------------------------------------------------------------------
# Execute stop-market order
# ---------------------------------------------------------------------------


def execute_stop_market(
    position: Position,
    snapshot: PerpetualSnapshot,
    *,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    now: datetime | None = None,
) -> CloseResult:
    """Execute a stop-loss market order, closing the entire position.

    The execution price is the stop side (bid for long close, ask for short
    close) with adverse slippage applied.
    """
    return execute_close(
        position,
        snapshot,
        close_quantity=position.quantity,
        exit_reason="stop_loss",
        slippage_bps=slippage_bps,
        now=now,
    )


# ---------------------------------------------------------------------------
# Execute close (full or partial)
# ---------------------------------------------------------------------------


def execute_close(
    position: Position,
    snapshot: PerpetualSnapshot,
    *,
    close_quantity: Decimal | None = None,
    exit_reason: str = "manual_close",
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    now: datetime | None = None,
) -> CloseResult:
    """Close a position (fully or partially) with proportional fee allocation.

    When closing a partial quantity, the entry fee is allocated proportionally:
        allocated_entry_fee = position.entry_fee * (close_qty / total_qty)

    The remaining position's entry_fee and allocated_entry_fee are adjusted
    accordingly, and the unallocated entry fee stays with the remaining
    position for allocation on the next close.

    PnL calculation for contract quantity:
        base_qty   = qty * contract_size
        gross_pnl = ± base_qty * (close_price - entry_price)
        close_fee = base_qty * close_price * FEE_RATE
        net_pnl   = gross_pnl - close_fee - allocated_entry_fee
        realized_net = net_pnl - already_paid_entry_fee_at_open

    For the golden test cases:
        Long: entry=10000, exit=10100, qty=1, fee=0.0006
          gross = 1*(10100-10000) = 100
          entry_fee = 1*10000*0.0006 = 6
          exit_fee = 1*10100*0.0006 = 6.06
          net = 100 - 6 - 6.06 = 87.94 ✓

        Short: entry=10000, exit=10100 (short close=ask), qty=1
          Wait — short close means buying at ask. If entry=10000 (short open
          at bid), exit=9900 (short close at ask, which is lower):
          Actually the golden_short fixture specifies net=88.06.
          Short: entry=10000, exit=9900, qty=1
          gross = -1*(9900-10000) = 100
          entry_fee = 6, exit_fee = 1*9900*0.0006 = 5.94
          net = 100 - 6 - 5.94 = 88.06 ✓
    """
    now = now or datetime.now(timezone.utc)

    if close_quantity is None or close_quantity >= position.quantity:
        close_qty = position.quantity
    else:
        close_qty = close_quantity

    if close_qty <= 0:
        return CloseResult(
            closed=False,
            close_price=Decimal(0),
            closed_quantity=Decimal(0),
            remaining_quantity=position.quantity,
            gross_pnl=Decimal(0),
            close_fee=Decimal(0),
            allocated_entry_fee=Decimal(0),
            net_pnl=Decimal(0),
            realized_net=Decimal(0),
            exit_reason=exit_reason,
            remaining_position=position,
        )

    # Close price: bid for long close, ask for short close, with slippage
    raw_close = get_execution_price(snapshot, position.direction, "close")
    close_price = apply_slippage(raw_close, position.direction, "close", bps=slippage_bps)

    sign = Decimal(1) if position.direction == "long" else Decimal(-1)
    contract_size = snapshot.contract_size
    base_qty = close_qty * contract_size
    gross_pnl = sign * base_qty * (close_price - position.entry_price)
    close_fee = base_qty * close_price * FEE_RATE

    # Proportional allocation of entry fee
    if position.quantity > 0:
        fraction = close_qty / position.quantity
    else:
        fraction = Decimal(0)
    allocated_entry_fee = position.entry_fee * fraction

    # Net PnL for this close (gross - close_fee - allocated entry fee)
    net_pnl = gross_pnl - close_fee - allocated_entry_fee

    # Realized net: net_pnl minus the entry fee that was already paid at open
    # (the entry fee was deducted from cash at open; now we account for it)
    # realized_net = gross_pnl - close_fee - position.entry_fee (full entry fee
    # for full close, or proportional for partial)
    realized_net = gross_pnl - close_fee - allocated_entry_fee

    remaining_qty = position.quantity - close_qty
    remaining_position: Position | None = None

    if remaining_qty > 0:
        # Update remaining position: reduce quantity, adjust entry fee allocation
        remaining_entry_fee = position.entry_fee - allocated_entry_fee
        remaining_position = Position(
            position_id=position.position_id,
            account_id=position.account_id,
            instrument=position.instrument,
            direction=position.direction,
            entry_price=position.entry_price,
            quantity=remaining_qty,
            leverage=position.leverage,
            stop_price=position.stop_price,
            take_profit=position.take_profit,
            reserved_margin=position.reserved_margin * fraction_remaining(
                close_qty, position.quantity
            ),
            entry_fee=remaining_entry_fee,
            allocated_entry_fee=Decimal(0),
            opened_at=position.opened_at,
            status="open",
        )
    # else: position is fully closed, no remaining

    return CloseResult(
        closed=True,
        close_price=close_price,
        closed_quantity=close_qty,
        remaining_quantity=remaining_qty,
        gross_pnl=gross_pnl,
        close_fee=close_fee,
        allocated_entry_fee=allocated_entry_fee,
        net_pnl=net_pnl,
        realized_net=realized_net,
        exit_reason=exit_reason,
        remaining_position=remaining_position,
    )


def fraction_remaining(closed: Decimal, total: Decimal) -> Decimal:
    """Return the fraction of the position that remains after a partial close."""
    if total <= 0:
        return Decimal(0)
    return (total - closed) / total


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def apply_funding(
    position: Position,
    funding_rate: Decimal,
    mark_price: Decimal,
    contract_size: Decimal = Decimal(1),
) -> FundingResult:
    """Apply a funding payment to a position.

    Funding is calculated as:
        funding = quantity * contract_size * mark_price * funding_rate

    For a positive funding rate:
      * longs pay  → funding_amount is negative
      * shorts receive → funding_amount is positive

    For a negative funding rate:
      * longs receive → funding_amount is positive
      * shorts pay → funding_amount is negative

    Returns :class:`FundingResult` with the signed funding amount.
    """
    if position.quantity <= 0:
        return FundingResult(
            funding_amount=Decimal(0),
            rate=funding_rate,
            mark_price=mark_price,
            quantity=position.quantity,
        )

    if contract_size <= 0:
        raise ValueError("contract_size: must be positive")
    base_funding = position.quantity * contract_size * mark_price * funding_rate
    if position.direction == "long":
        # Long pays positive rate (negative cash flow)
        funding_amount = -base_funding
    else:
        # Short receives positive rate (positive cash flow)
        funding_amount = base_funding

    return FundingResult(
        funding_amount=funding_amount,
        rate=funding_rate,
        mark_price=mark_price,
        quantity=position.quantity,
    )


# ---------------------------------------------------------------------------
# Unrealized PnL (mark-based)
# ---------------------------------------------------------------------------


def calculate_unrealized(
    position: Position,
    mark_price: Decimal,
    contract_size: Decimal = Decimal(1),
) -> Decimal:
    """Calculate unrealized PnL based on the current mark price.

    For a long position:
        unrealized = quantity * (mark - entry)
    For a short position:
        unrealized = quantity * (entry - mark)

    This is the gross unrealized PnL (before fees and funding).
    """
    if position.direction == "long":
        return position.quantity * contract_size * (mark_price - position.entry_price)
    elif position.direction == "short":
        return position.quantity * contract_size * (position.entry_price - mark_price)
    raise ValueError(f"direction: expected 'long' or 'short', got {position.direction!r}")


# ---------------------------------------------------------------------------
# Equity estimation
# ---------------------------------------------------------------------------


def estimate_equity(
    cash: Decimal,
    positions: list[Position],
    mark_prices: dict[str, Decimal],
) -> Decimal:
    """Estimate account equity: cash + unrealized PnL of all open positions.

    Parameters
    ----------
    cash
        Current cash balance (USDT).
    positions
        List of open positions.
    mark_prices
        Mapping of instrument → mark price.
    """
    equity = cash
    for pos in positions:
        mark = mark_prices.get(pos.instrument)
        if mark is None:
            continue
        equity += calculate_unrealized(pos, mark)
    return equity


# ---------------------------------------------------------------------------
# Margin adjustment (idempotent)
# ---------------------------------------------------------------------------


@dataclass
class MarginAdjustResult:
    """Result of an adjust_margin operation."""

    position_id: str
    delta: Decimal
    new_reserved_margin: Decimal
    new_liquidation_distance: Decimal
    idempotency_key: str
    applied: bool = True
    reason: str = ""


def adjust_margin(
    position: Position,
    delta: Decimal,
    *,
    idempotency_key: str,
    maintenance_margin_rate: Decimal,
    taker_fee_rate: Decimal = FEE_RATE,
    notional: Decimal | None = None,
    applied_keys: set[str] | None = None,
) -> MarginAdjustResult:
    """Adjust isolated margin on a position (idempotent).

    A positive ``delta`` adds margin (top-up); a negative delta withdraws.
    The operation is idempotent: if ``idempotency_key`` is in
    ``applied_keys``, the result is returned without re-applying.

    Parameters
    ----------
    position
        The open position to adjust.
    delta
        Amount of USDT to add (positive) or withdraw (negative).
    idempotency_key
        Unique key for this operation.  Retry with the same key is a no-op.
    maintenance_margin_rate
        Current V5 MMR from the risk tier snapshot.
    taker_fee_rate
        Taker fee rate for liquidation distance recalculation.
    notional
        Position notional value.  If omitted, estimated from entry * qty.
    applied_keys
        Set of already-applied idempotency keys.  If provided and the key
        is present, the operation is skipped.
    """
    if position.status != "open":
        return MarginAdjustResult(
            position_id=position.position_id,
            delta=delta,
            new_reserved_margin=position.reserved_margin,
            new_liquidation_distance=Decimal(0),
            idempotency_key=idempotency_key,
            applied=False,
            reason="position_not_open",
        )

    if applied_keys and idempotency_key in applied_keys:
        return MarginAdjustResult(
            position_id=position.position_id,
            delta=delta,
            new_reserved_margin=position.reserved_margin,
            new_liquidation_distance=Decimal(0),
            idempotency_key=idempotency_key,
            applied=False,
            reason="duplicate_idempotency_key",
        )

    new_margin = position.reserved_margin + delta
    if new_margin <= 0:
        return MarginAdjustResult(
            position_id=position.position_id,
            delta=delta,
            new_reserved_margin=position.reserved_margin,
            new_liquidation_distance=Decimal(0),
            idempotency_key=idempotency_key,
            applied=False,
            reason="topup_rejected: margin would be non-positive",
        )

    eff_notional = notional or (position.entry_price * position.quantity)
    d_liq = (new_margin) / eff_notional - maintenance_margin_rate - taker_fee_rate

    position.reserved_margin = new_margin

    return MarginAdjustResult(
        position_id=position.position_id,
        delta=delta,
        new_reserved_margin=new_margin,
        new_liquidation_distance=d_liq,
        idempotency_key=idempotency_key,
        applied=True,
    )


# ---------------------------------------------------------------------------
# Reverse position (atomic close + new order)
# ---------------------------------------------------------------------------


@dataclass
class ReverseResult:
    """Result of a reverse operation (close + new entry in one transaction)."""

    closed_position_id: str
    new_position_id: str
    close_result: CloseResult
    fill_result: FillResult
    reason: str = ""


def execute_reverse(
    position: Position,
    snapshot: PerpetualSnapshot,
    *,
    idempotency_key: str,
    new_quantity: Decimal | None = None,
    new_leverage: int | None = None,
    new_stop_price: Decimal | None = None,
    new_take_profit: Decimal | None = None,
) -> ReverseResult:
    """Reverse a position: close the current one and open in the opposite direction.

    This is a logical atomic operation.  The caller is responsible for
    wrapping both the close and new-order repository writes in a single
    database transaction.

    The new position direction is the opposite of the current one.
    Entry price, stop, and take-profit are derived from the current
    snapshot for the new direction.
    """
    # 1. Close current position
    close_res = execute_close(position, snapshot, close_quantity=position.quantity)

    # 2. Determine new direction
    new_direction = "short" if position.direction == "long" else "long"
    qty = new_quantity or position.quantity
    lev = new_leverage or position.leverage

    # 3. Execute new market order in opposite direction
    new_stop = new_stop_price or (
        snapshot.bid * Decimal("0.995") if new_direction == "long"
        else snapshot.ask * Decimal("1.005")
    )
    fill_res = execute_market_order(
        snapshot=snapshot,
        direction=new_direction,
        requested_quantity=qty,
        leverage=int(lev),
        stop_price=new_stop,
        take_profit=new_take_profit,
    )

    # 4. Generate new position ID
    import uuid
    new_pos_id = str(uuid.uuid4())

    return ReverseResult(
        closed_position_id=position.position_id,
        new_position_id=new_pos_id,
        close_result=close_res,
        fill_result=fill_res,
        reason="reverse_executed",
    )
