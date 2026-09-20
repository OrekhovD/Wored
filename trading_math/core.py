"""Deterministic isolated-margin trading math — the single source of truth.

This package is the reference implementation (ADR-01) for the order/liquidation/
settlement maths shared by the chatbot simulator (:mod:`chatbot.services.sim_math`)
and the paper-trading risk engine (:mod:`paper_trading.risk`).  Before it existed
the two modules carried divergent liquidation formulas (an additive approximation
in ``risk.py`` versus the exact division form in ``sim_math.py``); ADR-01 fixes the
division form as normative because it is the algebraic solution of ``equity =
maintenance`` and therefore holds for any leverage.

Design rules
------------
* Isolated margin only.  A ``margin_mode`` other than ``"isolated"`` is rejected
  rather than silently treated as isolated (closes defect D10 / TZ §6.2).
* Numeric type is preserved: ``Decimal`` in, ``Decimal`` out; ``float`` in,
  ``float`` out.  The paper-trading helper returns ``Decimal``; the simulator
  returns ``float``; both delegate here.
* No I/O, no clock, no randomness — pure functions, trivially property-testable.

The general isolated liquidation for entry ``E``, notional ``N``, effective margin
``M`` (isolated + extra), maintenance rate ``m`` and taker fee rate ``f``::

    long :  P_liq = E * (1 + f - M/N) / (1 - m)
    short:  P_liq = E * (M/N + 1 - f) / (1 + m)

With ``M/N = 1/leverage`` and ``extra = 0`` this reduces exactly to the historic
``sim_math`` v2 formulas, so behaviour is preserved for the simulator while the
risk engine gains the same fee- and maintenance-aware rigour.
"""
from __future__ import annotations

import math
from decimal import Decimal

__all__ = [
    "TAKER_FEE_RATE",
    "MAINTENANCE_MARGIN_RATE",
    "MAX_LEVERAGE",
    "MARGIN_MODES",
    "validate_order",
    "liquidation_price",
    "settlement",
    "position_size",
    "funding_accrual",
    "one",
]

TAKER_FEE_RATE = Decimal("0.0006")
MAINTENANCE_MARGIN_RATE = Decimal("0.005")
MAX_LEVERAGE = 100
MARGIN_MODES = ("isolated",)


def one(x) -> Decimal | float:
    """Return the multiplicative identity in the same numeric type as ``x``."""
    return Decimal(1) if isinstance(x, Decimal) else 1.0


