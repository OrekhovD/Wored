"""paper_trading.ticket — единственная реализация ручной сделки (deal ticket).

Модуль проверяет и считает ручную заявку там, где это делает домен, а не UI.
До его появления математика тикета жила в ``webui/paper_api._preview``, а
настоящий риск-гейт ``paper_trading.risk.check_order_risk`` стоял только на
автоматической ветке; при этом ``PaperTradingRunner._handle_submit_order``
начинается с предпосылки «the preview API already validated risk» и исполняет
команду без доменной проверки. Теперь и WebUI, и Telegram зовут
:func:`build_ticket`, и эта предпосылка становится правдой.

Правила источников (ничего не выдумывается):
  * лимиты берутся из ``paper_v2_days.settings_snapshot``; если ключа нет —
    используется дефолт :class:`RiskSettings`, и это явно возвращается в
    ``settings_notes``, а не подставляется молча;
  * объём считает :func:`calculate_position_size` с округлением вниз до шага
    инструмента, а не ``risk / |entry - stop|``;
  * ликвидация считает :func:`calculate_liquidation_price` (изолированная
    маржа); качество числа честно помечается в ``liquidation_quality`` —
    собственная ставка поддержки приходится из tier'а рынка, а не из допущения;
  * все деньги — :class:`~decimal.Decimal`.

Python 3.9 compatible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from paper_trading.contracts import Account, AccountKind, Position, TradingDay
from paper_trading.market import PerpetualSnapshot
from paper_trading.risk import (
    DEFAULT_MAINTENANCE_MARGIN_RATE,
    FEE_RATE,
    OrderRequest,
    PositionInfo,
    RiskSettings,
    calculate_liquidation_price,
    calculate_position_size,
    check_order_risk,
)

__all__ = [
    "DENIAL_TEXTS",
    "TICKET_TTL_SECONDS",
    "TRADABLE_DAY_STATES",
    "TicketDenied",
    "TicketRequest",
    "TicketResult",
    "build_ticket",
    "day_state_value",
    "denial_text",
    "money_str",
    "risk_settings_from_day",
]

ZERO = Decimal(0)

#: Сколько живёт подтверждённый тикет. Раньше это число было захардкожено в
#: ``_preview``; теперь оно одно на домен и его же читают клиенты.
TICKET_TTL_SECONDS = 30.0

SIDES = ("buy", "sell")
ORDER_TYPES = ("market", "limit")

# Ключи settings_snapshot -> поле RiskSettings. Порядок фиксирует стабильный
# список notes.
_SETTINGS_KEYS = (
    ("max_risk_per_order_usdt", "max_risk_per_order"),
    ("max_daily_loss_usdt", "max_daily_loss"),
    ("max_leverage", "max_leverage"),
    ("max_open_risk_usdt", "max_open_risk"),
    ("min_net_rr", "min_net_rr"),
)

#: Состояния дня, в которые ещё можно принять ручную заявку. Одно определение
#: для тикета и для сервиса-гейта: ``active`` — имя UI-контракта, ``running`` —
#: имя домена; все остальные (idle/starting/closing/closed/settlement/…) — нет.
TRADABLE_DAY_STATES = frozenset({"running", "active"})


#: Тексты отказов, которые приходят не из формулы тикета, а из проверки
#: состояния (день, счёт, доступность источника). Оба клиента — WebUI и
#: Telegram — берут формулировку отсюда, чтобы один код не мог значить две
#: разные вещи. ``account_not_owned`` отвечает «Счёт не найден»: API не должен
#: перечислять счета других владельцев.
DENIAL_TEXTS: dict[str, str] = {
    "day_not_active": "Торговый день не активен",
    "no_active_day": "Торговый день не активен",
    "account_not_found": "Счёт не найден",
    "account_not_owned": "Счёт не найден",
    "no_manual_account": "Ручная заявка доступна только на ручном счёте",
    "not_manual_account": "Ручная заявка доступна только на ручном счёте",
    "position_not_found": "Позиция не найдена",
    "idempotency_conflict": "Команда с таким ключом уже существует и отличается по составу",
    "service_unavailable": "Источник данных дня недоступен",
    "domain_state_unavailable": "Источник данных дня недоступен",
}


def denial_text(code: Any, *, default: str | None = None) -> str:
    """Человеческий текст кода отказа; неизвестный код называется как есть."""
    key = str(code or "")
    return DENIAL_TEXTS.get(key) or default or key or "Отказ домена"


def day_state_value(day: TradingDay) -> str:
    """Нормализовать состояние дня в строку значения (enum или str)."""
    return str(getattr(day.state, "value", day.state))


class TicketDenied(Exception):
    """Отказ тикета: причина, человеческий текст и коды риск-гейта.

    ``code`` — стабильный идентификатор для клиента (не переводится),
    ``message`` — текст для оператора на русском, ``reasons`` — коды
    доменного гейта, когда отказ пришёл из :func:`check_order_risk`.
    """

    def __init__(self, code: str, message: str, reasons: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reasons = list(reasons)


def money_str(value: Decimal) -> str:
    """Формат денег на провод: 6 знаков, без хвостовых нулей."""
    return format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") or "0"


def _dec(value: Any, label: str, *, positive: bool = True, code: str = "invalid_number") -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TicketDenied(code, f"{label}: неверное число") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise TicketDenied(code, f"{label}: требуется положительное число")
    return result


@dataclass(frozen=True)
class TicketRequest:
    """Параметры ручной заявки так, как их вводит оператор.

    ``leverage is None`` означает «не выбран» — значение по умолчанию
    разрешается только после того, как станут известны лимиты дня
    (``min(10, max_leverage)``, как делал UI), поэтому дефолт живёт здесь,
    а не в парсере.
    """

    side: str = "buy"
    order_type: str = "market"
    risk: Decimal = Decimal(10)
    stop_price: Decimal = Decimal(0)
    take_profit: Decimal | None = None
    leverage: int | None = None
    instrument: str = "BTC-USDT"
    price: Decimal | None = None

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> "TicketRequest":
        """Собрать запрос из client-_payload. Тексты ошибок — прежние UI."""
        side = str(body.get("side", "buy")).lower()
        if side not in SIDES:
            raise TicketDenied("invalid_side", "Сторона должна быть buy или sell")
        order_type = str(body.get("order_type", "market")).lower()
        if order_type not in ORDER_TYPES:
            raise TicketDenied("invalid_order_type", "Тип должен быть market или limit")
        risk = _dec(body.get("risk"), "Риск", code="invalid_risk")
        stop = _dec(body.get("stop_price"), "Стоп", code="invalid_stop")
        take_raw = body.get("take_profit")
        take_profit = (
            _dec(take_raw, "Цель", code="invalid_target") if take_raw not in (None, "") else None
        )
        price = None
        if order_type == "limit":
            price = _dec(body.get("price"), "Цена Limit", code="invalid_price")
        leverage: int | None
        leverage_raw = body.get("leverage")
        if leverage_raw in (None, ""):
            leverage = None
        else:
            try:
                leverage = int(leverage_raw)
            except (TypeError, ValueError) as exc:
                raise TicketDenied(
                    "invalid_leverage", "Плечо должно быть целым числом"
                ) from exc
        instrument = str(body.get("instrument") or "BTC-USDT")
        return cls(
            side=side,
            order_type=order_type,
            risk=risk,
            stop_price=stop,
            take_profit=take_profit,
            leverage=leverage,
            instrument=instrument,
            price=price,
        )


@dataclass(frozen=True)
class TicketResult:
    """Проведённый и проверенный тикет. Числа уже в Decimal."""

    quantity: Decimal
    entry_price: Decimal
    notional: Decimal
    reserved_margin: Decimal
    entry_fee: Decimal
    estimated_exit_fee: Decimal
    break_even: Decimal
    estimated_net_at_sl: Decimal
    estimated_net_at_tp: Decimal | None
    liquidation_price: Decimal
    liquidation_quality: str
    net_rr: Decimal | None
    side: str
    order_type: str
    leverage: int
    account_label: str
    funding_status: str
    funding_rate: Decimal | None
    price_as_of: str | None
    expires_at: str
    instrument: str
    market: dict[str, Any] = field(default_factory=dict)
    settings_notes: list[str] = field(default_factory=list)
    risk_tier: dict[str, Any] | None = None

    def as_ui_dict(self) -> dict[str, Any]:
        """Форма ответа deal-ticket: набор ключей прежнего ``_preview``."""
        return {
            "allowed": True,
            "reasons": [],
            "account_label": self.account_label,
            "quantity": money_str(self.quantity),
            "notional": money_str(self.notional),
            "reserved_margin": money_str(self.reserved_margin),
            "entry_fee": money_str(self.entry_fee),
            "estimated_exit_fee": money_str(self.estimated_exit_fee),
            "funding_status": self.funding_status,
            "funding_rate": money_str(self.funding_rate) if self.funding_rate is not None else None,
            "break_even": money_str(self.break_even),
            "estimated_net_at_tp": (
                money_str(self.estimated_net_at_tp) if self.estimated_net_at_tp is not None else None
            ),
            "estimated_net_at_sl": money_str(self.estimated_net_at_sl),
            "liquidation_price": money_str(self.liquidation_price),
            "liquidation_quality": self.liquidation_quality,
            "price_as_of": self.price_as_of,
            "expires_at": self.expires_at,
            "entry_price": money_str(self.entry_price),
            "side": self.side,
            "order_type": self.order_type,
            "leverage": self.leverage,
            "market": self.market,
            # Аддитивные поля: старые клиенты их игнорируют, новые обязаны
            # показать, откуда взялись лимиты и ликвидация.
            "instrument": self.instrument,
            "net_rr": money_str(self.net_rr) if self.net_rr is not None else None,
            "settings_notes": list(self.settings_notes),
            "risk_tier": self.risk_tier,
        }


def risk_settings_from_day(day: TradingDay) -> tuple[RiskSettings, list[str]]:
    """RiskSettings из снапшота настроек дня + список подставленных дефолтов.

    Дефолт не прячется: каждый ключ, которого в снапшоте нет или который не
    разбирается в число, попадает в ``notes`` как имя ключа снапшота.
    """
    snapshot = dict(day.settings_snapshot or {})
    notes: list[str] = []
    values: dict[str, Any] = {}
    for snap_key, field_name in _SETTINGS_KEYS:
        if snap_key not in snapshot:
            notes.append(snap_key)
            continue
        raw = snapshot.get(snap_key)
        if field_name == "max_leverage":
            try:
                values[field_name] = int(Decimal(str(raw)))
            except (InvalidOperation, TypeError, ValueError):
                notes.append(snap_key)
            continue
        try:
            values[field_name] = _dec(raw, snap_key)
        except TicketDenied:
            notes.append(snap_key)
    settings = RiskSettings(**values) if values else RiskSettings()
    return settings, notes


def _position_infos(positions: Iterable[Position]) -> list[PositionInfo]:
    """Открытые позиции домена в агрегат для гейта (leverage им не читается)."""
    out: list[PositionInfo] = []
    for pos in positions:
        if pos.stop_loss is None:
            # Без стопа риск-агрегат не определим; позицию не выдумываем.
            continue
        out.append(
            PositionInfo(
                position_id=str(pos.position_id),
                direction=getattr(pos.side, "value", str(pos.side)),
                entry_price=pos.avg_entry_price,
                quantity=pos.qty,
                stop_price=pos.stop_loss,
                leverage=1,
                reserved_margin=pos.isolated_margin,
            )
        )
    return out


def build_ticket(
    *,
    day: TradingDay,
    account: Account,
    request: TicketRequest,
    snapshot: PerpetualSnapshot,
    available_margin: Decimal,
    open_positions: Iterable[Position] = (),
    realized_net: Decimal = ZERO,
    paused: bool = False,
    now: datetime | None = None,
) -> TicketResult:
    """Проверить и посчитать ручную заявку. Единственная реализация.

    ``available_margin`` — свободный баланс счёта из журнала проводок
    (``get_account_balance`` уже удерживает зарезервированную маржу),
    ``realized_net`` — реализованный результат дня со знаком (убыток
    отрицателен): гейт просадки ожидает именно знаковое значение PnL.
    Бросает :class:`TicketDenied`, если заявка не проходит.
    """
    moment = now or datetime.now(timezone.utc)
    if day_state_value(day) not in TRADABLE_DAY_STATES:
        raise TicketDenied("day_not_active", "Торговый день не активен", ("day_not_active",))
    kind = getattr(account.kind, "value", account.kind)
    if str(kind) != AccountKind.manual.value:
        raise TicketDenied(
            "not_manual_account",
            "Ручная заявка доступна только на ручном счёте",
            ("not_manual_account",),
        )

    settings, notes = risk_settings_from_day(day)
    side = request.side
    if side not in SIDES:
        raise TicketDenied("invalid_side", "Сторона должна быть buy или sell")
    if request.order_type not in ORDER_TYPES:
        raise TicketDenied("invalid_order_type", "Тип должен быть market или limit")

    max_risk = settings.max_risk_per_order
    if request.risk > max_risk:
        raise TicketDenied(
            "risk_over_order_limit",
            "Риск превышает лимит одной заявки",
            ("risk_over_order_limit",),
        )

    if request.order_type == "market":
        entry = snapshot.entry_price(side)
    else:
        if request.price is None:
            raise TicketDenied("invalid_price", "Цена Limit: неверное число")
        entry = _dec(request.price, "Цена Limit", code="invalid_price")
        # Движка ожидающих ордеров в домене нет: неисполнимую limit-заявку
        # нельзя ни посчитать, ни исполнить, поэтому она отсекается здесь же,
        # где и считалась раньше (текст прежний).
        marketable = (
            (side == "buy" and entry >= snapshot.ask)
            or (side == "sell" and entry <= snapshot.bid)
        )
        if not marketable:
            raise TicketDenied(
                "limit_not_marketable",
                "Нерыночная Limit-заявка требует отдельного движка ожидающих ордеров",
                ("limit_not_marketable",),
            )

    stop = request.stop_price
    if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
        raise TicketDenied(
            "stop_on_wrong_side",
            "Стоп должен быть ниже Long и выше Short",
            ("stop_on_wrong_side",),
        )

    target = request.take_profit
    if target is not None and (
        (side == "buy" and target <= entry) or (side == "sell" and target >= entry)
    ):
        raise TicketDenied(
            "target_on_wrong_side",
            "Цель должна быть выше Long и ниже Short",
            ("target_on_wrong_side",),
        )

    leverage = settings.max_leverage if request.leverage is None else request.leverage
    if leverage < 1 or leverage > settings.max_leverage:
        raise TicketDenied(
            "leverage_out_of_range",
            f"Плечо должно быть от 1x до лимита {settings.max_leverage}x",
            ("leverage_out_of_range",),
        )

    try:
        quantity = calculate_position_size(
            request.risk,
            entry,
            stop,
            quantity_step=snapshot.quantity_step,
            contract_size=snapshot.contract_size,
        )
    except ValueError as exc:
        raise TicketDenied("invalid_size", f"Объём не рассчитан: {exc}") from exc
    if quantity <= 0:
        raise TicketDenied(
            "quantity_below_step",
            "Объём меньше шага инструмента",
            ("quantity_below_step",),
        )

    notional = quantity * snapshot.contract_size * entry
    reserved_margin = notional / Decimal(leverage)
    if reserved_margin > available_margin:
        raise TicketDenied(
            "insufficient_margin",
            "Недостаточно доступной маржи",
            ("insufficient_margin",),
        )

    order = OrderRequest(
        instrument=request.instrument,
        direction="long" if side == "buy" else "short",
        # Гейт прайсит по стакану; тип заявки сюда не доходит — runner исполняет
        # ручную заявку по текущей цене рынка для обоих типов.
        order_type=request.order_type,
        risk_amount=request.risk,
        stop_price=stop,
        take_profit=target,
        leverage=leverage,
        quantity=quantity,
        created_at=moment,
        account_id=str(account.account_id),
        is_authenticated=True,
    )
    gate = check_order_risk(
        order,
        snapshot,
        settings,
        open_positions=_position_infos(open_positions),
        daily_realized_loss=realized_net,
        available_margin=available_margin,
        now=moment,
        is_paused=paused,
        is_day_active=True,
    )
    if gate.denied:
        raise TicketDenied(
            "risk_gate_denied",
            "Заявка отклонена риск-гейтом: " + ", ".join(gate.reasons),
            gate.reasons,
        )

    tier = snapshot.risk_tier
    maintenance = (
        tier.maintenance_margin_rate if tier is not None else DEFAULT_MAINTENANCE_MARGIN_RATE
    )
    liquidation = calculate_liquidation_price(
        entry,
        leverage,
        "long" if side == "buy" else "short",
        maintenance_margin_rate=maintenance,
        taker_fee_rate=settings.taker_fee_rate,
        isolated_margin=reserved_margin,
        notional=notional,
    )
    if tier is None and "risk_tier_absent" not in notes:
        notes.append("risk_tier_absent")

    entry_fee = notional * FEE_RATE
    exit_reference = target if target is not None else stop
    estimated_exit_fee = quantity * snapshot.contract_size * exit_reference * FEE_RATE
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    net_at_sl = (
        sign * quantity * snapshot.contract_size * (stop - entry)
        - entry_fee
        - quantity * snapshot.contract_size * stop * FEE_RATE
    )
    net_at_tp = None
    if target is not None:
        net_at_tp = (
            sign * quantity * snapshot.contract_size * (target - entry)
            - entry_fee
            - estimated_exit_fee
        )
    fee_per_unit = (
        entry_fee + quantity * snapshot.contract_size * entry * FEE_RATE
    ) / quantity
    break_even = entry + fee_per_unit if side == "buy" else entry - fee_per_unit

    return TicketResult(
        quantity=quantity,
        entry_price=entry,
        notional=notional,
        reserved_margin=reserved_margin,
        entry_fee=entry_fee,
        estimated_exit_fee=estimated_exit_fee,
        break_even=break_even,
        estimated_net_at_sl=net_at_sl,
        estimated_net_at_tp=net_at_tp,
        liquidation_price=liquidation,
        # Число из доменной формулы; ставка поддержки — из tier'а рынка, если
        # он есть в снапшоте, иначе общий дефолт домена.
        liquidation_quality=(
            "isolated_tier_maintenance" if tier is not None else "isolated_default_maintenance"
        ),
        net_rr=gate.net_rr,
        side=side,
        order_type=request.order_type,
        leverage=leverage,
        account_label="Ручной счёт",
        funding_status="known" if snapshot.funding_rate is not None else "unknown",
        funding_rate=snapshot.funding_rate,
        price_as_of=snapshot.source_at,
        expires_at=(moment + timedelta(seconds=TICKET_TTL_SECONDS)).isoformat(),
        instrument=request.instrument,
        market=snapshot.public_dict(),
        settings_notes=notes,
        risk_tier=(
            None
            if tier is None
            else {
                "tier": tier.tier,
                "max_leverage": tier.max_leverage,
                "maintenance_margin_rate": money_str(tier.maintenance_margin_rate),
                "source_at": tier.source_at,
                "source": tier.source,
            }
        ),
    )
