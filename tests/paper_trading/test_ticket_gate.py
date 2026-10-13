"""Гейт единого тикета ручной сделки (:mod:`paper_trading.ticket`).

Тестируется то, ради чего тикет вынесен в домен: единственный источник
чисел и единственный veto, одинаковые для WebUI и Telegram. Числа считаются
по контракту BTC-USDT (contract_size 0.001, шаг объёма 0.001), как в
``tests/paper_trading/test_market_risk_tier.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from paper_trading.contracts import Account, AccountKind, DayState, TradingDay
from paper_trading.market import PerpetualSnapshot
from paper_trading.ticket import (
    TICKET_TTL_SECONDS,
    TicketDenied,
    TicketRequest,
    build_ticket,
    risk_settings_from_day,
)

OWNER = uuid4()
MANUAL = uuid4()

SETTINGS = {
    "opening_capital": "1000",
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "min_net_rr": "1.0",
    "timezone": "Asia/Bangkok",
    "end_time_local": "21:00",
}

# Ключи, которые читает deal-ticket UI. Список зафиксирован: тикет имеет право
# добавлять поля, но не имеет права убирать эти.
UI_KEYS = {
    "allowed", "reasons", "account_label", "quantity", "notional",
    "reserved_margin", "entry_fee", "estimated_exit_fee", "funding_status",
    "funding_rate", "break_even", "estimated_net_at_tp", "estimated_net_at_sl",
    "liquidation_price", "liquidation_quality", "price_as_of", "expires_at",
    "entry_price", "side", "order_type", "leverage", "market",
}


def snapshot(*, stale: bool = False, tier: bool = False) -> PerpetualSnapshot:
    stamp = datetime.now(timezone.utc)
    if stale:
        stamp -= timedelta(seconds=600)
    now = stamp.isoformat()
    from paper_trading.market import RiskTier

    return PerpetualSnapshot(
        schema_version=1,
        mode="live",
        venue="htx",
        market_type="linear-swap",
        contract_code="BTC-USDT",
        bid=Decimal("99990"),
        ask=Decimal("100000"),
        last=Decimal("99995"),
        mark=Decimal("99995"),
        index=Decimal("99994"),
        funding_rate=Decimal("0.0001"),
        next_funding_at=None,
        component_times={"ticker": now, "index": now, "mark": now, "funding": now},
        contract_size=Decimal("0.001"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        source_at=now,
        received_at=now,
        source="test",
        quality="live",
        risk_tier=(
            RiskTier(1, 20, Decimal("0.0028"), now, "fixture") if tier else None
        ),
    )


def day(settings: dict | None = SETTINGS, state: DayState = DayState.running) -> TradingDay:
    return TradingDay(
        day_id=uuid4(),
        owner_id=OWNER,
        state=state,
        settings_snapshot=dict(settings or {}),
    )


def account(kind: AccountKind = AccountKind.manual) -> Account:
    return Account(
        account_id=MANUAL,
        owner_id=OWNER,
        kind=kind,
        opening_deposit=Decimal("1000"),
    )


def request(**overrides) -> TicketRequest:
    base = dict(side="buy", order_type="market", risk=Decimal(1), stop_price=Decimal("99000"))
    base.update(overrides)
    return TicketRequest(**base)


def build(**kwargs):
    kwargs.setdefault("day", day())
    kwargs.setdefault("account", account())
    kwargs.setdefault("request", request())
    kwargs.setdefault("snapshot", snapshot())
    kwargs.setdefault("available_margin", Decimal("1000"))
    return build_ticket(**kwargs)


# ─── happy path ────────────────────────────────────────────────────────


def test_market_ticket_costs_by_contract_size_and_step() -> None:
    result = build()

    # risk 1 / (1000 * 0.001) = 1 контракт; notional = 1 * 0.001 * 100000
    assert result.quantity == Decimal("1")
    assert result.notional == Decimal("100.000")
    assert result.reserved_margin == Decimal("10.000")
    assert result.entry_fee == Decimal("0.05")
    assert result.entry_price == Decimal("100000")  # ask для buy
    assert result.net_rr is None  # цели нет — RR не считается


def test_ui_contract_keys_are_preserved() -> None:
    payload = build().as_ui_dict()

    assert UI_KEYS <= set(payload)
    assert payload["allowed"] is True and payload["reasons"] == []
    assert payload["account_label"] == "Ручной счёт"
    assert payload["side"] == "buy" and payload["leverage"] == 10
    assert payload["market"]["contract_code"] == "BTC-USDT"


def test_ttl_and_expiry_come_from_one_constant() -> None:
    moment = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    result = build(now=moment)

    assert result.expires_at == (moment + timedelta(seconds=TICKET_TTL_SECONDS)).isoformat()


def test_liquidation_uses_domain_formula_and_labels_tier_absence() -> None:
    without_tier = build()
    with_tier = build(snapshot=snapshot(tier=True))

    assert without_tier.liquidation_quality == "isolated_default_maintenance"
    assert "risk_tier_absent" in without_tier.settings_notes
    assert without_tier.risk_tier is None
    assert with_tier.liquidation_quality == "isolated_tier_maintenance"
    assert "risk_tier_absent" not in with_tier.settings_notes
    assert with_tier.risk_tier["max_leverage"] == 20
    # цена ликвидации ниже входа для long и не выдумана: 10x, maintenance 0.28%
    assert without_tier.liquidation_price < Decimal("100000")


def test_take_profit_produces_net_rr_and_tp_scenario() -> None:
    result = build(request=request(take_profit=Decimal("102000")))

    assert result.estimated_net_at_tp is not None
    assert result.net_rr is not None and result.net_rr > 1


# ─── отказы: тексты и коды прежние, решение домена ─────────────────────


def test_auto_account_is_not_manual_tradeable() -> None:
    try:
        build(account=account(AccountKind.auto))
    except TicketDenied as exc:
        assert exc.code == "not_manual_account"
        assert exc.message == "Ручная заявка доступна только на ручном счёте"
    else:
        raise AssertionError("auto-счёт принят как ручной")


def test_inactive_day_is_refused() -> None:
    for state in (DayState.idle, DayState.closing, DayState.closed):
        try:
            build(day=day(state=state))
        except TicketDenied as exc:
            assert exc.code == "day_not_active"
        else:
            raise AssertionError(f"день {state} принят")


def test_risk_over_per_order_limit() -> None:
    try:
        build(request=request(risk=Decimal(50)))
    except TicketDenied as exc:
        assert exc.code == "risk_over_order_limit"
        assert exc.message == "Риск превышает лимит одной заявки"
    else:
        raise AssertionError("сверх-лимит принят")


def test_stop_must_sit_behind_entry() -> None:
    try:
        build(request=request(stop_price=Decimal("100500")))
    except TicketDenied as exc:
        assert exc.code == "stop_on_wrong_side"
    else:
        raise AssertionError("стоп выше входа принят")


def test_target_must_sit_ahead_of_entry() -> None:
    try:
        build(request=request(take_profit=Decimal("99500")))
    except TicketDenied as exc:
        assert exc.code == "target_on_wrong_side"
    else:
        raise AssertionError("цель за стопом принята")


def test_leverage_out_of_day_range() -> None:
    try:
        build(request=request(leverage=50))
    except TicketDenied as exc:
        assert exc.code == "leverage_out_of_range"
        assert "10x" in exc.message
    else:
        raise AssertionError("плечо выше лимита принято")


def test_margin_shortage_is_refused_before_the_gate() -> None:
    try:
        build(available_margin=Decimal("1"))
    except TicketDenied as exc:
        assert exc.code == "insufficient_margin"
    else:
        raise AssertionError("заявка без маржи прошла")


def test_quantity_below_step_is_refused_instead_of_zero_position() -> None:
    try:
        build(request=request(risk=Decimal("0.0001")))
    except TicketDenied as exc:
        assert exc.code == "quantity_below_step"
    else:
        raise AssertionError("нулевой объём прошёл")


def test_non_marketable_limit_is_refused() -> None:
    try:
        build(request=request(order_type="limit", price=Decimal("90000")))
    except TicketDenied as exc:
        assert exc.code == "limit_not_marketable"
    else:
        raise AssertionError("нерыночная limit прошла")


def test_marketable_limit_is_accepted_at_its_price() -> None:
    result = build(request=request(order_type="limit", price=Decimal("100010")))

    assert result.order_type == "limit"
    assert result.entry_price == Decimal("100010")


def test_stale_snapshot_reaches_the_domain_gate() -> None:
    try:
        build(snapshot=snapshot(stale=True))
    except TicketDenied as exc:
        assert exc.code == "risk_gate_denied"
        assert "stale_snapshot" in exc.reasons
    else:
        raise AssertionError("протухший снапшот прошёл")


def test_reward_risk_below_minimum_is_denied_by_the_gate() -> None:
    try:
        build(request=request(take_profit=Decimal("100100")))
    except TicketDenied as exc:
        assert exc.code == "risk_gate_denied"
        assert "net_rr_below_minimum" in exc.reasons
    else:
        raise AssertionError("RR ниже минимума прошёл")


def test_open_risk_aggregation_blocks_the_third_leg() -> None:
    from paper_trading.contracts import Position, PositionSide, PositionStatus

    held = Position(
        position_id=uuid4(),
        account_id=MANUAL,
        day_id=day().day_id,
        side=PositionSide.long,
        qty=Decimal("15"),
        avg_entry_price=Decimal("100000"),
        isolated_margin=Decimal("100"),
        stop_loss=Decimal("99000"),
        status=PositionStatus.open,
    )
    # 15 контрактов * 0.001 * 1000 = 15 USDT открытого риска, плюс новый
    # риск 10 USDT = 25 > max_open_risk 20 из снапшота дня.
    assert held.qty * Decimal("0.001") * abs(held.avg_entry_price - held.stop_loss) == Decimal("15")
    try:
        build(request=request(risk=Decimal(10)), open_positions=[held])
    except TicketDenied as exc:
        assert exc.code == "risk_gate_denied"
        assert "max_open_risk_exceeded" in exc.reasons
    else:
        raise AssertionError("превышение открытого риска прошло")


def test_daily_drawdown_gate_uses_journal_result() -> None:
    try:
        build(realized_net=Decimal("-60"))
    except TicketDenied as exc:
        assert exc.code == "risk_gate_denied"
        assert "daily_drawdown_exceeded" in exc.reasons
    else:
        raise AssertionError("просадка выше лимита прошла")


def test_paused_day_blocks_new_entries() -> None:
    try:
        build(paused=True)
    except TicketDenied as exc:
        assert exc.code == "risk_gate_denied"
        assert "trading_paused" in exc.reasons
    else:
        raise AssertionError("заявка в паузе прошла")


# ─── настройки дня ─────────────────────────────────────────────────────


def test_missing_snapshot_keys_are_reported_not_silently_defaulted() -> None:
    settings, notes = risk_settings_from_day(day(settings={}))

    assert "max_risk_per_order_usdt" in notes
    assert "max_leverage" in notes
    assert settings.max_risk_per_order == Decimal(10)  # доменный дефолт
    result = build(day=day(settings={}))
    assert set(notes) <= set(result.settings_notes)


def test_unparseable_setting_falls_back_with_a_note() -> None:
    settings, notes = risk_settings_from_day(
        day(settings={**SETTINGS, "max_leverage": "abc"})
    )

    assert "max_leverage" in notes
    assert settings.max_leverage == 10


def test_request_parser_reuses_previous_ui_texts() -> None:
    valid = {"risk": "1", "stop_price": "99000"}
    try:
        TicketRequest.from_body({"side": "up"})
    except TicketDenied as exc:
        assert exc.message == "Сторона должна быть buy или sell"
    try:
        TicketRequest.from_body({"risk": "abc"})
    except TicketDenied as exc:
        assert exc.message == "Риск: неверное число"
    try:
        TicketRequest.from_body({"risk": "-1"})
    except TicketDenied as exc:
        assert exc.message == "Риск: требуется положительное число"
    try:
        TicketRequest.from_body({**valid, "leverage": "x"})
    except TicketDenied as exc:
        assert exc.code == "invalid_leverage"
    try:
        TicketRequest.from_body({**valid, "order_type": "stop"})
    except TicketDenied as exc:
        assert exc.code == "invalid_order_type"
    assert TicketRequest.from_body(valid).leverage is None
