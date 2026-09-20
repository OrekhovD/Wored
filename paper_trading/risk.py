"""Risk management gates for paper-trading order validation.

Every order passes through :func:`check_order_risk` before execution and
:func:`check_fill_risk` immediately before the fill is committed. All
monetary calculations use :class:`~decimal.Decimal`.

HTX BTC-USDT USDT-margined isolated perpetual:
  contract multiplier: 0.001
  price tick: 0.1
  quantity step: 0.001
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from paper_trading.market import (
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_SPREAD_MAX_BPS,
    PerpetualSnapshot,
    check_spread,
    is_fresh,
)

__all__ = [
    "OrderRequest",
    "PositionInfo",
    "RiskCheckResult",
    "RiskSettings",
    "calculate_liquidation_price",
    "calculate_position_size",
    "check_fill_risk",
    "check_order_risk",
    "check_reduce_only",
]

FEE_RATE = Decimal("0.0006")  # 0.06% taker fee
DEFAULT_MAINTENANCE_MARGIN_RATE = Decimal("0.0028")

# Default risk settings (USDT)
DEFAULT_MAX_RISK_PER_ORDER = Decimal(10)
DEFAULT_MAX_DAILY_LOSS = Decimal(50)
DEFAULT_MAX_LEVERAGE = 10
DEFAULT_MAX_OPEN_RISK = Decimal(20)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskSettings:
    """Risk policy limits for the paper-trading engine.

    All amounts are in USDT.
    """

    max_risk_per_order: Decimal = DEFAULT_MAX_RISK_PER_ORDER
    max_daily_loss: Decimal = DEFAULT_MAX_DAILY_LOSS
    max_leverage: int = DEFAULT_MAX_LEVERAGE
    max_open_risk: Decimal = DEFAULT_MAX_OPEN_RISK
    min_net_rr: Decimal = Decimal("1.0")
    max_spread_bps: int = DEFAULT_SPREAD_MAX_BPS
    snapshot_max_age: float = DEFAULT_MAX_AGE_SECONDS
    order_ttl_seconds: float = 30.0
    cooldown_seconds: float = 5.0
    require_risk_tier: bool = False
    risk_tier_max_age_seconds: float = 3600.0
    taker_fee_rate: Decimal = FEE_RATE


@dataclass
class OrderRequest:
    """An order submitted for risk checking.

    ``direction`` is ``"long"`` or ``"short"``.
    ``order_type`` is ``"market"`` or ``"stop_market"``.
    """

    instrument: str
    direction: str  # "long" or "short"
    order_type: str = "market"
    risk_amount: Decimal = Decimal(0)
    stop_price: Decimal = Decimal(0)
    take_profit: Decimal | None = None
    leverage: int = 10
    reduce_only: bool = False
    quantity: Decimal | None = None
    created_at: datetime | None = None
    idempotency_key: str | None = None
    is_authenticated: bool = True
    account_id: str = "proto-manual"
    extra_margin: Decimal = Decimal(0)


@dataclass
class PositionInfo:
    """Summary of an existing open position for risk aggregation."""

    position_id: str
    direction: str  # "long" or "short"
    entry_price: Decimal
    quantity: Decimal
    stop_price: Decimal
    leverage: int
    reserved_margin: Decimal
    unrealized_pnl: Decimal = Decimal(0)


@dataclass
class RiskCheckResult:
    """Result of a risk check: allowed/denied with reasons."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)
    quantity: Decimal | None = None
    entry_price: Decimal | None = None
    reserved_margin: Decimal | None = None
    risk_to_sl: Decimal | None = None
    liquidation_price: Decimal | None = None
    net_rr: Decimal | None = None

    @property
    def denied(self) -> bool:
        return not self.allowed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _side_from_direction(direction: str) -> str:
    """Map direction to order side: long→buy, short→sell."""
    d = direction.lower()
    if d == "long":
        return "buy"
    if d == "short":
        return "sell"
    raise ValueError(f"direction: expected 'long' or 'short', got {direction!r}")


