"""chatbot.services.day_control — управление торговым днём из чата (Q-24, срез P4b).

Экран ``/day`` ничего не решает.  Каждое действие — доменная команда через
:mod:`paper_trading.adapter`: та же точка, что и у WebUI-кнопки (ТЗ TB-03), —
и то же исполнение в runner'е.  Собственность этого модуля — карточка
подтверждения: что показано, то и подписывается, а ``expected`` уходит в
доменный compare-and-swap, чтобы «подтвердил одно, записалось другое» стало
невозможным.

Что здесь чинит Q-24 (старые кнопки ``пауза / ужесточить / снизить / закрыть
всё`` в ``handlers/pipeline.py``):

* у действия один адресат — домен.  Легаси-путь писал ``trading_sessions``,
  которой у живого дня нет (проверка этой сессии: 0 активных легаси-сессий
  против идущих ``paper_v2_days``), поэтому оператор слышал ``нет сессии``
  там, где шёл торговый день;
* ключ идемпотентности принадлежит попытке (``tg-<owner>-<ticket>``), а не дню.
  ``pause-<day>`` превращал законное «пауза → продолжить → пауза» внутри одного
  дня в replay уже исполненной команды: runner берёт в работу только
  ``accepted``, и второе нажатие молча не делало ничего;
* ``завершить день`` — исключение, и оно в домене: ``finish-<day>`` выдаёт и
  этот экран, и планировщик runner'а каждые 2 секунды, поэтому уникальный ключ
  породил бы дубли.  Для оператора это значит: если закрытие уже стоит в
  очереди, чат назовёт существующий ``command_id``, а не выдаст «ок»;
* «ужесточить» и «снизить» перестали быть словами.  Оператор называет ключи и
  числа, их разбирает доменный :func:`paper_trading.ticket.parse_limits` — тот
  же ``LIMIT_FIELDS``, что у карточки, поэтому неизвестный ключ отсекается, а не
  игнорируется молча; множащиеся «×0.5» нигде не выдумываются.

Правила поверхности те же, что у ``/deal``: тикет живёт ``TICKET_TTL_SECONDS``
(доменная константа), привязан к владельцу, подтверждение перечитывает день и
отказывается, если день или показанные лимиты успели измениться.

Здесь нет ни одного финансового числа, которое чат посчитал бы сам: число
ликвидации, объём и лимиты остаются за доменом.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from uuid import uuid4

from paper_trading import adapter as pt_adapter
from paper_trading.ticket import (
    LIMIT_FIELDS,
    TICKET_TTL_SECONDS,
    TRADABLE_DAY_STATES,
    TicketDenied,
    denial_text,
    parse_limits,
)
from services import paper_orders as deals

log = logging.getLogger("chatbot.day_control")

__all__ = [
    "ACTION_CLOSE_ALL",
    "ACTION_FINISH",
    "ACTION_LIMITS",
    "ACTION_PAUSE",
    "ACTION_RESUME",
    "ACTIONS",
    "DayControlError",
    "DayControlChanged",
    "DayControlExpired",
    "DayControlInvalid",
    "DayControlRefused",
    "DayControlUnknown",
    "DayControlUnavailable",
    "DayTicket",
    "NoActiveDay",
    "confirm",
    "load_day",
    "preview",
    "recognises_limit_args",
    "resolve_action",
    "usage_preview",
]

#: доменные действия экрана
ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_FINISH = "finish"
ACTION_CLOSE_ALL = "close_all"
ACTION_LIMITS = "limits"


@dataclass(frozen=True)
class ActionSpec:
    """Что действие делает на самом деле — текст карточки берётся отсюда."""

    action: str
    title: str
    effect: str
    needs_limits: bool = False
    closes_positions: bool = False


ACTIONS: dict[str, ActionSpec] = {
    ACTION_PAUSE: ActionSpec(
        ACTION_PAUSE,
        "⏸ Пауза автомата",
        "новые входы блокируются; открытые позиции и стоп-защита остаются в силе",
    ),
    ACTION_RESUME: ActionSpec(
        ACTION_RESUME,
        "▶️ Продолжить автомата",
        "снимает паузу; входы возобновятся на следующем цикле исполнителя",
    ),
    ACTION_FINISH: ActionSpec(
        ACTION_FINISH,
        "🏁 Завершить день",
        "отменяет неотстреленные заявки, закрывает позиции обоих счетов и "
        "переводит день в closed",
    ),
    ACTION_CLOSE_ALL: ActionSpec(
        ACTION_CLOSE_ALL,
        "🛑 Стоп авто",
        "блокирует новые входы и закрывает открытые позиции автомата; ручной "
        "счёт не трогается",
        closes_positions=True,
    ),
    ACTION_LIMITS: ActionSpec(
        ACTION_LIMITS,
        "🛡 Лимиты дня",
        "меняет лимиты текущего дня в settings_snapshot; новые числа действуют "
        "с ближайшего сигнала",
        needs_limits=True,
    ),
}

#: как оператор может позвать действие.  ``close`` сюда намеренно не внесён:
# «закрыть» одинаково читается как «завершить день» и как «закрыть позиции»,
#: а угадывать направление экран не имеет права
ALIASES: dict[str, str] = {
    "pause": ACTION_PAUSE, "пауза": ACTION_PAUSE,
    "resume": ACTION_RESUME, "continue": ACTION_RESUME, "продолжить": ACTION_RESUME,
    "finish": ACTION_FINISH, "endday": ACTION_FINISH, "завершить": ACTION_FINISH,
    "closeauto": ACTION_CLOSE_ALL, "closeall": ACTION_CLOSE_ALL,
    "stopauto": ACTION_CLOSE_ALL, "стоп": ACTION_CLOSE_ALL,
    "limits": ACTION_LIMITS, "tighten": ACTION_LIMITS, "reduce": ACTION_LIMITS,
}


# ─── отказа ─────────────────────────────────────────────────────────────────


class DayControlError(deals.PaperOrderError):
    """Отказ управления днём.

    Форма одна с экраном сделки (код + человеческий текст + нарушения), поэтому
    карточка отказа рендерится одним презентером; семья и коды — свои.
    """

    reason_code = "day_control_error"


class DayControlInvalid(DayControlError):
    """Подкоманда или аргументы не разобраны: карточка обязана назвать форму."""

    reason_code = "day_command_invalid"


class DayControlUnavailable(DayControlError):
    """Домен не отдал состояние дня: действие не выполнено."""

    reason_code = "day_state_unavailable"


class NoActiveDay(DayControlUnavailable):
    reason_code = "no_active_day"


class DayControlUnknown(DayControlError):
    """Ни своего живого тикета, ни чужого: существование чужого не раскрывается."""

    reason_code = "ticket_not_found"


class DayControlExpired(DayControlError):
    reason_code = "ticket_expired"


class DayControlChanged(DayControlError):
    """Между карточкой и подтверждением изменилось то, под чем подписывались."""

    reason_code = "ticket_changed"


class DayControlRefused(DayControlError):
    """Домен не принял команду; повтор её не отправит."""

    reason_code = "command_not_submitted"


def usage_preview() -> str:
    """Точная форма команды — её же показывает отказ парсера."""
    keys = ", ".join(sorted(LIMIT_FIELDS))
    return (
        "/day pause · /day resume · /day finish · /day closeauto · "
        f"/day limits <ключ>=<число> · /day status\n"
        f"разрешённые ключи лимитов: {keys}"
    )


def recognises_limit_args(text: str) -> bool:
    """Каждый токен строки — ключ лимитов дня.

    Форма «каждый токен key=value» — правило поверхности
    (``handlers/telegram_surface.py``), здесь проверяется только принадлежность
    ключей этому экрану: словарь отдаёт доменный ``LIMIT_FIELDS``, второй список
    допустимых ключей отсюда запрещён (ТЗ TB-03).  Нужно для отказа Q-20:
    ``max_risk_per_order_usdt=8``, набранное без команды, не должно уйти в модель.
    Достаточно одного признанного ключа: один лимит — уже намерение, в отличие от
    заявки, где без риска и стопа объёма нет.
    """
    keys = {token.partition("=")[0].strip().lower() for token in (text or "").split()}
    return bool(keys & set(LIMIT_FIELDS))


def resolve_action(word: str) -> ActionSpec:
    """Подкоманда → доменное действие; неизвестная называется как есть."""
    action = ALIASES.get((word or "").strip().lower())
    if action is None:
        raise DayControlInvalid(
            f"Подкоманда «{word}» не понята. Формы: " + usage_preview(),
            violations=[{
                "field": word or "",
                "code": "unknown_action",
                "message": "доменное действие не угадывается по похожим словам",
            }],
        )
    return ACTIONS[action]


# ─── ticket ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayTicket:
    """Карточка управления днём: предмет подтверждения, а не обещание."""

    ticket_id: str
    owner_id: str
    day_id: str
    day_state: str
    action: str
    expires_at: datetime
    limits: Mapping[str, str] = field(default_factory=dict)
    expected: Mapping[str, Optional[str]] = field(default_factory=dict)
    settings_notes: tuple[str, ...] = ()
    open_auto_positions: Optional[int] = None
    raw: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = deals.KIND_DAY

    @property
    def short_id(self) -> str:
        return self.ticket_id[:8]

    @property
    def idempotency_key(self) -> str:
        """Тот же ключ, что у сделки: формула одна (``ticket_idempotency_key``)."""
        return deals.ticket_idempotency_key(self.owner_id, self.ticket_id)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


# ─── domain reads ───────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def load_day(owner_id: str) -> dict[str, Any]:
    """Действующие лимиты и состояние дня; отсутствие дня — честный отказ.

    ``ok=false`` (домен недоступен) и ``no_active_day`` (дня просто нет) —
    разные условия (ТЗ §6): в первом случае экран не знает ничего, во втором
    знает, что управлять нечем.
    """
    try:
        day = await pt_adapter.load_day_limits(owner_id)
    except Exception as exc:
        log.warning("day control state read failed: %s", exc)
        raise DayControlUnavailable(
            denial_text("domain_state_unavailable")
            + ": состояние дня не прочитано, действие не выполнено."
        ) from exc
    if not day.get("ok"):
        code = str(day.get("error") or "domain_state_unavailable")
        if code == "no_active_day":
            raise NoActiveDay(
                "Торговый день не запущен — паузить, продолжать и закрывать "
                "нечего. День запускается в WebUI."
            )
        raise DayControlUnavailable(denial_text(code) + ": действие не выполнено.")
    return day


async def _auto_open_positions(owner_id: str) -> Optional[int]:
    """Сколько открытых позиций у автомата — ровно то, что обещает карточка.

    Число берётся из доменного состояния, а не считается в чате.  Если домен
    не отдал состояние, карточка «стоп авто» не показывается вовсе: обещать
    ликвидацию, не зная её предмета, нельзя.
    """
    try:
        state = await pt_adapter.get_current_state(owner_id)
    except Exception as exc:
        log.warning("day control positions read failed: %s", exc)
        raise DayControlUnavailable(
            denial_text("domain_state_unavailable")
            + ": число открытых позиций не прочитано, команда не отправлена."
        ) from exc
    if not state.get("ok"):
        raise DayControlUnavailable(
            denial_text(state.get("error") or "domain_state_unavailable")
            + ": команда не отправлена."
        )
    for account in state.get("accounts") or []:
        if str(account.get("kind")) == "auto":
            count = account.get("open_positions")
            return int(count) if count is not None else None
    return None


# ─── use cases: command and button both call these ─────────────────────────


async def preview(owner_id: str, word: str, raw: str = "") -> DayTicket:
    """Собрать карточку действия и положить её в общий магазин тикетов."""
    spec = resolve_action(word)
    day = await load_day(owner_id)

    limits: dict[str, str] = {}
    expected: dict[str, Optional[str]] = {}
    if spec.needs_limits:
        # Ключ снапшота и есть канонический ключ лимита: у лимитов нет
        # операторских алиасов, поэтому отображение выводится из доменного
        # LIMIT_FIELDS, а не составляется здесь заново (ТЗ TB-03).
        key_map = {key: key for key in LIMIT_FIELDS}
        body, violations = deals.parse_key_value_args(raw, key_map)
        if violations:
            raise DayControlInvalid(
                "Лимиты дня не разобраны — команда не отправлена. Формы: "
                + usage_preview(),
                violations=violations,
            )
        if not body:
            raise DayControlInvalid(
                "«ужесточить» и «снизить» — это не слово, а число: назовите "
                "ключи и значения. Формы: " + usage_preview(),
                violations=[{
                    "field": "limits",
                    "code": "empty_limits",
                    "message": "бот не множит и не уменьшает лимиты сам",
                }],
            )
        try:
            parsed = parse_limits(body)
        except TicketDenied as exc:
            raise DayControlInvalid(
                exc.message + ". Команда не отправлена.",
                reason_code=exc.code,
                violations=[{"field": code, "code": code, "message": ""}
                            for code in exc.reasons],
            ) from exc
        limits = {key: str(value) for key, value in parsed.items()}
        current = dict(day.get("limits") or {})
        expected = {key: current.get(key) for key in limits}

    open_positions = None
    if spec.closes_positions:
        open_positions = await _auto_open_positions(owner_id)

    return deals.tickets().put(DayTicket(
        ticket_id=str(uuid4()),
        owner_id=owner_id,
        day_id=str(day.get("day_id")),
        day_state=str(day.get("day_state")),
        action=spec.action,
        expires_at=_now() + timedelta(seconds=TICKET_TTL_SECONDS),
        limits=limits,
        expected=expected,
        settings_notes=tuple(str(item) for item in (day.get("settings_notes") or [])),
        open_auto_positions=open_positions,
        raw=(raw or "").strip(),
    ))


async def _resolve_ticket_id(owner_id: str, token: str) -> str:
    """Идентификатор разрешается только среди карточек управления днём."""
    try:
        return await deals.resolve_ticket_token(
            owner_id, token,
            kind=deals.KIND_DAY,
            not_found="такого тикета нет среди ваших живых — покажите заново: "
                      + usage_preview(),
        )
    except deals.TicketUnknown as exc:
        # Отказ остаётся в семье этого экрана: обработчик ловит
        # DayControlError, и чужой класс ушёл бы в аварийный обработчик
        # aiogram вместо карточки «операция не выполнена».
        raise DayControlUnknown(exc.message) from exc


async def confirm(owner_id: str, token: str) -> dict[str, Any]:
    """Подтвердить показанную карточку.  Единственная точка записи чата."""
    ticket_id = await _resolve_ticket_id(owner_id, token)
    record = deals.tickets().get(owner_id, ticket_id, kind=deals.KIND_DAY)
    if record is None or record.kind != deals.KIND_DAY:
        raise DayControlUnknown(
            "Тикет не найден — возможно, он уже истёк; покажите карточку заново: "
            + usage_preview()
        )
    now = _now()
    if record.is_expired(now):
        raise DayControlExpired(
            f"Карточка действительна {TICKET_TTL_SECONDS:.0f} с и истёкла в "
            f"{record.expires_at.isoformat()}; действие не выполнено."
        )

    # Состояние перечитывается: под старой карточкой день мог смениться или
    # получить другие лимиты, а подтверждать то, что оператор не видел, нельзя.
    day = await load_day(owner_id)
    if str(day.get("day_id")) != record.day_id:
        raise DayControlChanged(
            "Торговый день сменился между карточкой и подтверждением — старые "
            "числа к новому дню не относятся; действие не выполнено."
        )
    spec = ACTIONS[record.action]
    if spec.needs_limits:
        if str(day.get("day_state")) not in TRADABLE_DAY_STATES:
            raise DayControlChanged(
                f"День перешёл в состояние «{day.get('day_state')}» — лимиты "
                "посреди закрытия не меняются; команда не отправлена."
            )
        current = dict(day.get("limits") or {})
        moved = [
            key for key, value in record.expected.items()
            if current.get(key) != value
        ]
        if moved:
            raise DayControlChanged(
                "Лимиты дня успели измениться: "
                + ", ".join(f"{key} → {current.get(key)}" for key in sorted(moved))
                + ". Подписывать число, которое оператор не видел, нельзя — "
                  "покажите карточку заново.",
                violations=[{"field": key, "code": "limits_changed", "message": ""}
                            for key in sorted(moved)],
            )

    receipt = await _dispatch(record, owner_id)
    deals.tickets().remember_command(owner_id, str(receipt["command_id"]))
    return receipt


async def _dispatch(ticket: DayTicket, owner_id: str) -> dict[str, Any]:
    """Один вызов адаптера на действие — те же функции, что зовёт WebUI."""
    key = ticket.idempotency_key
    if ticket.action == ACTION_PAUSE:
        result = await pt_adapter.pause_auto(owner_id, idempotency_key=key)
    elif ticket.action == ACTION_RESUME:
        result = await pt_adapter.resume_auto(owner_id, idempotency_key=key)
    elif ticket.action == ACTION_CLOSE_ALL:
        result = await pt_adapter.close_all_positions(owner_id, idempotency_key=key)
    elif ticket.action == ACTION_FINISH:
        # Ключ у finish_day принадлежит дню (его выдаёт и планировщик runner'а),
        # поэтому сюда он не передаётся: совпадение — это уже стоящая команда.
        result = await pt_adapter.finish_day(owner_id)
    elif ticket.action == ACTION_LIMITS:
        result = await pt_adapter.set_day_limits(
            owner_id, dict(ticket.limits), expected=dict(ticket.expected),
            idempotency_key=key,
        )
    else:
        raise DayControlInvalid(
            f"Доменное действие «{ticket.action}» этому экрану неизвестно.",
            reason_code="unknown_action",
        )

    if not result.get("ok"):
        code = str(result.get("error") or "command_not_submitted")
        command_id = result.get("command_id")
        detail = denial_text(code)
        if code == "idempotency_conflict" and command_id:
            detail = (
                "Такая команда уже стоит в очереди (ключ идемпотентности занят)"
            )
        raise DayControlRefused(
            f"{detail}: действие не выполнено"
            + (f" — команда <code>{command_id}</code> уже зарегистрирована, "
               "повтор не нужен." if command_id else "."),
            reason_code=code,
        )
    return {
        "ok": True,
        "command_id": str(result["command_id"]),
        "action": ticket.action,
        "day_id": ticket.day_id,
        "ticket_id": ticket.ticket_id,
        "limits": dict(ticket.limits),
        "idempotency_key": ticket.idempotency_key,
    }


async def status(owner_id: str) -> dict[str, Any]:
    """Сводка для ``/day status``: состояние дня и действующие лимиты."""
    day = await load_day(owner_id)
    return {
        "day_id": str(day.get("day_id")),
        "day_state": str(day.get("day_state")),
        "limits": dict(day.get("limits") or {}),
        "settings_notes": [str(item) for item in (day.get("settings_notes") or [])],
    }
