"""Test harness for the Telegram read console (ТЗ Telegram Bot V1, P2 gate).

The gate is «реальный source → backend → Telegram mock»: fixtures imitate the
transport objects and the data sources, while the code under test stays the real
``integrations.wored_api`` → ``application.presenters`` → ``handlers.now`` chain.
Nothing here talks to Telegram, Postgres or Redis.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

ROOT = Path(__file__).resolve().parents[2]


def _mirror_container_pythonpath() -> None:
    """Resolve top-level packages exactly as the chatbot container does.

    The container runs with ``PYTHONPATH=/app:/opt`` where ``/app`` is
    ``chatbot/``, so ``storage`` means ``chatbot/storage``.  The repository-level
    ``conftest.py`` inserts ``webui``, ``chatbot`` and ``collector`` in that
    order, and because each insert goes to position 0 ``collector`` ends up first
    — ``import storage`` would then silently bind the *collector* copy, i.e. a
    different module than the one the bot imports in production.  Ordering is
    forced here instead of merely prepending, and the fixtures assert the
    resolution so a wrong module can never make a test pass.
    """
    for path in reversed([str(ROOT), str(ROOT / "chatbot")]):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


_mirror_container_pythonpath()


def day_state(value: str):
    """A real ``paper_trading.contracts.DayState`` member, as production returns.

    ``get_current_state`` puts the enum itself into ``day["state"]`` (only the
    report endpoints use ``.value``).  A fixture that hands back a plain string
    hides the difference: ``str(DayState.running)`` is ``"DayState.running"``,
    so the card would leak an internal type name.  Fixtures must therefore speak
    the domain's exact type, not a tidy approximation of it.
    """
    from paper_trading.contracts import DayState

    return DayState(value)

OWNER_ID = 5249526259
STRANGER_ID = 9999999999
VIEWER_ID = 777
CONTRACT = "BTC-USDT"


@pytest.fixture(autouse=True)
def bot_env(monkeypatch):
    """Deterministic authorization env for every test in this package."""
    monkeypatch.setenv("BOT_OWNER_IDS", str(OWNER_ID))
    monkeypatch.setenv("BOT_VIEWER_IDS", str(VIEWER_ID))
    monkeypatch.delenv("BOT_ADMIN_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_IDS", raising=False)
    monkeypatch.setenv("BOT_ALLOWED_CHAT_TYPES", "private")
    monkeypatch.setenv("BOT_ALLOW_GROUPS", "false")
    monkeypatch.setenv("BOT_DEFAULT_TIMEZONE", "Asia/Bangkok")
    monkeypatch.setenv("BOT_MARKET_REFRESH_MIN_SECONDS", "2")
    monkeypatch.setenv("PAPER_CONTRACTS", CONTRACT)
    monkeypatch.delenv("BOT_DEFAULT_INSTRUMENT", raising=False)
    monkeypatch.delenv("PAPER_MARKET_MAX_AGE_SECONDS", raising=False)
    # the Mini App link depends on the public origin, never on ambient env
    for key in ("WEBUI_PUBLIC_BASE_URL", "TG_MINIAPP_URL", "WEBUI_URL"):
        monkeypatch.delenv(key, raising=False)
    # reload-independent: the guard reads env lazily
    from security import guards

    monkeypatch.setattr(guards, "_refresh_limiter", None)
    yield


def snapshot_payload(*, age_seconds: float = 1.0, quality: str = "live") -> dict:
    """A payload shaped exactly like the collector's live Redis value."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    iso = moment.isoformat()
    return {
        "schema_version": 1,
        "mode": "",
        "venue": "htx",
        "market_type": "linear-swap",
        "contract_code": CONTRACT,
        "bid": "82668.3",
        "ask": "82668.4",
        "last": "82650.7",
        "mark": "82656.6",
        "index": "82694.40857142858",
        "funding_rate": "0.000046650486566967",
        "next_funding_at": (moment + timedelta(hours=4)).isoformat(),
        "component_times": {
            "ticker": iso,
            "index": iso,
            "mark": iso,
            "funding": iso,
        },
        "contract_size": "0.001",
        "price_tick": "0.1",
        "quantity_step": "0.001",
        "source_at": iso,
        "received_at": iso,
        "source": "htx-public-rest",
        "quality": quality,
    }


def forecast_detail_payload(*, symbol: str = "btcusdt", status: str = "completed") -> dict:
    """Shaped exactly like ``storage.postgres_client.get_latest_prediction_request``."""
    now = datetime.now(timezone.utc)
    return {
        "id": 191,
        "symbol": symbol,
        "horizon_hours": 4,
        "base_price": 83148.70,
        "status": status,
        "created_at": (now - timedelta(minutes=12)).isoformat(),
        "completed_models": 3,
        "failed_models": 0,
        "total_points": 48,
        "evaluated_points": 0,
        "avg_accuracy": None,
        "top_model": "deepseek-v4-pro",
        "models": [
            {
                "model_name": "deepseek-v4-pro",
                "status": "completed",
                "points": [
                    {
                        "predicted_price": 82980.4,
                        "predicted_change_pct": -0.2024,
                        "target_time": (now + timedelta(hours=3)).isoformat(),
                        "model_name": "deepseek-v4-pro",
                    }
                ],
            }
        ],
    }


