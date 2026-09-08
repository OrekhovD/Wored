"""Deterministic isolated-margin simulation policy, not exchange specifications."""
import math

TAKER_FEE = 0.0006
MAINTENANCE_MARGIN = 0.005
MAX_LEVERAGE = 100


def validate_order(direction: str, leverage: int, margin: float, price: float) -> None:
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    if type(leverage) is not int or not 1 <= leverage <= MAX_LEVERAGE:
        raise ValueError("leverage must be an integer from 1 to 100")
    if not all(math.isfinite(v) and v > 0 for v in (margin, price)):
        raise ValueError("margin and price must be finite positive numbers")
    if margin > 1_000_000:
        raise ValueError("simulation margin exceeds 1000000 USDT")


def liquidation_price(entry: float, leverage: int, direction: str,
                      calculation_version: int = 2, legacy_session: bool = False) -> float:
    if calculation_version == 1:
        adjustment = -MAINTENANCE_MARGIN if legacy_session else MAINTENANCE_MARGIN
        return entry * (1 - 1 / leverage + adjustment) if direction == "long" else entry * (1 + 1 / leverage - adjustment)
    # Solve equity = maintenance requirement, including the opening taker fee.
    if direction == "long":
        return max(0.0, entry * (1 - 1 / leverage + TAKER_FEE) / (1 - MAINTENANCE_MARGIN))
    return entry * (1 + 1 / leverage - TAKER_FEE) / (1 + MAINTENANCE_MARGIN)


def settlement(direction: str, entry: float, exit_price: float, size: float,
               entry_fee: float, funding: float = 0.0, calculation_version: int = 2) -> tuple[float, float]:
    close_fee = abs(size * (entry if calculation_version == 1 else exit_price)) * TAKER_FEE
    gross = (exit_price - entry) * size * (1 if direction == "long" else -1)
    return gross - entry_fee - close_fee - funding, close_fee


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
