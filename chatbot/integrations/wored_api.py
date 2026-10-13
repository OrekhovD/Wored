"""Typed read models for the Telegram console (ТЗ Telegram Bot V1 §6).

This is the only place where the bot touches market/forecast/session data, and
it is read-only by construction:

* market comes from the same Redis key the collector writes and the WebUI reads,
  validated by the shared domain helpers in ``paper_trading.market``;
* forecast comes from ``storage.postgres_client``, i.e. the same read model the
  dashboard prediction card uses;
* session/account state comes from ``paper_trading.adapter``, i.e. the domain
  service, never from a bot-local table.

The bot never recomputes a price, a P&L or a balance.  When a source is missing,
stale or invalid the DTO keeps the last confirmed value plus a reason code and
``quality``; absence is never rendered as ``0``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

log = logging.getLogger("chatbot.integrations")

SCHEMA_VERSION = 1

QUALITY_LIVE = "live"
QUALITY_STALE = "stale"
QUALITY_MISSING = "missing"
QUALITY_ERROR = "error"

DEFAULT_CONTRACT = "BTC-USDT"
DEFAULT_MAX_AGE_SECONDS = 5.0

#: commands and cards must state units and provenance explicitly (ТЗ §0, §2)
USDT = "USDT"


def default_contract() -> str:
    """First configured simulated contract.

    ``PAPER_CONTRACTS`` is the canonical key already used by the collector
    (``collector/htx/perpetual_market.py``); ``BOT_DEFAULT_INSTRUMENT`` lets the
    console point elsewhere without changing ingestion.
    """
    configured = os.getenv("BOT_DEFAULT_INSTRUMENT", "").strip()
    if configured:
        return configured.upper()
    first = os.getenv("PAPER_CONTRACTS", DEFAULT_CONTRACT).split(",")[0].strip()
    return (first or DEFAULT_CONTRACT).upper()


def market_key(instrument: str) -> str:
    """Redis-ключ одного perpetual-стакана.

    Префикс берётся из ``trading_math.market_state.MARKET_KEY_PREFIX`` — того же,
    что читает collector и планировщик планов, поэтому у строки ключа в боте
    одно определение, а не три.
    """
    from trading_math.market_state import MARKET_KEY_PREFIX

    return f"{MARKET_KEY_PREFIX}:{(instrument or default_contract()).upper()}"


def market_max_age() -> float:
    """Freshness window, same rule and bounds as ``webui/paper_market.py``."""
    try:
        value = float(os.getenv("PAPER_MARKET_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS)))
    except ValueError:
        value = DEFAULT_MAX_AGE_SECONDS
    return value if 0 < value <= 60 else DEFAULT_MAX_AGE_SECONDS


def _dec(value: Any) -> str | None:
    """Decimal → plain string; ``None`` stays ``None`` (never "0")."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def enum_value(value: Any) -> Any:
    """Enum → its value, everything else unchanged.

    ``paper_trading.service.get_current_state`` puts a raw ``DayState`` member in
    ``day["state"]`` (its report endpoints use ``.value``).  ``str(enum)`` renders
    ``"DayState.running"`` while JSON renders ``"running"``, so the WebUI and a
    naive ``str()`` would disagree: the card would show an internal type name
    instead of the state the dashboard shows.
    """
    return getattr(value, "value", value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(iso: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds between an ISO timestamp and now (negative = clock ahead)."""
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now or _utc_now()).timestamp() - parsed.timestamp()


# ─── Market ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketSnapshotV1:
    schema_version: int
    instrument: str
    venue: str
    market_type: str
    last: str | None
    bid: str | None
    ask: str | None
    mark: str | None
    index: str | None
    funding_rate: str | None
    next_funding_at: str | None
    price_tick: str | None
    quantity_step: str | None
    source: str
    source_quality: str
    quality: str
    as_of: str | None
    age_seconds: float | None
    is_fresh: bool
    unit: str
    reason_codes: tuple[str, ...] = ()
    raw_available: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def spread(self) -> str | None:
        if self.bid is None or self.ask is None:
            return None
        try:
            return format(Decimal(self.ask) - Decimal(self.bid), "f")
        except (ValueError, ArithmeticError):
            return None


async def read_market_snapshot(
    contract_code: str | None = None,
    *,
    redis_client: Any = None,
    max_age: float | None = None,
) -> MarketSnapshotV1:
    """Read one perpetual snapshot through the shared domain validators.

    Карточка и ручной тикет читают один и тот же снимок: форма DTO остаётся
    здесь, а чтение и валидация — в :func:`read_perpetual_snapshot`.
    """
    read = await read_perpetual_snapshot(
        contract_code, redis_client=redis_client, max_age=max_age
    )
    snapshot = read.snapshot
    if snapshot is None:
        # Absence keeps the feed's own source strings when the payload carried
        # them, so the card can say when the feed was last trustworthy (ТЗ §2).
        payload = read.payload
        return MarketSnapshotV1(
            schema_version=SCHEMA_VERSION, instrument=read.instrument, venue="htx",
            market_type="linear-swap", last=None, bid=None, ask=None, mark=None,
            index=None, funding_rate=None, next_funding_at=None, price_tick=None,
            quantity_step=None, source=str(payload.get("source") or "redis"),
            source_quality=str(payload.get("quality") or ""), quality=read.quality,
            as_of=read.as_of, age_seconds=read.age_seconds, is_fresh=False, unit=USDT,
            reason_codes=read.reason_codes,
        )
    return MarketSnapshotV1(
        schema_version=snapshot.schema_version,
        instrument=snapshot.contract_code,
        venue=snapshot.venue,
        market_type=snapshot.market_type,
        last=_dec(snapshot.last),
        bid=_dec(snapshot.bid),
        ask=_dec(snapshot.ask),
        mark=_dec(snapshot.mark),
        index=_dec(snapshot.index),
        funding_rate=_dec(snapshot.funding_rate),
        next_funding_at=snapshot.next_funding_at,
        price_tick=_dec(snapshot.price_tick),
        quantity_step=_dec(snapshot.quantity_step),
        source=snapshot.source,
        source_quality=snapshot.quality,
        quality=read.quality,
        as_of=snapshot.source_at,
        age_seconds=read.age_seconds,
        is_fresh=read.is_fresh,
        unit=USDT,
        reason_codes=read.reason_codes,
    )


@dataclass(frozen=True)
class PerpetualSnapshotReadV1:
    """Доменный снимок для ручной сделки: тот же тип, что принимает риск-гейт.

    ``snapshot`` — экземпляр ``paper_trading.market.PerpetualSnapshot`` или
    ``None``; ``payload`` — разобранный JSON, чтобы читатель карточки мог назвать
    источник отказа, не выдумывая его. DTO намеренно тонкий: считать цену по
    этому снимку может только домен.
    """

    schema_version: int
    instrument: str
    snapshot: Any | None
    quality: str
    as_of: str | None
    age_seconds: float | None
    is_fresh: bool
    reason_codes: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict, repr=False)


async def read_perpetual_snapshot(
    contract_code: str | None = None,
    *,
    redis_client: Any = None,
    max_age: float | None = None,
) -> PerpetualSnapshotReadV1:
    """Прочитать стакан как доменный ``PerpetualSnapshot``.

    Единственный читатель бота, который отдаёт сам тип, а не карточку: ручной
    тикет собирается доменным ``build_ticket``, и он принимает только доменный
    снимок (в ``webui.paper_market`` живёт одноимённый, но другой класс).
    Свежесть здесь только сообщается, но не запрещается: единственный veto
    протухшего стакана — ``paper_trading.risk.check_order_risk``.
    """
    from paper_trading.market import is_fresh, snapshot_from_dict

    instrument = (contract_code or default_contract()).upper()
    window = market_max_age() if max_age is None else float(max_age)

    def _refused(
        quality: str, codes: tuple[str, ...], payload: dict[str, Any]
    ) -> PerpetualSnapshotReadV1:
        as_of = str(payload.get("source_at") or "") or None
        return PerpetualSnapshotReadV1(
            schema_version=SCHEMA_VERSION, instrument=instrument, snapshot=None,
            quality=quality, as_of=as_of, age_seconds=_age_seconds(as_of),
            is_fresh=False, reason_codes=codes, payload=payload,
        )

    if redis_client is None:
        from storage.redis_client import get_redis

        redis_client = get_redis()

    try:
        raw = await redis_client.get(market_key(instrument))
    except Exception as exc:  # transport failure is an error, not an absence
        log.warning("perpetual snapshot read failed for %s: %s", instrument, exc)
        return _refused(QUALITY_ERROR, ("MARKET_BACKEND_UNAVAILABLE",), {})
    if raw is None:
        return _refused(QUALITY_MISSING, ("MARKET_MISSING",), {})

    payload = _as_dict(raw)
    try:
        snapshot = snapshot_from_dict(payload, expected_contract=instrument)
    except (ValueError, TypeError) as exc:
        log.warning("perpetual snapshot rejected for %s: %s", instrument, exc)
        return _refused(QUALITY_ERROR, ("MARKET_INVALID",), payload)

    fresh = is_fresh(snapshot, max_age=window)
    return PerpetualSnapshotReadV1(
        schema_version=SCHEMA_VERSION,
        instrument=instrument,
        snapshot=snapshot,
        quality=QUALITY_LIVE if fresh else QUALITY_STALE,
        as_of=snapshot.source_at,
        age_seconds=_age_seconds(snapshot.source_at),
        is_fresh=fresh,
        reason_codes=() if fresh else ("MARKET_STALE",),
        payload=payload,
    )


def _as_dict(raw: Any) -> dict[str, Any]:
    import json

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


# ─── Forecast ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForecastSummaryV1:
    schema_version: int
    forecast_id: int | None
    symbol: str
    horizon_hours: int | None
    base_price: str | None
    base_time: str | None
    predicted_close: str | None
    predicted_change_pct: str | None
    target_at: str | None
    model_name: str | None
    status: str
    quality: str
    as_of: str | None
    age_seconds: float | None
    unit: str
    completed_models: int = 0
    failed_models: int = 0
    evaluated_points: int = 0
    total_points: int = 0
    avg_accuracy: str | None = None
    range_low: str | None = None
    range_high: str | None = None
    range_method: str | None = None
    reason_codes: tuple[str, ...] = ()


async def read_latest_forecast(contract_code: str | None = None) -> ForecastSummaryV1:
    """Latest forecast for the console card, via the dashboard's own read model."""
    from storage.postgres_client import get_latest_prediction_request

    instrument = (contract_code or default_contract()).upper()
    symbol = instrument.replace("-", "").lower()
    empty = ForecastSummaryV1(
        schema_version=SCHEMA_VERSION, forecast_id=None, symbol=symbol,
        horizon_hours=None, base_price=None, base_time=None, predicted_close=None,
        predicted_change_pct=None, target_at=None, model_name=None, status="none",
        quality=QUALITY_MISSING, as_of=None, age_seconds=None, unit=USDT,
        reason_codes=("FORECAST_NONE",),
    )

    try:
        detail = await get_latest_prediction_request(symbol=symbol)
    except Exception as exc:
        log.warning("forecast read failed for %s: %s", symbol, exc)
        return ForecastSummaryV1(
            schema_version=SCHEMA_VERSION, forecast_id=None, symbol=symbol,
            horizon_hours=None, base_price=None, base_time=None, predicted_close=None,
            predicted_change_pct=None, target_at=None, model_name=None, status="error",
            quality=QUALITY_ERROR, as_of=None, age_seconds=None, unit=USDT,
            reason_codes=("FORECAST_BACKEND_UNAVAILABLE",),
        )

    if not detail:
        return empty

    reasons: list[str] = []
    models = detail.get("models") or []
    last_point: dict[str, Any] | None = None
    model_name: str | None = None
    for model in models:
        if model.get("status") != "completed":
            continue
        points = model.get("points") or []
        if points:
            last_point = points[-1]
            model_name = model.get("model_name")
            break
    if last_point is None and models:
        reasons.append("FORECAST_NO_POINTS")
    if model_name is None:
        for model in models:
            if model.get("model_name"):
                model_name = model["model_name"]
                break

    as_of = detail.get("created_at")
    age = _age_seconds(as_of)
    target_at = (last_point or {}).get("target_time")
    status = str(detail.get("status") or "unknown")

    # The window having passed makes the forecast history, not a forward value.
    if target_at:
        try:
            target_dt = datetime.fromisoformat(str(target_at))
        except ValueError:
            target_dt = None
        if target_dt is not None and target_dt <= _utc_now():
            status = "expired" if status == "completed" else status
            reasons.append("FORECAST_STALE")
    elif status == "completed":
        reasons.append("FORECAST_NO_TARGET_TIME")

    if status == "failed":
        quality = QUALITY_ERROR
    elif reasons:
        quality = QUALITY_STALE
    else:
        quality = QUALITY_LIVE

    avg_accuracy = detail.get("avg_accuracy")
    return ForecastSummaryV1(
        schema_version=SCHEMA_VERSION,
        forecast_id=detail.get("id"),
        symbol=symbol,
        horizon_hours=detail.get("horizon_hours"),
        base_price=_dec(detail.get("base_price")),
        base_time=as_of,
        predicted_close=_dec((last_point or {}).get("predicted_price")),
        predicted_change_pct=_dec((last_point or {}).get("predicted_change_pct")),
        target_at=target_at,
        model_name=model_name,
        status=status,
        quality=quality,
        as_of=as_of,
        age_seconds=age,
        unit=USDT,
        completed_models=int(detail.get("completed_models") or 0),
        failed_models=int(detail.get("failed_models") or 0),
        evaluated_points=int(detail.get("evaluated_points") or 0),
        total_points=int(detail.get("total_points") or 0),
        avg_accuracy=_dec(avg_accuracy),
        # No calibrated band exists in the shared read model yet: show N/A, do
        # not derive a pseudo-range from confidence (ТЗ §U03 forbids invented bars).
        reason_codes=tuple(reasons) or (),
    )


# ─── Session / accounts ────────────────────────────────────────────────


@dataclass(frozen=True)
class PositionReadV1:
    """Open position exactly as the domain reports it (ТЗ DTO ``PositionV1``).

    Only fields the domain already computes are carried: no bot-side P&L, no
    unrealized value, no liquidation estimate.
    """

    position_id: str
    side: str
    qty: str | None
    avg_entry: str | None
    stop: str | None
    target: str | None
    unit: str = USDT


@dataclass(frozen=True)
class AccountReadV1:
    account_id: str
    kind: str
    currency: str
    cash: str | None
    open_positions: int
    positions: tuple[PositionReadV1, ...] = ()


@dataclass(frozen=True)
class SessionStateV1:
    schema_version: int
    day_id: str | None
    state: str
    started_at: str | None
    ends_at: str | None
    accounts: tuple[AccountReadV1, ...]
    open_positions: int
    next_action: str
    quality: str
    as_of: str | None
    reason_codes: tuple[str, ...] = ()

    @property
    def has_session(self) -> bool:
        return self.day_id is not None


async def read_session_state(owner_id: str) -> SessionStateV1:
    """Trading-day state straight from the ``paper_trading`` domain service."""
    from paper_trading.adapter import get_current_state

    as_of = _utc_now().isoformat()
    try:
        state = await get_current_state(owner_id)
    except Exception as exc:
        log.warning("session read failed: %s", exc)
        return SessionStateV1(
            schema_version=SCHEMA_VERSION, day_id=None, state="unknown",
            started_at=None, ends_at=None, accounts=(), open_positions=0,
            next_action="retry", quality=QUALITY_ERROR, as_of=as_of,
            reason_codes=("SESSION_BACKEND_UNAVAILABLE",),
        )

    if not state.get("ok"):
        return SessionStateV1(
            schema_version=SCHEMA_VERSION, day_id=None, state="unknown",
            started_at=None, ends_at=None, accounts=(), open_positions=0,
            next_action="retry", quality=QUALITY_ERROR, as_of=as_of,
            reason_codes=(f"SESSION_{str(state.get('error') or 'error').upper()}",),
        )

    day = state.get("day") or None
    accounts = tuple(
        AccountReadV1(
            account_id=str(a.get("id")),
            kind=str(a.get("kind")),
            currency=str(a.get("currency") or USDT),
            cash=_dec(a.get("cash")),
            open_positions=int(a.get("open_positions") or 0),
            positions=tuple(
                PositionReadV1(
                    position_id=str(p.get("id")),
                    side=str(p.get("side") or ""),
                    qty=_dec(p.get("qty")),
                    avg_entry=_dec(p.get("avg_entry")),
                    stop=_dec(p.get("stop")),
                    target=_dec(p.get("target")),
                    unit=str(a.get("currency") or USDT),
                )
                for p in (a.get("positions") or [])
            ),
        )
        for a in (state.get("accounts") or [])
    )
    open_positions = sum(a.open_positions for a in accounts)

    if day is None:
        return SessionStateV1(
            schema_version=SCHEMA_VERSION, day_id=None, state="none",
            started_at=None, ends_at=None, accounts=accounts, open_positions=open_positions,
            next_action=str(state.get("next_action") or "start"), quality=QUALITY_MISSING,
            as_of=as_of, reason_codes=("SESSION_NONE",),
        )

    return SessionStateV1(
        schema_version=SCHEMA_VERSION,
        day_id=str(day.get("id")),
        state=str(enum_value(day.get("state")) or "unknown"),
        started_at=day.get("start_at"),
        ends_at=day.get("end_at"),
        accounts=accounts,
        open_positions=open_positions,
        next_action=str(state.get("next_action") or "trade"),
        quality=QUALITY_LIVE,
        as_of=as_of,
    )