def _as_decimal(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def validate_order(
    direction: str,
    leverage: int,
    margin,
    price,
    *,
    margin_mode: str = "isolated",
    max_margin=1_000_000,
) -> None:
    """Validate an isolated-margin order request; raise ``ValueError`` if invalid.

    The checks are a superset of the historic simulator validator: same leverage
    bounds and finiteness rules, plus an explicit cross-margin rejection.
    """
    if margin_mode not in MARGIN_MODES:
        raise ValueError("cross margin not implemented; isolated only")
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    if type(leverage) is not int or not 1 <= leverage <= MAX_LEVERAGE:
        raise ValueError(f"leverage must be an integer from 1 to {MAX_LEVERAGE}")

    if not _finite(margin) or not _finite(price):
        raise ValueError("margin and price must be finite positive numbers")
    margin_dec = _as_decimal(margin)
    price_dec = _as_decimal(price)
    if margin_dec <= 0 or price_dec <= 0:
        raise ValueError("margin and price must be finite positive numbers")
    if margin_dec > _as_decimal(max_margin):
        raise ValueError(f"simulation margin exceeds {max_margin} USDT")


def _finite(x) -> bool:
    if isinstance(x, Decimal):
        return x.is_finite()
    return math.isfinite(x)


def liquidation_price(
    entry,
    leverage: int,
    direction: str,
    *,
    maintenance_margin_rate=MAINTENANCE_MARGIN_RATE,
    taker_fee_rate=TAKER_FEE_RATE,
    isolated_margin=None,
    extra_margin=0,
    notional=None,
) -> Decimal | float:
    """Exact isolated-margin liquidation price (equation ``equity = maintenance``).

    Convenience path: pass ``leverage`` and optionally an explicit ``notional`` /
    ``isolated_margin`` / ``extra_margin`` for the general case.  When only
    ``leverage`` is given the margin fraction is ``1/leverage`` (matching the
    simulator); when ``notional`` is supplied the effective margin overrides it.
    """
    unit = Decimal(1)  # all arithmetic is Decimal; result is cast to float if needed
    entry_dec = _as_decimal(entry)
    direction = direction.lower()
    if direction not in {"long", "short"}:
        raise ValueError(f"direction: expected 'long' or 'short', got {direction!r}")
    if entry_dec <= 0:
        raise ValueError("entry_price: must be positive")
    if leverage < 1:
        raise ValueError("leverage: must be >= 1")

    m = _as_decimal(maintenance_margin_rate)
    f = _as_decimal(taker_fee_rate)
    if m < 0 or f < 0:
        raise ValueError("margin and fee rates must be non-negative")

    if isolated_margin is not None:
        extra = _as_decimal(extra_margin)
        if extra < 0:
            raise ValueError("extra_margin: must be non-negative")
        effective_margin = _as_decimal(isolated_margin) + extra
        if notional is None:
            raise ValueError("notional: required with explicit isolated_margin")
        notional_dec = _as_decimal(notional)
        if notional_dec <= 0 or effective_margin <= 0:
            raise ValueError("notional and isolated_margin must be positive")
        margin_fraction = effective_margin / notional_dec
    else:
        margin_fraction = Decimal(1) / Decimal(leverage)

    if direction == "long":
        numerator = unit + f - margin_fraction
        denominator = unit - m
        raw = entry_dec * numerator / denominator
        if raw < 0:
            raw = Decimal(0)
    else:  # short
        raw = entry_dec * (margin_fraction + unit - f) / (unit + m)

    return raw if isinstance(entry, Decimal) else float(raw)


def settlement(
    direction: str,
    entry,
    exit_price,
    size,
    entry_fee,
    funding=0,
    *,
    taker_fee_rate=TAKER_FEE_RATE,
) -> tuple[Decimal | float, Decimal | float]:
    """Return ``(net_pnl, close_fee)`` for closing ``size`` at ``exit_price``.

    ``close_fee`` is charged on the *exit* notional (the corrected behaviour;
    the legacy v1 path charged it on entry notional).  ``net_pnl`` is gross minus
    entry fee, close fee and funding.  ``settlement(exit == entry)`` is exactly
    ``-(entry_fee + close_fee)`` because gross is then zero.
    """
    direction = direction.lower()
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    entry_dec = _as_decimal(entry)
    exit_dec = _as_decimal(exit_price)
    size_dec = _as_decimal(size)
    sign = Decimal(1) if direction == "long" else Decimal(-1)
    close_fee = abs(size_dec * exit_dec) * _as_decimal(taker_fee_rate)
    gross = (exit_dec - entry_dec) * size_dec * sign
    net = gross - _as_decimal(entry_fee) - close_fee - _as_decimal(funding)
    if isinstance(entry, Decimal) or isinstance(exit_price, Decimal):
        return net, close_fee
    return float(net), float(close_fee)


def position_size(margin, leverage: int, entry) -> Decimal | float:
    """Contract/coin size for ``margin`` at ``leverage`` entered at ``entry``."""
    entry_dec = _as_decimal(entry)
    if entry_dec <= 0:
        raise ValueError("entry: must be positive")
    if leverage < 1:
        raise ValueError("leverage: must be >= 1")
    size = _as_decimal(margin) * Decimal(leverage) / entry_dec
    return size if isinstance(margin, Decimal) or isinstance(entry, Decimal) else float(size)


def funding_accrual(notional, funding_rate, periods: Decimal = Decimal(1)) -> Decimal | float:
    """Holding cost of ``funding_rate`` applied to ``notional`` over ``periods``.

    Positive rate: longs pay shorts (a cost to a long).  The sign convention here
    returns the accrual magnitude to subtract from a long's PnL; callers holding
    shorts negate it.
    """
    accrual = _as_decimal(notional) * _as_decimal(funding_rate) * _as_decimal(periods)
    return accrual if isinstance(notional, Decimal) else float(accrual)
