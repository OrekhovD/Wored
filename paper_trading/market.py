"""Validated market-data snapshots and execution-price selection for paper trading.

This module is the pure-data layer: no FastAPI, no Redis-coupled HTTP layer.
The existing ``webui.paper_market`` module couples snapshot parsing to the HTTP
request/response cycle; this package extracts the reusable core so the risk and
execution modules (and their unit tests) can operate on snapshots without an
HTTP context.

HTX BTC-USDT USDT-margined isolated perpetual contract parameters:
  * contract multiplier (contract_size): 0.001 BTC
  * price tick:  0.1 USDT
  * quantity step: 0.001 contracts

All monetary values are :class:`~decimal.Decimal` — never float.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "DEFAULT_AVAILABLE_FRACTION",
    "DEFAULT_LATENCY_MS",
    "DEFAULT_MAX_AGE_SECONDS",
    "DEFAULT_SLIPPAGE_BPS",
    "DEFAULT_SPREAD_MAX_BPS",
    "PerpetualSnapshot",
    "apply_latency",
    "apply_slippage",
    "available_quantity",
    "check_spread",
    "get_execution_price",
    "is_fresh",
    "read_snapshot_from_redis",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "validate_snapshot",
]

DEFAULT_MAX_AGE_SECONDS = 5.0
DEFAULT_LATENCY_MS = 250
DEFAULT_SLIPPAGE_BPS = 2
DEFAULT_SPREAD_MAX_BPS = 10
DEFAULT_AVAILABLE_FRACTION = Decimal("0.1")

_BPS = Decimal("0.0001")  # 1 basis point


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_decimal(value: Any, field_name: str, *, positive: bool = True) -> Decimal:
    """Convert ``value`` to a finite Decimal, raising ``ValueError`` on failure."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: invalid decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name}: invalid decimal")
    if positive and result <= 0:
        raise ValueError(f"{field_name}: must be positive")
    return result


def _to_decimal_signed(value: Any, field_name: str) -> Decimal:
    """Like ``_to_decimal`` but allows zero and negative values (for funding)."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: invalid decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name}: invalid decimal")
    return result


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    """Parse a timestamp (epoch s/ms or ISO-8601 string) into UTC datetime."""
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:  # milliseconds
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}: missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerpetualSnapshot:
    """A validated HTX USDT-margined perpetual market snapshot.

    All price fields are :class:`Decimal` in USDT. ``funding_rate`` may be
    negative (shorts pay longs) or zero; it is ``None`` only in demo mode
    when funding is unknown.
    """

    schema_version: int
    mode: str
    venue: str
    market_type: str
    contract_code: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    mark: Decimal
    index: Decimal
    funding_rate: Decimal | None
    next_funding_at: str | None
    component_times: dict[str, str]
    contract_size: Decimal
    price_tick: Decimal
    quantity_step: Decimal
    source_at: str
    received_at: str
    source: str
    quality: str

    # --- convenience selectors (kept compatible with webui.paper_market) ---

    def entry_price(self, side: str) -> Decimal:
        """Price at which a new position is opened: ask for buy, bid for sell."""
        return self.ask if side == "buy" else self.bid

    def close_price(self, position_side: str) -> Decimal:
        """Price at which an existing position is closed: bid for long, ask for short."""
        return self.bid if position_side == "long" else self.ask

    def public_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict with Decimal fields as plain strings."""
        return {
            key: (format(value, "f") if isinstance(value, Decimal) else value)
            for key, value in asdict(self).items()
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_snapshot(
    snapshot: PerpetualSnapshot,
    *,
    expected_contract: str | None = None,
) -> PerpetualSnapshot:
    """Validate invariants of a :class:`PerpetualSnapshot`.

    Checks:
      * ``bid > 0`` and ``ask >= bid`` (no crossed book)
      * ``mark > 0``
      * ``funding_rate`` may be negative or zero but must be finite if present
      * component timestamps are parseable and not in the far future

    Raises :class:`ValueError` if any invariant is violated.
    Returns the snapshot unchanged on success (enables fluent use).
    """
    if snapshot.bid <= 0:
        raise ValueError("bid: must be positive")
    if snapshot.ask < snapshot.bid:
        raise ValueError("book: ask is below bid (crossed)")
    if snapshot.mark <= 0:
        raise ValueError("mark: must be positive")

    if snapshot.funding_rate is not None:
        if not snapshot.funding_rate.is_finite():
            raise ValueError("funding_rate: invalid decimal")

    if expected_contract is not None and snapshot.contract_code != expected_contract:
        raise ValueError(
            f"contract_code: expected {expected_contract}, "
            f"got {snapshot.contract_code}"
        )

    now = datetime.now(timezone.utc)
    for component_name in ("ticker", "index", "mark", "funding"):
        raw = snapshot.component_times.get(component_name)
        if not raw:
            raise ValueError(f"component_times.{component_name}: missing")
        try:
            ts = _parse_timestamp(raw, f"component_times.{component_name}")
        except ValueError:
            raise
        age = (now - ts).total_seconds()
        if age < -300:  # 5-minute tolerance for clock skew
            raise ValueError(
                f"component_times.{component_name}: timestamp is in the future"
            )

    return snapshot


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def is_fresh(snapshot: PerpetualSnapshot, *, max_age: float = DEFAULT_MAX_AGE_SECONDS) -> bool:
    """Return ``True`` if ``snapshot.source_at`` is within ``max_age`` seconds of now."""
    try:
        source_at = _parse_timestamp(snapshot.source_at, "source_at")
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - source_at).total_seconds()
    return -2.0 <= age <= max_age


