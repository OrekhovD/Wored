"""P4 gate — ручная сделка в чате поверх единого доменного тикета.

Тестируется не «карточка отрисовалась», а три обещания среза:

* числа тикета считает домен — чат не имеет своей формулы объёма, ликвидации
  или риска (Q-21);
* подтверждение не имеет власти над старыми числами: состояние перечитывается,
  тикет пересобирается, и любое расхождение (день, объём, veto, TTL) — отказ
  до записи команды;
* идемпотентность детерминирована ключом тикета, а не случайным uuid, и
  непрочитанный статус называется «не подтверждено» с ``command_id``.

Подменён только транспортный край (адатер домена и Redis-читатель), как в
``tests/telegram/conftest.py``: ``services.paper_orders`` →
``application.presenters`` → ``handlers.paper_deal`` выполняются настоящими.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from aiogram.filters import CommandObject
from conftest import (
    CONTRACT,
    OWNER_ID,
    VIEWER_ID,
    make_callback,
    make_message,
    session_state_payload,
    snapshot_payload,
)

from handlers import paper_deal as screen
from paper_trading import adapter as pt_adapter
from paper_trading.contracts import Account, AccountKind, DayState, TradingDay
from security import guards
from services import paper_orders as deals

DAY_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
OTHER_DAY_ID = "9e8d7c6b-5a49-4837-7665-54433221100f"
ACCOUNT_ID = "00000000-0000-4000-8000-000000000001"
OTHER_ACCOUNT_ID = "00000000-0000-4000-8000-000000000002"
COMMAND_ID = "11111111-2222-4333-8444-555555555555"

#: стакан conftest: bid 82668.3 / ask 82668.4, contract_size 0.001, шаг 0.001
ARGS = "side=buy risk=10 stop=82000"
#: 10 / (668.4 * 0.001) = 14.9610… → вниз до шага
EXPECTED_QTY = Decimal("14.961")

SETTINGS = {
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "min_net_rr": "1.0",
}


def _principal(user_id: int = OWNER_ID):
    from security.principal import resolve

    principal = resolve(user_id)
    assert principal is not None
    return principal


def _state(*, running: bool = True, day_id: str = DAY_ID, account_id: str = ACCOUNT_ID) -> dict:
    payload = session_state_payload(running=running)
    if running:
        payload["day"]["id"] = day_id
        payload["accounts"][0]["id"] = account_id
    return payload


def _buttons(outcome: dict) -> list[str]:
    markup = outcome["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.fixture
def no_rate_limit(monkeypatch):
    """Лимитер экрана против двойного нажатия не должен мешать тестам."""
    monkeypatch.setattr(screen._write_limiter, "allow", lambda *a, **k: True)


@pytest.fixture
def deal_domain(monkeypatch, wired_sources):
    """Входы тикета из фикстур; ``build_ticket`` и читатель рынка — настоящие."""
    cfg = {
        "margin": Decimal("1000"),
        "realized": Decimal("0"),
        "positions": [],
        "day_id": DAY_ID,
        "day_state": DayState.running,
        "settings": dict(SETTINGS),
        "inputs_ok": True,
        "inputs_error": "",
        "submit_reply": {"ok": True, "command_id": COMMAND_ID},
        "status_reply": {"ok": True, "command_id": COMMAND_ID, "status": "accepted",
                         "result": None, "error": None},
        "status_raises": None,
    }
    calls = {"inputs": [], "submit": [], "status": []}

    wired_sources["set_session"](_state())

    async def _ticket_inputs(owner_id=None, account_id=None):
        calls["inputs"].append((owner_id, account_id))
        if not cfg["inputs_ok"]:
            return {"ok": False, "error": cfg["inputs_error"] or "no_active_day"}
        owner = UUID(owner_id)
        return {
            "ok": True,
            "day": TradingDay(
                day_id=UUID(cfg["day_id"]), owner_id=owner, state=cfg["day_state"],
                settings_snapshot=dict(cfg["settings"]),
            ),
            "account": Account(
                account_id=UUID(ACCOUNT_ID), owner_id=owner, kind=AccountKind.manual,
                opening_deposit=Decimal("1000"),
            ),
            "available_margin": cfg["margin"],
            "open_positions": list(cfg["positions"]),
            "realized_net": cfg["realized"],
            "day_id": cfg["day_id"],
            "account_id": ACCOUNT_ID,
        }

    async def _submit(**kwargs):
        calls["submit"].append(kwargs)
        return dict(cfg["submit_reply"])

    async def _status(command_id, owner_id=None):
        calls["status"].append((command_id, owner_id))
        if cfg["status_raises"] is not None:
            raise cfg["status_raises"]
        return dict(cfg["status_reply"])

    monkeypatch.setattr(pt_adapter, "ticket_inputs", _ticket_inputs)
    monkeypatch.setattr(pt_adapter, "submit_manual_order", _submit)
    monkeypatch.setattr(pt_adapter, "get_command_status", _status)
    deals.tickets().reset()
    try:
        yield SimpleNamespace(cfg=cfg, calls=calls, sources=wired_sources)
    finally:
        deals.tickets().reset()


# ─── парсер: форма, а не числа ────────────────────────────────────────────


def test_parser_accepts_only_known_keys_and_leaves_values_to_domain() -> None:
    body = deals.parse_ticket_args(ARGS + " tp=84000 lev=5")

    assert body == {"side": "buy", "risk": "10", "stop_price": "82000",
                    "take_profit": "84000", "leverage": "5", "instrument": "BTC-USDT"}


def test_parser_names_the_exact_command_on_bad_shape() -> None:
    with pytest.raises(deals.TicketInvalid) as caught:
        deals.parse_ticket_args("risk 10 stop=")

    codes = [v["code"] for v in caught.value.violations]
    assert "bad_token" in codes and "missing_required" in codes
    assert "/deal preview" in caught.value.message


def test_parser_rejects_a_duplicated_key_instead_of_choosing() -> None:
    with pytest.raises(deals.TicketInvalid) as caught:
        deals.parse_ticket_args("risk=10 risk=20 stop=82000")

    assert [v["code"] for v in caught.value.violations] == ["duplicate_key"]


def test_parser_refuses_an_instrument_the_day_cannot_execute() -> None:
    with pytest.raises(deals.TicketInvalid) as caught:
        deals.parse_ticket_args("side=buy risk=10 stop=82000 inst=ETH-USDT")

    assert caught.value.violations[0]["code"] == "instrument_not_tradable"
    assert "BTC-USDT" in caught.value.message


# ─── предпросмотр: числа только из домена ─────────────────────────────────


async def test_preview_builds_the_ticket_from_domain_inputs(deal_domain) -> None:
    principal = _principal()

    ticket = await deals.preview(principal.owner_id, ARGS)

    assert ticket.result.quantity == EXPECTED_QTY
    assert ticket.result.entry_price == Decimal("82668.4")  # ask для buy
    assert ticket.result.liquidation_quality in {
        "isolated_tier_maintenance", "isolated_default_maintenance"
    }
    assert ticket.result.liquidation_price < ticket.result.entry_price
    assert ticket.day_id == DAY_ID and ticket.account_id == ACCOUNT_ID
    assert ticket.short_id == ticket.ticket_id[:8]
    assert len(deal_domain.calls["inputs"]) == 1


async def test_ticket_ttl_is_the_domain_constant(deal_domain) -> None:
    ticket = await deals.preview(_principal().owner_id, ARGS)
    expires = datetime.fromisoformat(ticket.result.expires_at)

    assert (expires - ticket.created_at) <= timedelta(seconds=deals.TICKET_TTL_SECONDS)
    assert not ticket.is_expired(datetime.now(timezone.utc))


async def test_preview_without_a_day_refuses_and_creates_nothing(deal_domain) -> None:
    deal_domain.sources["set_session"](_state(running=False))

    with pytest.raises(deals.NoActiveDay) as caught:
        await deals.preview(_principal().owner_id, ARGS)

    assert "WebUI" in caught.value.message
    assert "не запущен" in caught.value.message
    assert deal_domain.calls["submit"] == []
    assert len(deals.tickets()) == 0


async def test_service_unavailable_is_not_reported_as_no_day(deal_domain) -> None:
    # ``ok=false`` (домен недоступен) и ``day=None`` (дня нет) — разные условия (ТЗ §6)
    deal_domain.sources["set_session"]({"ok": False, "error": "service_unavailable",
                                        "day": None, "accounts": []})

    with pytest.raises(deals.TicketUnavailable) as caught:
        await deals.preview(_principal().owner_id, ARGS)

    assert not isinstance(caught.value, deals.NoActiveDay)
    assert "недоступен" in caught.value.message


async def test_ticket_denied_by_domain_surfaces_the_gate_code(deal_domain) -> None:
    deal_domain.cfg["margin"] = Decimal("1")

    with pytest.raises(deals.PaperOrderError) as caught:
        await deals.preview(_principal().owner_id, ARGS)

    assert caught.value.reason_code == "insufficient_margin"
    assert [v["code"] for v in caught.value.violations] == ["insufficient_margin"]


# ─── подтверждение: свежие входы и пересобранный тикет ────────────────────


async def test_confirm_rereads_state_and_submits_the_shown_quantity(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    receipt = await deals.confirm(principal.owner_id, ticket.short_id)

    submitted = deal_domain.calls["submit"][0]
    assert receipt["ok"] is True and receipt["command_id"] == COMMAND_ID
    assert submitted["qty"] == str(ticket.result.quantity)
    assert submitted["idempotency_key"] == ticket.idempotency_key
    assert submitted["expected_day_id"] == DAY_ID
    assert submitted["account_id"] == ACCOUNT_ID
    # перечитывание состояния: load_state вызывается и на preview, и на confirm
    assert len(deal_domain.sources["calls"]["session"]) == 2


async def test_idempotency_key_is_deterministic_per_ticket(deal_domain) -> None:
    owner = _principal().owner_id
    first = await deals.preview(owner, ARGS)
    second = await deals.preview(owner, ARGS)

    assert first.idempotency_key == f"tg-{owner}-{first.ticket_id}"
    assert first.idempotency_key != second.idempotency_key


async def test_confirm_after_ttl_refuses_before_any_write(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    future = datetime.now(timezone.utc) + timedelta(seconds=deals.TICKET_TTL_SECONDS + 5)
    original = deals._now

    deals._now = lambda: future  # type: ignore[assignment]
    try:
        with pytest.raises(deals.TicketExpired):
            await deals.confirm(principal.owner_id, ticket.short_id)
    finally:
        deals._now = original  # type: ignore[assignment]

    assert deal_domain.calls["submit"] == []


async def test_confirm_refuses_when_the_day_changed_under_the_ticket(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    deal_domain.cfg["day_id"] = OTHER_DAY_ID
    deal_domain.sources["set_session"](_state(day_id=OTHER_DAY_ID))

    with pytest.raises(deals.TicketChanged) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert "сменился" in caught.value.message
    assert deal_domain.calls["submit"] == []


async def test_confirm_refuses_when_the_account_changed_under_the_ticket(deal_domain) -> None:
    """Другой ручной счёт = другая маржа: старые числа к нему не относятся."""
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    deal_domain.sources["set_session"](_state(account_id=OTHER_ACCOUNT_ID))

    with pytest.raises(deals.TicketChanged) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert "аккаунт сменился" in caught.value.message
    assert deal_domain.calls["submit"] == []


async def test_confirm_rebuilds_and_refuses_when_the_gate_now_says_no(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    deal_domain.cfg["margin"] = Decimal("0.001")

    with pytest.raises(deals.OrderSubmissionRefused) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert caught.value.reason_code == "insufficient_margin"
    assert deal_domain.calls["submit"] == []


async def test_confirm_refuses_a_quantity_that_was_never_shown(deal_domain) -> None:
    """Объём пересчитался из-за цены, но гейт всё ещё «да» — подписываться нельзя."""
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)

    moved = snapshot_payload()
    moved["bid"], moved["ask"] = "83668.3", "83668.4"
    moved["last"], moved["mark"], moved["index"] = "83668.4", "83668.4", "83668.5"
    deal_domain.sources["redis"].values[f"market:perpetual:htx:{CONTRACT}"] = moved

    with pytest.raises(deals.TicketChanged) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert str(ticket.result.quantity) in caught.value.message
    assert str(EXPECTED_QTY) in caught.value.message
    assert deal_domain.calls["submit"] == []


async def test_domain_submission_refusal_is_returned_verbatim(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)
    deal_domain.cfg["submit_reply"] = {"ok": False, "error": "day_not_active"}

    with pytest.raises(deals.OrderSubmissionRefused) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert caught.value.reason_code == "day_not_active"
    assert "не отправлена повторно" in caught.value.message


async def test_idempotency_conflict_hands_back_the_existing_command(deal_domain) -> None:
    principal = _principal()
    ticket = await deals.preview(principal.owner_id, ARGS)
    deal_domain.cfg["submit_reply"] = {"ok": False, "error": "idempotency_conflict",
                                       "command_id": COMMAND_ID}

    with pytest.raises(deals.OrderSubmissionRefused) as caught:
        await deals.confirm(principal.owner_id, ticket.short_id)

    assert COMMAND_ID in caught.value.message
    assert caught.value.reason_code == "idempotency_conflict"


# ─── статус команды ───────────────────────────────────────────────────────


async def test_status_reads_the_domain_enum_value(deal_domain, monkeypatch) -> None:
    from paper_trading.contracts import CommandStatus

    deal_domain.cfg["status_reply"] = {"ok": True, "command_id": COMMAND_ID,
                                       "status": CommandStatus.completed,
                                       "result": {"position_id": "pos-1"}, "error": None}
    monkeypatch.setattr(deals, "STATUS_POLL_TIMEOUT_SECONDS", 0.0)
    await deals.preview(_principal().owner_id, ARGS)

    read = await deals.command_status(_principal().owner_id, COMMAND_ID)

    assert read.status == "completed" and read.settled is True
    assert read.position_id == "pos-1"


async def test_status_that_did_not_settle_is_not_called_a_failure(deal_domain, monkeypatch) -> None:
    deal_domain.cfg["status_reply"] = {"ok": True, "command_id": COMMAND_ID,
                                       "status": "processing", "result": None, "error": None}
    monkeypatch.setattr(deals, "STATUS_POLL_TIMEOUT_SECONDS", 0.0)

    read = await deals.command_status(_principal().owner_id, COMMAND_ID)

    assert read.status == "processing" and read.settled is False
    assert COMMAND_ID in read.command_id


async def test_unreachable_status_read_names_command_id_and_result_unknown(deal_domain) -> None:
    deal_domain.cfg["status_raises"] = RuntimeError("domain is down")

    with pytest.raises(deals.StatusUnknown) as caught:
        await deals.command_status(_principal().owner_id, COMMAND_ID)

    assert caught.value.reason_code == "RESULT_UNKNOWN"
    assert caught.value.command_id == COMMAND_ID
    assert COMMAND_ID in caught.value.message
    assert "не подтверждён" in caught.value.message


async def test_status_of_someone_elses_command_is_not_distinguished(deal_domain) -> None:
    deal_domain.cfg["status_reply"] = {"ok": False, "error": "command_not_found"}

    with pytest.raises(deals.TicketUnknown):
        await deals.command_status(_principal().owner_id, COMMAND_ID)

    # чтение всегда скоупится по владельцу — иначе чужие параметры утекли бы в карточку
    assert deal_domain.calls["status"][0][1] == _principal().owner_id


async def test_command_id_accepts_only_a_full_uuid_or_an_own_prefix() -> None:
    with pytest.raises(deals.TicketUnknown):
        await deals.resolve_command_id("owner-x", "not-a-uuid")


# ─── экран: права, кнопки, отказы ──────────────────────────────────────────


async def test_preview_command_renders_the_domain_ticket(no_rate_limit, deal_domain) -> None:
    message = make_message(text="/deal preview " + ARGS)

    await screen.cmd_deal(message, _principal(),
                          CommandObject(command="deal", args="preview " + ARGS))

    text = message.sent[0]["text"]
    assert "Ручная сделка" in text
    assert str(EXPECTED_QTY) in text
    assert "подтверждение — кнопкой" in text
    assert "лимиты дня" not in text  # снапшот дня полный — заметностей про лимиты нет
    assert any(b.startswith("deal:confirm:") for b in _buttons(message.sent[0]))


async def test_defaulted_limits_and_absent_tier_are_named_separately(
    no_rate_limit, deal_domain
) -> None:
    """Пометка про дефолт лимита и пометка про отсутствие тира — разные строки.

    ``settings_notes`` приходит из домена одним списком, но смысл у них
    разный: ``min_net_rr`` — подставленный дефолт RiskSettings, а
    ``risk_tier_absent`` — ставка поддержки для ликвидации. Смешать их в одну
    строку значит соврать про то, что именно взято из дефолта.
    """
    deal_domain.cfg["settings"] = {k: v for k, v in SETTINGS.items() if k != "min_net_rr"}
    message = make_message(text="/deal preview " + ARGS)

    await screen.cmd_deal(message, _principal(),
                          CommandObject(command="deal", args="preview " + ARGS))

    lines = message.sent[0]["text"].splitlines()
    defaults = [ln for ln in lines if "взяты из дефолта RiskSettings" in ln]
    tier = [ln for ln in lines if "тира риска" in ln]
    assert len(defaults) == 1 and "min_net_rr" in defaults[0]
    assert len(tier) == 1 and "min_net_rr" not in tier[0]
    assert "risk_tier_absent" not in defaults[0]
    # отказа из-за подставленного дефолта нет: тикет собирается и живёт как обычно
    assert any(b.startswith("deal:confirm:") for b in _buttons(message.sent[0]))


async def test_confirm_button_is_absent_for_a_non_owner(no_rate_limit, deal_domain) -> None:
    message = make_message(text="/deal preview " + ARGS)

    await screen.cmd_deal(message, _principal(VIEWER_ID),
                          CommandObject(command="deal", args="preview " + ARGS))

    buttons = _buttons(message.sent[0])
    assert not any(b.startswith("deal:confirm:") for b in buttons)
    assert any(b.startswith("deal:rebuild:") for b in buttons)


async def test_confirm_callback_of_a_non_owner_writes_nothing(no_rate_limit, deal_domain) -> None:
    principal = _principal(VIEWER_ID)
    ticket = await deals.preview(principal.owner_id, ARGS)
    call = make_callback(user_id=VIEWER_ID, data=f"deal:confirm:{ticket.short_id}")

    await screen.cb_deal_confirm(call, principal)

    assert deal_domain.calls["submit"] == []
    assert call.answered[0]["text"].startswith("🔒")


async def test_callback_data_stays_inside_telegrams_limit(no_rate_limit, deal_domain) -> None:
    principal = _principal()
    message = make_message(text="/deal preview " + ARGS)

    await screen.show_ticket(message, principal, ARGS)
    buttons = _buttons(message.sent[0])
    await screen.show_status(message, principal, COMMAND_ID)
    status_buttons = _buttons(message.sent[-1])

    assert all(len(b.encode("utf-8")) <= 64 for b in buttons + status_buttons)
    assert f"deal:status:{COMMAND_ID}" in status_buttons
    assert any(b.startswith("deal:confirm:") for b in buttons)
    assert any(b.startswith("deal:rebuild:") for b in buttons)
    assert "nav:now" in buttons and "deal:positions" in buttons


def test_the_screen_never_offers_to_close_a_position() -> None:
    """Объём P4: чат читает позиции, но не закрывает их (долг — RFC Q-24).

    Проверяется дерево исходника, а не подстрока: комментарий «close_position
    не вызывается» не должен ронять тест, а настоящая ветка закрытия — должна.
    Если такая ветка появится, долг придётся закрывать решением, а не молча.
    """
    tree = ast.parse(pathlib.Path(screen.__file__).read_text(encoding="utf-8"))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    callbacks = {c.value for c in ast.walk(tree)
                 if isinstance(c, ast.Constant) and isinstance(c.value, str)}

    assert "close_position" not in used
    assert not any(c.startswith("deal:close") for c in callbacks)


async def test_refusal_is_the_console_card_with_the_domain_code(no_rate_limit, deal_domain) -> None:
    deal_domain.cfg["margin"] = Decimal("1")
    message = make_message(text="/deal preview " + ARGS)

    await screen.cmd_deal(message, _principal(),
                          CommandObject(command="deal", args="preview " + ARGS))

    text = message.sent[0]["text"]
    assert "Сделка: операция не выполнена" in text and "insufficient_margin" in text
    assert "Недостаточно доступной маржи" in text
    assert not any(b.startswith("deal:confirm:") for b in _buttons(message.sent[0]))


async def test_deal_positions_delegates_to_the_existing_screen(monkeypatch) -> None:
    called: list[str] = []

    async def _positions(target, principal):
        called.append("now.show_positions")

    monkeypatch.setattr(screen, "show_positions", _positions)
    message = make_message(text="/deal positions")

    await screen.cmd_deal(message, _principal(),
                          CommandObject(command="deal", args="positions"))

    assert called == ["now.show_positions"]


async def test_unknown_subcommand_shows_the_form_once(no_rate_limit, deal_domain) -> None:
    message = make_message(text="/deal frobnicate")

    await screen.cmd_deal(message, _principal(),
                          CommandObject(command="deal", args="frobnicate"))

    assert len(message.sent) == 1
    assert "не понята" in message.sent[0]["text"]
    assert "/deal preview" in message.sent[0]["text"]


def test_limiter_exists_and_is_per_actor_action() -> None:
    """Экран держит свой лимитер записи, а не общий на весь бот."""
    limiter = guards.RateLimiter(1.0)

    assert limiter.allow(1, "deal:confirm:abcd") is True
    assert limiter.allow(1, "deal:confirm:abcd") is False
    assert limiter.allow(2, "deal:confirm:abcd") is True
