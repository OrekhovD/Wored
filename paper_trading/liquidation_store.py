"""paper_trading.liquidation_store — PG persistence half of the liquidation chain.

:mod:`paper_trading.liquidation` is the pure state machine: it decides whether a
mark crossing the isolated-margin liquidation price produces exactly one forced
full close.  This module is the thin durable layer the ТЗ §2 gap asked for —
it takes a *persisted* ``contracts.Position``, evaluates the machine against the
current snapshot, and writes the outcome back through the existing domain
primitives:

  * ``evaluate_for_position`` — translate, evaluate, report (adds the
    ``liq:{position_id}`` idempotency key the ledger is stamped with);
  * ``build_liquidation_postings`` — one signed-cashflow event keyed on that
    deterministic ``event_key`` with ``source_type=liquidation``;
  * ``persist_liquidation`` — commit fill + ``status='liquidated'`` + postings
    in the single ``commit_position_close`` transaction.

Nothing here re-derives PnL, fees or the liquidation price: economics come from
:func:`paper_trading.execution.execute_close` via the state machine, the price
from :mod:`paper_trading.risk` (ADR-01), and the accounting from
:mod:`paper_trading.ledger`.  Exactly-once is structural — the domain refuses a
second commit while the position row is not ``open``, and
``UNIQUE(source_type, source_ref, bucket)`` refuses a second posting of the same
``event_key``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from paper_trading.contracts import (
    Fill,
    JournalBucket,
    JournalPosting,
    JournalSourceType,
    OrderSide,
    Position,
    PositionStatus,
    money,
)
from paper_trading.ledger import make_posting
from paper_trading.liquidation import (
    LiquidationOutcome,
    LiquidationState,
    PositionLiquidationState,
    evaluate_liquidation,
)
from paper_trading.market import DEFAULT_SLIPPAGE_BPS, PerpetualSnapshot
from paper_trading.execution import Position as ExecutionPosition

__all__ = [
    "LiquidationDecision",
    "build_liquidation_postings",
    "evaluate_for_position",
    "persist_liquidation",
    "to_execution_position",
]

#: Leverage is not stored on the position row; the isolated margin plus the
#: entry notional fully determine the liquidation price, so the recorded
#: ``isolated_margin`` is authoritative and leverage is only the fallback
#: divisor when no margin was reserved.
DEFAULT_LEVERAGE = 10


def to_execution_position(
    pos: Position, *, leverage: int = DEFAULT_LEVERAGE
) -> ExecutionPosition:
    """Translate the persisted position contract into execution input.

    Same mapping the runner uses for its closes — one shape, never two.
    """
    return ExecutionPosition(
        position_id=str(pos.position_id),
        account_id=str(pos.account_id),
        instrument=pos.instrument,
        direction=pos.side.value,
        entry_price=pos.avg_entry_price,
        quantity=pos.qty,
        leverage=leverage,
        stop_price=pos.stop_loss or Decimal(0),
        take_profit=pos.take_profit,
        reserved_margin=pos.isolated_margin,
        entry_fee=pos.entry_fee,
        opened_at=pos.opened_at.isoformat() if pos.opened_at else "",
        status=pos.status.value,
    )


@dataclass(frozen=True)
class LiquidationDecision:
    """Evaluation of one persisted position, plus the ids needed to persist it."""

    position: Position
    state: PositionLiquidationState
    fill_id: UUID | None = None
    event_id: UUID | None = None

    @property
    def outcome(self) -> LiquidationOutcome:
        return _OUTCOME_BY_REASON.get(self.state.reason_code, LiquidationOutcome.noop)

    @property
    def should_persist(self) -> bool:
        """True only when the close fill is durable and not yet recorded."""
        return (
            self.state.state is LiquidationState.liquidated
            and self.state.close is not None
            and self.position.status is not PositionStatus.liquidated
        )


_OUTCOME_BY_REASON: dict[str, LiquidationOutcome] = {
    "liquidated": LiquidationOutcome.closed,
    "stale_market": LiquidationOutcome.blocked_stale_market,
    "close_not_executed": LiquidationOutcome.triggered,
}


def evaluate_for_position(
    position: Position,
    snapshot: PerpetualSnapshot,
    *,
    leverage: int = DEFAULT_LEVERAGE,
    max_market_age_seconds: float = 5.0,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    now: datetime | None = None,
) -> LiquidationDecision:
    """Run the pure state machine against a persisted position.

    A position already stamped ``liquidated`` short-circuits to ``noop`` without
    consulting the mark, so a retry after a successful commit can never produce
    a second fill (MC-16 crash/retry half).
    """
    state = evaluate_liquidation(
        to_execution_position(position, leverage=leverage),
        snapshot,
        max_market_age_seconds=max_market_age_seconds,
        slippage_bps=slippage_bps,
        now=now,
    )
    if state.state is not LiquidationState.liquidated or state.close is None:
        return LiquidationDecision(position=position, state=state)
    return LiquidationDecision(
        position=position,
        state=state,
        fill_id=uuid4(),
        event_id=uuid4(),
    )


def build_liquidation_postings(
    decision: LiquidationDecision,
    *,
    currency: str = "USDT",
) -> list[JournalPosting]:
    """Signed cashflows for one forced exit, stamped with its ``event_key``.

    ``source_ref`` is the deterministic ``liq:{position_id}`` key rather than the
    random fill id: the DB uniqueness on ``(source_type, source_ref, bucket)``
    then *is* the liquidation idempotency guard, so a replayed commit raises
    instead of double-counting the loss.
    """
    close = decision.state.close
    position = decision.position
    if close is None or decision.event_id is None or decision.fill_id is None:
        raise ValueError("liquidation postings require a closed liquidation result")
    event_key = decision.state.event_key or f"liq:{position.position_id}"
    occurred = decision.state.evaluated_at
    common = {
        "account_id": position.account_id,
        "source_type": JournalSourceType.liquidation,
        "source_ref": event_key,
        "day_id": position.day_id,
        "currency": currency,
        "occurred_at": occurred,
        "event_id": decision.event_id,
    }
    postings = [
        make_posting(
            bucket=JournalBucket.exit_fee,
            amount=-money(close.close_fee),
            **common,
        ),
        make_posting(
            bucket=JournalBucket.realized_gross_pnl,
            amount=money(close.gross_pnl),
            **common,
        ),
    ]
    if position.isolated_margin > 0:
        postings.insert(
            0,
            make_posting(
                bucket=JournalBucket.released_margin,
                amount=money(position.isolated_margin),
                **common,
            ),
        )
    return postings


async def persist_liquidation(
    repository,
    decision: LiquidationDecision,
    *,
    currency: str = "USDT",
) -> bool:
    """Commit exactly one forced close: fill + ``liquidated`` + ledger.

    Returns ``False`` when there is nothing durable to write (not triggered, or
    blocked on a stale market) — the caller leaves the position ``open``.  A
    second call for the same position is refused by the domain
    (``position is not open``) before any row is touched.
    """
    if not decision.should_persist:
        return False
    close = decision.state.close
    position = decision.position
    if close is None or decision.fill_id is None:
        raise ValueError("persist_liquidation requires a liquidated close result")
    fill = Fill(
        fill_id=decision.fill_id,
        order_id=position.position_id,
        account_id=position.account_id,
        execution_quote_id=decision.state.event_key or f"liq:{position.position_id}",
        instrument=position.instrument,
        side=OrderSide.sell if position.side.value == "long" else OrderSide.buy,
        price=money(close.close_price),
        qty=money(close.closed_quantity),
        fee=money(close.close_fee),
        is_close=True,
        source_timestamp=decision.state.evaluated_at,
        receive_timestamp=decision.state.evaluated_at,
        execute_timestamp=decision.state.evaluated_at,
    )
    await repository.commit_position_close(
        position_id=position.position_id,
        fill=fill,
        close_price=money(close.close_price),
        realized_gross_pnl=money(close.gross_pnl),
        realized_net_pnl=money(close.realized_net),
        exit_fee=money(close.close_fee),
        postings=build_liquidation_postings(decision, currency=currency),
        status=PositionStatus.liquidated,
    )
    return True