# ---------------------------------------------------------------------------
# Execution price
# ---------------------------------------------------------------------------

# side mapping: (direction, action) -> price selector
#   long open  = buy  -> ask
#   long close = sell -> bid
#   short open = sell -> bid
#   short close = buy -> ask


def get_execution_price(
    snapshot: PerpetualSnapshot,
    direction: str,
    action: str,
) -> Decimal:
    """Return the raw execution price for a given direction and action.

    Parameters
    ----------
    snapshot
        Validated market snapshot.
    direction
        ``"long"`` or ``"short"``.
    action
        ``"open"`` or ``"close"``.

    * long open  → ask
    * long close → bid
    * short open → bid
    * short close → ask
    """
    direction = direction.lower()
    action = action.lower()
    if direction not in ("long", "short"):
        raise ValueError(f"direction: expected 'long' or 'short', got {direction!r}")
    if action not in ("open", "close"):
        raise ValueError(f"action: expected 'open' or 'close', got {action!r}")

    if direction == "long":
        return snapshot.ask if action == "open" else snapshot.bid
    # short
    return snapshot.bid if action == "open" else snapshot.ask


# ---------------------------------------------------------------------------
# Slippage
# ---------------------------------------------------------------------------


def apply_slippage(
    price: Decimal,
    direction: str,
    action: str,
    *,
    bps: int = DEFAULT_SLIPPAGE_BPS,
) -> Decimal:
    """Apply adverse slippage to ``price``.

    Slippage is always adverse to the trader:
      * buying  (long open, short close) → price increases
      * selling (long close, short open) → price decreases

    ``bps`` is in basis points (1 bps = 0.01%).
    """
    direction = direction.lower()
    action = action.lower()
    adverse = Decimal(bps) * _BPS
    is_buy = (direction == "long" and action == "open") or (
        direction == "short" and action == "close"
    )
    if is_buy:
        return price * (Decimal(1) + adverse)
    return price * (Decimal(1) - adverse)


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def apply_latency(
    snapshot: PerpetualSnapshot,
    *,
    latency_ms: int = DEFAULT_LATENCY_MS,
) -> PerpetualSnapshot:
    """Return a new snapshot with ``source_at`` shifted forward by ``latency_ms``.

    This models the delay between the exchange publishing data and the engine
    consuming it — the snapshot is ``latency_ms`` older than its ``source_at``
    suggests, so we push ``source_at`` back (making it older) by that amount.
    """
    if latency_ms < 0:
        raise ValueError("latency_ms: must be non-negative")
    source_dt = _parse_timestamp(snapshot.source_at, "source_at")
    received_dt = _parse_timestamp(snapshot.received_at, "received_at")
    offset = _microseconds(latency_ms)
    return PerpetualSnapshot(
        schema_version=snapshot.schema_version,
        mode=snapshot.mode,
        venue=snapshot.venue,
        market_type=snapshot.market_type,
        contract_code=snapshot.contract_code,
        bid=snapshot.bid,
        ask=snapshot.ask,
        last=snapshot.last,
        mark=snapshot.mark,
        index=snapshot.index,
        funding_rate=snapshot.funding_rate,
        next_funding_at=snapshot.next_funding_at,
        component_times=dict(snapshot.component_times),
        contract_size=snapshot.contract_size,
        price_tick=snapshot.price_tick,
        quantity_step=snapshot.quantity_step,
        source_at=(source_dt - offset).isoformat(),
        received_at=(received_dt - offset).isoformat(),
        source=snapshot.source,
        quality=snapshot.quality,
    )


def _microseconds(ms: int) -> Any:
    from datetime import timedelta

    return timedelta(milliseconds=ms)


# ---------------------------------------------------------------------------
# Spread check
# ---------------------------------------------------------------------------


def check_spread(
    snapshot: PerpetualSnapshot,
    *,
    max_bps: int = DEFAULT_SPREAD_MAX_BPS,
) -> bool:
    """Return ``True`` if the bid-ask spread is within ``max_bps`` basis points.

    Spread is measured relative to the mid-price:
      spread_bps = (ask - bid) / mid * 10000
    """
    if snapshot.bid <= 0:
        return False
    mid = (snapshot.bid + snapshot.ask) / Decimal(2)
    if mid <= 0:
        return False
    spread_bps = (snapshot.ask - snapshot.bid) / mid / _BPS
    return spread_bps <= Decimal(max_bps)


# ---------------------------------------------------------------------------
# Available quantity
# ---------------------------------------------------------------------------


