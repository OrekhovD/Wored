"""P4b gate (Q-24) — управление торговым днём из чата поверх доменной команды.

Тест проверяет не «карточка отрисовалась», а четыре обещания среза:

* у действия один адресат — домен: ни один путь экрана не пишет
  ``trading_sessions``, и исполнитель тот же, что у WebUI-кнопки (TB-03);
* ключ идемпотентности принадлежит карточке, а не дню: законное
  «пауза → продолжить → пауза» внутри одного дня не является replay, а
  ``finish_day`` остаётся дневным, потому что его выдаёт и планировщик runner'а;
* подтверждение не имеет власти над старыми числами: день перечитывается,
  показанные лимиты сверяются, и любое расхождение — отказ до отправки команды;
* «ужесточить» и «снизить» — не слова, а ``key=value``: числа разбирает доменный
  ``parse_limits``, своего словаря ключей и своих множителей у чата нет.

Подменён только транспортный край (адатер домена), как в
``tests/telegram/conftest.py``: ``services.day_control`` →
``application.presenters`` → ``handlers.day_control`` выполняются настоящими.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject
from conftest import OWNER_ID, VIEWER_ID, make_callback, make_message

from handlers import day_control as screen
from paper_trading import adapter as pt_adapter
from paper_trading import ticket as domain_ticket
from security import guards
from services import day_control as control
from services import paper_orders as deals

DAY_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
OTHER_DAY_ID = "9e8d7c6b-5a49-4837-7665-54433221100f"
AUTO_ACCOUNT = "00000000-0000-4000-8000-00000000000a"
COMMAND_ID = "11111111-2222-4333-8444-555555555555"

#: канонические строки снапшота дня — их форма принадлежит домену
LIMITS = {
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


def _buttons(outcome: dict) -> list[str]:
    markup = outcome["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


@pytest.fixture
def no_rate_limit(monkeypatch):
    """Лимитер против случайного двойного нажатия не должен мешать тестам."""
    monkeypatch.setattr(screen._write_limiter, "allow", lambda *a, **k: True)


@pytest.fixture
def day_domain(monkeypatch):
    """Фейковый доменный край экрана; вся логика тикета и карточек — настоящая."""
    cfg = {
        "day_id": DAY_ID,
        "day_state": "running",
        "limits": dict(LIMITS),
        "notes": [],
        "limits_ok": True,
        "limits_error": "",
        "state_ok": True,
        "state_error": "",
        "auto_positions": 2,
        "auto_account": True,
        "reply": {"ok": True, "command_id": COMMAND_ID},
    }
    calls: dict[str, list] = {
        "read": [], "state": [], "pause": [], "resume": [],
        "close_all": [], "finish": [], "limits": [], "status": [],
    }

    async def _load_day_limits(owner_id, **kwargs):
        calls["read"].append(owner_id)
        if not cfg["limits_ok"]:
            return {"ok": False, "error": cfg["limits_error"] or "service_unavailable"}
        return {
            "ok": True,
            "day_id": cfg["day_id"],
            "day_state": cfg["day_state"],
            "limits": dict(cfg["limits"]),
            "settings_notes": list(cfg["notes"]),
        }

    async def _get_current_state(owner_id, **kwargs):
        calls["state"].append(owner_id)
        if not cfg["state_ok"]:
            return {"ok": False, "error": cfg["state_error"] or "service_unavailable"}
        accounts = [{
            "id": "manual-0001", "kind": "manual", "currency": "USDT",
            "cash": "998.45", "open_positions": 1, "positions": [],
        }]
        if cfg["auto_account"]:
            accounts.insert(0, {
                "id": AUTO_ACCOUNT, "kind": "auto", "currency": "USDT",
                "cash": "1000.00", "open_positions": cfg["auto_positions"],
                "positions": [],
            })
        return {"ok": True, "day": {"id": cfg["day_id"]}, "accounts": accounts,
                "next_action": "trade"}

    def _recorder(name):
        async def _call(owner_id, *args, **kwargs):
            calls[name].append(dict(kwargs, owner_id=owner_id, _args=args))
            return dict(cfg["reply"])

        return _call

    async def _set_day_limits(owner_id, limits, expected=None, idempotency_key=None):
        calls["limits"].append({
            "owner_id": owner_id, "limits": dict(limits), "expected": dict(expected or {}),
            "idempotency_key": idempotency_key,
        })
        return dict(cfg["reply"])

    async def _get_command_status(command_id, owner_id=None):
        calls["status"].append((command_id, owner_id))
        return {"ok": True, "command_id": command_id, "status": "completed",
                "result": None, "error": None}

    monkeypatch.setattr(pt_adapter, "load_day_limits", _load_day_limits)
    monkeypatch.setattr(pt_adapter, "get_current_state", _get_current_state)
    monkeypatch.setattr(pt_adapter, "pause_auto", _recorder("pause"))
    monkeypatch.setattr(pt_adapter, "resume_auto", _recorder("resume"))
    monkeypatch.setattr(pt_adapter, "close_all_positions", _recorder("close_all"))
    monkeypatch.setattr(pt_adapter, "finish_day", _recorder("finish"))
    monkeypatch.setattr(pt_adapter, "set_day_limits", _set_day_limits)
    monkeypatch.setattr(pt_adapter, "get_command_status", _get_command_status)
    deals.tickets().reset()
    try:
        yield SimpleNamespace(cfg=cfg, calls=calls)
    finally:
        deals.tickets().reset()


def _broken_legacy_writers(monkeypatch):
    """Любой вызов в legacy-сессию = падение теста (Q-24 чинится удалением пути)."""
    import services.session_manager as sm

    def _boom(name):
        async def _raise(*args, **kwargs):
            raise AssertionError(f"legacy path used: {name}()")

        return _raise

    for name in ("get_active_session", "apply_revision_command", "create_session"):
        if hasattr(sm, name):
            monkeypatch.setattr(sm, name, _boom(name))
    import services.stats_audit as audit

    if hasattr(audit, "session_closeout"):
        monkeypatch.setattr(audit, "session_closeout", _boom("session_closeout"))


# ─── форма команды принадлежит владельцу экрана ─────────────────────────────


def test_recogniser_uses_the_domain_limit_dictionary() -> None:
    """Второго списка ключей в чате нет — только доменный ``LIMIT_FIELDS``."""
    assert control.recognises_limit_args("max_risk_per_order_usdt=8")
    assert not control.recognises_limit_args("side=buy risk=10")
    assert set(sorted(control.LIMIT_FIELDS)) == set(domain_ticket.LIMIT_FIELDS)


@pytest.mark.parametrize("word", ["close", "стопавто", "tightne"])
def test_ambiguous_or_unknown_subcommand_is_named_not_guessed(word: str) -> None:
    with pytest.raises(control.DayControlInvalid) as caught:
        control.resolve_action(word)

    assert caught.value.violations[0]["code"] == "unknown_action"
    assert "/day limits" in caught.value.message


def test_tighten_and_reduce_are_aliases_of_one_domain_action() -> None:
    """«ужесточить» и «снизить» — один и тот же лимитный контракт, без ×0.5."""
    assert control.ALIASES["tighten"] == control.ACTION_LIMITS
    assert control.ALIASES["reduce"] == control.ACTION_LIMITS
    assert control.ACTIONS[control.ACTION_LIMITS].needs_limits


# ─── предпросмотр: ни одна команда не записана ─────────────────────────────


async def test_preview_creates_a_card_and_no_command(day_domain) -> None:
    ticket = await control.preview(_principal().owner_id, "pause")

    assert ticket.action == control.ACTION_PAUSE
    assert ticket.day_id == DAY_ID and ticket.day_state == "running"
    assert ticket.kind == deals.KIND_DAY
    assert day_domain.calls["pause"] == []
    assert len(deals.tickets()) == 1


async def test_idempotency_key_belongs_to_the_ticket_not_the_day(day_domain) -> None:
    """Ядро Q-24: две карточки одного дня — две разные команды, не replay."""
    owner = _principal().owner_id
    first = await control.preview(owner, "pause")
    second = await control.preview(owner, "pause")

    assert first.day_id == second.day_id
    assert first.idempotency_key != second.idempotency_key
    assert first.idempotency_key == f"tg-{owner}-{first.ticket_id}"


async def test_preview_without_a_day_refuses_and_creates_nothing(day_domain) -> None:
    day_domain.cfg["limits_ok"] = False
    day_domain.cfg["limits_error"] = "no_active_day"

    with pytest.raises(control.NoActiveDay) as caught:
        await control.preview(_principal().owner_id, "pause")

    assert "не запущен" in caught.value.message and "WebUI" in caught.value.message
    assert len(deals.tickets()) == 0


async def test_unreadable_state_is_not_reported_as_no_day(day_domain) -> None:
    """``ok=false`` и ``no_active_day`` — разные условия (ТЗ §6)."""
    day_domain.cfg["limits_ok"] = False
    day_domain.cfg["limits_error"] = "service_unavailable"

    with pytest.raises(control.DayControlUnavailable) as caught:
        await control.preview(_principal().owner_id, "pause")

    assert not isinstance(caught.value, control.NoActiveDay)
    assert "действие не выполнено" in caught.value.message


async def test_closeauto_names_the_auto_positions_it_will_liquidate(day_domain) -> None:
    ticket = await control.preview(_principal().owner_id, "closeauto")

    assert ticket.open_auto_positions == 2
    assert ticket.action == control.ACTION_CLOSE_ALL


async def test_closeauto_is_not_offered_when_the_count_is_unknown(day_domain) -> None:
    """Обещать ликвидацию, не зная её предмета, нельзя — отказа достаточно."""
    day_domain.cfg["state_ok"] = False

    with pytest.raises(control.DayControlUnavailable) as caught:
        await control.preview(_principal().owner_id, "closeauto")

    assert "команда не отправлена" in caught.value.message
    assert day_domain.calls["close_all"] == []


async def test_limits_without_numbers_are_refused_as_a_word(day_domain) -> None:
    owner = _principal().owner_id

    for word in ("limits", "tighten", "reduce"):
        with pytest.raises(control.DayControlInvalid) as caught:
            await control.preview(owner, word, "")

        assert caught.value.violations[0]["code"] == "empty_limits"
    assert day_domain.calls["limits"] == []


async def test_limits_reject_an_unknown_key_instead_of_ignoring_it(day_domain) -> None:
    with pytest.raises(control.DayControlInvalid) as caught:
        await control.preview(_principal().owner_id, "limits", "max_leverage=5 bogus=1")

    assert [v["code"] for v in caught.value.violations] == ["bad_token"]
    assert "max_leverage" in caught.value.message


async def test_limits_card_carries_shown_values_as_the_compare_and_swap(day_domain) -> None:
    """``expected`` = ровно те канонические строки, что видел оператор."""
    ticket = await control.preview(
        _principal().owner_id, "limits", "max_leverage=5 max_daily_loss_usdt=25",
    )

    assert ticket.limits == {"max_leverage": "5", "max_daily_loss_usdt": "25"}
    assert ticket.expected == {"max_leverage": "10", "max_daily_loss_usdt": "50"}


# ─── подтверждение: старое число не имеет власти ───────────────────────────


async def test_confirm_sends_the_domain_call_with_the_card_key(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "pause")

    receipt = await control.confirm(owner, ticket.short_id)

    assert receipt["ok"] and receipt["command_id"] == COMMAND_ID
    assert day_domain.calls["pause"][0]["idempotency_key"] == ticket.idempotency_key


async def test_finish_day_keeps_the_day_scoped_key(day_domain) -> None:
    """Исключение зафиксировано: планировщик runner'а зовёт finish сам."""
    owner = _principal().owner_id
    ticket = await control.preview(owner, "finish")

    await control.confirm(owner, ticket.short_id)

    assert day_domain.calls["finish"][0].get("idempotency_key") is None
    assert day_domain.calls["finish"][0]["_args"] == ()


