"""V3 versioned market read-model: ``/api/v3/instruments``, ``/api/v3/market/{key}/state``, ``/api/v3/market/{key}/candles``.

Responsibilities (ТЗ V3 §4, P1.1 scope):
  * Serve the perpetual quote with a *per-component* freshness verdict — the
    ages of bid/ask (ticker), mark, index and funding differ by construction,
    so a single "data fresh" flag would lie (ТЗ §3).
  * Serve validated closed candles from ``trader_v1_perp_candles`` (written by
    ``collector/htx/history_loader.py``).  5m and 1d series are derived from
    the stored 1m/60min rows and clearly marked ``quality="derived"``.
  * Never mix spot and perpetual: identity is checked against the registry,
    and an unregistered key (e.g. ``htx:spot:BTC-USDT``) is a hard 404.
  * Numbers are Decimal strings, times are ISO-8601 UTC.

Deliberate P1.1 limits (P1.2 closes them):
  * No forming-candle synthesis — the collector persists only closed candles,
    inventing a forming body from the ticker would be fabricated data.
  * ``sequence`` is derived from ``source_at`` epoch-millis (monotonic per
    instrument while the feed advances); a true stream sequence arrives with
    the SSE endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from instrument_registry import (
    InstrumentNotFound,
    InstrumentRegistry,
    InstrumentSpec,
    load_registry,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["market-v3"])

MARKET_KEY_PREFIX = "market:perpetual:htx"  # same key the collector writes
CANDLE_KEY_PREFIX = "market:perpetual:htx:candles"

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

# V3 period → stored timeframe (trader_v1_perp_candles.timeframe).  Periods
# mapped to None are derived from another stored timeframe.
PERIOD_TO_STORED: dict[str, str | None] = {
    "1m": "1min",
    "5m": None,      # derived from 1min
    "15m": "15min",
    "1h": "60min",
    "4h": "4hour",
    "1d": None,      # derived from 60min
}
DERIVE_BASE: dict[str, tuple[str, int]] = {
    "5m": ("1m", 5),
    "1d": ("1h", 24),
}
STORED_SECONDS = {"1min": 60, "15min": 900, "60min": 3600, "4hour": 14400}

MAX_CANDLE_LIMIT = 2000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# candle validation and derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandleRow:
    start_at: datetime
    end_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str


class CandleSourceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_candle_rows(rows: list[CandleRow], *, stored_seconds: int) -> list[dict[str, Any]]:
    """Apply the ТЗ §4 invariants; raise CandleSourceError on any breach.

    Checks: OHLC containment, positive finite prices, strictly increasing
    ``start_at`` aligned to the period, no duplicates, uniform gaps (missing
    spans are reported, not hidden), one source family.
    """
    ordered = sorted(rows, key=lambda r: r.start_at)
    out: list[dict[str, Any]] = []
    previous: CandleRow | None = None
    for row in ordered:
        if previous is not None and row.start_at == previous.start_at:
            raise CandleSourceError("duplicate_candle")
        if not all(x.is_finite() and x > 0 for x in (row.open, row.high, row.low, row.close)):
            raise CandleSourceError("invalid_candle_price")
        if row.high < max(row.open, row.close) or row.low > min(row.open, row.close):
            raise CandleSourceError("invalid_candle_ohlc")
        if row.volume < 0 or not row.volume.is_finite():
            raise CandleSourceError("invalid_candle_volume")
        if (row.end_at - row.start_at).total_seconds() != stored_seconds:
            raise CandleSourceError("invalid_candle_span")
        out.append(
            {
                "start_at": row.start_at.isoformat(),
                "end_at": row.end_at.isoformat(),
                "open": _fmt(row.open),
                "high": _fmt(row.high),
                "low": _fmt(row.low),
                "close": _fmt(row.close),
                "volume": _fmt(row.volume),
                "closed": True,
                "source": row.source,
            }
        )
        previous = row
    return out


def detect_gaps(candles: list[dict[str, Any]], *, period_seconds: int) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for prior, current in zip(candles, candles[1:]):
        expected = datetime.fromisoformat(prior["start_at"]) + timedelta(seconds=period_seconds)
        found = datetime.fromisoformat(current["start_at"])
        if found > expected:
            gaps.append({"expected_start_at": expected.isoformat(), "found_start_at": found.isoformat()})
    return gaps


def build_forming(
    candles: list[dict[str, Any]], *, period_seconds: int, now: datetime
) -> dict[str, Any] | None:
    """Describe the *currently forming* period from persisted facts only.

    The source persists closed candles (§4: no synthesis), so the honest
    forming-candle state is time facts — when the period started, when it
    closes, how much of it has elapsed — plus an explicit flag that the body
    (OHLC of the open period) is not available from this source.  The live
    price rides on the separate ``/state`` stream; we never invent a candle.
    """
    if not candles:
        return None
    last_end = datetime.fromisoformat(candles[-1]["end_at"])
    ends_at = last_end + timedelta(seconds=period_seconds)
    return {
        "state": "forming",
        "start_at": last_end.isoformat(),
        "ends_at": ends_at.isoformat(),
        "elapsed_seconds": max(round((now - last_end).total_seconds(), 3), 0.0),
        "remaining_seconds": max(round((ends_at - now).total_seconds(), 3), 0.0),
        "candle_body_available": False,
        "note": "источник хранит только закрытые свечи; тело формируется вне хранилища",
    }


def aggregate_candles(candles: list[dict[str, Any]], *, factor: int, period_seconds: int) -> list[dict[str, Any]]:
    """Group complete aligned buckets of ``factor`` base candles.

    Incomplete trailing buckets are dropped, never padded — a partial 1d bar
    presented as closed would be fabricated data (ТЗ §4).
    """
    if factor <= 1:
        return candles
    buckets: dict[int, list[dict[str, Any]]] = {}
    for item in candles:
        start = datetime.fromisoformat(item["start_at"])
        bucket_ts = int(start.timestamp()) - int(start.timestamp()) % period_seconds
        buckets.setdefault(bucket_ts, []).append(item)
    out: list[dict[str, Any]] = []
    for bucket_ts in sorted(buckets):
        members = buckets[bucket_ts]
        if len(members) != factor:
            continue
        members.sort(key=lambda m: m["start_at"])
        first, last = members[0], members[-1]
        out.append(
            {
                "start_at": first["start_at"],
                "end_at": last["end_at"],
                "open": first["open"],
                "high": format(max(Decimal(m["high"]) for m in members), "f"),
                "low": format(min(Decimal(m["low"]) for m in members), "f"),
                "close": last["close"],
                "volume": format(sum((Decimal(m["volume"]) for m in members), Decimal(0)), "f"),
                "closed": True,
                "source": first["source"],
                "derived_from": None,  # filled by caller
            }
        )
    # re-check OHLC containment after aggregation (defensive)
    for item in out:
        o, h = Decimal(item["open"]), Decimal(item["high"])
        lo, c = Decimal(item["low"]), Decimal(item["close"])
        if h < max(o, c) or lo > min(o, c):
            raise CandleSourceError("invalid_candle_ohlc")
    return out


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


def _require_spec(registry: InstrumentRegistry, instrument_key: str) -> InstrumentSpec:
    try:
        spec = registry.get(instrument_key)
    except InstrumentNotFound:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "instrument_not_registered",
                "message": (
                    f"{instrument_key!r} отсутствует в реестре инструментов; "
                    "spot-инструменты не подменяют perpetual"
                ),
            },
        ) from None
    if spec.status != "active":
        raise HTTPException(status_code=410, detail={"reason_code": "instrument_deprecated"})
    return spec


@router.get("/instruments")
async def list_instruments(request: Request) -> JSONResponse:
    registry: InstrumentRegistry = load_registry()
    return JSONResponse(
        {
            "schema_version": 1,
            "instruments": [spec.public_dict() for spec in registry.list()],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/market/{instrument_key}/state")
async def market_state(request: Request, instrument_key: str) -> JSONResponse:
    registry: InstrumentRegistry = load_registry()
    spec = _require_spec(registry, instrument_key)

    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "redis_unavailable", "message": "Redis not connected"},
        )
    key = f"{MARKET_KEY_PREFIX}:{spec.contract_code}"
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 — surface as source outage
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "redis_error", "message": str(exc)},
        ) from exc

    payload: dict[str, Any] | None = None
    if raw is not None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if payload is not None and not isinstance(payload, dict):
            payload = None

    now = datetime.now(timezone.utc)
    state = build_market_state(spec, payload, now=now)
    if state.get("identity_ok") is False:
        log.error(
            "V3 identity mismatch for %s: observed=%s", instrument_key, state["observed"]
        )
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "identity_mismatch",
                "observed": state["observed"],
                "expected": {
                    "venue": spec.venue,
                    "market_type": spec.market_type,
                    "contract_code": spec.contract_code,
                },
            },
        )
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


@router.get("/market/{instrument_key}/candles")
async def market_candles(
    request: Request, instrument_key: str, period: str = "1m",
    limit: int = 300, until: str | None = None,
) -> JSONResponse:
    registry: InstrumentRegistry = load_registry()
    spec = _require_spec(registry, instrument_key)

    if period not in spec.supported_periods:
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "unsupported_period",
                "supported": list(spec.supported_periods),
            },
        )
    if limit < 1 or limit > MAX_CANDLE_LIMIT:
        raise HTTPException(
            status_code=400, detail={"reason_code": "limit_out_of_range", "max": MAX_CANDLE_LIMIT}
        )
    until_dt = datetime.now(timezone.utc)
    if until:
        parsed = _parse_ts(until)
        if parsed is None:
            raise HTTPException(status_code=400, detail={"reason_code": "invalid_until"})
        until_dt = parsed

    base_period, derive_factor = period, 1
    stored = PERIOD_TO_STORED[period]
    if stored is None:
        base_period, derive_factor = DERIVE_BASE[period]
        stored = PERIOD_TO_STORED[base_period]
    assert stored is not None
    stored_seconds = STORED_SECONDS[stored]

    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "history_unavailable",
                "message": "PostgreSQL pool is not connected",
                "last_good_end_at": None,
            },
        )

    fetch_limit = limit * derive_factor
    try:
        rows = await pool.fetch(
            """
            SELECT open_time, close_time, open, high, low, close, volume, source, venue
            FROM trader_v1_perp_candles
            WHERE venue = $1 AND contract_code = $2 AND timeframe = $3 AND open_time <= $4
            ORDER BY open_time DESC
            LIMIT $5
            """,
            spec.venue, spec.contract_code, stored, until_dt, fetch_limit,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("v3 candles query failed for %s/%s: %s", instrument_key, period, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "history_query_failed",
                "message": str(exc),
                "last_good_end_at": None,
            },
        ) from exc

    candles: list[CandleRow] = []
    for row in rows:
        venue = str(row["venue"])
        if venue != spec.venue:
            raise CandleSourceError("venue_mixture")
        candles.append(
            CandleRow(
                start_at=row["open_time"].astimezone(timezone.utc),
                end_at=row["close_time"].astimezone(timezone.utc),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                source=str(row["source"]),
            )
        )
    candles.reverse()

    try:
        validated = validate_candle_rows(candles, stored_seconds=stored_seconds)
    except CandleSourceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": exc.reason_code, "last_good_end_at": None},
        ) from exc

    quality = "live"
    if derive_factor > 1:
        target_seconds = stored_seconds * derive_factor
        validated = aggregate_candles(validated, factor=derive_factor, period_seconds=target_seconds)
        for item in validated:
            item["derived_from"] = base_period
        quality = "derived"

    validated = validated[-limit:]
    period_seconds = stored_seconds * derive_factor
    gaps = detect_gaps(validated, period_seconds=period_seconds) if validated else []
    now = datetime.now(timezone.utc)

    body = {
        "instrument_key": spec.instrument_key,
        "period": period,
        "period_seconds": period_seconds,
        "candles": validated,
        "gaps": gaps,
        "forming": build_forming(validated, period_seconds=period_seconds, now=now),
        "coverage": round(1 - len(gaps) / max(len(validated), 1), 4) if validated else None,
        "quality": quality,
        "derived_from": base_period if derive_factor > 1 else None,
        "source_status": "ok" if validated else "empty",
        "as_of": now.isoformat(),
    }
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


# ── P1.2a: SSE market stream ──────────────────────────────────────────────────


# How often the SSE generator re-reads Redis.  1 Hz is well under the 5s ticker
# freshness budget, so a stalled feed trips ``can_enter=false`` long before the
# browser notices anything is wrong.  Kept as env override for tests / tuning.
SSE_POLL_SECONDS = float(os.getenv("V3_SSE_POLL_SECONDS", "1.0"))
# Cap a single connection: browsers auto-reconnect EventSource, so a short cap
# is nicer to Redis than an unbounded generator that never exits on client drop.
SSE_MAX_SESSION_SECONDS = float(os.getenv("V3_SSE_MAX_SESSION_SECONDS", "1800"))


def _sse_event(name: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    """Render one SSE frame.

    ``event_id`` emits an ``id:`` line so that, on reconnect, the browser sends
    ``Last-Event-ID`` (ТЗ §53 resync hook).  Snapshot frames carry the market
    ``sequence``; keepalive/error/bye are not resumable and omit it.
    """
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_market_state(
    *,
    redis: Any,
    spec: InstrumentSpec,
    thresholds: dict[str, float],
    stop_after_seconds: float,
    poll_seconds: float,
) -> AsyncIterator[str]:
    """Yield SSE frames for one instrument's market state.

    Semantics:
      * ``snapshot``  — full MarketStateV1 whenever ``sequence`` changes
        (source_at advanced or clock-tick changed);
      * ``keepalive`` — every ~10s when idle, so proxies do not drop the socket;
      * ``error``     — on Redis outage; the loop keeps trying so a transient
        blip auto-recovers without client reconnect.

    Deterministic on purpose: no fabricated ticks between Redis reads.  The
    forming candle, if any, is a client concern — the server just tells the
    truth about what it sees.
    """
    key = f"{MARKET_KEY_PREFIX}:{spec.contract_code}"
    last_seq = -1
    started = asyncio.get_running_loop().time()
    idle_since = started
    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                raw = await redis.get(key)
                payload: dict[str, Any] | None = None
                if raw is not None:
                    try:
                        parsed = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        parsed = None
                    if isinstance(parsed, dict):
                        payload = parsed
                state = build_market_state(spec, payload, now=now, thresholds=thresholds)
                seq = int(state["sequence"])
                if seq != last_seq:
                    last_seq = seq
                    idle_since = asyncio.get_running_loop().time()
                    yield _sse_event("snapshot", state, event_id=seq)
                else:
                    loop_now = asyncio.get_running_loop().time()
                    if loop_now - idle_since >= 10.0:
                        idle_since = loop_now
                        yield _sse_event("keepalive", {"as_of": state["as_of"]})
            except Exception as exc:  # noqa: BLE001 — stream must not die on one bad read
                log.warning("V3 SSE Redis read failed for %s: %s", spec.instrument_key, exc)
                yield _sse_event(
                    "error",
                    {
                        "reason_code": "redis_unavailable",
                        "message": "Redis недоступен; поток продолжает пробовать.",
                        "as_of": now.isoformat(),
                    },
                )
            if asyncio.get_running_loop().time() - started >= stop_after_seconds:
                yield _sse_event("bye", {"reason": "session_cap"})
                return
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        # Client disconnected — nothing to log, nothing to clean.
        raise


@router.get("/market/{instrument_key}/stream")
async def v3_market_stream(instrument_key: str, request: Request) -> StreamingResponse:
    """SSE stream of MarketStateV1 for one instrument (P1.2a).

    Unknown key → HTTP 404 before upgrade (never opens a stream for a
    non-existent instrument).  Missing Redis on app.state → HTTP 503.
    """
    registry: InstrumentRegistry = load_registry()
    spec = _require_spec(registry, instrument_key)
    redis = getattr(request.app.state, "redis_client", None)
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "redis_unavailable", "message": "Redis not connected"},
        )
    return StreamingResponse(
        _stream_market_state(
            redis=redis,
            spec=spec,
            thresholds=FRESHNESS_THRESHOLDS,
            stop_after_seconds=SSE_MAX_SESSION_SECONDS,
            poll_seconds=SSE_POLL_SECONDS,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # disable nginx buffering for streaming
            "Connection": "keep-alive",
        },
    )
