"""TB-01 — deny-by-default permissions, negative cases first (ТЗ Telegram Bot V1 §3).

Every test asserts the *refusal*: the guarded handler must not run, no Principal
may be injected, and the refusal must not leak anything about the request.
"""

from __future__ import annotations

import logging

import pytest

from conftest import OWNER_ID, STRANGER_ID, VIEWER_ID, make_callback, make_message
from security import guards
from security import principal as P


async def _call_middleware(event):
    """Run the guard exactly as aiogram does; return (handler_ran, data, result)."""
    ran: list[bool] = []

    async def handler(event_, data):
        ran.append(True)
        return "handled"

    data: dict = {}
    result = await guards.auth_middleware(handler, event, data)
    return bool(ran), data, result


# ─── Unknown actors ────────────────────────────────────────────────────


async def test_unknown_user_is_denied_and_handler_never_runs():
    message = make_message(user_id=STRANGER_ID, text="/now")
    ran, data, result = await _call_middleware(message)

    assert ran is False
    assert "principal" not in data
    assert message.sent and guards.DENIED_TEXT in message.sent[0]["text"]
    assert result is message


async def test_group_chat_is_denied_even_for_the_owner():
    message = make_message(user_id=OWNER_ID, chat_type="group", text="/now")
    ran, _, _ = await _call_middleware(message)

    assert ran is False
    assert message.sent[0]["text"] == guards.GROUP_TEXT


async def test_callback_from_unknown_user_is_answered_and_not_executed():
    call = make_callback(user_id=STRANGER_ID, data="nav:positions")
    ran, data, _ = await _call_middleware(call)

    assert ran is False
    assert "principal" not in data
    assert call.answered and call.answered[0]["text"] == guards.DENIED_TEXT
    # a refused callback must not touch the message it points at
    assert not call.message.edited


async def test_missing_actor_is_denied():
    message = make_message(user_id=OWNER_ID)
    object.__setattr__(message, "from_user", None)
    ran, data, _ = await _call_middleware(message)

    assert ran is False
    assert "principal" not in data


# ─── Allowlist configuration fails closed ──────────────────────────────


async def test_empty_allowlist_denies_everyone(monkeypatch):
    monkeypatch.delenv("BOT_OWNER_IDS", raising=False)
    monkeypatch.delenv("BOT_VIEWER_IDS", raising=False)
    monkeypatch.delenv("BOT_ADMIN_IDS", raising=False)

    message = make_message(user_id=OWNER_ID)
    ran, _, _ = await _call_middleware(message)
    assert ran is False

    warning = P.configuration_warning()
    assert warning and "BOT_OWNER_IDS" in warning


async def test_non_numeric_id_in_allowlist_is_not_partially_trusted(monkeypatch):
    monkeypatch.setenv("BOT_OWNER_IDS", f"{OWNER_ID}, secret_token_like_text")

    assert P.owner_ids()[0] == set()
    message = make_message(user_id=OWNER_ID)
    ran, _, _ = await _call_middleware(message)
    assert ran is False


async def test_legacy_alias_is_accepted_and_reported_by_name(monkeypatch):
    monkeypatch.delenv("BOT_OWNER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", str(OWNER_ID))

    ids, used = P.owner_ids()
    assert ids == {OWNER_ID}
    assert used == "TELEGRAM_ADMIN_IDS"
    assert "legacy alias" in P.configuration_warning()

    message = make_message(user_id=OWNER_ID)
    ran, data, _ = await _call_middleware(message)
    assert ran is True
    assert data["principal"].role == P.ROLE_OWNER


# ─── Roles ─────────────────────────────────────────────────────────────


async def test_owner_is_resolved_with_server_derived_owner_id():
    message = make_message(user_id=OWNER_ID)
    ran, data, _ = await _call_middleware(message)

    principal = data["principal"]
    assert ran is True
    assert principal.role == P.ROLE_OWNER
    assert principal.may_mutate is True
    assert principal.is_reader is True


async def test_viewer_may_read_but_may_not_mutate():
    message = make_message(user_id=VIEWER_ID, text="/forecast")
    ran, data, _ = await _call_middleware(message)

    principal = data["principal"]
    assert ran is True
    assert principal.role == P.ROLE_VIEWER
    assert principal.is_reader is True
    assert principal.may_mutate is False


@pytest.mark.parametrize("bad_id", [None, 0, -1, "5249526259", 12.5])
def test_resolve_never_raises_on_bad_input(bad_id):
    assert P.resolve(bad_id) is None


def test_owner_identity_matches_the_paper_trading_domain():
    """One person = one owner_id in Telegram, WebUI and paper_trading."""
    from paper_trading.adapter import owner_id_from_telegram

    assert P.owner_id_for(OWNER_ID) == owner_id_from_telegram(OWNER_ID)


# ─── Audit hygiene (ТЗ §3: no private content in logs) ─────────────────


async def test_denial_audit_carries_no_message_content(caplog):
    secret_text = "тайное сообщение про ордера и баланс"
    message = make_message(user_id=STRANGER_ID, text=secret_text)

    with caplog.at_level(logging.WARNING, logger="chatbot.security"):
        await _call_middleware(message)

    lines = [record.getMessage() for record in caplog.records if "AUTH_DENIED" in record.getMessage()]
    assert lines, "denial must be audited"
    assert secret_text not in "\n".join(lines)
    assert "reason=NOT_ALLOWLISTED" in lines[0]
    assert str(STRANGER_ID) in lines[0]


# ─── Wiring ────────────────────────────────────────────────────────────


def test_install_registers_the_guard_for_messages_and_callbacks():
    class FakeDispatcher:
        def __init__(self):
            self.message = self._Point()
            self.callback_query = self._Point()

        class _Point:
            def __init__(self):
                self.outer_middleware_registered = []

            def outer_middleware(self, func):
                self.outer_middleware_registered.append(func)
                return func

    dp = FakeDispatcher()
    guards.install(dp)

    assert dp.message.outer_middleware_registered == [guards.auth_middleware]
    assert dp.callback_query.outer_middleware_registered == [guards.auth_middleware]


def test_groups_are_refused_when_configuration_claims_otherwise():
    """BOT_ALLOW_GROUPS is an explicit opt-in, never a default."""
    assert P.private_chat_only() is True
