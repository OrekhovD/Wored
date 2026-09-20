"""Deterministic isolated-margin simulation policy, not exchange specifications.

The canonical (``calculation_version == 2``) liquidation and settlement maths are
delegated to :mod:`trading_math` so the simulator and the paper-trading risk
engine share one definition (ADR-01).  A ``calculation_version == 1`` path is kept
here on purpose: positions opened before the unification are reconciled with the
exact numbers they were created with, so legacy sessions must not silently change
value.
"""
from trading_math import (
    MAX_LEVERAGE,
    liquidation_price as _core_liquidation_price,
    settlement as _core_settlement,
    validate_order as _core_validate_order,
)

# Retained for import compatibility; the canonical values now live in trading_math.
TAKER_FEE = float(0.0006)
MAINTENANCE_MARGIN = float(0.005)

__all__ = [
    "TAKER_FEE",
    "MAINTENANCE_MARGIN",
    "MAX_LEVERAGE",
    "validate_order",
    "liquidation_price",
    "settlement",
    "preview",
]


def validate_order(direction: str, leverage: int, margin: float, price: float) -> None:
    # Isolated-only is enforced by the shared core (rejects cross margin).
    _core_validate_order(direction, leverage, margin, price)


def liquidation_price(entry: float, leverage: int, direction: str,
                      calculation_version: int = 2, legacy_session: bool = False) -> float:
    if calculation_version == 1:
        # Legacy additive approximation, preserved for old-session reconciliation.
        adjustment = -MAINTENANCE_MARGIN if legacy_session else MAINTENANCE_MARGIN
        return entry * (1 - 1 / leverage + adjustment) if direction == "long" else entry * (1 + 1 / leverage - adjustment)
    return _core_liquidation_price(entry, leverage, direction)


def settlement(direction: str, entry: float, exit_price: float, size: float,
               entry_fee: float, funding: float = 0.0, calculation_version: int = 2) -> tuple[float, float]:
    if calculation_version == 1:
        # Legacy: close fee charged on entry notional (kept for old sessions).
        close_fee = abs(size * entry) * TAKER_FEE
        gross = (exit_price - entry) * size * (1 if direction == "long" else -1)
        return gross - entry_fee - close_fee - funding, close_fee
    return _core_settlement(direction, entry, exit_price, size, entry_fee, funding)


def preview(direction: str, leverage: int, margin: float, entry: float) -> dict:
    validate_order(direction, leverage, margin, entry)
    notional = margin * leverage
    size = notional / entry
    liq = liquidation_price(entry, leverage, direction)
    scenarios, liquidated = {}, {}
    for pct in (1, -1, 5, -5):
        target = entry * (1 + pct / 100)
        hit = target <= liq if direction == "long" else target >= liq
        pnl, _ = settlement(direction, entry, liq if hit else target, size, notional * TAKER_FEE)
        label = f"{pct:+d}%"
        scenarios[label], liquidated[label] = round(pnl, 2), hit
    return {"direction": direction, "entry_price": entry, "leverage": leverage, "margin": margin,
            "notional": notional, "size": size, "taker_fee": notional * TAKER_FEE,
            "liquidation_price": liq, "liq_distance_pct": abs(liq - entry) / entry * 100,
            "scenarios": scenarios, "scenario_liquidated": liquidated,
            "calculation_version": "isolated-v2", "funding_in_scenarios": False}
