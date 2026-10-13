"""chatbot.services.paper_orders — ручная сделка в чате поверх доменного тикета.

Экран ``/deal`` ничего не считает.  Единственная реализация проверки и расчёта
ручной заявки — :func:`paper_trading.ticket.build_ticket` (Q-21), тот же вызов,
что делает WebUI-презентер.  Чат добавляет только то, чего в домене нет:
идентификацию владельца по Telegram, свой читатель стакана и короткоживущий
тикет, который подтверждается отдельным нажатием.

Правила среза (ТЗ §2, TB-03, §6):

* ни одного финансового числа от клиента: лимиты — ``settings_snapshot`` дня,
  доступная маржа — журнал проводок, открытые позиции и реализованный результат
  — БД; всё это отдаёт ``paper_trading.adapter.ticket_inputs``;
* тикет привязан к владельцу и к дню и живёт ровно ``TICKET_TTL_SECONDS``;
* подтверждение перечитывает состояние и **пересобирает** тикет: veto гейта,
  новый день или другой объём означают отказ, а не «исполнить как показано»;
* идемпотентность детерминирована: ``tg-<owner>-<ticket>``, поэтому повторное
  нажатие на ту же карточку — replay той же команды, а не вторая заявка;
* из чата позиция не закрывается (решение куратора по объёму P4);
* статус команды, не дошедший за бюджет опроса, — ``RESULT_UNKNOWN`` с
  ``command_id``: «не подтверждено», а не «не удалось».

Здесь нет импорта ``webui.paper_market``: в нём живёт одноимённый, но другой
``PerpetualSnapshot``, и доменный гейт его не принимает.

Домен вызывается через модуль :mod:`paper_trading.adapter` целиком, а не через
``from … import имя``: ``handlers/now.py`` и ``integrations/wored_api.py``
обращаются к адаптеру тем же способом, и ровно это держит единственную точку
подмены для тестов (``wired_sources``) живой.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from integrations import wored_api as api
from paper_trading import adapter as pt_adapter
from paper_trading.ticket import (
    TICKET_TTL_SECONDS,
    TicketDenied,
    TicketRequest,
    TicketResult,
    build_ticket,
    denial_text,
)
from security import principal as principal_mod
from services import simulation_plans as plans

log = logging.getLogger("chatbot.paper_orders")

__all__ = [
    "ARG_KEYS",
    "CommandStatusRead",
    "DealTicket",
    "MIN_BARE_ARG_KEYS",
    "NoActiveDay",
    "OrderSubmissionRefused",
    "PaperOrderError",
    "STATUS_POLL_TIMEOUT_SECONDS",
    "TICKET_TTL_SECONDS",
    "TicketChanged",
    "TicketExpired",
    "TicketInvalid",
    "TicketStore",
    "TicketUnknown",
    "TicketUnavailable",
    "command_status",
    "confirm",
    "owner_id_for",
    "parse_ticket_args",
    "positions_owner",
    "preview",
    "recall",
    "recognises_ticket_args",
    "resolve_command_id",
    "tickets",
    "tradable_instrument",
    "usage_preview",
]

#: аргументы тикета, как их вводит оператор → имена тела доменного запроса.
#: Карточка подсказки рендерится отсюда, поэтому текст и парсер не разъедутся.
ARG_KEYS: dict[str, str] = {
    "side": "side",
    "type": "order_type",
    "risk": "risk",
    "stop": "stop_price",
    "tp": "take_profit",
    "lev": "leverage",
    "inst": "instrument",
    "price": "price",
}

#: без них объём определить нельзя, остальное домен берёт по умолчанию
REQUIRED_KEYS = ("risk", "stop")

#: Порог «строка key=value — это аргументы /deal» для отказа Q-20: не меньше
#: двух признанных ключей, и среди них один обязательный.  Один-единственный
#: `risk=10` или `price=5` — ещё не заявка, а `type=market price=100` без риска и
#: стопа торговое намерение не выражает; такие строки остаются тексту.
MIN_BARE_ARG_KEYS = 2

#: сколько ждём терминального статуса команды и с каким шагом опрашиваем.
#: Константы, а не ключи ENV: новых переменных окружения срез не вводит.
STATUS_POLL_TIMEOUT_SECONDS = 10.0
STATUS_POLL_INTERVAL_SECONDS = 1.0
STATUS_READ_TIMEOUT_SECONDS = 3.0

#: статусы команды, после которых больше ждать нечего (``CommandStatus``)
TERMINAL_STATUSES = frozenset({"completed", "failed", "conflict"})


def usage_preview(*, instrument: str | None = None) -> str:
    """Точная форма команды — её же показывает отказ парсера."""
    pair = f" inst={instrument}" if instrument else ""
    return f"/deal preview side=buy risk=10{pair} stop=63000 tp=66000 lev=10"


# ─── отказа ─────────────────────────────────────────────────────────────────


class PaperOrderError(plans.PlanServiceError):
    """Отказ ручной сделки: код + человеческий текст, как и у экранов планов."""

    reason_code = "paper_order_error"

    def __init__(self, message: str, *, reason_code: str | None = None,
                 violations: list[dict[str, str]] | None = None) -> None:
        super().__init__(message, reason_code=reason_code)
        self.violations = list(violations or [])


class TicketInvalid(PaperOrderError):
    """Аргументы не разобраны: карточка обязана показать точную команду."""

    reason_code = "ticket_invalid"


class TicketUnavailable(PaperOrderError):
    """Домен не дал входов тикета: нет дня, нет ручного счёта, нет источника."""

    reason_code = "paper_state_unavailable"


class NoActiveDay(TicketUnavailable):
    reason_code = "no_active_day"


class TicketUnknown(PaperOrderError):
    """Ни своего живого тикета, ни чужого: существование чужого не раскрывается."""

    reason_code = "ticket_not_found"


class TicketExpired(PaperOrderError):
    reason_code = "ticket_expired"


class TicketChanged(PaperOrderError):
    """Между карточкой и подтверждением изменилось то, под чем подписывались."""

    reason_code = "ticket_changed"


class OrderSubmissionRefused(PaperOrderError):
    reason_code = "order_not_submitted"


class StatusUnknown(PaperOrderError):
    """Команда есть, её состояния подтвердить нечем: «не подтверждено»."""

    reason_code = "RESULT_UNKNOWN"

    def __init__(self, message: str, *, command_id: str) -> None:
        super().__init__(message)
        self.command_id = command_id


# ─── identity ───────────────────────────────────────────────────────────────


def owner_id_for(telegram_user_id: int) -> str:
    """Канонический владелец — то же правило, что у WebUI и домена."""
    return principal_mod.owner_id_for(telegram_user_id)


def tradable_instrument() -> str:
    """Единственный контракт, который бот готов обсудить в тикете.

    Ручная команда не несёт инструмент (``submit_manual_order`` пишет side/qty/
    stop/tp), а исполняется на контракте, настроенном для дня.  Обещать числа
    для другого инструмента — значит соврать в карточке, поэтому чат принимает
    только настроенный контракт; долг фиксируется в RFC.
    """
    return api.default_contract()


# ─── parser (shape only) ────────────────────────────────────────────────────


def parse_ticket_args(raw: str) -> dict[str, Any]:
    """``side=buy risk=10 stop=63000 …`` → тело заявки для домена.

    Проверяется только форма: токен есть ``key=value``, ключ известен, ключ не
    повторяется, значение не пустое, обязательные поля на месте.  Разбором
    значений и чисел занимается доменный :meth:`TicketRequest.from_body` —
    та же реализация, что у WebUI (ТЗ TB-03).
    """
    body: dict[str, Any] = {}
    violations: list[dict[str, str]] = []
    for token in (raw or "").split():
        key, sep, value = token.partition("=")
        field = ARG_KEYS.get(key.strip().lower())
        if not sep or not field:
            violations.append({
                "field": key or token,
                "code": "bad_token",
                "message": "формат ответа: key=value; разрешённые ключи: "
                           + ", ".join(sorted(ARG_KEYS)),
            })
            continue
        if field in body:
            violations.append({
                "field": field,
                "code": "duplicate_key",
                "message": f"ключ «{key}» указан дважды — последний ответ важнее первого, "
                           "поэтому бот ничего не выбирает сам",
            })
            continue
        if not value.strip():
            violations.append({
                "field": field,
                "code": "empty_value",
                "message": "значение пустое — уберите ключ или заполните его",
            })
            continue
        body[field] = value.strip()

    missing = [ARG_KEYS[key] for key in REQUIRED_KEYS if ARG_KEYS[key] not in body]
    if missing:
        violations.append({
            "field": ", ".join(missing),
            "code": "missing_required",
            "message": "без риска и стопа объём не определить; полная форма: "
                       + usage_preview(instrument=tradable_instrument()),
        })
    if violations:
        raise TicketInvalid(
            "Аргументы тикета не разобраны — заявка не отправлена. Полная форма: "
            + usage_preview(instrument=tradable_instrument()),
            violations=violations,
        )

    instrument = str(body.get("instrument") or tradable_instrument()).upper()
    if instrument != tradable_instrument().upper():
        raise TicketInvalid(
            f"Инструмент {instrument} в чате недоступен: ручная команда дня "
            f"исполняется на {tradable_instrument()}, и числа для другого "
            "инструмента были бы выдуманы.",
            violations=[{
                "field": "instrument",
                "code": "instrument_not_tradable",
                "message": f"разрешён только {tradable_instrument()}",
            }],
        )
    body["instrument"] = instrument
    return body


def recognises_ticket_args(text: str) -> bool:
    """В строке два и больше ключей тикета, и хотя бы один обязательный.

    Форма «каждый токен — key=value» — правило поверхности
    (``handlers/telegram_surface.py``), здесь проверяется только принадлежность
    ключей этому экрану.  Значения не разбираются: отказ обязан называть команду
    и при битых числах, иначе здесь появился бы второй парсер тикета (TB-03).
    """
    fields = {
        ARG_KEYS.get(token.partition("=")[0].strip().lower())
        for token in (text or "").split()
    } - {None}
    required = set(REQUIRED_KEYS)
    return len(fields) >= MIN_BARE_ARG_KEYS and bool(fields & required)


# ─── ticket store ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DealTicket:
    """Тикет, показанный оператору: его числа и есть предмет подтверждения."""

    ticket_id: str
    owner_id: str
    account_id: str
    day_id: str
    request: TicketRequest
    result: TicketResult
    expires_at: datetime
    raw: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def short_id(self) -> str:
        return self.ticket_id[:8]

    @property
    def idempotency_key(self) -> str:
        """Детерминированный ключ: та же карточка → та же команда."""
        return f"tg-{self.owner_id}-{self.ticket_id}"

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class TicketStore:
    """In-memory тикеты и последние команды чата.

    Хранилище сознательно не БД: тикет — это намерение одного оператора на
    30 секунд, а не торговый факт; фактом его делает команда в
    ``paper_v2_commands``.  Записи хранятся с привязкой к владельцу, поэтому
    чужой идентификатор не отличим от несуществующего.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, DealTicket] = {}
        self._commands: dict[str, list[str]] = {}

    def put(self, ticket: DealTicket) -> DealTicket:
        self.prune(datetime.now(timezone.utc))
        self._tickets[ticket.ticket_id] = ticket
        return ticket

    def get(self, owner_id: str, ticket_id: str) -> DealTicket | None:
        ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.owner_id != owner_id:
            return None
        return ticket

    def peek(self, owner_id: str, ticket_id: str) -> DealTicket | None:
        """Тикет без проверки TTL — нужен для «пересобрать по прежним аргументам».

        Само подтверждение TTL проверяет обязательно; здесь важно только то,
        что чужой идентификатор по-прежнему не отличим от несуществующего.
        """
        return self.get(owner_id, ticket_id)

    def all_ids(self, owner_id: str) -> list[str]:
        """Идентификаторы владельца, включая истёкшие."""
        return [
            ticket_id
            for ticket_id, ticket in self._tickets.items()
            if ticket.owner_id == owner_id
        ]

    def remember_command(self, owner_id: str, command_id: str) -> None:
        recent = self._commands.setdefault(owner_id, [])
        recent.append(command_id)
        del recent[:-20]

    def command_ids(self, owner_id: str) -> list[str]:
        return list(self._commands.get(owner_id) or [])

    def prune(self, now: datetime) -> int:
        stale = [
            ticket_id
            for ticket_id, ticket in self._tickets.items()
            if ticket.is_expired(now)
        ]
        for ticket_id in stale:
            del self._tickets[ticket_id]
        return len(stale)

    def reset(self) -> None:
        self._tickets.clear()
        self._commands.clear()

    def __len__(self) -> int:
        return len(self._tickets)


