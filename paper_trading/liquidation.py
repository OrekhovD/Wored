"""paper_trading.liquidation — mark-triggered liquidation state machine (P5, MC-16).

The contract gap this closes (ТЗ §2): ``PositionStatus.liquidated`` existed in
the domain but no handler produced it.  One chain of truth is enforced here:

    open position → mark crosses the isolated-margin liquidation price
                  → PENDING event → forced FULL close (exactly one close fill,
                    ``exit_reason="liquidation"``) → LIQUIDATED.

Rules (MC-16):

  * trigger is **mark-based** (``stop_policy=mark`` is the only implemented
    policy — the same pin the session plan validator enforces);
  * a **stale market** must not fire a fill: the event stays ``pending`` /
    ``blocked`` with ``reason_code="stale_market"`` and the position remains
    ``open`` — no execution against a quote the system would not trade;
  * **exactly one close fill**: an already-liquidated position (or a replay of
    the same ``event_key`` after ``mark_closed``) evaluates to ``noop``; the
    deterministic ``event_key`` is the idempotency identity the PG layer keys
    postings/fills on (crash/retry proof lives in the P5.2 integration tests);
  * close economics reuse the single domain implementation
  :func:`paper_trading.execution.execute_close` with full quantity — this
  module never re-derives PnL or fees.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

from paper_trading.execution import (
    CloseResult,
    Position,
    execute_close,
)
from paper_trading.market import DEFAULT_SLIPPAGE_BPS, PerpetualSnapshot, is_fresh
from paper_trading.risk import (
    DEFAULT_MAINTENANCE_MARGIN_RATE,
    calculate_liquidation_price,
)

__all__ = [
    "LiquidationOutcome",
    "LiquidationState",
    "PositionLiquidationState",
    "evaluate_liquidation",
    "mark_closed",
]


class LiquidationState(str, enum.Enum):
    """Per-position liquidation lifecycle."""

    open = "open"
    pending = "pending"        # trigger seen, fill not yet durable
    liquidated = "liquidated"  # exactly one close fill recorded


class LiquidationOutcome(str, enum.Enum):
    """Why the evaluation returned what it returned."""

    noop = "noop"                    # nothing to do (not triggered / already closed)
    triggered = "triggered"          # PENDING event created (or already pending)
    closed = "closed"                # fill executed on this call
    blocked_stale_market = "blocked_stale_market"


@dataclass(frozen=True)
class PositionLiquidationState:
    """Current liquidation view of one position.

    ``event_key`` is deterministic — ``liq:{position_id}`` — so a crash and
    retry re-produces the SAME key and the PG layer behind it replays instead
    of double-posting.  ``close`` carries the exactly-one CloseResult once the
    fill has been produced.
    """

    state: LiquidationState
    reason_code: str
    liquidation_price: Decimal | None = None
    mark_price: Decimal | None = None
    event_key: str | None = None
    close: CloseResult | None = None
    evaluated_at: datetime | None = None


def _noop(reason: str, liq_price: Decimal | None, mark: Decimal | None,
          now: datetime, state: LiquidationState = LiquidationState.open,
          close: CloseResult | None = None,
          event_key: str | None = None) -> PositionLiquidationState:
    return PositionLiquidationState(
        state=state, reason_code=reason, liquidation_price=liq_price,
        mark_price=mark, event_key=event_key, close=close, evaluated_at=now,
    )


def _liq_price_for(position: Position, snapshot: PerpetualSnapshot) -> Decimal:
    """Isolated-margin liquidation price shared with the risk engine (ADR-01).

    ``reserved_margin`` is used when the opener recorded it; otherwise the
    leverage-implied fraction applies — one formula, never a re-derivation.
    """
    margin = position.reserved_margin if position.reserved_margin > 0 else None
    notional = position.entry_price * position.quantity * snapshot.contract_size
    return calculate_liquidation_price(
        position.entry_price,
        int(position.leverage),
        position.direction,
        maintenance_margin_rate=DEFAULT_MAINTENANCE_MARGIN_RATE,
        isolated_margin=margin,
        notional=notional,
    )


def evaluate_liquidation(
    position: Position,
    snapshot: PerpetualSnapshot,
    *,
    current_state: PositionLiquidationState | None = None,
    max_market_age_seconds: float = 5.0,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    now: datetime | None = None,
) -> PositionLiquidationState:
    """Advance the liquidation state machine for one position by one step.

    Idempotent: calling it again after ``liquidated`` (or with an externally
    supplied ``current_state``) never produces a second fill.
    """
    now = now or datetime.now(timezone.utc)

    # Already-consumed state short-circuits FIRST: a retry must see noop even
    # if the mark is still beyond the liquidation price.
    if current_state is not None and current_state.state is LiquidationState.liquidated:
        return _noop("already_liquidated", current_state.liquidation_price,
                     current_state.mark_price, now,
                     state=LiquidationState.liquidated,
                     close=current_state.close,
                     event_key=current_state.event_key)
    if position.status == "liquidated":
        return _noop("already_liquidated", None, None, now,
                     state=LiquidationState.liquidated)
    if position.status != "open" or position.quantity <= 0:
        return _noop("position_not_open", None, None, now)

    liq_price = _liq_price_for(position, snapshot)
    mark = snapshot.mark

    triggered = (mark <= liq_price) if position.direction == "long" else (mark >= liq_price)
    if not triggered:
        return _noop("not_triggered", liq_price, mark, now)

    event_key = f"liq:{position.position_id}"

    # Stale market: the trigger is real but unexecutable — the event waits.
    # No fill, no posting, position stays open (MC-16 negative half).
    if not is_fresh(snapshot, max_age=max_market_age_seconds):
        return PositionLiquidationState(
            state=LiquidationState.pending, reason_code="stale_market",
            liquidation_price=liq_price, mark_price=mark, event_key=event_key,
            evaluated_at=now,
        )

    # Forced FULL close through the single domain executor.
    close = execute_close(
        position, snapshot,
        close_quantity=position.quantity,
        exit_reason="liquidation",
        slippage_bps=slippage_bps,
        now=now,
    )
    if not close.closed:
        # defensive: full-quantity close of a positive position cannot fail;
        # if it ever does, stay pending rather than half-account for it.
        return PositionLiquidationState(
            state=LiquidationState.pending, reason_code="close_not_executed",
            liquidation_price=liq_price, mark_price=mark, event_key=event_key,
            evaluated_at=now,
        )
    return PositionLiquidationState(
        state=LiquidationState.liquidated, reason_code="liquidated",
        liquidation_price=liq_price, mark_price=mark, event_key=event_key,
        close=close, evaluated_at=now,
    )


def mark_closed(position: Position, result: PositionLiquidationState) -> Position:
    """Return the position copy stamped as liquidated (domain-layer persistence hook).

    Only valid for a ``liquidated`` result carrying a full close; anything else
    raises — the state machine refuses to record a status without its fill.
    """
    if result.state is not LiquidationState.liquidated or result.close is None:
        raise ValueError("mark_closed requires a liquidated result with a close fill")
    return replace(position, status="liquidated")