def _quantize_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` down to the nearest multiple of ``step``."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding="ROUND_DOWN") * step


def _risk_tier_is_fresh(source_at: str, now: datetime, max_age_seconds: float) -> bool:
    try:
        parsed = datetime.fromisoformat(source_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (now - parsed.astimezone(timezone.utc)).total_seconds()
    return -2.0 <= age <= max_age_seconds


# ---------------------------------------------------------------------------
# Position size
# ---------------------------------------------------------------------------


def calculate_position_size(
    risk_amount: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    *,
    quantity_step: Decimal = Decimal("0.001"),
    contract_size: Decimal = Decimal(1),
) -> Decimal:
    """Calculate position quantity from risk amount and stop distance.

    quantity_contracts = risk_amount / (|entry - stop| * contract_size)

    The result is rounded down to ``quantity_step``.
    """
    if risk_amount <= 0:
        raise ValueError("risk_amount: must be positive")
    if entry_price <= 0:
        raise ValueError("entry_price: must be positive")
    if stop_price <= 0:
        raise ValueError("stop_price: must be positive")
    if stop_price == entry_price:
        raise ValueError("stop_price: cannot equal entry_price")
    if contract_size <= 0:
        raise ValueError("contract_size: must be positive")

    raw_qty = risk_amount / (abs(entry_price - stop_price) * contract_size)
    return _quantize_to_step(raw_qty, quantity_step)


# ---------------------------------------------------------------------------
# Liquidation price (estimated, isolated margin)
# ---------------------------------------------------------------------------


def calculate_liquidation_price(
    entry_price: Decimal,
    leverage: int,
    direction: str,
    *,
    maintenance_margin_rate: Decimal = DEFAULT_MAINTENANCE_MARGIN_RATE,
    taker_fee_rate: Decimal = FEE_RATE,
    isolated_margin: Decimal | None = None,
    extra_margin: Decimal = Decimal(0),
    notional: Decimal | None = None,
) -> Decimal:
    """Isolated-margin liquidation price — delegates to :mod:`trading_math`.

    This thin wrapper exists so the paper-trading risk engine and the chatbot
    simulator share exactly one definition of the formula (ADR-01).  The core
    solves ``equity = maintenance`` (division form), which supersedes the earlier
    additive approximation used here; the change makes the estimate slightly
    stricter (lower long / higher short liquidation prices) and is recorded as an
    ADR because it shifts a handful of exact-value risk tests.

    ``notional`` defaults to ``entry_price`` for the leverage-implied case, so
    ``margin_fraction`` becomes ``1/leverage`` when no explicit margin is given.
    """
    from trading_math import liquidation_price as _core_liquidation_price

    return _core_liquidation_price(
        entry_price,
        leverage,
        direction,
        maintenance_margin_rate=maintenance_margin_rate,
        taker_fee_rate=taker_fee_rate,
        isolated_margin=isolated_margin,
        extra_margin=extra_margin,
        notional=notional if notional is not None or isolated_margin is None else entry_price,
    )


# ---------------------------------------------------------------------------
# Reduce-only check
# ---------------------------------------------------------------------------


def check_reduce_only(
    order: OrderRequest,
    open_positions: list[PositionInfo],
    *,
    instrument: str = "",
) -> bool:
    """Check that a reduce-only order has a matching open position.

    A reduce-only order must close or reduce an existing position in the
    opposite direction. Returns ``True`` if valid.
    """
    if not order.reduce_only:
        return True  # Not reduce-only, no constraint

    # Must have at least one open position in the opposite direction
    for pos in open_positions:
        if instrument and pos.position_id.startswith("__"):
            continue
        # A reduce-only long order closes a short position, and vice versa
        if order.direction == "long" and pos.direction == "short":
            return True
        if order.direction == "short" and pos.direction == "long":
            return True
    return False


# ---------------------------------------------------------------------------
# Core risk gate: check_order_risk
# ---------------------------------------------------------------------------


def check_order_risk(
    order: OrderRequest,
    snapshot: PerpetualSnapshot,
    settings: RiskSettings,
    *,
    open_positions: list[PositionInfo] | None = None,
    daily_realized_loss: Decimal = Decimal(0),
    available_margin: Decimal = Decimal(0),
    now: datetime | None = None,
    is_paused: bool = False,
    is_day_active: bool = True,
    existing_order_keys: list[str] | None = None,
    last_order_at: datetime | None = None,
) -> RiskCheckResult:
    """Run all pre-execution risk gates on an order.

    Gates checked (in order):
      1. Authentication
      2. Snapshot freshness
      3. Order TTL (not expired)
      4. Direction validity
      5. Quantity precision (aligned to quantity_step)
      6. Margin sufficiency
      7. Leverage within limit
      8. Risk-to-stop within per-order limit
      9. Total open risk within limit
     10. Daily drawdown (realized + unrealized) within limit
     11. Net reward:risk >= min_net_rr
     12. Stop before liquidation
     13. Spread within limit
     14. No conflicting pending order
     15. Not paused / cooldown elapsed / day still active

    Returns :class:`RiskCheckResult` with ``allowed=True`` and computed
    fields, or ``allowed=False`` with a list of failure reasons.
    """
    reasons: list[str] = []
    now = now or datetime.now(timezone.utc)
    open_positions = open_positions or []
    existing_order_keys = existing_order_keys or []

    # 1. Authentication
    if not order.is_authenticated:
        reasons.append("not_authenticated")

    # 2. Snapshot freshness
    if not is_fresh(snapshot, max_age=settings.snapshot_max_age):
        reasons.append("stale_snapshot")

    # 3. Order TTL
    if order.created_at is not None:
        ttl_expired = (now - order.created_at).total_seconds() > settings.order_ttl_seconds
        if ttl_expired:
            reasons.append("order_ttl_expired")

    # 4. Direction validity
    d = order.direction.lower()
    if d not in ("long", "short"):
        reasons.append("invalid_direction")

    # 5. Quantity precision
    qty = order.quantity
    if qty is not None and qty > 0:
        step = snapshot.quantity_step
        if step > 0:
            remainder = qty % step
            if remainder != 0:
                reasons.append("quantity_precision_mismatch")

    # Determine entry price from snapshot
    side = _side_from_direction(d) if d in ("long", "short") else "buy"
    entry_price = snapshot.entry_price(side)

    # 6. Margin & 7. Leverage
    leverage = order.leverage
    tier = snapshot.risk_tier
    if settings.require_risk_tier and tier is None:
        reasons.append("risk_tier_unavailable")
    if tier is not None and not _risk_tier_is_fresh(
        tier.source_at,
        now,
        settings.risk_tier_max_age_seconds,
    ):
        reasons.append("risk_tier_stale")
    tier_max_leverage = tier.max_leverage if tier is not None else settings.max_leverage
    if leverage < 1 or leverage > min(settings.max_leverage, tier_max_leverage):
        reasons.append("leverage_out_of_range")

    # Calculate quantity if not provided
    if qty is None or qty <= 0:
        try:
            qty = calculate_position_size(
                order.risk_amount,
                entry_price,
                order.stop_price,
                quantity_step=snapshot.quantity_step,
                contract_size=snapshot.contract_size,
            )
        except ValueError:
            qty = None

    reserved_margin = None
    notional = None
    if qty is not None and qty > 0 and leverage >= 1:
        notional = qty * snapshot.contract_size * entry_price
        reserved_margin = notional / Decimal(leverage)
        if reserved_margin > available_margin:
            reasons.append("insufficient_margin")

    # 8. Risk to stop
    risk_to_sl: Decimal | None = None
    if (
        qty is not None
        and qty > 0
        and order.stop_price > 0
        and entry_price > 0
    ):
        risk_to_sl = qty * snapshot.contract_size * abs(entry_price - order.stop_price)
        if risk_to_sl > settings.max_risk_per_order:
            reasons.append("risk_per_order_exceeded")

    # 9. Total open risk
    total_open_risk = Decimal(0)
    for pos in open_positions:
        pos_risk = (
            pos.quantity
            * snapshot.contract_size
            * abs(pos.entry_price - pos.stop_price)
        )
        total_open_risk += pos_risk
    if risk_to_sl is not None:
        total_open_risk += risk_to_sl
    if total_open_risk > settings.max_open_risk:
        reasons.append("max_open_risk_exceeded")

    # 10. Daily drawdown with unrealized
    unrealized_total = sum(
        (pos.unrealized_pnl for pos in open_positions), Decimal(0)
    )
    total_drawdown = daily_realized_loss + unrealized_total
    # Drawdown is negative PnL; compare abs of negative portion
    drawdown_amount = max(-total_drawdown, Decimal(0))
    if drawdown_amount > settings.max_daily_loss:
        reasons.append("daily_drawdown_exceeded")

    # 11. Net reward:risk >= min
    net_rr: Decimal | None = None
    if (
        risk_to_sl is not None
        and risk_to_sl > 0
        and order.take_profit is not None
        and order.take_profit > 0
        and qty is not None
        and qty > 0
    ):
        gross_reward = qty * snapshot.contract_size * abs(order.take_profit - entry_price)
        entry_fee = notional * FEE_RATE if notional else Decimal(0)
        exit_fee = qty * snapshot.contract_size * order.take_profit * FEE_RATE
        net_reward = gross_reward - entry_fee - exit_fee
        net_rr = net_reward / risk_to_sl
        if net_rr < settings.min_net_rr:
            reasons.append("net_rr_below_minimum")

    # 12. Stop before liquidation
    liq_price: Decimal | None = None
    if leverage >= 1 and entry_price > 0 and d in ("long", "short"):
        maintenance_margin_rate = (
            tier.maintenance_margin_rate
            if tier is not None
            else DEFAULT_MAINTENANCE_MARGIN_RATE
        )
        liq_price = calculate_liquidation_price(
            entry_price,
            leverage,
            d,
            maintenance_margin_rate=maintenance_margin_rate,
            taker_fee_rate=settings.taker_fee_rate,
            isolated_margin=reserved_margin,
            extra_margin=order.extra_margin,
            notional=notional,
        )
        if order.stop_price > 0:
            if d == "long" and order.stop_price <= liq_price or d == "short" and order.stop_price >= liq_price:
                reasons.append("stop_after_liquidation")

    # 13. Spread
    if not check_spread(snapshot, max_bps=settings.max_spread_bps):
        reasons.append("spread_too_wide")

    # 14. No conflicting order
    if order.idempotency_key and order.idempotency_key in existing_order_keys:
        reasons.append("duplicate_order")

    # 15. Pause / cooldown / day end
    if is_paused:
        reasons.append("trading_paused")
    if not is_day_active:
        reasons.append("day_not_active")
    if last_order_at is not None:
        cooldown_elapsed = (now - last_order_at).total_seconds()
        if cooldown_elapsed < settings.cooldown_seconds:
            reasons.append("cooldown_active")

    allowed = len(reasons) == 0
    return RiskCheckResult(
        allowed=allowed,
        reasons=reasons,
        quantity=qty if qty and qty > 0 else None,
        entry_price=entry_price if allowed or entry_price > 0 else None,
        reserved_margin=reserved_margin,
        risk_to_sl=risk_to_sl,
        liquidation_price=liq_price,
        net_rr=net_rr,
    )


# ---------------------------------------------------------------------------
# Pre-fill re-check
# ---------------------------------------------------------------------------


def check_fill_risk(
    order: OrderRequest,
    snapshot: PerpetualSnapshot,
    settings: RiskSettings,
    *,
    reserved_quantity: Decimal | None = None,
    reserved_entry_price: Decimal | None = None,
    available_margin: Decimal = Decimal(0),
    now: datetime | None = None,
) -> RiskCheckResult:
    """Re-check risk immediately before committing a fill.

    This is a lighter gate than :func:`check_order_risk` — it focuses on
    conditions that may have changed since the order was approved:
      * Snapshot still fresh
      * Spread still within limit
      * Margin still sufficient
      * Price hasn't moved adversely beyond a tolerance
    """
    reasons: list[str] = []
    now = now or datetime.now(timezone.utc)

    if not is_fresh(snapshot, max_age=settings.snapshot_max_age):
        reasons.append("stale_snapshot_at_fill")

    if not check_spread(snapshot, max_bps=settings.max_spread_bps):
        reasons.append("spread_too_wide_at_fill")

    tier = snapshot.risk_tier
    if settings.require_risk_tier and tier is None:
        reasons.append("risk_tier_unavailable_at_fill")
    if tier is not None and not _risk_tier_is_fresh(
        tier.source_at,
        now,
        settings.risk_tier_max_age_seconds,
    ):
        reasons.append("risk_tier_stale_at_fill")
    tier_max_leverage = tier.max_leverage if tier is not None else settings.max_leverage
    if order.leverage < 1 or order.leverage > min(settings.max_leverage, tier_max_leverage):
        reasons.append("leverage_out_of_range_at_fill")

    d = order.direction.lower()
    side = _side_from_direction(d) if d in ("long", "short") else "buy"
    current_price = snapshot.entry_price(side)

    if reserved_entry_price is not None and reserved_entry_price > 0:
        # Allow up to 2% adverse movement
        tolerance = reserved_entry_price * Decimal("0.02")
        is_buy = side == "buy"
        if is_buy and current_price > reserved_entry_price + tolerance or not is_buy and current_price < reserved_entry_price - tolerance:
            reasons.append("price_adverse_at_fill")

    if (
        reserved_quantity is not None
        and reserved_quantity > 0
        and current_price > 0
    ):
        notional = reserved_quantity * snapshot.contract_size * current_price
        margin_needed = notional / Decimal(order.leverage) if order.leverage >= 1 else notional
        if margin_needed > available_margin:
            reasons.append("insufficient_margin_at_fill")

    allowed = len(reasons) == 0
    return RiskCheckResult(
        allowed=allowed,
        reasons=reasons,
        quantity=reserved_quantity,
        entry_price=current_price if allowed else None,
    )
