"""Immutable, ledger-derived session/account reports (ТЗ V3 §7, P6.1).

Two hard rules drive this module:

  * **A report is a snapshot, never the live balance.**  ТЗ §7/§83 forbids
    pulling "the account's current balance" for an *old* report — the number
    may have moved after the next day.  Everything here is built from a
    supplied set of *closed trades* plus a *chronological equity series*, so a
    report generated for day *D* is reproducible long after day *D+1* changed
    the live account.
  * **Manual and auto stay independent.**  They are two accounts; their balances
    are never pooled (ТЗ §85).  :class:`SessionReportV1` carries each
    :class:`AccountReportV1` separately and only *compares* them.

Metrics reuse the single deterministic implementation in
:mod:`paper_trading.metrics` (drawdown / profit factor / average R) so the
dashboard, this report and the block-D statistical gate can never disagree.

This layer is **pure** — no DB, no clock, no I/O.  The caller supplies trade
rows and the settlement/reconciliation facts it observed; the module projects
them into a hashable, exportable report.  Persistence (the ``simulation_reports``
table) and the JSON/CSV/HTML exporters build on these structures in P6.2/P6.3.

Null-safety contract (ТЗ §85): a metric that needs a sufficient sample
(win-rate, expectancy, profit factor, ROI) returns ``None`` with the reason
``insufficient_sample`` when there are too few trades, and ``None`` with
``division_by_zero`` when its denominator is zero.  We never emit a fabricated
``0`` where the honest answer is "not enough data".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from paper_trading.metrics import (
    average_r,
    equity_curve,
    max_drawdown,
    profit_factor,
)

__all__ = [
    "MIN_TRADES_DEFAULT",
    "SCHEMA_VERSION",
    "Trade",
    "AccountReportV1",
    "SessionReportV1",
    "build_account_report",
    "build_session_report",
    "canonical_hash",
]

#: Report contract version — pinned into every snapshot so a later reader knows
#: exactly which metric definitions produced the numbers.
SCHEMA_VERSION = 1

#: Below this many closed trades, sample-sensitive metrics are reported as
#: ``None`` / ``insufficient_sample`` rather than a misleading point estimate.
MIN_TRADES_DEFAULT = 10

_ZERO = Decimal(0)


# ---------------------------------------------------------------------------
# small strict helpers
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    """Parse a finite Decimal, or ``None`` for empty/garbage/non-finite input.

    Monetary columns arrive as ``Decimal`` (asyncpg) or numeric strings; floats
    go through ``str`` so a binary-float artefact never leaks into an exact sum.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _q(value: Decimal | None) -> str | None:
    """Canonical Decimal string (fixed notation) for the wire/export, or None."""
    return format(value, "f") if value is not None else None


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    """One *closed* position reduced to what a report needs.

    The caller (repository query) maps a ``paper_v2_positions`` row plus its
    realized ledger amounts into this.  Amounts are already the settled,
    recorded facts — the report never re-derives P&L from prices, it reads the
    numbers the execution/ledger path committed.
    """

    position_id: str
    realized_net: Decimal | None
    realized_gross: Decimal | None = None
    entry_fee: Decimal | None = None
    exit_fee: Decimal | None = None
    funding_cashflow: Decimal | None = None
    is_liquidated: bool = False
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    # Optional planned risk (in quote ccy) so expectancy can be an average R;
    # when absent, expectancy falls back to the average realized net per trade.
    planned_risk: Decimal | None = None
    # Optional entry/exit mark + qty for time-in-market & turnover helpers.
    notional: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "realized_net", _dec(self.realized_net))
        object.__setattr__(self, "realized_gross", _dec(self.realized_gross))
        object.__setattr__(self, "entry_fee", _dec(self.entry_fee))
        object.__setattr__(self, "exit_fee", _dec(self.exit_fee))
        object.__setattr__(self, "funding_cashflow", _dec(self.funding_cashflow))
        object.__setattr__(self, "planned_risk", _dec(self.planned_risk))
        object.__setattr__(self, "notional", _dec(self.notional))


