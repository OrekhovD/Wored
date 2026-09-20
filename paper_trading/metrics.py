"""Deterministic trading-performance metrics (Self-Learn block C, D8).

The live evaluation path (``POST /api/strategy/evaluate``) used to compute a
*pseudo*-drawdown by walking ``sim_positions`` in ``closed_at DESC`` order and
accumulating PnL — the newest trade first — which is not the realised drawdown
of the account.  This module owns the correct definitions so both the webui and
the block-D statistical gate consume one implementation:

* :func:`max_drawdown` — peak-to-trough of the **chronological** equity curve,
  exact :class:`~decimal.Decimal` arithmetic (testable to 1e-8).
* :func:`sharpe` / :func:`sortino` — annualised, on *daily* returns.
* :func:`profit_factor`, :func:`average_r`, :func:`liquidation_share`,
  :func:`fee_turnover` — trade-level aggregates.

Pure functions: no I/O, no clock. Monetary inputs may be ``Decimal`` or ``float``;
``max_drawdown`` preserves the input type, the risk-adjusted ratios return floats
because they need a square root.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Iterable, List, Sequence

__all__ = [
    "equity_curve",
    "max_drawdown",
    "daily_returns",
    "sharpe",
    "sortino",
    "profit_factor",
    "average_r",
    "liquidation_share",
    "fee_turnover",
    "risk_adjusted_net_pnl",
]


def equity_curve(net_pnls: Iterable, starting_equity=Decimal(0)):
    """Cumulative equity from an ordered iterable of per-trade net PnLs.

    ``net_pnls`` MUST be in chronological order (oldest first).  The result is
    ``[starting_equity, starting_equity + pnl_0, ...]`` — one point more than the
    number of PnLs, so the first peak is the account's initial equity.  Decimal
    in → Decimal out; float in → float out.
    """
    as_decimal = isinstance(starting_equity, Decimal)
    running = starting_equity
    points = [running]
    for pnl in net_pnls:
        term = Decimal(str(pnl)) if as_decimal and not isinstance(pnl, Decimal) else pnl
        running = running + term
        points.append(running)
    return points


def max_drawdown(equity: Sequence) -> Decimal | float:
    """Largest peak-to-trough decline of a chronological equity curve.

    ``equity`` is the running account equity (or cumulative PnL), oldest first.
    Returns a non-negative number of the same numeric family as the input
    (``Decimal`` stays ``Decimal``).  A monotonically rising curve gives ``0``.
    """
    if not equity:
        return Decimal(0)
    is_dec = isinstance(equity[0], Decimal)
    zero = Decimal(0) if is_dec else 0.0
    peak = equity[0]
    worst = zero
    for value in equity:
        if value > peak:
            peak = value
        dd = peak - value
        if dd > worst:
            worst = dd
    return worst


def daily_returns(equity: Sequence) -> List[float]:
    """Simple period returns ``e[t]/e[t-1] - 1`` from a chronological curve."""
    out: List[float] = []
    for prev, cur in zip(equity, equity[1:]):
        prev_f = float(prev)
        if prev_f == 0.0:
            continue
        out.append(float(cur) / prev_f - 1.0)
    return out


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def sharpe(returns: Sequence[float], periods_per_year: int = 365) -> float:
    """Annualised Sharpe ratio of a return series (excess over zero)."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    sd = _stdev(returns)
    if sd == 0.0:
        return 0.0
    return (mean / sd) * math.sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: int = 365, mar: float = 0.0) -> float:
    """Annualised Sortino ratio — downside deviation in the denominator."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside = [min(r - mar, 0.0) for r in returns]
    dd = math.sqrt(sum(d * d for d in downside) / (len(returns) - 1))
    if dd == 0.0:
        return 0.0
    return (mean / dd) * math.sqrt(periods_per_year)


def profit_factor(net_pnls: Iterable) -> float:
    """Gross profit / gross loss.  ``inf`` when there are only wins, ``0`` empty."""
    gross_win = 0.0
    gross_loss = 0.0
    for pnl in net_pnls:
        p = float(pnl)
        if p >= 0:
            gross_win += p
        else:
            gross_loss -= p
    if gross_loss == 0.0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def average_r(r_multiples: Iterable[float]) -> float:
    """Mean R-multiple per trade (0.0 when empty)."""
    rs = list(r_multiples)
    return (sum(rs) / len(rs)) if rs else 0.0


def liquidation_share(liquidated_flags: Iterable[bool]) -> float:
    """Fraction of closed trades that ended in liquidation."""
    flags = list(liquidated_flags)
    return (sum(1 for f in flags if f) / len(flags)) if flags else 0.0


def fee_turnover(fees: Iterable) -> Decimal | float:
    """Total fee drag: sum of absolute fees paid across the episode set.

    Decimal when every input is Decimal, otherwise float (floats are the norm
    coming out of ``sim_positions``)."""
    values = list(fees)
    if values and all(isinstance(f, Decimal) for f in values):
        return sum((abs(f) for f in values), Decimal(0))
    return sum(abs(float(f)) for f in values)


def risk_adjusted_net_pnl(net_pnl, max_dd, min_sample: int = 30, sample: int = 0):
    """Net PnL haircut by realised drawdown — the block-C target function.

    Replaces the arbitrary weighted ``accuracy_score`` (D9) as the acceptance
    objective: a strategy is judged on *risk-adjusted* net PnL, i.e. realised
    profit penalised by the worst equity drawdown it produced.  Below
    ``min_sample`` episodes the adjustment is reported but the caller should
    treat the result as low-confidence (block D enforces the sample floor).
    """
    adj = float(net_pnl) - float(max_dd)
    return adj
