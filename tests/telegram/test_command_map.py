"""TB-03 — one command, one implementation (ТЗ Telegram Bot V1 §2, §8 P2 gate).

The ТЗ forbids «две разные реализации одного действия».  These tests check that at
three levels: the wiring in ``main.py`` (guard first, console first), the command
surface of every router the bot includes (no command may have two owners), and
behaviour (a deprecated command must render byte-identical output to the new one,
because it literally calls the same function object).
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from conftest import OWNER_ID, forecast_detail_payload, make_message, session_state_payload

from handlers import (
    alerts,
    analytics,
    callbacks,
    chat,
    day_control,
    deprecated,
    market,
    menu,
    news,
    now as console,
    paper_deal,
    pipeline,
    plans,
    portfolio,
    predictions,
    settings,
    simulation_v3,
    start,
    trader,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_SRC = (ROOT / "chatbot" / "main.py").read_text(encoding="utf-8")

#: the routers main.py includes, in the same order it includes them
_ROUTERS = [
    console.router, simulation_v3.router, deprecated.router, start.router,
    menu.router, callbacks.router,
    market.router, alerts.router, analytics.router, portfolio.router,
    predictions.router, settings.router, trader.router, plans.router,
    pipeline.router, news.router, paper_deal.router, day_control.router,
    chat.router,
]


def _command_names_of(handler) -> set[str]:
    """Extract command strings from an aiogram handler's filter chain.

    aiogram wraps every filter into a ``FilterObject``, so the real ``Command``
    instance lives on ``filter.callback``.
    """
    names: set[str] = set()
    for flt in getattr(handler, "filters", ()) or ():
        target = getattr(flt, "callback", flt)
        for cmd in getattr(target, "commands", None) or ():
            value = getattr(cmd, "command", cmd)
            if isinstance(value, str):
                names.add(value.lstrip("/").lower())
    return names


def _owners_by_command() -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for router in _ROUTERS:
        for handler in router.message.handlers:
            module = getattr(handler.callback, "__module__", "?")
            for name in _command_names_of(handler):
                owners.setdefault(name, []).append(f"{module}.{handler.callback.__name__}")
    return owners


# ─── Wiring order ──────────────────────────────────────────────────────


def test_guard_is_installed_before_any_router():
    """Deny-by-default must run before the first router can match an update."""
    install_at = MAIN_SRC.index("guards.install(dp)")
    first_include = min(
        m.start() for m in re.finditer(r"dp\.include_router\(", MAIN_SRC)
    )
    assert install_at < first_include


def test_console_and_deprecated_are_included_first_and_chat_last():
    order = re.findall(r"dp\.include_router\((\w+)", MAIN_SRC)
    # the plan router must precede the redirect layer, or ``/plan`` would shadow
    # the canonical ``/plans`` it redirects to
    assert order[:3] == ["console_router", "simulation_v3_router", "deprecated_router"]
    assert order.index("chat_router") == len(order) - 1


# ─── Command surface ───────────────────────────────────────────────────


def test_no_command_has_two_owners():
    owners = _owners_by_command()
    duplicates = {cmd: who for cmd, who in owners.items() if len(who) > 1}
    assert not duplicates, f"second implementation found: {duplicates}"


@pytest.mark.parametrize("command", ["start", "now", "market", "forecast", "session",
                                     "positions", "report", "help", "cancel"])
def test_console_owns_every_new_command(command):
    assert _owners_by_command().get(command) == [f"handlers.now.cmd_{command}"]


def test_plans_command_is_owned_by_the_v3_screen_layer():
    """P3c: ``/plans`` is canonical, not a redirect any more."""
    assert _owners_by_command().get("plans") == ["handlers.simulation_v3.cmd_plans"]


def test_legacy_routers_no_longer_bind_moved_commands():
    owners = _owners_by_command()
    for command in ("market", "forecast", "predictions", "portfolio", "trader",
                    "plans", "diff", "accuracy", "session", "plan", "start"):
        assert len(owners.get(command, [])) <= 1, command


def test_session_and_plan_are_not_bound_in_pipeline_anymore():
    """Q-01: the console owns /session, the V3 layer owns /plans, /plan redirects."""
    assert not hasattr(pipeline, "cmd_session")
    assert not hasattr(pipeline, "cmd_plan")
    # the legacy daily-pipeline router registers nothing at all: its helpers are
    # unreachable from the command surface, they are not a second implementation
    assert not plans.router.message.handlers
    assert not plans.router.callback_query.handlers


def test_deal_and_day_commands_have_exactly_one_owner():
    """P4/P4b: ручная сделка и управление дня — по одному экрану на команду."""
    owners = _owners_by_command()
    assert owners.get("deal") == ["handlers.paper_deal.cmd_deal"]
    assert owners.get("day") == ["handlers.day_control.cmd_day"]


def test_mutable_day_actions_are_not_encoded_in_button_literals():
    """Q-24: мутирующее действие не кодируется строкой кнопки (ТЗ §3).

    Инвентарь по всем экранам: строковые литералы фильтров ``F.data`` не должны
    содержать слова убитого действия. Текстовые regex-фильтры не считаются:
    «пауза сессии» — распознаваемая фраза, а не полномочие кнопки. Docstring
    тоже не считается: он имеет право назвать удалённый путь.
    """
    handlers_dir = pathlib.Path(day_control.__file__).parent
    offenders: dict[str, set[str]] = {}
    for path in sorted(handlers_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not any(
                    isinstance(child, ast.Attribute) and child.attr == "data"
                    for child in ast.walk(decorator)
                ):
                    continue
                literals = {
                    c.value for c in ast.walk(decorator)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }
                hits = {v for v in literals if any(
                    word in v.lower() for word in
                    ("pause", "tighten", "reduce", "close_all", "closeall")
                )}
                if hits:
                    offenders.setdefault(path.name, set()).update(hits)

    assert offenders == {}, offenders


# ─── Redirect identity ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "old,target,screen_name",
    [
        ("predictions", "/forecast", "show_forecast"),
        ("portfolio", "/positions", "show_positions"),
        ("trader", "/session", "show_session"),
        ("accuracy", "/report", "show_reports"),
        ("diff", "/session", "show_session"),
    ],
)
def test_deprecated_command_calls_the_same_screen_object(old, target, screen_name):
    mapped_target, screen = deprecated._REDIRECTS[old]
    assert mapped_target == target
    assert screen is getattr(console, screen_name)


def test_plan_redirect_targets_the_v3_plan_screen():
    """``/plan`` resolves to the same function object ``/plans`` uses (TB-03)."""
    assert "plan" not in deprecated._REDIRECTS
    assert "plans" not in deprecated._REDIRECTS
    source = pathlib.Path(deprecated.__file__).read_text(encoding="utf-8")
    assert "plans_v3.show_plans" in source


@pytest.mark.parametrize("old", sorted(deprecated._REDIRECTS))
def test_deprecated_handlers_exist_for_every_old_command(old):
    handler = getattr(deprecated, f"cmd_{old}", None)
    assert handler is not None and callable(handler)


async def test_redirect_renders_identical_to_the_new_command(wired_sources):
    """/portfolio and /positions must not be able to show different numbers."""
    wired_sources["set_session"](session_state_payload(running=True))
    principal = _principal()

    direct = make_message(user_id=OWNER_ID, text="/positions")
    await console.show_positions(direct, principal)

    legacy = make_message(user_id=OWNER_ID, text="/portfolio")
    await deprecated.cmd_portfolio(legacy, principal)

    assert legacy.sent[0]["text"] == deprecated._NOTE.replace("{old}", "portfolio").replace(
        "{new}", "/positions"
    )
    assert legacy.sent[1]["text"] == direct.sent[0]["text"]


async def test_forecast_redirect_renders_the_same_card_as_forecast(wired_sources):
    wired_sources["set_forecast"](forecast_detail_payload())
    principal = _principal()

    direct = make_message(user_id=OWNER_ID, text="/forecast")
    await console.show_forecast(direct, principal)

    legacy = make_message(user_id=OWNER_ID, text="/predictions")
    await deprecated.cmd_predictions(legacy, principal)

    assert legacy.sent[1]["text"] == direct.sent[0]["text"]


async def test_legacy_redirect_carries_the_deprecation_note(wired_sources, plan_store):
    principal = _principal()
    message = make_message(user_id=OWNER_ID, text="/plan")
    await deprecated.cmd_plan(message, principal)

    assert "устарела" in message.sent[0]["text"]
    assert "<code>/plans</code>" in message.sent[0]["text"]
    # the old daily-pipeline tables are not touched: the screen is the V3 list
    assert "Мои планы" in message.sent[1]["text"]
    assert "session_plans" not in message.sent[1]["text"]


# ─── Reply-keyboard buttons route to the same screens ──────────────────


def test_menu_buttons_use_console_screens():
    """A button and the matching command cannot diverge (ТЗ §2 nav)."""
    handlers = {h.callback.__name__ for h in menu.router.message.handlers}
    assert set(handlers) == {
        "menu_market", "menu_analytics", "menu_predictions",
        "menu_portfolio", "menu_alerts", "menu_session_status",
    }
    # the legacy read helpers are no longer imported by the menu layer at all
    src = (ROOT / "chatbot" / "handlers" / "menu.py").read_text(encoding="utf-8")
    assert "send_market_data" not in src
    assert "_build_status_response" not in src


def test_start_router_has_no_second_start_command():
    owners = _owners_by_command()
    assert owners.get("start") == ["handlers.now.cmd_start"]
    assert hasattr(start, "get_main_keyboard")


def _principal():
    from security.principal import resolve

    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal
