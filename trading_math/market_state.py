"""Shared V3 market-state primitive: per-component freshness, quote, capabilities.

Moved verbatim out of ``webui/market_workspace.py`` in P3c.  The plan domain
needs the *same* market gate in the dashboard and in the Telegram bot:
``can_enter`` is exactly what produces the ``stale_quote`` violation that blocks
approval, so a second copy in the bot would give one plan two different answers
(ТЗ P3: «из UI виден тот же ``plan_hash`` и violations, что в WebUI»).

Dependency rule: stdlib plus :mod:`trading_math.instrument_registry` only -- no
FastAPI, no asyncpg, no redis client.  The caller reads the raw snapshot dict
through its own transport and passes the current time, which keeps this a pure
function that loads inside the chatbot container (Python 3.9).

Environment: ``V3_*_MAX_AGE_SECONDS`` are read once at import, the same way the
webui copy did; unset keys give identical defaults in every runtime.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from trading_math.instrument_registry import InstrumentSpec

MARKET_KEY_PREFIX = "market:perpetual:htx"  # same key the collector writes


# Per-component freshness thresholds in seconds.  Rationale (ТЗ §4 asks for
# thresholds + rationale to be recorded):
#   ticker  — HTX perpetual WS ticks sub-second; 5 s is the existing runtime
#             contract (paper_market.DEFAULT_MAX_AGE_SECONDS) and the runner
#             refuses anything older.
#   mark    — HTX publishes mark roughly once a minute; 90 s tolerates one
#             missed publish without lying about freshness.
#   index   — same cadence as mark.
#   funding — funding is refreshed hours apart; 1 h staleness only matters at
#             the moment of charging, which is the runner's job, not the
#             read-model's.  A stale funding must not block the whole quote.
FRESHNESS_THRESHOLDS: dict[str, float] = {
    "ticker": float(os.getenv("V3_TICKER_MAX_AGE_SECONDS", "5")),
    "mark": float(os.getenv("V3_MARK_MAX_AGE_SECONDS", "90")),
    "index": float(os.getenv("V3_INDEX_MAX_AGE_SECONDS", "90")),
    "funding": float(os.getenv("V3_FUNDING_MAX_AGE_SECONDS", "3600")),
}


def _dec(value: Any) -> Decimal | None:
    """Parse a positive finite Decimal; anything else is treated as missing."""
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def _dec_signed(value: Any) -> Decimal | None:
    """Funding rate may legitimately be negative or zero."""
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _component_quality(
    seen_at: datetime | None, *, now: datetime, threshold_seconds: float
) -> str:
    if seen_at is None:
        return "missing"
    # Small forward-skew tolerance: the exchange clock may lead ours by a few
    # seconds (same convention parse_live_snapshot uses for source_at).
    age = (now - seen_at).total_seconds()
    if age < -30:
        return "missing"
    return "live" if age <= threshold_seconds else "stale"


_WORST_ORDER = {"live": 0, "stale": 1, "missing": 2}


def build_market_state(
    spec: InstrumentSpec,
    payload: dict[str, Any] | None,
    *,
    now: datetime,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compose the V3 state response from a raw snapshot dict.

    Returns ``identity_ok=False`` (and no quote) when the payload describes a
    different venue/market/contract than the registry entry — the caller must
    surface that as an error, never as a substitution.
    """
    limits = thresholds or FRESHNESS_THRESHOLDS
    if payload is None:
        quality = {name: "missing" for name in limits}
        quality["worst"] = "missing"
        return {
            "instrument_key": spec.instrument_key,
            "snapshot_id": str(uuid4()),
            "sequence": 0,
            "as_of": now.isoformat(),
            "quote": None,
            "spread": None,
            "quality": quality,
            "source": None,
            "capabilities": {
                "can_enter": False,
                "can_close": True,  # protective actions stay available
                "reason_code": "snapshot_missing",
            },
        }

    venue = str(payload.get("venue") or "")
    market_type = str(payload.get("market_type") or "")
    contract = str(payload.get("contract_code") or "")
    if (venue, market_type, contract) != (spec.venue, spec.market_type, spec.contract_code):
        return {"identity_ok": False, "observed": {"venue": venue, "market_type": market_type, "contract_code": contract}}

    bid = _dec(payload.get("bid"))
    ask = _dec(payload.get("ask"))
    last = _dec(payload.get("last"))
    mark = _dec(payload.get("mark"))
    index = _dec(payload.get("index"))
    funding_rate = _dec_signed(payload.get("funding_rate"))

    crossed = bid is not None and ask is not None and bid > ask
    component_times_raw = payload.get("component_times") or {}
    if not isinstance(component_times_raw, dict):
        component_times_raw = {}
    ticker_at = _parse_ts(component_times_raw.get("ticker")) or _parse_ts(payload.get("source_at"))
    mark_at = _parse_ts(component_times_raw.get("mark"))
    index_at = _parse_ts(component_times_raw.get("index"))
    funding_at = _parse_ts(component_times_raw.get("funding"))

    quality = {
        "ticker": _component_quality(
            None if crossed else ticker_at, now=now, threshold_seconds=limits["ticker"]
        ),
        "mark": _component_quality(mark_at, now=now, threshold_seconds=limits["mark"]),
        "index": _component_quality(index_at, now=now, threshold_seconds=limits["index"]),
        "funding": _component_quality(funding_at, now=now, threshold_seconds=limits["funding"]),
    }
    # Crossed books invalidate the ticker component regardless of age.
    if crossed and quality["ticker"] == "live":
        quality["ticker"] = "stale"
    quality["worst"] = max(
        (quality[name] for name in limits), key=lambda q: _WORST_ORDER[q]
    )

    spread = (ask - bid) if (bid is not None and ask is not None) else None
    source_at = _parse_ts(payload.get("source_at"))
    source = str(payload.get("source") or "unknown")
    sequence = int(source_at.timestamp() * 1000) if source_at else 0

    can_enter = quality["worst"] == "live"
    reason = None
    if not can_enter:
        stale = [name for name, q in quality.items() if q != "live" and name != "worst"]
        reason = f"market_degraded:{','.join(stale) if stale else quality['worst']}"

    return {
        "instrument_key": spec.instrument_key,
        "snapshot_id": str(uuid4()),
        "sequence": sequence,
        "as_of": now.isoformat(),
        "quote": {
            "bid": _fmt(bid),
            "ask": _fmt(ask),
            "last": _fmt(last),
            "mark": _fmt(mark),
            "index": _fmt(index),
            "funding_rate": _fmt(funding_rate),
            "next_funding_at": payload.get("next_funding_at"),
            "source_at": _iso(source_at),
            "received_at": _iso(_parse_ts(payload.get("received_at"))),
            "component_times": {
                "ticker": _iso(ticker_at),
                "mark": _iso(mark_at),
                "index": _iso(index_at),
                "funding": _iso(funding_at),
            },
        },
        "spread": _fmt(spread),
        "quality": quality,
        "source": source,
        "capabilities": {
            "can_enter": can_enter,
            "can_close": True,
            "reason_code": reason,
        },
    }