_TICKETS = TicketStore()


def tickets() -> TicketStore:
    """Один магазин на процесс: карточка и кнопка должны видеть один тикет."""
    return _TICKETS


async def positions_owner(owner_id: str):
    """Позиции владельца — только существующий читатель ``wored_api``.

    Экран рендерит ``handlers.now.show_positions`` (тот же, что у ``/positions``):
    вторая карточка или второй читатель позиций в боте запрещены (TB-03), и
    презентер не считает PnL.
    """
    return await api.read_session_state(owner_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── domain inputs ──────────────────────────────────────────────────────────


async def load_state(owner_id: str) -> dict[str, Any]:
    """Авторитетное состояние дня; отсутствие дня — честный отказ.

    ``ok=false`` (домен недоступен) и ``day=None`` (дня просто нет) — разные
    условия, и карточка обязана назвать то из них, которое случилось на самом
    деле (ТЗ §6); позицию в песочнице ни то, ни другое не создаёт.
    """
    try:
        state = await pt_adapter.get_current_state(owner_id)
    except Exception as exc:
        log.warning("deal state read failed: %s", exc)
        raise TicketUnavailable(
            denial_text("domain_state_unavailable")
            + ": состояние дня не прочитано, заявка не отправлена."
        ) from exc
    if not state.get("ok"):
        raise TicketUnavailable(
            denial_text(state.get("error") or "domain_state_unavailable")
            + ": заявка не отправлена."
        )
    if not state.get("day"):
        raise NoActiveDay(
            "Торговый день не запущен. Ручные сделки доступны только внутри "
            "активного дня — запустите его в WebUI."
        )
    return state


async def _manual_account_id(owner_id: str, state: dict[str, Any]) -> str:
    manual = next(
        (a for a in (state.get("accounts") or []) if str(a.get("kind")) == "manual"),
        None,
    )
    if manual is None:
        raise TicketUnavailable(denial_text("no_manual_account") + ": заявка не отправлена.")
    return str(manual.get("id"))


async def _ticket_inputs(owner_id: str, account_id: str) -> dict[str, Any]:
    try:
        inputs = await pt_adapter.ticket_inputs(owner_id=owner_id, account_id=account_id)
    except Exception as exc:
        log.warning("deal ticket inputs failed: %s", exc)
        raise TicketUnavailable(
            denial_text("domain_state_unavailable") + ": входы тикета не получены."
        ) from exc
    if not inputs.get("ok"):
        raise TicketUnavailable(denial_text(inputs.get("error")))
    return inputs


async def _market_snapshot(instrument: str):
    read = await api.read_perpetual_snapshot(instrument)
    if read.snapshot is None:
        raise TicketUnavailable(
            f"Стакан {instrument} недоступен ({', '.join(read.reason_codes) or 'нет данных'}): "
            "тикет не рассчитан."
        )
    return read


def _request_from(body: dict[str, Any]) -> TicketRequest:
    try:
        return TicketRequest.from_body(body)
    except TicketDenied as exc:
        raise TicketInvalid(
            exc.message,
            reason_code=exc.code,
            violations=[{"field": exc.code, "code": code, "message": ""
                         } for code in exc.reasons],
        ) from exc


def _build(request: TicketRequest, inputs: dict[str, Any], snapshot: Any) -> TicketResult:
    """Один вызов доменного тикета — из доменных входов, не из карточки."""
    return build_ticket(
        day=inputs["day"],
        account=inputs["account"],
        request=request,
        snapshot=snapshot,
        available_margin=inputs["available_margin"],
        open_positions=inputs["open_positions"],
        realized_net=inputs["realized_net"],
    )


# ─── use cases: command and button both call these ─────────────────────────


async def preview(owner_id: str, raw: str) -> DealTicket:
    """Собрать тикет и положить его в магазин под ``ticket_id``."""
    body = parse_ticket_args(raw)
    request = _request_from(body)

    state = await load_state(owner_id)
    account_id = await _manual_account_id(owner_id, state)
    inputs = await _ticket_inputs(owner_id, account_id)
    read = await _market_snapshot(request.instrument)

    try:
        result = _build(request, inputs, read.snapshot)
    except TicketDenied as exc:
        raise PaperOrderError(
            exc.message,
            reason_code=exc.code,
            violations=[{"field": "risk_gate", "code": code, "message": "код доменного гейта"}
                        for code in exc.reasons],
        ) from exc

    expires_at = datetime.fromisoformat(result.expires_at)
    return tickets().put(DealTicket(
        ticket_id=str(uuid4()),
        owner_id=owner_id,
        account_id=str(inputs["account_id"]),
        day_id=str(inputs["day_id"]),
        request=request,
        result=result,
        expires_at=expires_at,
        raw=(raw or "").strip(),
    ))


async def _resolve_ticket_id(owner_id: str, token: str) -> str:
    """Полный uuid или единственный префикс собственного тикета.

    Кандидаты — все тикеты владельца, а не только живые: истёкший тикет обязан
    отвечаться как истёкший (:class:`TicketExpired`), а не как несуществующий,
    потому что для оператора это два разных события (ТЗ §6). Чужой
    идентификатор в список кандидатов не попадает вообще.
    """
    store = tickets()

    async def _own_ids() -> list[str]:
        return store.all_ids(owner_id)

    try:
        return await plans.resolve_prefixed(
            token, _own_ids,
            entity_label="тикета", entity_dative="тикетам",
            not_found="такого тикета нет среди ваших живых — соберите заново: "
                      + usage_preview(instrument=tradable_instrument()),
            reason_code="ambiguous_ticket_id",
        )
    except plans.PlanServiceError as exc:
        raise TicketUnknown(exc.message) from exc


async def recall(owner_id: str, token: str) -> DealTicket:
    """Тикет по идентификатору, включая истёкший — только ради пересборки.

    Новый тикет строится заново со свежими входами; старый нужен здесь лишь
    как источник аргументов оператора, чтобы не просить их ввести повторно.
    Правило «что такое разрешимый идентификатор» — общее, :func:`_resolve_ticket_id`.
    """
    ticket_id = await _resolve_ticket_id(owner_id, token)
    record = tickets().peek(owner_id, ticket_id)
    if record is None:
        raise TicketUnknown("такого тикета нет среди ваших")
    return record


async def confirm(owner_id: str, token: str) -> dict[str, Any]:
    """Подтвердить показанный тикет. Ничего не отправляется без пересборки."""
    ticket_id = await _resolve_ticket_id(owner_id, token)
    record = tickets().get(owner_id, ticket_id)
    if record is None:
        raise TicketUnknown(
            "Тикет не найден — возможно, он уже истёк; соберите заново: "
            + usage_preview(instrument=tradable_instrument())
        )
    now = _now()
    if record.is_expired(now):
        raise TicketExpired(
            f"Тикет действителен {TICKET_TTL_SECONDS:.0f} с и истёк в "
            f"{record.expires_at.isoformat()}; заявка не отправлена."
        )

    # Состояние перечитывается: цена, маржа и позиции могли уйти с момента
    # карточки, а подтверждать по старым числам нельзя.
    state = await load_state(owner_id)
    account_id = await _manual_account_id(owner_id, state)
    day = state.get("day") or {}
    if str(day.get("id")) != record.day_id:
        raise TicketChanged(
            "Торговый день сменился между тикетом и подтверждением — старые "
            "числа к новому дню не относятся; заявка не отправлена."
        )
    if account_id != record.account_id:
        raise TicketChanged(
            "Ручной аккаунт сменился между тикетом и подтверждением — маржа и "
            "лимиты на карточке относятся к другому счёту; заявка не отправлена."
        )
    inputs = await _ticket_inputs(owner_id, account_id)
    read = await _market_snapshot(record.request.instrument)
    try:
        rebuilt = _build(record.request, inputs, read.snapshot)
    except TicketDenied as exc:
        raise OrderSubmissionRefused(
            "Гейт отказал при подтверждении: " + exc.message,
            reason_code=exc.code,
            violations=[{"field": "risk_gate", "code": code, "message": "код доменного гейта"}
                        for code in exc.reasons],
        ) from exc

    if rebuilt.quantity != record.result.quantity:
        raise TicketChanged(
            f"Объём пересчитался с {record.result.quantity} на {rebuilt.quantity} — "
            "подтверждать число, которого оператор не видел, нельзя; "
            "покажите тикет заново: " + usage_preview(instrument=tradable_instrument())
        )

    result = await pt_adapter.submit_manual_order(
        owner_id=owner_id,
        account_id=record.account_id,
        side=record.result.side,
        order_type=record.result.order_type,
        qty=str(record.result.quantity),
        stop_loss=str(record.request.stop_price),
        take_profit=(
            str(record.request.take_profit) if record.request.take_profit is not None else None
        ),
        idempotency_key=record.idempotency_key,
        expected_day_id=record.day_id,
    )
    if not result.get("ok"):
        code = str(result.get("error") or "order_not_submitted")
        command_id = result.get("command_id")
        detail = denial_text(code)
        if command_id:
            detail += f" — команда <code>{command_id}</code> уже занята этим ключом."
        raise OrderSubmissionRefused(
            f"{detail}. Заявка не отправлена повторно.",
            reason_code=code,
        )
    command_id = str(result["command_id"])
    tickets().remember_command(owner_id, command_id)
    return {
        "ok": True,
        "command_id": command_id,
        "ticket_id": record.ticket_id,
        "day_id": record.day_id,
        "account_id": record.account_id,
        "quantity": record.result.quantity,
        "idempotency_key": record.idempotency_key,
    }


async def resolve_command_id(owner_id: str, token: str) -> str:
    """Полный uuid команды или единственный префикс из памяти этого владельца."""
    text = (token or "").strip()
    if not text:
        raise TicketUnknown("укажите command_id из карточки подтверждения")
    store = tickets()
    matches = [c for c in store.command_ids(owner_id) if c.startswith(text)]
    if len(matches) > 1:
        raise TicketUnknown(
            f"префикс «{text}» подходит к {len(matches)} командам — уточните идентификатор"
        )
    if matches:
        return matches[0]
    try:
        UUID(text)
    except ValueError as exc:
        raise TicketUnknown(
            "такой идентификатор не похож на uuid команды; полный command_id — "
            "из карточки подтверждения или из WebUI"
        ) from exc
    return text


@dataclass(frozen=True)
class CommandStatusRead:
    command_id: str
    status: str
    settled: bool
    result: dict[str, Any] | None
    error: str | None

    @property
    def position_id(self) -> str | None:
        if isinstance(self.result, dict):
            pid = self.result.get("position_id")
            if pid:
                return str(pid)
        return None


async def _read_once(owner_id: str, command_id: str) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            pt_adapter.get_command_status(command_id, owner_id=owner_id),
            timeout=STATUS_READ_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise StatusUnknown(
            f"Статус команды <code>{command_id}</code> не подтверждён — "
            "опрос домена не успел завершиться.",
            command_id=command_id,
        ) from exc
    except Exception as exc:
        log.warning("command status read failed for %s: %s", command_id, exc)
        raise StatusUnknown(
            f"Статус команды <code>{command_id}</code> не подтверждён: "
            "домен недоступен.",
            command_id=command_id,
        ) from exc


async def command_status(owner_id: str, token: str) -> CommandStatusRead:
    """Опросить статус с бюджетом; не дождались терминала — ``settled=False``.

    ``RESULT_UNKNOWN`` поднимается только когда сам опрос не удался: в этом
    случае ничего не известно, и карточка обязана назвать ``command_id``, чтобы
    оператор проверил его позже, а не повторял заявку.
    """
    command_id = await resolve_command_id(owner_id, token)
    deadline = time.monotonic() + STATUS_POLL_TIMEOUT_SECONDS
    payload: dict[str, Any] = {}
    while True:
        payload = await _read_once(owner_id, command_id)
        if not payload.get("ok"):
            code = str(payload.get("error") or "command_not_found")
            if code == "command_not_found":
                # чужая команда и несуществующая отвечаются одинаково
                raise TicketUnknown(
                    f"Команды <code>{command_id}</code> у этого владельца нет."
                )
            raise TicketUnknown(
                denial_text(code) + ": статус команды не прочитан.",
                reason_code=code,
            )
        status = str(api.enum_value(payload.get("status")) or "")
        # enum → значение: ``get_command_status`` отдаёт ``CommandStatus``, а
        # ``str(CommandStatus.completed)`` — «CommandStatus.completed», не
        # «completed»; на таком «статусе» опрос никогда не увидел бы терминала.
        error = api.enum_value(payload.get("error"))
        if status in TERMINAL_STATUSES or time.monotonic() >= deadline:
            return CommandStatusRead(
                command_id=command_id,
                status=status or "unknown",
                settled=status in TERMINAL_STATUSES,
                result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
                error=str(error) if error is not None else None,
            )
        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)
