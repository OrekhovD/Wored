"""The Telegram input surface: mistyped commands and no-op re-renders (RFC Q-15/Q-16).

Found in the live P3c run of 13.10.2026, not in review:

* ``/plans,`` — a comma glued to the command — matched no ``Command`` filter and
  was answered by the AI chat fallback as a generated **trade plan** with entry,
  stop, targets and a confidence figure, i.e. exactly the artifact the domain
  contract exists to prevent ("no invented numbers");
* re-opening an unchanged console screen raised Telegram's benign
  ``message is not modified`` and the screen wrapper painted it as
  ``BACKEND_UNAVAILABLE`` — a false "data service is down" alarm.

Record: ``docs/CURATOR-REPORT-P3C-2026-10-12.md`` §6.1/§11.
"""

from __future__ import annotations

import pathlib

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message
from conftest import OWNER_ID, make_callback, make_message
from pydantic import ValidationError

from handlers import chat as chat_module
from handlers import now as console
from handlers import simulation_v3 as screens
from handlers import telegram_surface as surface
from security.principal import resolve

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_NOT_MODIFIED_TEXT = (
    "Telegram server says - Bad Request: message is not modified: specified new "
    "message content and reply markup are exactly the same as a current content "
    "and reply markup of the message"
)
_PARSE_ERROR_TEXT = "Telegram server says - Bad Request: Can't parse entities: unsupported start tag"


def _bad_request(text: str) -> TelegramBadRequest:
    method = EditMessageText(text="x", chat_id=1, message_id=1)
    return TelegramBadRequest(method=method, message=text)


def _principal():
    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal


# ─── Q-15: the command token ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/plans,", "/plans"),
        ("/plans.", "/plans"),
        ("/plans!?", "/plans"),
        ("/plans,new", "/plans,new"),          # punctuation inside is not a typo tail
        ("/plans, new", "/plans new"),         # the comma was on the command
        ("/plans show <id>", "/plans show <id>"),   # arguments stay byte-identical
        ("/plans@W_W_O_O_bot,", "/plans@W_W_O_O_bot"),
        ("/now", "/now"),
        ("  /market,", "  /market"),           # leading indent preserved
        ("рынок", "рынок"),
        ("", ""),
    ],
)
def test_trailing_punctuation_is_stripped_from_the_command_token(raw, expected):
    assert surface.normalize_command_text(raw) == expected


def test_normalizer_leaves_non_text_alone():
    assert surface.normalize_command_text(None) is None
    assert surface.normalize_command_text(42) == 42


def test_normalizer_never_invents_a_command():
    # punctuation-only input carries no command name and must not gain one
    assert surface.normalize_command_text("/") == "/"
    assert surface.normalize_command_text("/,") == "/"
    assert not any(c.isalpha() for c in surface.normalize_command_text("/,.!?"))


@pytest.mark.parametrize(
    ("text", "looks_like_command"),
    [("/plans", True), (" /plans,", True), ("/", True), ("планы", False), ("", False), (None, False)],
)
def test_slash_shaped_input_is_recognised_as_a_command_attempt(text, looks_like_command):
    assert surface.looks_like_command(text) is looks_like_command


async def test_middleware_rewrites_the_message_before_any_router_sees_it():
    seen: dict[str, object] = {}

    async def _handler(event, data):
        seen["text"] = event.text
        return "handled"

    message = make_message(user_id=OWNER_ID, text="/plans,")
    result = await surface.command_surface_middleware(_handler, message, {})

    assert result == "handled"
    assert seen["text"] == "/plans"


async def test_middleware_leaves_conversational_text_untouched():
    async def _handler(event, data):
        return event.text

    message = make_message(user_id=OWNER_ID, text="что по рынку?")
    assert await surface.command_surface_middleware(_handler, message, {}) == "что по рынку?"


def test_main_installs_the_surface_middleware_once_before_every_router():
    """The rule belongs to the process, not to one screen (TB-03)."""
    source = (REPO_ROOT / "chatbot" / "main.py").read_text(encoding="utf-8")
    install = "dp.message.outer_middleware(command_surface_middleware)"
    assert source.count(install) == 1
    assert source.index(install) < source.index("dp.include_router(console_router)")
    # startup must be verifiable from the logs, not assumed
    assert "SURFACE: command token rule installed" in source