# ---------------------------------------------------------------------------
# account report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountReportV1:
    kind: str  # "manual" | "auto"
    currency: str
    initial_budget: str | None
    final_equity: str | None
    net_pnl: str | None

    gross_traded: str | None
    entry_fees: str | None
    exit_fees: str | None
    total_fees: str | None
    funding_cashflow: str | None
    slippage_included: bool  # whether realized_net already nets slippage

    realized_gross: str | None

    # sample-sensitive metrics — None carries a reason below.
    roi: str | None
    max_drawdown: str | None
    win_rate: str | None
    expectancy: str | None
    profit_factor: float | None

    trades_closed: int
    trades_liquidated: int
    wins: int
    losses: int

    time_in_market_seconds: int | None
    rejected_entries: int

    reasons: dict[str, str] = field(default_factory=dict)  # metric -> null reason

    def to_dict(self) -> dict[str, Any]:
        return dict(
            schema_version=SCHEMA_VERSION,
            kind=self.kind,
            currency=self.currency,
            initial_budget=self.initial_budget,
            final_equity=self.final_equity,
            net_pnl=self.net_pnl,
            gross_traded=self.gross_traded,
            entry_fees=self.entry_fees,
            exit_fees=self.exit_fees,
            total_fees=self.total_fees,
            funding_cashflow=self.funding_cashflow,
            slippage_included=self.slippage_included,
            realized_gross=self.realized_gross,
            roi=self.roi,
            max_drawdown=self.max_drawdown,
            win_rate=self.win_rate,
            expectancy=self.expectancy,
            profit_factor=self.profit_factor,
            trades_closed=self.trades_closed,
            trades_liquidated=self.trades_liquidated,
            wins=self.wins,
            losses=self.losses,
            time_in_market_seconds=self.time_in_market_seconds,
            rejected_entries=self.rejected_entries,
            reasons=dict(self.reasons),
        )


