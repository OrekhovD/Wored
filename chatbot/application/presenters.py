"""Card presenters for the Telegram console (ТЗ Telegram Bot V1 §2, §5).

A card is a fixed contract: ``status / title / body / as_of / source / quality /
entity_ids / next_actions / request_id``.  Presenters only format read models
produced in :mod:`integrations.wored_api`; they never compute a financial value.

Formatting rules that the ТЗ makes explicit:

* absence renders as ``N/A``, never as ``0``;
* stale data keeps the last confirmed value plus its age and reason;
* every number carries a unit and every timestamp carries a zone;
* everything that came from outside (model names, source strings, states) is
  HTML-escaped before it reaches a message.
"""

from __future__ import annotations

import html
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger("chatbot.application")

NA = "N/A"

try:  # zoneinfo is stdlib on 3.9, but tzdata may be absent in a slim image
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def esc(value: Any) -> str:
    """Escape any foreign text before it goes into an HTML message."""
    return html.escape(str(value if value is not None else NA), quote=False)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# ─── Card contract ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Card:
    status: str
    title: str
    body: str
    as_of: str | None
    source: str
    quality: str
    entity_ids: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    request_id: str = ""

    def render(self) -> str:
        return f"<b>{self.title}</b>\n{self.body}"


# ─── Value formatting ──────────────────────────────────────────────────


def money(value: Any, unit: str = "USDT", *, dp: int = 2) -> str:
    """Group and round for display only; the domain value stays a Decimal."""
    if value is None:
        return NA
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return NA
    return f"{number:,.{dp}f} {unit}"


def pct(value: Any, *, dp: int = 2, signed: bool = True) -> str:
    if value is None:
        return NA
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return NA
    sign = "+" if signed else ""
    return f"{number:{sign}.{dp}f}%"


def plain(value: Any) -> str:
    return esc(value) if value not in (None, "") else NA


def zone_label(tz_name: str) -> str:
    """Short zone suffix for the header, falling back to UTC if tzdata is absent."""
    if ZoneInfo is not None:
        try:
            ZoneInfo(tz_name)
            return tz_name
        except Exception:  # bad key or missing tzdata
            pass
    return "UTC"


def format_time(iso: Any, tz_name: str) -> tuple[str, str]:
    """Return ``(clock, zone)`` for an ISO timestamp, never a bare number."""
    if not iso:
        return NA, NA
    raw = str(iso)
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return esc(raw[:19]), "ISO"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if ZoneInfo is not None:
        try:
            moment = moment.astimezone(ZoneInfo(tz_name))
        except Exception:
            moment = moment.astimezone(timezone.utc)
            tz_name = "UTC"
    else:
        moment = moment.astimezone(timezone.utc)
        tz_name = "UTC"
    suffix = _offset_suffix(moment)
    return moment.strftime("%H:%M:%S"), f"{suffix} ({tz_name})"


