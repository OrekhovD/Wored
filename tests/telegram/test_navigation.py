"""Navigation and refresh contract for the console (ТЗ §2 nav, §3 callbacks, §7 perf).

The first-level menu is the inline keyboard «Сейчас / Прогноз / Сессия / Позиции /
Итоги / Ещё».  Callbacks in P2 are navigation only: they carry no resource id and
no authority, they are ACKed before any read, and the refresh path is throttled.
"""

from __future__ import annotations

from conftest import OWNER_ID, make_callback, make_message, snapshot_payload

from handlers import now as console
from security import guards
from security.principal import resolve

CONTRACT = "BTC-USDT"


def _principal():
    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal


# ─── Menu shape ────────────────────────────────────────────────────────


def test_nav_keyboard_has_the_six_first_level_entries():
    kb = console.nav_keyboard()
    texts = [b.text for row in kb.inline_keyboard for b in row]
    data = [b.callback_data for row in kb.inline_keyboard for b in row]

    for label, callback in (
        ("Сейчас", "nav:now"),
        ("Прогноз", "nav:forecast"),
        ("Сессия", "nav:session"),
        ("Позиции", "nav:positions"),
        ("Итоги", "nav:report"),
        ("Ещё", "nav:more"),
    ):
        assert any(label in t for t in texts), label
        assert callback in data

    assert data.count("now:refresh") == 1


def test_nav_without_refresh_is_used_by_detail_screens():
    kb = console.nav_keyboard(include_refresh=False)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "now:refresh" not in data
    assert len(data) == 6


def test_every_navigation_callback_resolves_to_one_screen():
    for data, screen in console._SCREENS.items():
        assert callable(screen), data
    assert set(console._SCREENS) == {
        "nav:now", "nav:forecast", "nav:session", "nav:positions",
        "nav:report", "nav:more", "nav:help", "now:refresh",
    }


def test_callback_data_carries_no_resource_id_or_authority():
    """A stale card must not be able to execute anything (ТЗ §3, P2 scope)."""
    for data in console._SCREENS:
        assert ":" in data
        parts = data.split(":")
        assert len(parts) == 2
        assert parts[1].isidentifier()


# ─── Callback behaviour ────────────────────────────────────────────────


async def test_navigation_callback_acks_before_rendering(wired_sources):
    wired_sources["redis"].values = {f"market:perpetual:htx:{CONTRACT}": snapshot_payload()}
    call = make_callback(user_id=OWNER_ID, data="nav:now")

    await console.cb_navigate(call, _principal())

    assert call.answered and call.answered[0]["text"] is None   # plain ACK, no alert
    assert call.message.edited, "the card must be refreshed in place"


async def test_unknown_callback_data_matches_no_screen():
    """Mutating ids are simply absent from the P2 surface (ТЗ §3)."""
    for forbidden in ("nav:delete_session", "session:finish", "order:confirm"):
        assert forbidden not in console._SCREENS


async def test_refresh_is_throttled_per_actor(wired_sources):
    wired_sources["redis"].values = {f"market:perpetual:htx:{CONTRACT}": snapshot_payload()}

    first = make_callback(user_id=OWNER_ID, data="now:refresh")
    await console.cb_navigate(first, _principal())
    assert first.message.edited

    second = make_callback(user_id=OWNER_ID, data="now:refresh")
    await console.cb_navigate(second, _principal())

    assert not second.message.edited, "second tap within the window must not re-read"
    assert "не чаще" in second.answered[-1]["text"]


async def test_throttle_window_is_configurable(monkeypatch):
    monkeypatch.setenv("BOT_MARKET_REFRESH_MIN_SECONDS", "0")
    monkeypatch.setattr(guards, "_refresh_limiter", None)
    assert guards.refresh_limiter().allow(OWNER_ID, "now") is True
    assert guards.refresh_limiter().allow(OWNER_ID, "now") is True


async def test_screen_failure_becomes_a_normalized_error_card(wired_sources, monkeypatch):
    """No traceback, no partial numbers, only a request id the user can quote."""

    async def _explode(*args, **kwargs):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(console.api, "read_market_snapshot", _explode)

    message = make_message(user_id=OWNER_ID, text="/now")
    await console._render(console.show_now, message, _principal())

    text = message.sent[-1]["text"]
    assert "BACKEND_UNAVAILABLE" in text
    assert "Traceback" not in text
    assert "req <code>" in text


# ─── Mini App link policy ──────────────────────────────────────────────


def test_miniapp_button_requires_a_public_https_origin(monkeypatch):
    monkeypatch.setenv("WEBUI_PUBLIC_BASE_URL", "https://trader.example.org")
    button = console._miniapp_button()
    assert button is not None
    assert button.web_app is not None
    assert button.web_app.url == "https://trader.example.org/command-deck"


def test_miniapp_button_is_dropped_for_localhost(monkeypatch):
    monkeypatch.setenv("WEBUI_PUBLIC_BASE_URL", "http://localhost:8080")
    assert console._miniapp_button() is None


async def test_more_screen_states_the_miniapp_reality(wired_sources, monkeypatch):
    """«Ещё» must say honestly whether the graph link exists (U10)."""
    monkeypatch.delenv("WEBUI_PUBLIC_BASE_URL", raising=False)
    message = make_message(user_id=OWNER_ID, text="/now")
    await console.show_more(message, _principal())
    assert "Mini App" in message.sent[0]["text"]
    assert "не настроен" in message.sent[0]["text"]

    monkeypatch.setenv("WEBUI_PUBLIC_BASE_URL", "https://trader.example.org")
    other = make_message(user_id=OWNER_ID, text="/now")
    await console.show_more(other, _principal())
    assert "🟢 Mini App" in other.sent[0]["text"]