async def test_confirm_refusal_keeps_the_existing_command_id(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "resume")
    day_domain.cfg["reply"] = {"ok": False, "error": "idempotency_conflict",
                               "command_id": COMMAND_ID}

    with pytest.raises(control.DayControlRefused) as caught:
        await control.confirm(owner, ticket.short_id)

    assert caught.value.reason_code == "idempotency_conflict"
    assert COMMAND_ID in caught.value.message
    assert "уже" in caught.value.message


async def test_confirm_refuses_when_the_day_changed_under_the_card(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "pause")
    day_domain.cfg["day_id"] = OTHER_DAY_ID

    with pytest.raises(control.DayControlChanged) as caught:
        await control.confirm(owner, ticket.short_id)

    assert "сменился" in caught.value.message
    assert day_domain.calls["pause"] == []


async def test_confirm_refuses_when_limits_moved_under_the_card(day_domain) -> None:
    """CAS-предпроверка: оператор не подписывает число, которого не видел."""
    owner = _principal().owner_id
    ticket = await control.preview(owner, "limits", "max_leverage=5")
    day_domain.cfg["limits"]["max_leverage"] = "8"

    with pytest.raises(control.DayControlChanged) as caught:
        await control.confirm(owner, ticket.short_id)

    assert "max_leverage" in caught.value.message
    assert [v["code"] for v in caught.value.violations] == ["limits_changed"]
    assert day_domain.calls["limits"] == []