# ─── Q-15: the model must not answer a command ─────────────────────────


async def test_unknown_command_is_refused_instead_of_generated(monkeypatch):
    async def _exploding_route(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the model must not answer command-shaped input")

    monkeypatch.setattr(chat_module, "route_request", _exploding_route)

    message = make_message(user_id=OWNER_ID, text="/plans,new")
    await chat_module.cmd_chat(message, _principal())

    text = message.sent[-1]["text"]
    assert "не распознана" in text
    assert "/help" in text
    assert "82,650" not in text               # no market numbers
    assert "stop" not in text.lower()         # and no fabricated trade levels


async def test_generated_trade_plan_path_is_not_reachable_from_slash_input(monkeypatch):
    calls: list[str] = []

    async def _fake_route(text, *args, **kwargs):
        calls.append(text)
        return "🎯 LONG $82,750, стоп $82,300"

    monkeypatch.setattr(chat_module, "route_request", _fake_route)

    for raw in ("/plans,", "/palns", "/plan,", "/wtf"):
        message = make_message(user_id=OWNER_ID, text=raw)
        await chat_module.cmd_chat(message, _principal())

    assert calls == []


async def test_conversational_text_still_reaches_the_model(monkeypatch):
    calls: list[str] = []

    async def _fake_route(text, *args, **kwargs):
        calls.append(text)
        return "обычный ответ"

    monkeypatch.setattr(chat_module, "route_request", _fake_route)

    message = make_message(user_id=OWNER_ID, text="объясни что такое funding rate")
    await chat_module.cmd_chat(message, _principal())

    assert calls == ["объясни что такое funding rate"]


async def test_deterministic_product_intent_keeps_priority_over_the_refusal(monkeypatch):
    """The guard sits after the keyword dispatch, so «покажи рынок» still works."""
    hits: list[str] = []

    from handlers import market as market_module

    async def _fake_market(message):
        hits.append("market")

    monkeypatch.setattr(market_module, "send_market_data", _fake_market)

    message = make_message(user_id=OWNER_ID, text="покажи рынок")
    await chat_module.cmd_chat(message, _principal())

    assert hits == ["market"]


# ─── Q-16: "message is not modified" is a success, not an outage ───────


def test_the_no_op_edit_is_named_precisely():
    assert surface.is_no_op_edit(_bad_request(_NOT_MODIFIED_TEXT)) is True
    assert surface.is_no_op_edit(_bad_request(_PARSE_ERROR_TEXT)) is False
    assert surface.is_no_op_edit(RuntimeError("db down")) is False


def _callback_raising_on_edit(error: TelegramBadRequest) -> tuple[object, list[str]]:
    """A callback whose message records edit attempts and then refuses them."""
    call = make_callback(user_id=OWNER_ID, data="nav:now")
    attempts: list[str] = []

    async def _edit(text, **kwargs):
        attempts.append(text)
        raise error

    object.__setattr__(call.message, "edit_text", _edit)
    return call, attempts


async def test_console_rereply_reports_no_op_as_false_not_as_failure():
    call, attempts = _callback_raising_on_edit(_bad_request(_NOT_MODIFIED_TEXT))

    ok = await console._reply(call, "🎯 Сессия", console.nav_keyboard(include_refresh=False))

    assert ok is False
    assert attempts == ["🎯 Сессия"]


async def test_console_rereply_still_raises_a_real_bad_request():
    call, _ = _callback_raising_on_edit(_bad_request(_PARSE_ERROR_TEXT))

    with pytest.raises(TelegramBadRequest):
        await console._reply(call, "текст", console.nav_keyboard(include_refresh=False))


async def test_rerender_of_an_unchanged_screen_is_not_reported_as_backend_failure():
    """/session twice in a row must not claim the data service died (Q-16)."""
    call, attempts = _callback_raising_on_edit(_bad_request(_NOT_MODIFIED_TEXT))

    async def _screen(target, principal):
        await console._reply(target, "🎯 Сессия не запущена", console.nav_keyboard(include_refresh=False))

    await console._render(_screen, call, _principal())

    assert attempts == ["🎯 Сессия не запущена"]
    assert all("BACKEND_UNAVAILABLE" not in text for text in attempts)
    assert call.message.sent == []


async def test_genuine_screen_failure_still_produces_the_error_card(wired_sources, monkeypatch):
    """The no-op must not become a blanket excuse: real reads still report honestly."""

    async def _explode(*args, **kwargs):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(console.api, "read_market_snapshot", _explode)

    message = make_message(user_id=OWNER_ID, text="/now")
    await console._render(console.show_now, message, _principal())

    assert "BACKEND_UNAVAILABLE" in message.sent[-1]["text"]


async def test_main_card_rerender_is_not_reported_as_a_backend_failure(wired_sources):
    """``/now`` twice: the second tap has nothing to repaint, so it stays quiet."""
    call, attempts = _callback_raising_on_edit(_bad_request(_NOT_MODIFIED_TEXT))

    await console._render(console.show_now, call, _principal())

    assert attempts, "the main card must still be rendered once"
    assert all("BACKEND_UNAVAILABLE" not in text for text in attempts)
    assert call.message.sent == []


def test_no_console_screen_reimplements_the_reply_branch():
    """TB-03: one send/edit branch, in ``_reply``, for every console card."""
    source = pathlib.Path(console.__file__).read_text(encoding="utf-8")
    assert source.count("target.message.edit_text(") == 1


async def test_plan_card_recheck_no_op_does_not_abort_the_callback():
    """«Перепроверить» on an unchanged plan: card stays, callback does not explode."""
    call, attempts = _callback_raising_on_edit(_bad_request(_NOT_MODIFIED_TEXT))

    ok = await screens._reply(call, "📋 план", screens.plans_keyboard(can_write=True))

    assert ok is False
    assert attempts == ["📋 план"]


async def test_plan_card_real_bad_request_is_not_swallowed():
    call, _ = _callback_raising_on_edit(_bad_request(_PARSE_ERROR_TEXT))

    with pytest.raises(TelegramBadRequest):
        await screens._reply(call, "📋 план", screens.plans_keyboard(can_write=True))


async def test_surface_module_is_the_only_place_that_knows_the_rule():
    """One implementation per rule (TB-03): handlers delegate, none re-state it."""
    for module in (console, screens):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "surface.is_no_op_edit" in source
        assert "message is not modified" not in source
    chat_source = pathlib.Path(chat_module.__file__).read_text(encoding="utf-8")
    assert "surface.looks_like_command" in chat_source


def test_aiogram_message_text_is_frozen_so_the_rewrite_uses_the_bypass():
    """The middleware rests on this: assignment is refused, the bypass works.

    aiogram builds its models with ``frozen=True``, so a naive ``event.text = …``
    inside the middleware would raise on every corrected command.  Asserted here
    rather than trusted, because the failure would only appear in production.
    """
    message: Message = make_message(user_id=OWNER_ID, text="/plans,")

    with pytest.raises(ValidationError):
        message.text = "/plans"

    surface.set_event_text(message, "/plans")
    assert message.text == "/plans"
    assert message.chat.id == OWNER_ID
    assert message.from_user.id == OWNER_ID


async def test_corrected_token_is_matched_by_the_real_command_filter():
    """End-to-end shape of Q-15: after the middleware, ``Command("plans")`` hits."""
    from aiogram import Bot
    from aiogram.filters import Command

    bot = Bot(token="123456:TEST-TOKEN")
    try:
        missed = make_message(user_id=OWNER_ID, text="/plans,")
        assert await Command("plans")(missed, bot) is False

        corrected = make_message(user_id=OWNER_ID, text="/plans,")

        async def _noop(event, data):
            return None

        await surface.command_surface_middleware(_noop, corrected, {})
        matched = await Command("plans")(corrected, bot)
        assert matched is not False
        assert matched["command"].command == "plans"
    finally:
        await bot.session.close()