def session_state_payload(*, running: bool = True) -> dict:
    """Shape returned by ``paper_trading.service.get_current_state``."""
    if not running:
        return {"ok": True, "day": None, "accounts": [], "next_action": "start"}
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "day": {
            "id": "faaf2226-1111-2222-3333-444455556666",
            "state": day_state("running"),
            "start_at": now.replace(hour=7, minute=0, second=0, microsecond=0).isoformat(),
            "end_at": now.replace(hour=14, minute=0, second=0, microsecond=0).isoformat(),
        },
        "accounts": [
            {
                "id": "acc-0001-aaaa-bbbb-ccccdddddddd",
                "kind": "manual",
                "currency": "USDT",
                "cash": "998.4521",
                "open_positions": 1,
                "positions": [
                    {
                        "id": "pos-0001-aaaa-bbbb-ccccdddddddd",
                        "side": "long",
                        "qty": "0.002",
                        "avg_entry": "82600.0",
                        "stop": "82100.0",
                        "target": "83400.0",
                    }
                ],
            }
        ],
        "next_action": "trade",
    }


class FakeRedis:
    """Async Redis stand-in with only what the console reads."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}
        self.requested: list[str] = []

    async def get(self, key: str):
        self.requested.append(key)
        value = self.values.get(key)
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value


class FakeMessage(Message):
    """Real aiogram Message that records outgoing text instead of sending it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "sent", [])
        object.__setattr__(self, "edited", [])

    async def answer(self, text, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return self

    async def edit_text(self, text, **kwargs):
        self.edited.append({"text": text, **kwargs})
        return self


class FakeCallback(CallbackQuery):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "answered", [])

    async def answer(self, text=None, **kwargs):
        self.answered.append({"text": text, **kwargs})
        return True


def make_user(user_id: int, first_name: str = "Оператор") -> User:
    return User(id=user_id, is_bot=False, first_name=first_name)


def make_message(user_id: int = OWNER_ID, chat_type: str = "private", text: str = "/now") -> FakeMessage:
    chat = Chat(id=user_id if chat_type == "private" else -100123, type=chat_type)
    return FakeMessage(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=make_user(user_id),
        text=text,
    )


def make_callback(user_id: int = OWNER_ID, data: str = "nav:now", chat_type: str = "private") -> FakeCallback:
    message = make_message(user_id=user_id, chat_type=chat_type)
    return FakeCallback(
        id="cb-1",
        from_user=make_user(user_id),
        message=message,
        data=data,
        chat_instance="instance-1",
    )


@pytest.fixture
def owner_principal():
    from security.principal import resolve

    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal


@pytest.fixture
def plan_store(monkeypatch):
    """Bind the bot's plan service to the dev store; everything else stays real.

    Only the store handle is swapped (P3b moved it into :mod:`trading_math`).  The
    write policy, the shared validation pipeline, the presenters and the handlers
    run exactly as in the container, so a test cannot pass on a stubbed domain.
    """
    from services import simulation_plans as sp
    from trading_math.simulation_store import MemorySimulationStore

    assert hasattr(sp, "get_store"), "services.simulation_plans must own the store binding"
    store = MemorySimulationStore()

    async def _get_store():
        return store

    monkeypatch.setattr(sp, "get_store", _get_store)
    return store


@pytest.fixture
def fake_redis():
    return FakeRedis({f"market:perpetual:htx:{CONTRACT}": snapshot_payload()})


@pytest.fixture
def wired_sources(monkeypatch, fake_redis):
    """Point every external read at fixtures; the console code stays untouched.

    Only the transport boundary is replaced.  ``integrations.wored_api``,
    ``application.presenters`` and ``handlers.now`` run for real, which is what
    the P2 gate («source → backend → Telegram mock») demands.
    """
    import storage.redis_client as redis_module
    import storage.postgres_client as pg_module
    from paper_trading import adapter as pt_adapter

    assert hasattr(pg_module, "get_latest_prediction_request"), (
        "storage.postgres_client resolved to "
        f"{getattr(pg_module, '__file__', '?')} — must be the chatbot copy"
    )
    assert hasattr(redis_module, "get_redis"), (
        f"storage.redis_client resolved to {getattr(redis_module, '__file__', '?')}"
    )

    monkeypatch.setattr(redis_module, "get_redis", lambda: fake_redis)

    calls: dict[str, list] = {"forecast": [], "session": [], "reports": []}

    async def _latest_prediction_request(*, symbol: str = "", **kwargs):
        calls["forecast"].append(symbol)
        return wired_sources.forecast

    async def _get_current_state(owner_id: str, **kwargs):
        calls["session"].append(owner_id)
        return wired_sources.session

    async def _list_day_reports(owner_id: str, limit: int = 30, **kwargs):
        calls["reports"].append(owner_id)
        return wired_sources.reports

    monkeypatch.setattr(pg_module, "get_latest_prediction_request", _latest_prediction_request)
    monkeypatch.setattr(pt_adapter, "get_current_state", _get_current_state)
    monkeypatch.setattr(pt_adapter, "list_day_reports", _list_day_reports)

    wired_sources.forecast = None
    wired_sources.session = session_state_payload(running=False)
    wired_sources.reports = []
    wired_sources.calls = calls
    yield {
        "redis": fake_redis,
        "calls": calls,
        "set_forecast": lambda value: setattr(wired_sources, "forecast", value),
        "set_session": lambda value: setattr(wired_sources, "session", value),
        "set_reports": lambda value: setattr(wired_sources, "reports", value),
    }