def _offset_suffix(moment) -> str:
    """Fixed offset label from ``utcoffset()``, never from ``strftime`` slicing.

    The live card said ``14:26:29 (UTC+0 (Asia/Bangkok))``: slicing ``"+0700"
    [:-3]`` produced ``"+0"``, so a correct Bangkok clock carried a false UTC+0
    label.  An explicit timezone is only worth printing if it is arithmetically
    true, so the offset is taken from the tzinfo itself (whole hours and
    half-hour zones both covered).
    """
    delta = moment.utcoffset()
    total = 0 if delta is None else int(delta.total_seconds())
    if total == 0:
        return "UTC"
    sign = "+" if total > 0 else "-"
    hours, remainder = divmod(abs(total), 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def age_text(age_seconds: Any) -> str:
    if age_seconds is None:
        return NA
    try:
        seconds = float(age_seconds)
    except (TypeError, ValueError):
        return NA
    if seconds < 0:
        return f"{abs(seconds):.0f} с впереди (часы)"
    if seconds < 90:
        return f"{seconds:.0f} с"
    if seconds < 5400:
        return f"{seconds / 60:.0f} мин"
    return f"{seconds / 3600:.1f} ч"


def quality_badge(quality: str) -> str:
    return {
        "live": "🟢",
        "stale": "🟠",
        "missing": "⚪",
        "error": "🔴",
    }.get(quality, "⚪")


# ─── The «Сейчас» card ─────────────────────────────────────────────────


QUALITY_WORDS = {
    "live": "свежие",
    "stale": "устаревшие",
    "missing": "нет данных",
    "error": "ошибка источника",
}

#: Actions that exist in the read slice; anything else is declared, not faked.
NAV_ACTIONS = ("now", "forecast", "session", "positions", "report", "more")


def _reason_suffix(reason_codes: tuple[str, ...]) -> str:
    if not reason_codes:
        return ""
    return " · " + ", ".join(esc(code) for code in reason_codes)


#: public alias — screens may render a reason list without reaching into privates
reason_text = _reason_suffix


def build_now_card(
    market: Any,
    forecast: Any,
    session: Any,
    *,
    tz_name: str,
    mode_label: str,
    request_id: str | None = None,
) -> Card:
    """One main card: market · forecast · session · risk (ТЗ §2)."""
    clock, zone = format_time(market.as_of, tz_name)

    header = (
        f"WORED · {esc(market.venue.upper())} {esc(market.instrument)} "
        f"{esc(market.market_type)} · {esc(mode_label)}"
    )

    lines: list[str] = []

    # ── Рынок ──
    if market.quality == "live" or market.is_fresh:
        price = money(market.last, market.unit, dp=1)
        mark = money(market.mark, market.unit, dp=1)
        lines.append(
            f"{quality_badge(market.quality)} Рынок: {price} | mark {mark} | "
            f"обновлено {clock} ({esc(zone)}), {age_text(market.age_seconds)} назад"
        )
    else:
        last_known = money(market.last, market.unit, dp=1)
        lines.append(
            f"{quality_badge(market.quality)} Рынок: последнее достоверное {last_known} "
            f"от {clock} ({esc(zone)}), {age_text(market.age_seconds)} назад · "
            f"{esc(QUALITY_WORDS.get(market.quality, market.quality))}"
            f"{_reason_suffix(market.reason_codes)}"
        )

    # ── Прогноз ──
    if forecast.forecast_id is None:
        lines.append(
            f"{quality_badge(forecast.quality)} Прогноз: нет проверенного прогноза"
            f"{_reason_suffix(forecast.reason_codes)}"
        )
    else:
        change = pct(forecast.predicted_change_pct)
        target = money(forecast.predicted_close, forecast.unit, dp=1)
        f_clock, f_zone = format_time(forecast.target_at, tz_name)
        band = NA if forecast.range_low is None else (
            f"{money(forecast.range_low, forecast.unit, dp=0)}–"
            f"{money(forecast.range_high, forecast.unit, dp=0)}"
        )
        lines.append(
            f"{quality_badge(forecast.quality)} Прогноз {esc(forecast.horizon_hours)}ч: "
            f"{change} · {target} · диапазон {band} · до {f_clock} ({esc(f_zone)}) · "
            f"id <code>{esc(forecast.forecast_id)}</code> · {esc(forecast.status)}"
            f"{_reason_suffix(forecast.reason_codes)}"
        )

    # ── Сессия ──
    if not session.has_session:
        lines.append(
            f"{quality_badge(session.quality)} Сессия: не запущена"
            f"{_reason_suffix(session.reason_codes)}"
        )
    else:
        parts = []
        for account in session.accounts:
            parts.append(
                f"{esc(account.kind)} {money(account.cash, account.currency)}"
            )
        accounts_text = " / ".join(parts) if parts else NA
        s_clock, s_zone = format_time(session.ends_at, tz_name)
        lines.append(
            f"{quality_badge(session.quality)} Сессия: {esc(session.state.upper())} · "
            f"id <code>{esc((session.day_id or '')[:8])}</code> · {accounts_text} · "
            f"до {s_clock} ({esc(s_zone)})"
        )

    # ── Риск ──
    # Only counts and domain-provided balances are shown.  Drawdown and P&L are
    # derived by the domain/ledger, so until that read model exists they are N/A,
    # never a bot-side formula (ТЗ §0 forbids the bot recomputing P&L).
    lines.append(
        f"Риск: {session.open_positions} откр. поз. · просадка {NA} · "
        f"лимит {NA} <i>(доменное read-model появится в P4/P5)</i>"
    )

    # ── Источник / качество ──
    lines.append(
        f"Источник: {esc(market.source)} · данные: "
        f"{esc(QUALITY_WORDS.get(market.quality, market.quality))}"
    )

    entity_ids = tuple(
        item for item in (
            str(forecast.forecast_id or ""),
            session.day_id or "",
        ) if item
    )

    status = "ok" if market.quality == "live" else market.quality
    return Card(
        status=status,
        title=header,
        body="\n".join(lines),
        as_of=market.as_of or session.as_of,
        source=f"{market.venue}:{market.instrument}",
        quality=market.quality,
        entity_ids=entity_ids,
        next_actions=NAV_ACTIONS,
        request_id=request_id or new_request_id(),
    )


def build_market_detail_card(market: Any, *, tz_name: str) -> Card:
    """U02 detail block: bid/ask/mark/index/funding and per-component age."""
    clock, zone = format_time(market.as_of, tz_name)
    funding_pct = None
    if market.funding_rate is not None:
        try:
            funding_pct = format(Decimal(str(market.funding_rate)) * 100, "f")
        except (InvalidOperation, ValueError):
            funding_pct = None
    nf_clock, nf_zone = format_time(market.next_funding_at, tz_name)

    lines = [
        f"Инструмент: {esc(market.instrument)} · {esc(market.market_type)} · {esc(market.venue)}",
        f"last {money(market.last, market.unit, dp=1)} · bid {money(market.bid, market.unit, dp=1)} · "
        f"ask {money(market.ask, market.unit, dp=1)}",
        f"mark {money(market.mark, market.unit, dp=1)} · index {money(market.index, market.unit, dp=1)} · "
        f"spread {money(market.spread, market.unit, dp=1)}",
        f"funding {pct(funding_pct, dp=4)} · следующий {nf_clock} ({esc(nf_zone)})",
        f"шаг цены {plain(market.price_tick)} · шаг объёма {plain(market.quantity_step)}",
        f"обновлено {clock} ({esc(zone)}) · {age_text(market.age_seconds)} назад · "
        f"свежесть: {esc(QUALITY_WORDS.get(market.quality, market.quality))}"
        f"{_reason_suffix(market.reason_codes)}",
        f"источник: {esc(market.source)} (feed quality: {plain(market.source_quality)})",
    ]
    return Card(
        status="ok" if market.is_fresh else market.quality,
        title=f"📊 Рынок · {esc(market.instrument)}",
        body="\n".join(lines),
        as_of=market.as_of,
        source=f"{market.venue}:{market.instrument}",
        quality=market.quality,
        next_actions=NAV_ACTIONS,
        request_id=new_request_id(),
    )


def build_forecast_card(forecast: Any, market: Any, *, tz_name: str) -> Card:
    """U03 forecast block; explains what is known and what is not."""
    lines: list[str] = []
    if forecast.forecast_id is None:
        lines.append("Нет проверенного прогноза для этого инструмента.")
        lines.append("Расчёт прогноза — экран «Прогноз» (запрос асинхронный).")
        lines.append(f"{_reason_suffix(forecast.reason_codes)}".strip())
    else:
        b_clock, b_zone = format_time(forecast.base_time, tz_name)
        t_clock, t_zone = format_time(forecast.target_at, tz_name)
        lines.append(f"instrument: {esc(forecast.symbol)} · горизонт {esc(forecast.horizon_hours)} ч")
        lines.append(
            f"факт base: {money(forecast.base_price, forecast.unit, dp=1)} от {b_clock} ({esc(b_zone)})"
        )
        lines.append(
            f"прогноз: {money(forecast.predicted_close, forecast.unit, dp=1)} · "
            f"{pct(forecast.predicted_change_pct)} · цель {t_clock} ({esc(t_zone)})"
        )
        lines.append(f"диапазон: {NA} (калиброванный коридор пока не в общем read-model)")
        lines.append(f"статус: {esc(forecast.status)} · моделей готово {forecast.completed_models}, "
                     f"сбой {forecast.failed_models}")
        lines.append(
            f"точек проверено: {forecast.evaluated_points} из {forecast.total_points} · "
            f"accuracy: {pct(forecast.avg_accuracy) if forecast.avg_accuracy else NA}"
        )
        if forecast.model_name:
            lines.append(f"модель: {esc(forecast.model_name)}")
        if market.is_fresh and forecast.base_price and market.mark:
            try:
                drift = (Decimal(str(market.mark)) - Decimal(str(forecast.base_price)))
                lines.append(
                    f"расхождение base↔текущий mark: {money(drift, forecast.unit, dp=1)}"
                )
            except (InvalidOperation, ValueError):
                pass
        lines.append(f"причины: {', '.join(esc(c) for c in forecast.reason_codes) or 'нет'}")

    return Card(
        status="ok" if forecast.forecast_id else "empty",
        title=f"🔮 Прогноз · {esc(forecast.symbol)}",
        body="\n".join(lines),
        as_of=forecast.as_of,
        source="forecast_requests",
        quality=forecast.quality,
        entity_ids=(str(forecast.forecast_id),) if forecast.forecast_id else (),
        next_actions=NAV_ACTIONS,
        request_id=new_request_id(),
    )


def build_help_card(mode_label: str, tz_name: str, *, market_max_age: float = 5.0) -> Card:
    """U01 / U11 — what the bot is, what it is not, and what each number means."""
    lines = [
        f"Режим: <b>{esc(mode_label)}</b>. Бот не отправляет заявки на биржу и не обещает доходность.",
        "Каждое число — из того же источника, что и WebUI: рынок из Redis-снимка HTX perpetual, "
        "прогноз из forecast_requests, сессия из сервиса paper_trading.",
        "«Факт» — закрытое значение источника; «прогноз» — модельное значение с временем расчёта. "
        "Они не складываются.",
        "Команды: /now /market /forecast /session /positions /report /alerts /plans /deal "
        "/help /cancel.",
        f"Задержка рынка: снимок старше {market_max_age:.0f} с помечается stale и показывается "
        f"с возрастом и причиной; время — в {esc(tz_name)}.",
        "Комиссия и funding учитываются доменом в отчёте сессии, не в этой карточке.",
        "Стоимость AI: в V1 платные маршруты выключены (BOT_ENABLE_PAID_AI=false).",
        "Инцидент: /now покажет качество источника и причину; детали — «Ещё» → WebUI.",
    ]
    return Card(
        status="ok",
        title="ℹ️ WORED Console · помощь",
        body="\n".join(lines),
        as_of=datetime.now(timezone.utc).isoformat(),
        source="bot",
        quality="live",
        next_actions=NAV_ACTIONS,
        request_id=new_request_id(),
    )


def build_error_card(code: str, *, request_id: str | None = None, detail: str = "") -> Card:
    """ТЗ §6 error taxonomy → one honest card, no traceback, no partial numbers."""
    texts = {
        "AUTH_DENIED": "⛔ Доступ запрещён.",
        "MARKET_STALE": "🟠 Рынок не свежий. Новые входы запрещены в домене.",
        "BACKEND_UNAVAILABLE": "🔴 Сервис данных недоступен. Повторите через минуту.",
        "CONTEXT_EXPIRED": "⌛ Эта карточка устарела — обновите «Сейчас».",
        "NOT_IMPLEMENTED": "🚧 Этот экран ещё не готов в новой консоли.",
    }
    body = texts.get(code, "🔴 Неизвестная ошибка.")
    # the machine-readable code is always shown: a user quotes this, not a
    # traceback (ТЗ §6 error taxonomy)
    body += f"\n<code>{esc(code)}</code>"
    if detail:
        body += f"\n<i>{esc(detail)}</i>"
    return Card(
        status="error",
        title="WORED Console",
        body=body,
        as_of=datetime.now(timezone.utc).isoformat(),
        source="bot",
        quality="error",
        request_id=request_id or new_request_id(),
    )


# ─── Plans of the V3 simulation domain (P3c) ───────────────────────────

#: statuses that may appear on a plan card; unknown values render verbatim but
#: escaped, because inventing a friendly synonym would hide a domain state
_PLAN_STATUS_WORD = {
    "draft": "черновик",
    "approved": "утверждён",
    "superseded": "заменён версией",
    "rejected": "отклонён",
}

PLAN_LIST_ACTIONS = ("plans", "plan_new", "plan_show", "plan_preview")

#: the proposal screens answer with the same action vocabulary plus their own
PROPOSAL_ACTIONS = ("plans", "proposal_help", "proposal_show", "proposal_adopt")


def _age_since(moment: Any, *, now: datetime | None = None) -> str:
    """Human age of a stored row, from the storage timestamp — never a guess."""
    if moment is None:
        return NA
    moment_now = now or datetime.now(timezone.utc)
    value = moment
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return NA
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return age_text((moment_now - value.astimezone(timezone.utc)).total_seconds())


def _violations_lines(violations: list[dict[str, str]], *, limit: int = 6) -> list[str]:
    """The domain's own violation list, verbatim, capped for one message.

    Never paraphrased and never padded: a missing line would claim a clean plan.
    """
    lines: list[str] = []
    for item in violations[:limit]:
        field = esc(item.get("field") or "—")
        code = esc(item.get("code") or "—")
        message = esc(item.get("message") or "")
        lines.append(f"  · <code>{field}</code> {code} — {message}")
    hidden = len(violations) - limit
    if hidden > 0:
        lines.append(f"  <i>и ещё {hidden} (полный список — в предпросмотре и WebUI)</i>")
    return lines


def build_plan_list_card(
    plans: list[Any],
    *,
    tz_name: str,
    store_note: str = "",
    now: datetime | None = None,
) -> Card:
    """«Мои планы»: one row per own plan, newest first.

    Only stored fields are shown.  The hash is displayed as a short prefix
    because the full value belongs to the preview card, where it is compared
    before an approval.
    """
    if not plans:
        body_lines = [
            "⚪ Своих планов ещё нет.",
            "«Новый черновик» создаёт план с полями, которые уже проходят контракт;",
            "изменить поля можно кнопкой, утвердить — только вы, и только после предпросмотра.",
        ]
        if store_note:
            body_lines.append(f"<i>{esc(store_note)}</i>")
        return Card(
            status="empty", title="📋 <b>Мои планы</b>", body="\n".join(body_lines),
            as_of=(now or datetime.now(timezone.utc)).isoformat(), source="simulation_store",
            quality="live", next_actions=PLAN_LIST_ACTIONS, request_id=new_request_id(),
        )

    body_lines = [f"Показано: {len(plans)} · порядок: новые сверху", ""]
    for rec in plans:
        plan = rec.plan or {}
        status_word = _PLAN_STATUS_WORD.get(rec.status, str(rec.status))
        budgets = "{manual} / {auto}".format(
            manual=money(plan.get("manual_budget"), dp=2),
            auto=money(plan.get("auto_budget"), dp=2),
        )
        hash_prefix = (rec.plan_hash or "").replace("sha256:", "")[:8] or NA
        body_lines.append(
            "<code>{pid}</code> <b>{status}</b> v{version} · {mode} · {instr}\n"
            "  бюджеты {budgets} · риск/ордер {risk} · обновлён {age} · hash <code>{hashp}</code>".format(
                pid=esc(rec.plan_id[:8]),
                status=esc(status_word),
                version=rec.version,
                mode=esc(plan.get("mode") or NA),
                instr=esc(plan.get("instrument_key") or NA),
                budgets=budgets,
                risk=money(plan.get("max_risk_per_order"), dp=2),
                age=_age_since(getattr(rec, "updated_at", None), now=now),
                hashp=esc(hash_prefix),
            )
        )
        if rec.violations:
            body_lines.append(f"  <i>нарушений: {len(rec.violations)}</i>")
    body_lines.append("")
    body_lines.append(
        "<i>запуск сессии — только WebUI; здесь план создаётся, правится и утверждается</i>"
    )
    if store_note:
        body_lines.append(f"<i>{esc(store_note)}</i>")
    return Card(
        status="ok",
        title="📋 <b>Мои планы</b>",
        body="\n".join(body_lines),
        as_of=(now or datetime.now(timezone.utc)).isoformat(),
        source="simulation_store",
        quality="live",
        entity_ids=tuple(str(rec.plan_id) for rec in plans),
        next_actions=PLAN_LIST_ACTIONS,
        request_id=new_request_id(),
    )


def build_plan_preview_card(
    rec: Any,
    result: dict[str, Any],
    *,
    tz_name: str,
    title_prefix: str = "📋 План",
) -> Card:
    """The card the P3 exit gate is about: fields, limits, data quality, worst
    allowed loss, the domain's violation list and the ``plan_hash``.

    Every number comes from the shared pipeline in :mod:`trading_math`; nothing
    is recalculated here.  ``plan_hash`` is shown only when the pipeline emitted
    one, i.e. when nothing blocks the plan.
    """
    plan = rec.plan or {}
    clock_start, zone_start = format_time(plan.get("start_at"), tz_name)
    clock_end, zone_end = format_time(plan.get("end_at"), tz_name)
    worst = result.get("worst_case_risk") or {}
    drivers = worst.get("drivers") or {}
    market = result.get("market") or {}
    violations = list(result.get("violations") or [])
    can_approve = bool(result.get("can_approve"))

    lines: list[str] = []
    lines.append(
        f"<code>{esc(rec.plan_id[:8])}</code> · {_PLAN_STATUS_WORD.get(rec.status, esc(rec.status))} "
        f"· v{rec.version} · обновлён {_age_since(getattr(rec, 'updated_at', None))}"
    )
    lines.append("")
    lines.append(
        "Инструмент: {instr} · режим: {mode}\n"
        "Окно: {s} → {e} ({z}) · часовой пояс плана: {tz}".format(
            instr=esc(plan.get("instrument_key") or NA),
            mode=esc(plan.get("mode") or NA),
            s=esc(clock_start),
            e=esc(clock_end),
            z=esc(zone_end),
            tz=esc(plan.get("timezone") or NA),
        )
    )
    lines.append(
        "Бюджеты: ручной {manual} · авто {auto}\n"
        "Риск: ордер {risk} · день {daily} · сессия {sess} · exposure {exp} · "
        "поз. {pos} · плечо {lev}·x".format(
            manual=money(plan.get("manual_budget")),
            auto=money(plan.get("auto_budget")),
            risk=money(plan.get("max_risk_per_order")),
            daily=money(plan.get("max_daily_loss")),
            sess=money(plan.get("max_session_loss")),
            exp=money(plan.get("max_total_exposure")),
            pos=plain(plan.get("max_open_positions")),
            lev=plain(plan.get("max_leverage")),
        )
    )
    lines.append(
        "Стороны: {sides} · cooldown {cd} мин · часы {hours} · SL/TP {sl}/{tp} · "
        "закрытие в конце: {close}".format(
            sides=esc(", ".join(plan.get("allowed_sides") or []) or NA),
            cd=plain(plan.get("cooldown_minutes")),
            hours=esc(plan.get("trading_hours") or NA),
            sl=esc(plan.get("stop_policy") or NA),
            tp=esc(plan.get("take_profit_policy") or NA),
            close=plain(plan.get("close_at_end")),
        )
    )
    lines.append(
        "Стратегия: {sid} {ver} · стиль {style}\n"
        "Политики: комиссия {fee} · проскальзывание {slip} · funding {fund} · "
        "данные {data}".format(
            sid=esc(plan.get("strategy_id") or NA),
            ver=esc(plan.get("strategy_version") or NA),
            style=esc(plan.get("style") or NA),
            fee=esc(plan.get("fee_schedule_version") or NA),
            slip=esc(plan.get("slippage_model_version") or NA),
            fund=esc(plan.get("funding_model_version") or NA),
            data=esc(plan.get("market_data_policy") or NA),
        )
    )
    lines.append("")
    lines.append(
        "Худший допустимый убыток: {worst} USDT\n"
        "  <i>метод: {method} (сессия {sess} · волны {wave} · день×поз {daily})</i>".format(
            worst=plain(worst.get("worst_allowed_loss_usdt")),
            method=esc(worst.get("method") or NA),
            sess=plain(drivers.get("max_session_loss_usdt")),
            wave=plain(drivers.get("positions_x_risk_usdt")),
            daily=plain(drivers.get("daily_x_positions_usdt")),
        )
    )

    if market.get("coverage"):
        cov = market["coverage"]
        lines.append(
            "Покрытие истории: {avail} из {need} интервалов 1мин{why}".format(
                avail=plain(cov.get("available_intervals")),
                need=plain(cov.get("required_intervals")),
                why=(f" · {esc(cov['reason_code'])}" if cov.get("reason_code") else ""),
            )
        )
    elif market:
        lines.append(
            "Рынок на момент проверки: can_enter={enter} · качество={q} · {as_of}"
            " · snapshot <code>{sid}</code>".format(
                enter=esc(str(bool(market.get("can_enter")))).lower(),
                q=esc(market.get("quality_worst") or NA),
                as_of=esc(market.get("as_of") or NA),
                sid=esc(str(market.get("snapshot_id") or NA)[:8]),
            )
        )
    else:
        lines.append(f"Рынок на момент проверки: {NA} <i>(источник недоступен)</i>")

    lines.append("")
    if violations:
        lines.append(f"Нарушения домена ({len(violations)}):")
        lines.extend(_violations_lines(violations))
    else:
        lines.append("Нарушений нет — контракт и гейты домена пройдены.")

    lines.append("")
    if result.get("plan_hash"):
        lines.append(f"<code>{esc(result['plan_hash'])}</code>")
    else:
        lines.append(
            f"<i>plan_hash не вычислен, пока есть нарушения</i> · can_approve: "
            f"<b>{esc(str(can_approve).lower())}</b>"
        )
    lines.append(
        "<i>Утверждение закрепляет эту версию; правка после утверждения = новая версия. "
        "Запуск сессии остаётся в WebUI.</i>"
    )

    return Card(
        status="ok" if can_approve else "blocked",
        title=f"{title_prefix} <code>{esc(rec.plan_id[:8])}</code>",
        body="\n".join(lines),
        as_of=plan.get("start_at"),
        source="plan_validation",
        quality="live" if not violations else "stale",
        entity_ids=(str(rec.plan_id),),
        next_actions=PLAN_LIST_ACTIONS,
        request_id=new_request_id(),
    )


def build_plan_refused_card(reason_code: str, message: str, *, violations: list[dict[str, str]] | None = None,
                            entity: str = "План",
                            source: str = "simulation_store",
                            next_actions: tuple[str, ...] = PLAN_LIST_ACTIONS) -> Card:
    """A refused operation, in the domain's own words.

    The machine-readable ``reason_code`` is always present (ТЗ §6); the violation
    list, when the domain produced one, is appended verbatim rather than
    summarised — a summary would be a second opinion about the same object.

    One card serves every refusal in the console (P2 plans, P4 manual deals):
    only the surface noun, the owning source and the next-action hints differ, so
    ``entity``/``source``/``next_actions`` are arguments with the historical plan
    defaults.
    """
    lines = [esc(message), f"<code>{esc(reason_code)}</code>"]
    if violations:
        lines.append(f"Нарушения ({len(violations)}):")
        lines.extend(_violations_lines(violations))
    return Card(
        status="error",
        title=f"📋 <b>{esc(entity)}: операция не выполнена</b>",
        body="\n".join(lines),
        as_of=datetime.now(timezone.utc).isoformat(),
        source=source,
        quality="error",
        next_actions=next_actions,
        request_id=new_request_id(),
    )


# ─── proposal cards (ТЗ §5 U05, phase P3d) ────────────────────────────────────

#: how the console names the three domain states (``PROPOSAL_STATES``)
_PROPOSAL_STATUS_WORD = {
    "pending": "формируется",
    "completed": "готово",
    "failed": "не сформировано",
}

#: answer token → label → domain option list.  The *values* are always passed in
#: by the caller from the domain constants, so a card cannot advertise a choice
#: the validator would refuse; numeric ranges are deliberately not repeated here
#: — a number written on a card would be a second source of truth, and
#: ``normalize_questionnaire`` already answers a bad value with its own violation.
_QUESTION_HINTS: tuple[tuple[str, str, str], ...] = (
    ("goal", "цель", "goal"),
    ("duration", "окно симуляции, часов", ""),
    ("side", "стороны", "side_preference"),
    ("involvement", "степень участия", "involvement"),
    ("manual", "ручной бюджет, USDT", ""),
    ("auto", "автономный бюджет, USDT", ""),
    ("total", "общий бюджет, USDT (если manual/auto не указаны)", ""),
    ("maxloss", "приемлемый убыток, USDT", ""),
    ("hours", "торговые часы", ""),
    ("instrument", "инструмент из реестра", ""),
)


def build_questionnaire_card(
    *, options: dict[str, Any], source_label: str, default_instrument: str = "",
) -> Card:
    """The questionnaire the owner answers in one line (ТЗ §5 U05).

    The card is the *only* thing that turns an answer into a proposal: no free
    text reaches an agent, and the same ``key=value`` tokens are read by the
    deterministic parser in :mod:`services.simulation_proposals`.
    """
    lines = [
        "Ответ — одна строка: <code>ключ=значение</code>, ключи через пробел.",
        "",
    ]
    for token, label, option_key in _QUESTION_HINTS:
        allowed = options.get(option_key) if option_key else None
        if allowed:
            lines.append(f"  · <code>{esc(token)}</code> — {esc(label)}: {esc(', '.join(allowed))}")
        else:
            lines.append(f"  · <code>{esc(token)}</code> — {esc(label)}")
    lines.append("")
    lines.append(
        "Инструмент по умолчанию: <code>{instr}</code>· источник черновика: "
        "<b>{src}</b>".format(instr=esc(default_instrument or NA), src=esc(source_label))
    )
    lines.append(
        "<i>Черновик не утверждается и не запускается сам: сначала карточка "
        "предложения с нарушениями и plan_hash, потом ваше решение.</i>"
    )
    return Card(
        status="ok",
        title="🧭 <b>Анкета предложения</b>",
        body="\n".join(lines),
        as_of=datetime.now(timezone.utc).isoformat(),
        source="simulation_proposal",
        quality="live",
        next_actions=PROPOSAL_ACTIONS,
        request_id=new_request_id(),
    )


def build_proposal_card(rec: Any, *, tz_name: str, adopted_plan: Any = None) -> Card:
    """One proposal: the answer it got, the draft it produced, the veto it met.

    TB-12 in the chat surface: budgets, limits, data quality and the
    ``plan_hash`` on this card are the output of the *shared* deterministic
    validator — the same digest string the dashboard shows for the same draft,
    because both read :mod:`trading_math.simulation_proposal`.  Nothing here
    recalculates a number, and an agent's field is shown as a proposed value
    with the validator's verdict under it, never as an accepted one.
    """
    q = rec.questionnaire or {}
    draft = rec.draft or {}
    validator = rec.validator or {}
    violations = list(validator.get("violations") or [])
    adoptable = bool(validator.get("adoptable"))
    sources = rec.sources or {}
    market = sources.get("market") or {}

    lines = [
        "<code>{pid}</code> · {status} · обновлён {age}".format(
            pid=esc(rec.proposal_id[:8]),
            status=esc(_PROPOSAL_STATUS_WORD.get(rec.status, str(rec.status))),
            age=_age_since(getattr(rec, "updated_at", None)),
        ),
        f"Источник: {plain(rec.model_version)} · инструмент: {plain(sources.get('instrument_key'))}",
    ]

    if rec.status == "failed":
        lines.append("")
        lines.append(f"<b>Агент не справился:</b> {plain(rec.reason_message)}")
        lines.append(f"<code>{esc(rec.reason_code or NA)}</code>")
        lines.append("<i>Ручной путь доступен: /plans new, затем /plans set.</i>")
    else:
        lines.append("")
        lines.append(
            "Анкета: цель {goal} · окно {dur} ч · стороны {side} · участие {inv}\n"
            "бюджеты {manual} / {auto} · приемлемый убыток {mal} · часы {hours}".format(
                goal=plain(q.get("goal")),
                dur=plain(q.get("duration_hours")),
                side=plain(q.get("side_preference")),
                inv=plain(q.get("involvement")),
                manual=money(q.get("manual_budget")),
                auto=money(q.get("auto_budget")),
                mal=money(q.get("max_acceptable_loss")),
                hours=plain(q.get("trading_hours")),
            )
        )

    if draft:
        clock_start, zone_start = format_time(draft.get("start_at"), tz_name)
        clock_end, zone_end = format_time(draft.get("end_at"), tz_name)
        lines.append("")
        lines.append(
            "Предложенный черновик:\n"
            "  окно {s} → {e} ({z}) · бюджет {manual} / {auto}\n"
            "  риск/ордер {risk} · день {daily} · сессия {sess} · exposure {exp}\n"
            "  поз. {pos} · плечо {lev}·x · cooldown {cd} мин · стороны {sides}".format(
                s=esc(clock_start), e=esc(clock_end), z=esc(zone_end),
                manual=money(draft.get("manual_budget")),
                auto=money(draft.get("auto_budget")),
                risk=money(draft.get("max_risk_per_order")),
                daily=money(draft.get("max_daily_loss")),
                sess=money(draft.get("max_session_loss")),
                exp=money(draft.get("max_total_exposure")),
                pos=plain(draft.get("max_open_positions")),
                lev=plain(draft.get("max_leverage")),
                cd=plain(draft.get("cooldown_minutes")),
                sides=esc(", ".join(draft.get("allowed_sides") or []) or NA),
            )
        )

    rationale = rec.rationale or {}
    if rationale:
        lines.append("")
        lines.append(f"Обоснование агента ({len(rationale)}):")
        for key in list(rationale)[:6]:
            lines.append(f"  · <code>{esc(key)}</code> — {esc(rationale[key])}")
        if len(rationale) > 6:
            lines.append(f"  <i>и ещё {len(rationale) - 6}</i>")
    for label, items in (("Допущения", rec.assumptions), ("Чего не хватает", rec.missing_data)):
        if items:
            lines.append(f"{label}: " + "; ".join(esc(item) for item in list(items)[:4]))

    if market:
        lines.append(
            "Рынок на момент черновика: качество {q} · {as_of} · snapshot "
            "<code>{sid}</code>".format(
                q=esc(market.get("quality_worst") or NA),
                as_of=esc(market.get("as_of") or NA),
                sid=esc(str(market.get("snapshot_id") or NA)[:8]),
            )
        )
    elif rec.status == "completed":
        lines.append(f"Рынок на момент черновика: {NA} <i>(snapshot недоступен)</i>")

    lines.append("")
    if violations:
        lines.append(f"Детерминированный валидатор — нарушения ({len(violations)}):")
        lines.extend(_violations_lines(violations))
    elif rec.status == "completed":
        lines.append("Нарушений нет — контракт и гейты домена пройдены.")

    lines.append("")
    if validator.get("plan_hash"):
        lines.append(f"<code>{esc(validator['plan_hash'])}</code>")
    elif rec.status == "completed":
        lines.append(
            "<i>plan_hash не вычислен, пока есть нарушения</i> · adoptable: "
            f"<b>{esc(str(adoptable).lower())}</b>"
        )

    if adopted_plan is not None:
        lines.append(
            "Это предложение уже стало черновиком <code>{pid}</code> · {status}".format(
                pid=esc(getattr(adopted_plan, "plan_id", NA)[:8]),
                status=esc(_PLAN_STATUS_WORD.get(getattr(adopted_plan, "status", ""), NA)),
            )
        )
    lines.append(
        "<i>«Принять» создаёт обычный черновик с этими полями; утверждение — двумя "
        "шагами с карточки плана, запуск сессии остаётся в WebUI.</i>"
    )

    quality = {
        "failed": "error",
        "pending": "missing",
    }.get(rec.status, "live" if adoptable else "stale")
    return Card(
        status="ok" if adoptable else ("error" if rec.status == "failed" else "blocked"),
        title="🤖 <b>Предложение плана</b> <code>{pid}</code>".format(pid=esc(rec.proposal_id[:8])),
        body="\n".join(lines),
        as_of=draft.get("start_at") or rec.created_at.isoformat(),
        source="simulation_proposal",
        quality=quality,
        entity_ids=(str(rec.proposal_id),),
        next_actions=PROPOSAL_ACTIONS,
        request_id=new_request_id(),
    )


def build_proposal_list_card(rows: list[Any], *, tz_name: str) -> Card:
    """«Мои предложения»: newest first, with the verdict on each row."""
    if not rows:
        return Card(
            status="empty",
            title="🤖 <b>Мои предложения</b>",
            body=(
                "⚪ Предложений ещё нет.\n"
                "<code>/plans propose</code> покажет анкету, ответ — одна строка."
            ),
            as_of=datetime.now(timezone.utc).isoformat(),
            source="simulation_proposal",
            quality="missing",
            next_actions=PROPOSAL_ACTIONS,
            request_id=new_request_id(),
        )
    lines = [f"Показано: {len(rows)} · порядок: новые сверху", ""]
    for rec in rows:
        validator = rec.validator or {}
        lines.append(
            "<code>{pid}</code> <b>{status}</b> · {model} · {goal} {dur} ч · "
            "{verdict} · {age}".format(
                pid=esc(rec.proposal_id[:8]),
                status=esc(_PROPOSAL_STATUS_WORD.get(rec.status, str(rec.status))),
                model=plain(rec.model_version),
                goal=plain((rec.questionnaire or {}).get("goal")),
                dur=plain((rec.questionnaire or {}).get("duration_hours")),
                verdict=(
                    "нарушений нет" if validator.get("adoptable")
                    else (esc(rec.reason_code) if rec.status == "failed"
                          else f"нарушений: {len(validator.get('violations') or [])}")
                ),
                age=_age_since(getattr(rec, "updated_at", None)),
            )
        )
        if getattr(rec, "adopted_plan_id", None):
            lines.append(f"  <i>принято как черновик <code>{esc(rec.adopted_plan_id[:8])}</code></i>")
    return Card(
        status="ok",
        title="🤖 <b>Мои предложения</b>",
        body="\n".join(lines),
        as_of=datetime.now(timezone.utc).isoformat(),
        source="simulation_proposal",
        quality="live",
        entity_ids=tuple(str(rec.proposal_id) for rec in rows),
        next_actions=PROPOSAL_ACTIONS,
        request_id=new_request_id(),
    )


# ─── deal ticket card (ТЗ §5 U07, phase P4) ─────────────────────────────────

DEAL_ACTIONS = ("deal_preview", "deal_confirm", "deal_status", "deal_positions")

#: как домен помечает качество числа ликвидации → слово оператору
_LIQUIDATION_WORD = {
    "isolated_tier_maintenance": "изолированная маржа, ставка поддержки из tier рынка",
    "isolated_default_maintenance": "изолированная маржа, общий дефолт ставки",
}


def build_deal_ticket_card(deal: Any, *, tz_name: str) -> Card:
    """Deal-ticket: ровно те числа, которые посчитал доменный гейт.

    Презентёр ничего не вычисляет: всё берётся из
    :meth:`paper_trading.ticket.TicketResult.as_ui_dict` (тот же набор ключей,
    что отдаёт WebUI) и из полей запроса оператора.  Подставленные дефолты
    лимитов показываются обязательно: молча принятый дефолт был бы вторым
    источником чисел, о котором оператор не знает.
    """
    result = deal.result.as_ui_dict()
    request = deal.request
    clock, zone = format_time(result.get("expires_at"), tz_name)
    seen, seen_zone = format_time(result.get("price_as_of"), tz_name)
    quality = str((result.get("market") or {}).get("quality") or "unknown")
    liquidation = _LIQUIDATION_WORD.get(
        str(result.get("liquidation_quality")), plain(result.get("liquidation_quality"))
    )

    lines = [
        f"<code>{esc(deal.short_id)}</code> · {esc(result.get('account_label'))} · "
        f"заявка {esc(result.get('order_type'))} {esc(result.get('side'))} · "
        f"{esc(result.get('instrument'))}",
        f"объём {plain(result.get('quantity'))} · вход "
        f"{plain(result.get('entry_price'))} USDT (оценка по стакану) · "
        f"плечо {plain(result.get('leverage'))}x",
        f"стоп {plain(request.stop_price)} · цель {plain(request.take_profit)} · "
        f"RR после комиссий {plain(result.get('net_rr'))}",
        f"стоимость позиции {plain(result.get('notional'))} · маржа "
        f"{plain(result.get('reserved_margin'))} USDT · комиссии "
        f"{plain(result.get('entry_fee'))} + {plain(result.get('estimated_exit_fee'))} USDT",
        f"безубыток {plain(result.get('break_even'))} · ликвидация "
        f"{plain(result.get('liquidation_price'))} USDT ({esc(liquidation)})",
        f"результат при стопе {plain(result.get('estimated_net_at_sl'))} · при цели "
        f"{plain(result.get('estimated_net_at_tp'))} USDT",
        f"финансирование: {esc(result.get('funding_status'))} · "
        f"{plain(result.get('funding_rate'))}",
        f"цена стакана {seen} ({esc(seen_zone)}) · тикет действует до "
        f"{clock} ({esc(zone)})",
    ]
    notes = [str(item) for item in (result.get("settings_notes") or [])]
    # Две разные честные пометки: ``risk_tier_absent`` — про ставку поддержки для
    # ликвидации, остальные ключи — про лимиты RiskSettings. Смешивать их в одну
    # строку нельзя: пользователь иначе решит, что дефолт подставлен в плечо.
    defaulted = [item for item in notes if item != "risk_tier_absent"]
    if defaulted:
        lines.append(
            "⚠️ лимиты дня взяты из дефолта RiskSettings, не из снапшота дня: "
            + esc(", ".join(defaulted))
        )
    if "risk_tier_absent" in notes:
        lines.append(
            "⚠️ тира риска в снапшоте дня нет — ставка поддержки для ликвидации "
            "взята из доменного дефолта"
        )
    lines.append(
        "<i>числа рассчитаны доменным тикет-гейтом; подтверждение — кнопка, "
        "до неё ни одна команда не записана.</i>"
    )
    return Card(
        status="ok",
        title=f"🎯 Ручная сделка · {esc(result.get('side'))} "
              f"{esc(result.get('instrument'))}",
        body="\n".join(lines),
        as_of=result.get("price_as_of"),
        source="paper_trading.ticket",
        quality=quality,
        entity_ids=(str(deal.ticket_id), str(deal.day_id), str(deal.account_id)),
        next_actions=DEAL_ACTIONS,
        request_id=new_request_id(),
    )
