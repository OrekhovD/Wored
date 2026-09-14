"""
paper_trading.ledger — double-entry-ish append-only ledger and P&L math.

All monetary values are ``decimal.Decimal``.  In PostgreSQL money is
``NUMERIC(20,8)``; in JSON it is serialised as decimal strings.

Key formulas (REQ-06):

    gross = direction × qty × (exit − entry)
    net   = gross − entry_fee − exit_fee + funding_cashflow
    equity = cash + unrealized

On partial exit, entry fee and position cost are allocated proportionally
to the closed quantity, preserving the residual.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID, uuid4

from paper_trading.contracts import (
    JournalBucket,
    JournalPosting,
    JournalSourceType,
    Position,
    PositionSide,
    ZERO,
    money,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Direction multiplier
# ---------------------------------------------------------------------------

#: Long → +1, Short → −1.  Used in gross P&L: gross = dir * qty * (exit-entry).
_DIR_MULT: Dict[PositionSide, int] = {
    PositionSide.long: 1,
    PositionSide.short: -1,
}


def direction_multiplier(side: PositionSide) -> int:
    """Return +1 for long, −1 for short."""
    return _DIR_MULT[side]


# ---------------------------------------------------------------------------
# Core P&L calculations
# ---------------------------------------------------------------------------


def calculate_gross_pnl(
    side: PositionSide,
    qty: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """Gross realized P&L: ``direction × qty × (exit − entry)``.

    Long:  +(exit - entry) * qty  → profit when exit > entry.
    Short: -(exit - entry) * qty  → profit when exit < entry.
    """
    if qty <= 0:
        return ZERO
    raw = direction_multiplier(side) * qty * (exit_price - entry_price)
    return money(raw)


def calculate_net_pnl(
    side: PositionSide,
    qty: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
    funding_cashflow: Decimal = ZERO,
) -> Decimal:
    """Net realized P&L: ``gross − entry_fee − exit_fee + funding_cashflow``.

    ``funding_cashflow`` is signed: positive rate → long pays (negative),
    short receives (positive).  The caller is responsible for the sign;
    this function adds it directly.
    """
    gross = calculate_gross_pnl(side, qty, entry_price, exit_price)
    net = gross - entry_fee - exit_fee + funding_cashflow
    return money(net)


# ---------------------------------------------------------------------------
# Partial-exit allocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartialExitAllocation:
    """Result of allocating costs/PnL for a partial exit.

    The *closed* portion gets its proportional share of entry fee and
    position cost; the *remaining* portion retains the residual so the
    open position's economics stay exact.
    """

    closed_qty: Decimal
    remaining_qty: Decimal
    closed_entry_fee_share: Decimal
    remaining_entry_fee_share: Decimal
    closed_cost_basis: Decimal
    remaining_cost_basis: Decimal
    closed_gross_pnl: Decimal
    closed_net_pnl: Decimal


def allocate_partial_exit(
    side: PositionSide,
    total_qty: Decimal,
    close_qty: Decimal,
    avg_entry_price: Decimal,
    exit_price: Decimal,
    total_entry_fee: Decimal,
    exit_fee: Decimal,
    funding_cashflow: Decimal = ZERO,
) -> PartialExitAllocation:
    """Allocate entry fee and cost proportionally for a partial close.

    Rounding: division uses full Decimal precision; final values are
    quantised to 8 dp.  Residual adjustments are applied to the *remaining*
    portion so the closed portion's PnL is exact.

    Raises ``ValueError`` if ``close_qty`` > ``total_qty`` or either is ≤ 0.
    """
    if close_qty <= 0:
        raise ValueError(f"close_qty must be positive, got {close_qty}")
    if total_qty <= 0:
        raise ValueError(f"total_qty must be positive, got {total_qty}")
    if close_qty > total_qty:
        raise ValueError(
            f"close_qty {close_qty} exceeds total_qty {total_qty}"
        )

    # Proportion closed (full precision, quantise results).
    frac = close_qty / total_qty
    remaining_frac = Decimal("1") - frac

    closed_entry_fee = money(total_entry_fee * frac)
    remaining_entry_fee = money(total_entry_fee * remaining_frac)
    # Residual: ensure closed + remaining == total (exact).
    fee_residual = total_entry_fee - closed_entry_fee - remaining_entry_fee
    remaining_entry_fee = money(remaining_entry_fee + fee_residual)

    closed_cost = money(avg_entry_price * close_qty)
    remaining_cost = money(avg_entry_price * (total_qty - close_qty))
    cost_residual = (
        money(avg_entry_price * total_qty) - closed_cost - remaining_cost
    )
    remaining_cost = money(remaining_cost + cost_residual)

    closed_gross = calculate_gross_pnl(side, close_qty, avg_entry_price, exit_price)
    # Net for the closed portion: gross - its entry_fee share - exit_fee + funding share
    closed_funding = money(funding_cashflow * frac)
    closed_net = money(
        closed_gross - closed_entry_fee - exit_fee + closed_funding
    )

    return PartialExitAllocation(
        closed_qty=money(close_qty),
        remaining_qty=money(total_qty - close_qty),
        closed_entry_fee_share=closed_entry_fee,
        remaining_entry_fee_share=remaining_entry_fee,
        closed_cost_basis=closed_cost,
        remaining_cost_basis=remaining_cost,
        closed_gross_pnl=closed_gross,
        closed_net_pnl=closed_net,
    )


# ---------------------------------------------------------------------------
# Equity
# ---------------------------------------------------------------------------


def calculate_equity(cash: Decimal, unrealized: Decimal) -> Decimal:
    """``equity = cash + unrealized``.

    Margin is a reserve, not a repeated expense — it's already part of cash
    or reserved separately.
    """
    return money(cash + unrealized)


# ---------------------------------------------------------------------------
# Journal posting helpers
# ---------------------------------------------------------------------------


def make_posting(
    account_id: UUID,
    bucket: JournalBucket,
    amount: Decimal,
    source_type: JournalSourceType = JournalSourceType.fill,
    source_ref: Optional[str] = None,
    day_id: Optional[UUID] = None,
    currency: str = "USDT",
    occurred_at: Optional[object] = None,
) -> JournalPosting:
    """Build a ``JournalPosting`` with a fresh ``event_id``."""
    return JournalPosting(
        posting_id=uuid4(),
        account_id=account_id,
        day_id=day_id,
        event_id=uuid4(),
        source_type=source_type,
        source_ref=source_ref,
        currency=currency,
        bucket=bucket,
        amount=money(amount),
        occurred_at=occurred_at if isinstance(occurred_at, object) else None,  # type: ignore[arg-type]
        created_at=None,
        schema_version=2,
    )


def post_entry(
    account_id: UUID,
    postings: Sequence[JournalPosting],
) -> List[JournalPosting]:
    """Validate and return a balanced journal entry (list of postings).

    This is the domain-level entry point.  The repository persists them
    inside a PostgreSQL transaction with ``SELECT ... FOR UPDATE`` on the
    account row, enforcing append-only and source uniqueness.

    Validation rules:
      - At least one posting.
      - Every posting has the same ``account_id``.
      - Sum of signed amounts must be zero (double-entry balance).

    Raises ``ValueError`` on violation.
    """
    if not postings:
        raise ValueError("A journal entry requires at least one posting")

    acct = postings[0].account_id
    total = ZERO
    for p in postings:
        if p.account_id != acct:
            raise ValueError(
                f"All postings must share account_id; got {p.account_id} "
                f"vs {acct}"
            )
        total += p.amount

    if total != ZERO:
        raise ValueError(
            f"Journal entry not balanced: sum={total} (must be 0)"
        )

    return list(postings)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationResult:
    """Result of reconciling the journal against position/account state."""

    account_id: UUID
    balanced: bool
    expected_cash: Decimal = field(default_factory=lambda: ZERO)
    actual_cash: Decimal = field(default_factory=lambda: ZERO)
    expected_realized_pnl: Decimal = field(default_factory=lambda: ZERO)
    actual_realized_pnl: Decimal = field(default_factory=lambda: ZERO)
    mismatches: List[str] = field(default_factory=list)

    @property
    def cash_diff(self) -> Decimal:
        return money(self.actual_cash - self.expected_cash)

    @property
    def pnl_diff(self) -> Decimal:
        return money(self.actual_realized_pnl - self.expected_realized_pnl)


def reconcile(
    account_id: UUID,
    journal_postings: Sequence[JournalPosting],
    expected_cash: Decimal,
    expected_realized_pnl: Decimal,
    actual_cash: Decimal,
    actual_realized_pnl: Decimal,
) -> ReconciliationResult:
    """Reconcile journal totals against account/position projections.

    Sums the journal by bucket to derive expected cash and realized PnL,
    then compares against actual values reported by the repository.

    Returns a ``ReconciliationResult`` with ``balanced=True`` only if both
    cash and PnL match exactly (Decimal equality, no tolerance).
    """
    expected_cash_calc = ZERO
    expected_pnl_calc = ZERO

    for p in journal_postings:
        if p.account_id != account_id:
            continue
        if p.bucket == JournalBucket.cash:
            expected_cash_calc += p.amount
        elif p.bucket == JournalBucket.deposit:
            expected_cash_calc += p.amount
        elif p.bucket == JournalBucket.withdrawal:
            expected_cash_calc += p.amount
        elif p.bucket == JournalBucket.realized_gross_pnl:
            expected_pnl_calc += p.amount
        elif p.bucket == JournalBucket.realized_net_pnl:
            expected_pnl_calc += p.amount

    mismatches: List[str] = []

    cash_expected = money(expected_cash_calc + expected_cash)
    if cash_expected != money(actual_cash):
        mismatches.append(
            f"cash: expected {cash_expected} actual {money(actual_cash)} "
            f"diff {money(actual_cash - cash_expected)}"
        )

    pnl_expected = money(expected_pnl_calc + expected_realized_pnl)
    if pnl_expected != money(actual_realized_pnl):
        mismatches.append(
            f"realized_pnl: expected {pnl_expected} actual "
            f"{money(actual_realized_pnl)} diff "
            f"{money(actual_realized_pnl - pnl_expected)}"
        )

    return ReconciliationResult(
        account_id=account_id,
        balanced=len(mismatches) == 0,
        expected_cash=cash_expected,
        actual_cash=money(actual_cash),
        expected_realized_pnl=pnl_expected,
        actual_realized_pnl=money(actual_realized_pnl),
        mismatches=mismatches,
    )


# ---------------------------------------------------------------------------
# Account equity / balance queries (domain-level, data supplied by repo)
# ---------------------------------------------------------------------------


def get_account_balance(
    journal_postings: Sequence[JournalPosting],
    account_id: UUID,
) -> Decimal:
    """Compute the current cash balance from journal postings.

    Sums cash + deposit - withdrawal + adjustments.  Margin reservations
    are tracked separately (reserved_margin / released_margin).
    """
    balance = ZERO
    for p in journal_postings:
        if p.account_id != account_id:
            continue
        if p.bucket in (
            JournalBucket.cash,
            JournalBucket.deposit,
            JournalBucket.withdrawal,
            JournalBucket.adjustment,
        ):
            balance += p.amount
    return money(balance)


def get_account_equity(
    journal_postings: Sequence[JournalPosting],
    account_id: UUID,
    unrealized_pnl: Decimal,
) -> Decimal:
    """Compute equity = cash balance + unrealized PnL."""
    cash = get_account_balance(journal_postings, account_id)
    return calculate_equity(cash, unrealized_pnl)


# ---------------------------------------------------------------------------
# Convenience: build a standard fill posting set
# ---------------------------------------------------------------------------


def build_fill_postings(
    account_id: UUID,
    fill_price: Decimal,
    fill_qty: Decimal,
    fee: Decimal,
    is_close: bool,
    side: PositionSide,
    day_id: Optional[UUID] = None,
    source_ref: Optional[str] = None,
    realized_gross: Decimal = ZERO,
    realized_net: Decimal = ZERO,
) -> List[JournalPosting]:
    """Build the standard set of journal postings for a fill.

    For an **opening** fill:
      - cash: −(price × qty)  [buy] or +(price × qty) [sell short]
      - entry_fee: −fee

    For a **closing** fill:
      - cash: +(price × qty)  [closing long] or −(price × qty) [closing short]
      - exit_fee: −fee
      - realized_gross_pnl: gross
      - realized_net_pnl: net (if non-zero, to avoid duplicate with gross)

    The postings balance to zero when unrealized PnL is realised on close.
    For openings, the cash outflow is balanced by the reserved_margin
    release/reduction handled separately by execution.py.
    """
    postings: List[JournalPosting] = []
    notional = money(fill_price * fill_qty)

    if is_close:
        # Closing: cash flows back, PnL realised.
        if side == PositionSide.long:
            # Long close: receive cash
            cash_flow = notional
        else:
            # Short close: pay to buy back
            cash_flow = -notional
        postings.append(
            make_posting(
                account_id=account_id,
                bucket=JournalBucket.cash,
                amount=cash_flow,
                source_type=JournalSourceType.fill,
                source_ref=source_ref,
                day_id=day_id,
            )
        )
        postings.append(
            make_posting(
                account_id=account_id,
                bucket=JournalBucket.exit_fee,
                amount=-fee,
                source_type=JournalSourceType.fill,
                source_ref=source_ref,
                day_id=day_id,
            )
        )
        if realized_gross != ZERO:
            postings.append(
                make_posting(
                    account_id=account_id,
                    bucket=JournalBucket.realized_gross_pnl,
                    amount=realized_gross,
                    source_type=JournalSourceType.fill,
                    source_ref=source_ref,
                    day_id=day_id,
                )
            )
        if realized_net != ZERO:
            postings.append(
                make_posting(
                    account_id=account_id,
                    bucket=JournalBucket.realized_net_pnl,
                    amount=realized_net,
                    source_type=JournalSourceType.fill,
                    source_ref=source_ref,
                    day_id=day_id,
                )
            )
    else:
        # Opening: cash flows out (margin reservation), fee charged.
        if side == PositionSide.long:
            cash_flow = -notional
        else:
            cash_flow = notional
        postings.append(
            make_posting(
                account_id=account_id,
                bucket=JournalBucket.reserved_margin,
                amount=cash_flow,
                source_type=JournalSourceType.fill,
                source_ref=source_ref,
                day_id=day_id,
            )
        )
        postings.append(
            make_posting(
                account_id=account_id,
                bucket=JournalBucket.entry_fee,
                amount=-fee,
                source_type=JournalSourceType.fill,
                source_ref=source_ref,
                day_id=day_id,
            )
        )

    return postings