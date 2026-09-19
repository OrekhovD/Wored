"""Validated market snapshots for the paper-trading execution path.

`demo` and `live` are deliberately separate modes. Live mode never falls back
to a fabricated price: a missing, malformed or stale Redis snapshot blocks the
paper command before account state can change.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, Request


MARKET_KEY_PREFIX = "market:perpetual:htx"
DEMO_PRICE = Decimal("64250")
DEFAULT_MAX_AGE_SECONDS = 5.0


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field}: must be positive")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def contract_code(instrument: str) -> str:
    compact = instrument.upper().replace("-", "").replace("/", "").strip()
    if not compact.endswith("USDT") or len(compact) <= 4:
        raise ValueError("instrument: expected a USDT contract")
    return f"{compact[:-4]}-USDT"


def paper_market_mode(request: Request) -> str:
    configured = getattr(request.app.state, "paper_market_mode", None)
    mode = str(configured or os.getenv("PAPER_MARKET_MODE", "demo")).strip().lower()
    if mode not in {"demo", "live"}:
        raise HTTPException(status_code=500, detail="PAPER_MARKET_MODE должен быть demo или live")
    return mode


@dataclass(frozen=True)
class PerpetualSnapshot:
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

    def entry_price(self, side: str) -> Decimal:
        return self.ask if side == "buy" else self.bid

    def close_price(self, position_side: str) -> Decimal:
        return self.bid if position_side == "long" else self.ask

    def public_dict(self) -> dict[str, Any]:
        return {
            key: (format(value, "f") if isinstance(value, Decimal) else value)
            for key, value in asdict(self).items()
        }


def demo_snapshot(instrument: str, *, now: datetime | None = None) -> PerpetualSnapshot:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return PerpetualSnapshot(
        schema_version=1,
        mode="demo",
        venue="prototype",
        market_type="linear-swap",
        contract_code=contract_code(instrument),
        bid=DEMO_PRICE,
        ask=DEMO_PRICE,
        last=DEMO_PRICE,
        mark=DEMO_PRICE,
        index=DEMO_PRICE,
        funding_rate=None,
        next_funding_at=None,
        component_times={
            "ticker": timestamp,
            "index": timestamp,
            "mark": timestamp,
            "funding": timestamp,
        },
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at=timestamp,
        received_at=timestamp,
        source="wored-demo",
        quality="prototype",
    )


def parse_live_snapshot(
    raw: str | dict[str, Any],
    *,
    expected_contract: str,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> PerpetualSnapshot:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("snapshot: invalid JSON object") from exc

    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("schema_version: expected 1")
    if payload.get("venue") != "htx" or payload.get("market_type") != "linear-swap":
        raise ValueError("market identity: expected HTX linear-swap")
    if payload.get("contract_code") != expected_contract:
        raise ValueError("contract_code: unexpected contract")

    bid = _decimal(payload.get("bid"), "bid")
    ask = _decimal(payload.get("ask"), "ask")
    last = _decimal(payload.get("last"), "last")
    mark = _decimal(payload.get("mark"), "mark")
    index = _decimal(payload.get("index"), "index")
    if bid > ask:
        raise ValueError("book: bid exceeds ask")

    funding_raw = payload.get("funding_rate")
    if funding_raw is None:
        raise ValueError("funding_rate: missing")
    try:
        funding_rate = Decimal(str(funding_raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("funding_rate: invalid decimal") from exc
    if not funding_rate.is_finite():
        raise ValueError("funding_rate: invalid decimal")

    source_at = _timestamp(payload.get("source_at"), "source_at")
    received_at = _timestamp(payload.get("received_at"), "received_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - source_at).total_seconds()
    if age < -2:
        raise ValueError("source_at: timestamp is in the future")
    if age > max_age_seconds:
        raise ValueError(f"snapshot: stale by {age:.3f}s")

    component_times_raw = payload.get("component_times")
    if not isinstance(component_times_raw, dict):
        raise ValueError("component_times: missing")
    component_times = {
        name: _timestamp(component_times_raw.get(name), f"component_times.{name}").isoformat()
        for name in ("ticker", "index", "mark", "funding")
    }
    mark_age = (current - _timestamp(component_times["mark"], "component_times.mark")).total_seconds()
    if mark_age < -2 or mark_age > 90:
        raise ValueError(f"mark price: stale by {mark_age:.3f}s")

    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code=expected_contract,
        bid=bid,
        ask=ask,
        last=last,
        mark=mark,
        index=index,
        funding_rate=funding_rate,
        next_funding_at=payload.get("next_funding_at"),
        component_times=component_times,
        contract_size=_decimal(payload.get("contract_size"), "contract_size"),
        price_tick=_decimal(payload.get("price_tick"), "price_tick"),
        quantity_step=_decimal(payload.get("quantity_step"), "quantity_step"),
        source_at=source_at.isoformat(),
        received_at=received_at.isoformat(),
        source=str(payload.get("source") or "htx-public-api"),
        quality="live",
    )


async def resolve_market_snapshot(request: Request, instrument: str) -> PerpetualSnapshot:
    mode = paper_market_mode(request)
    if mode == "demo":
        return demo_snapshot(instrument)

    expected_contract = contract_code(instrument)
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Perpetual feed недоступен: Redis не подключён")

    key = f"{MARKET_KEY_PREFIX}:{expected_contract}"
    try:
        raw = await redis_client.get(key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Perpetual feed недоступен: ошибка Redis") from exc
    if raw is None:
        raise HTTPException(status_code=409, detail=f"Нет perpetual-снимка {expected_contract}")

    try:
        max_age = float(os.getenv("PAPER_MARKET_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS)))
        if not 0 < max_age <= 60:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="PAPER_MARKET_MAX_AGE_SECONDS должен быть числом от 0 до 60",
        ) from exc
    try:
        return parse_live_snapshot(
            raw,
            expected_contract=expected_contract,
            max_age_seconds=max_age,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Perpetual feed заблокирован: {exc}") from exc