async def test_confirm_refuses_limits_on_a_day_that_stopped_trading(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "limits", "max_leverage=5")
    day_domain.cfg["day_state"] = "closed"

    with pytest.raises(control.DayControlChanged) as caught:
        await control.confirm(owner, ticket.short_id)

    assert "closed" in caught.value.message
    assert day_domain.calls["limits"] == []


async def test_expired_card_is_not_executed(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "pause")
    stale = dataclasses.replace(
        ticket, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    deals.tickets().put(stale)

    with pytest.raises(control.DayControlExpired) as caught:
        await control.confirm(owner, ticket.short_id)

    assert "действие не выполнено" in caught.value.message
    assert day_domain.calls["pause"] == []


async def test_another_owner_cannot_confirm_the_card(day_domain) -> None:
    owner = _principal().owner_id
    ticket = await control.preview(owner, "pause")
    stranger = _principal(VIEWER_ID)

    with pytest.raises(control.DayControlUnknown):
        await control.confirm(stranger.owner_id, ticket.short_id)

    assert day_domain.calls["pause"] == []


async def test_day_card_is_invisible_to_the_deal_screen(day_domain) -> None:
    """Магазин один, виды разные: префикс ``/deal`` чужую карточку не подтвердит."""
    owner = _principal().owner_id
    ticket = await control.preview(owner, "pause")

    with pytest.raises(deals.TicketUnknown):
        await deals.resolve_ticket_token(owner, ticket.short_id, kind=deals.KIND_DEAL,
                                        not_found="нет")

    resolved = await deals.resolve_ticket_token(owner, ticket.short_id,
                                                kind=deals.KIND_DAY, not_found="нет")
    assert resolved == ticket.ticket_id


# ─── экран: права, кнопки, отказы ──────────────────────────────────────────


async def test_bare_day_command_lists_every_domain_action(no_rate_limit, day_domain) -> None:
    message = make_message(text="/day")

    await screen.cmd_day(message, _principal(), CommandObject(command="day", args=""))

    text = message.sent[0]["text"]
    for action in control.ACTIONS.values():
        assert action.title in text
    assert "/day closeauto" in text and "runner" in text
    assert not any(b.startswith("daycmd:confirm:") for b in _buttons(message.sent[0]))
    assert day_domain.calls["pause"] == day_domain.calls["finish"] == []


async def test_unknown_subcommand_shows_the_form_once_without_writing(
    no_rate_limit, day_domain
) -> None:
    message = make_message(text="/day frobnicate")

    await screen.cmd_day(message, _principal(),
                         CommandObject(command="day", args="frobnicate"))

    assert len(message.sent) == 1
    assert "не понята" in message.sent[0]["text"]
    assert day_domain.calls["pause"] == []


async def test_confirm_button_is_absent_for_a_non_owner(no_rate_limit, day_domain) -> None:
    message = make_message(text="/day pause")

    await screen.cmd_day(message, _principal(VIEWER_ID),
                         CommandObject(command="day", args="pause"))

    buttons = _buttons(message.sent[0])
    assert not any(b.startswith("daycmd:confirm:") for b in buttons)
    assert "подтвердить может только владелец" in message.sent[0]["text"]


async def test_non_owner_callback_writes_nothing(no_rate_limit, day_domain) -> None:
    principal = _principal(VIEWER_ID)
    ticket = await control.preview(_principal().owner_id, "pause")
    call = make_callback(user_id=VIEWER_ID, data=f"daycmd:confirm:{ticket.short_id}")

    await screen.cb_day_confirm(call, principal)

    assert day_domain.calls["pause"] == []
    assert call.answered[0]["text"].startswith("🔒")


async def test_confirm_receipt_names_the_command_and_the_executor(
    no_rate_limit, day_domain
) -> None:
    principal = _principal()
    message = make_message(text="/day closeauto")
    await screen.cmd_day(message, principal,
                         CommandObject(command="day", args="closeauto"))
    token = [b for b in _buttons(message.sent[0]) if b.startswith("daycmd:confirm:")][0]
    call = make_callback(user_id=OWNER_ID, data=token)

    await screen.cb_day_confirm(call, principal)

    text = call.message.edited[-1]["text"]
    assert COMMAND_ID in text
    assert "Ключ идемпотентности принадлежит карточке" in text
    assert "runner" in text
    assert f"daycmd:status:{COMMAND_ID}" in _buttons(call.message.edited[-1])


async def test_limits_refusal_is_the_console_card_with_the_domain_code(
    no_rate_limit, day_domain
) -> None:
    message = make_message(text="/day tighten")

    await screen.cmd_day(message, _principal(),
                         CommandObject(command="day", args="tighten"))

    text = message.sent[0]["text"]
    assert "День: операция не выполнена" in text and "empty_limits" in text
    assert "daycmd:confirm" not in " ".join(_buttons(message.sent[0]))


async def test_day_status_screen_shows_domain_limits_verbatim(no_rate_limit, day_domain) -> None:
    day_domain.cfg["notes"] = ["min_net_rr"]
    message = make_message(text="/day status")

    await screen.cmd_day(message, _principal(),
                         CommandObject(command="day", args="status"))

    text = message.sent[0]["text"]
    assert DAY_ID[:8] in text
    for key, value in LIMITS.items():
        assert f"{key} = {value}" in text
    assert "действует дефолт RiskSettings" in text and "min_net_rr" in text


async def test_command_status_read_is_owner_scoped(no_rate_limit, day_domain) -> None:
    principal = _principal()
    ticket = await control.preview(principal.owner_id, "pause")
    await control.confirm(principal.owner_id, ticket.short_id)
    message = make_message(text=f"/day status {COMMAND_ID}")

    await screen.cmd_day(message, principal,
                         CommandObject(command="day", args=f"status {COMMAND_ID}"))

    assert day_domain.calls["status"] == [(COMMAND_ID, principal.owner_id)]
    assert "исполнена" in message.sent[0]["text"]


async def test_callback_data_stays_inside_telegrams_limit(no_rate_limit, day_domain) -> None:
    principal = _principal()
    message = make_message(text="/day limits max_leverage=5")

    await screen.cmd_day(message, principal,
                         CommandObject(command="day", args="limits max_leverage=5"))

    buttons = _buttons(message.sent[0])
    assert all(len(b.encode("utf-8")) <= 64 for b in buttons)
    assert "daycmd:status" in buttons and "daycmd:positions" in buttons


def test_the_day_screen_never_closes_a_single_position() -> None:
    """Объём P4b: ликвидация автомата есть, закрытие позиции по id — P4c.

    Проверяется дерево исходника: упоминание в комментарии тест не роняет, а
    настоящая ветка ``close_position`` — роняет.
    """
    tree = ast.parse(pathlib.Path(screen.__file__).read_text(encoding="utf-8"))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    callbacks = {c.value for c in ast.walk(tree)
                 if isinstance(c, ast.Constant) and isinstance(c.value, str)}

    assert "close_position" not in used
    assert "session_manager" not in used
    assert not any(c.startswith("daycmd:close") for c in callbacks)


def test_limiter_is_per_actor_and_card() -> None:
    limiter = guards.RateLimiter(1.0)

    assert limiter.allow(1, "daycmd:confirm:abcd") is True
    assert limiter.allow(1, "daycmd:confirm:abcd") is False
    assert limiter.allow(2, "daycmd:confirm:abcd") is True
    assert limiter.allow(1, "daycmd:confirm:dcba") is True


# ─── Q-24: легаси-исполнители удалены, а не переименованы ──────────────────


def test_pipeline_no_longer_contains_legacy_control_writers() -> None:
    """AST, а не подстрока: docstring имеет право назвать убитый путь.

    Проверяются исполняемые имена и строки фильтров-декораторов. Тот же
    ``pipeline_close_all`` в комментарии «здесь была эта кнопка» тест не роняет,
    а настоящая ветка-исполнитель — роняет.
    """
    import handlers.pipeline as legacy

    tree = ast.parse(pathlib.Path(legacy.__file__).read_text(encoding="utf-8"))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    callback_literals: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                callback_literals |= {
                    c.value for c in ast.walk(dec)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }

    assert not {"_handle_revision_safe", "_handle_stop_safe",
                "apply_revision_command", "session_closeout"} & used
    for retired in ("pipeline_pause", "pipeline_tighten", "pipeline_reduce",
                    "pipeline_close_all"):
        assert retired not in callback_literals
    assert "pipeline_day_control" in callback_literals


async def test_control_phrase_never_reaches_the_legacy_writer(monkeypatch) -> None:
    _broken_legacy_writers(monkeypatch)
    from handlers import pipeline

    text = await pipeline.handle_pipeline_intent(
        {"intent": "pipeline_revision_cmd", "command": "pause"}, user_id=OWNER_ID,
    )

    assert "/day" in text and "trading_sessions" in text


async def test_control_phrase_is_still_a_product_intent(monkeypatch) -> None:
    """Снимается исполнитель, а не распознавание: в модель фраза не уйдёт."""
    from handlers import chat

    assert chat._is_product_intent("пауза сессии") is True


def test_bare_limit_payload_is_refused_before_the_model() -> None:
    from handlers import chat

    text = chat._bare_payload_refusal("max_risk_per_order_usdt=8")

    assert text is not None
    assert "/day limits" in text and "max_risk_per_order_usdt" in text
    # форма живёт у владельца экрана и экранирована: уголков HTML в карточке нет
    assert "&lt;ключ&gt;=&lt;число&gt;" in text