def build_account_report(
    kind: str,
    trades: Sequence[Trade],
    *,
    initial_budget: Any = None,
    currency: str = "USDT",
    slippage_included: bool = True,
    rejected_entries: int = 0,
    min_trades: int = MIN_TRADES_DEFAULT,
) -> AccountReportV1:
    """Project a closed-trade list for ONE account into a report snapshot.

    ``trades`` must be in chronological order (oldest close first) so the equity
    curve, drawdown and time-in-market are the *realised* series, not a
    newest-first artefact (the mistake ``metrics.py`` documents).  Missing
    sample-dependent numbers are returned as ``None`` with a reason.
    """
    budget = _dec(initial_budget)

    nets = [t.realized_net for t in trades if t.realized_net is not None]
    grosses = [t.realized_gross for t in trades if t.realized_gross is not None]
    entry_fees = [t.entry_fee for t in trades if t.entry_fee is not None]
    exit_fees = [t.exit_fee for t in trades if t.exit_fee is not None]
    fundings = [t.funding_cashflow for t in trades if t.funding_cashflow is not None]
    notionals = [t.notional for t in trades if t.notional is not None]

    net_pnl = sum(nets, _ZERO) if nets else None
    realized_gross = sum(grosses, _ZERO) if grosses else None
    fees_in = sum(entry_fees, _ZERO) if entry_fees else _ZERO
    fees_out = sum(exit_fees, _ZERO) if exit_fees else _ZERO
    total_fees = fees_in + fees_out
    funding_sum = sum(fundings, _ZERO) if fundings else None
    gross_traded = sum(notionals, _ZERO) if notionals else None

    final_equity = (budget + net_pnl) if (budget is not None and net_pnl is not None) else None

    n = len(trades)
    wins = sum(1 for t in trades if t.realized_net is not None and t.realized_net > _ZERO)
    losses = sum(1 for t in trades if t.realized_net is not None and t.realized_net < _ZERO)
    liquidated = sum(1 for t in trades if t.is_liquidated)

    reasons: dict[str, str] = {}

    # ROI = net / initial budget.  Zero/absent budget is an honest null, not 0.
    roi_val: Decimal | None = None
    if net_pnl is None or budget is None or budget == _ZERO:
        reasons["roi"] = "division_by_zero" if (budget == _ZERO) else "insufficient_data"
    else:
        roi_val = net_pnl / budget

    # Drawdown needs >= 2 equity points (initial + at least one closed trade).
    dd_val: Decimal | None = None
    if nets and len(nets) + 1 >= 2:
        start = budget if budget is not None else _ZERO
        curve = equity_curve(nets, starting_equity=start)
        dd_val = _dec(max_drawdown(curve))
    else:
        reasons["max_drawdown"] = "insufficient_sample"

    if n < min_trades:
        reasons["win_rate"] = "insufficient_sample"
        reasons["expectancy"] = "insufficient_sample"
        reasons["profit_factor"] = "insufficient_sample"
        win_rate_val: Decimal | None = None
        expectancy_val: Decimal | None = None
        pf_val: float | None = None
    else:
        denom = wins + losses
        win_rate_val = (Decimal(wins) / Decimal(denom)) if denom else None
        if win_rate_val is None:
            reasons["win_rate"] = "division_by_zero"
        # expectancy: average R when planned risk present, else average net/trade
        r_multiples = [
            float(t.realized_net / t.planned_risk)
            for t in trades
            if t.realized_net is not None
            and t.planned_risk is not None
            and t.planned_risk != _ZERO
        ]
        if len(r_multiples) >= min_trades:
            expectancy_val = Decimal(str(average_r(r_multiples)))
        elif nets:
            expectancy_val = sum(nets, _ZERO) / Decimal(len(nets))
        else:
            expectancy_val = None
            reasons["expectancy"] = "insufficient_sample"
        pf_val = profit_factor(nets) if nets else None
        if pf_val is None:
            reasons["profit_factor"] = "insufficient_sample"

    # time in market: sum of (closed_at - opened_at) over complete pairs
    tis: int | None = None
    total_seconds = 0
    any_pair = False
    for t in trades:
        if t.opened_at is not None and t.closed_at is not None:
            any_pair = True
            delta = t.closed_at - t.opened_at
            total_seconds += int(delta.total_seconds())
    if any_pair:
        tis = total_seconds

    return AccountReportV1(
        kind=str(kind),
        currency=currency,
        initial_budget=_q(budget),
        final_equity=_q(final_equity),
        net_pnl=_q(net_pnl),
        gross_traded=_q(gross_traded),
        entry_fees=_q(fees_in) if entry_fees else None,
        exit_fees=_q(fees_out) if exit_fees else None,
        total_fees=_q(total_fees),
        funding_cashflow=_q(funding_sum),
        slippage_included=bool(slippage_included),
        realized_gross=_q(realized_gross),
        roi=_q(roi_val),
        max_drawdown=_q(dd_val),
        win_rate=_q(win_rate_val),
        expectancy=_q(expectancy_val),
        profit_factor=pf_val,
        trades_closed=n,
        trades_liquidated=liquidated,
        wins=wins,
        losses=losses,
        time_in_market_seconds=tis,
        rejected_entries=int(rejected_entries),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# session report (manual + auto, never pooled)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionReportV1:
    schema_version: int
    session_id: str
    day_id: str
    instrument_key: str
    as_of: str
    settlement_complete: bool
    reconciliation_ok: bool
    # final = a PASS report only when settled AND reconciled AND nothing left open
    is_final: bool
    not_final_reason: str | None
    market_quality: str
    source_status: str
    strategy_version: str | None
    fee_model_version: str | None
    slippage_model_version: str | None
    funding_model_version: str | None
    plan_hash: str | None
    replay_data_hash: str | None
    open_positions: int
    reconciliation_detail: dict[str, Any]
    accounts: dict[str, AccountReportV1]

    def to_dict(self) -> dict[str, Any]:
        return dict(
            schema_version=self.schema_version,
            session_id=self.session_id,
            day_id=self.day_id,
            instrument_key=self.instrument_key,
            as_of=self.as_of,
            settlement_complete=self.settlement_complete,
            reconciliation_ok=self.reconciliation_ok,
            is_final=self.is_final,
            not_final_reason=self.not_final_reason,
            market_quality=self.market_quality,
            source_status=self.source_status,
            strategy_version=self.strategy_version,
            fee_model_version=self.fee_model_version,
            slippage_model_version=self.slippage_model_version,
            funding_model_version=self.funding_model_version,
            plan_hash=self.plan_hash,
            replay_data_hash=self.replay_data_hash,
            open_positions=self.open_positions,
            reconciliation_detail=dict(self.reconciliation_detail),
            accounts={k: v.to_dict() for k, v in self.accounts.items()},
        )


def build_session_report(
    *,
    session_id: str,
    day_id: str,
    instrument_key: str,
    as_of: datetime,
    accounts: dict[str, AccountReportV1],
    settlement_complete: bool,
    reconciliation_ok: bool,
    open_positions: int = 0,
    market_quality: str = "unknown",
    source_status: str = "unknown",
    strategy_version: str | None = None,
    fee_model_version: str | None = None,
    slippage_model_version: str | None = None,
    funding_model_version: str | None = None,
    plan_hash: str | None = None,
    replay_data_hash: str | None = None,
    reconciliation_detail: dict[str, Any] | None = None,
) -> SessionReportV1:
    """Assemble the per-account reports into a session snapshot.

    A report is **final** (issuable as PASS) only when settlement is complete,
    reconciliation is clean, and no position is still open (ТЗ §83: an unsettled
    session must not publish a final PASS).  Otherwise it carries an explicit
    ``not_final_reason`` so the UI/exports can show *why* it is provisional.
    """
    reason: str | None = None
    if not settlement_complete:
        reason = "settlement_pending"
    elif not reconciliation_ok:
        reason = "reconciliation_failed"
    elif open_positions > 0:
        reason = "open_positions_remain"
    is_final = reason is None

    as_of_iso = as_of.isoformat() if isinstance(as_of, datetime) else str(as_of)
    return SessionReportV1(
        schema_version=SCHEMA_VERSION,
        session_id=str(session_id),
        day_id=str(day_id),
        instrument_key=str(instrument_key),
        as_of=as_of_iso,
        settlement_complete=bool(settlement_complete),
        reconciliation_ok=bool(reconciliation_ok),
        is_final=is_final,
        not_final_reason=reason,
        market_quality=str(market_quality),
        source_status=str(source_status),
        strategy_version=strategy_version,
        fee_model_version=fee_model_version,
        slippage_model_version=slippage_model_version,
        funding_model_version=funding_model_version,
        plan_hash=plan_hash,
        replay_data_hash=replay_data_hash,
        open_positions=int(open_positions),
        reconciliation_detail=dict(reconciliation_detail or {}),
        accounts=dict(accounts),
    )


# ---------------------------------------------------------------------------
# canonical hash (immutability anchor for P6.2)
# ---------------------------------------------------------------------------


def canonical_hash(report: SessionReportV1 | AccountReportV1 | dict[str, Any]) -> str:
    """Deterministic sha256 over the report's canonical JSON.

    Key-sorted, separator-stripped, Decimals already reduced to canonical
    strings, so two structurally identical reports hash identically and any
    byte-level change flips the digest.  P6.2 stores this on the
    ``simulation_reports`` row so an old report can be proven unchanged even
    after a new session writes to the same owner's accounts (ТЗ §85 / MC-19).
    """
    payload = report.to_dict() if isinstance(report, (SessionReportV1, AccountReportV1)) else report
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