def available_quantity(
    snapshot: PerpetualSnapshot,
    *,
    fraction: Decimal = DEFAULT_AVAILABLE_FRACTION,
    used: Decimal = Decimal("0"),
) -> Decimal:
    """Estimate the executable quantity available at the top of book.

    For v1 (top-of-book snapshot model), the available quantity is a fraction
    of the observed notional at the best quote, minus already-used volume in
    this quote sequence.  Without real order-book depth, we use the bid/ask
    price and the contract size to estimate how much is executable.

    The result is always rounded down to the nearest ``quantity_step``.
    """
    if fraction <= 0:
        raise ValueError("fraction: must be positive")
    step = snapshot.quantity_step
    if step <= 0:
        raise ValueError("quantity_step: must be positive")

    # Use observed price to estimate notional, then convert to contracts
    # For HTX BTC-USDT: contract_size=0.001 BTC, so 1 contract = 0.001 * price
    ref_price = snapshot.ask if snapshot.ask else snapshot.last
    if ref_price <= 0:
        ref_price = snapshot.last
    if ref_price <= 0:
        raise ValueError("no valid reference price for liquidity estimation")

    contract_notional = snapshot.contract_size * ref_price
    # Max visible notional = some assumed top-of-book size * fraction
    # Without real volume data, use a configurable fraction of 1 BTC equivalent
    max_notional = ref_price * fraction  # e.g. 0.1 * price = 0.1 BTC worth
    available_notional = max_notional - used * contract_notional
    if available_notional <= 0:
        return Decimal("0")

    raw_contracts = available_notional / contract_notional
    quantized = (raw_contracts / step).to_integral_value(rounding="ROUND_DOWN") * step
    return quantized if quantized > 0 else Decimal("0")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def snapshot_from_dict(
    data: dict[str, Any],
    *,
    expected_contract: str | None = None,
    mode: str | None = None,
) -> PerpetualSnapshot:
    """Construct and validate a :class:`PerpetualSnapshot` from a dict.

    This is the inverse of :func:`snapshot_to_dict` and is the primary way
    snapshots are created from JSON payloads (e.g. Redis values).
    """
    payload = dict(data)

    contract_code = str(payload.get("contract_code", "")).upper()
    if expected_contract is not None and contract_code != expected_contract:
        raise ValueError(
            f"contract_code: expected {expected_contract}, got {contract_code}"
        )

    funding_raw = payload.get("funding_rate")
    funding_rate: Decimal | None
    if funding_raw is None or funding_raw == "":
        funding_rate = None
    else:
        funding_rate = _to_decimal_signed(funding_raw, "funding_rate")

    snapshot = PerpetualSnapshot(
        schema_version=int(payload.get("schema_version", 1)),
        mode=str(mode or payload.get("mode", "live")),
        venue=str(payload.get("venue", "htx")),
        market_type=str(payload.get("market_type", "linear-swap")),
        contract_code=contract_code,
        bid=_to_decimal(payload.get("bid"), "bid"),
        ask=_to_decimal(payload.get("ask"), "ask"),
        last=_to_decimal(payload.get("last"), "last"),
        mark=_to_decimal(payload.get("mark"), "mark"),
        index=_to_decimal(payload.get("index"), "index"),
        funding_rate=funding_rate,
        next_funding_at=payload.get("next_funding_at"),
        component_times=dict(payload.get("component_times") or {}),
        contract_size=_to_decimal(payload.get("contract_size"), "contract_size"),
        price_tick=_to_decimal(payload.get("price_tick"), "price_tick"),
        quantity_step=_to_decimal(payload.get("quantity_step"), "quantity_step"),
        source_at=str(payload.get("source_at", "")),
        received_at=str(payload.get("received_at", "")),
        source=str(payload.get("source", "htx-public-api")),
        quality=str(payload.get("quality", "live")),
    )
    return validate_snapshot(snapshot, expected_contract=expected_contract)


def snapshot_to_dict(snapshot: PerpetualSnapshot) -> dict[str, Any]:
    """Serialize a :class:`PerpetualSnapshot` to a JSON-safe dict."""
    return snapshot.public_dict()


# ---------------------------------------------------------------------------
# Redis reader
# ---------------------------------------------------------------------------


async def read_snapshot_from_redis(
    redis_client: Any,
    contract_code: str,
    *,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
    key_prefix: str = "market:perpetual:htx",
) -> PerpetualSnapshot:
    """Read and validate a perpetual snapshot from Redis.

    Parameters
    ----------
    redis_client
        An async Redis client (must have ``.get(key)``).
    contract_code
        e.g. ``"BTC-USDT"``.
    max_age
        Maximum acceptable age in seconds (checked by :func:`is_fresh`).

    Raises :class:`ValueError` if the key is missing, JSON is invalid, the
    snapshot fails validation, or it is stale.
    """
    key = f"{key_prefix}:{contract_code}"
    raw = await redis_client.get(key)
    if raw is None:
        raise ValueError(f"No perpetual snapshot for {contract_code}")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"snapshot: invalid JSON for {contract_code}") from exc

    snapshot = snapshot_from_dict(payload, expected_contract=contract_code)
    if not is_fresh(snapshot, max_age=max_age):
        raise ValueError(f"snapshot: stale for {contract_code}")
    return snapshot