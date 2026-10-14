"""Q-01 (curator decision 2026-10-08) — only V3 + paper_v2 may start a day.

Before this slice, a failed ``paper_trading`` import or a refused ``start_day``
fell through silently to ``services.session_manager``, which wrote
``trading_sessions``/``session_plans``.  One owner then had two different
"current sessions" with different balances.  These tests pin the fail-closed
behaviour: no domain answer means no start anywhere, and the legacy writer is
provably untouched.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from conftest import OWNER_ID, session_state_payload

from handlers import pipeline
from paper_trading import adapter as pt_adapter

INTENT = {
    "intent": "pipeline_start",
    "budget": 100.0,
    "risk_mode": "balanced",
    "trade_profile": {"trade_direction": "auto", "trade_horizon": "fast"},
}


@pytest.fixture
def legacy_writer_guard(monkeypatch):
    """Any call into the legacy session writers fails the test loudly."""
    import services.session_manager as sm

    touched: list[str] = []

    def _make(name):
        async def _boom(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"legacy path used: {name}()")

        return _boom

    for name in ("create_session", "create_session_with_bootstrap", "bootstrap_session",
                 "generate_initial_plan", "apply_trade_profile_to_session"):
        if hasattr(sm, name):
            monkeypatch.setattr(sm, name, _make(name))
    return touched


@pytest.fixture
def legacy_status_guard(monkeypatch):
    """The legacy trading_sessions status renderer must not be used either."""

    async def _boom(*args, **kwargs):
        raise AssertionError("legacy _build_status_response used for a domain day")

    monkeypatch.setattr(pipeline, "_build_status_response", _boom)


async def test_refused_domain_start_returns_backend_unavailable(monkeypatch, legacy_writer_guard):
    async def _no_day(owner_id, **kwargs):
        return session_state_payload(running=False)

    async def _refused(**kwargs):
        return {"ok": False, "error": "service_unavailable"}

    monkeypatch.setattr(pt_adapter, "get_current_state", _no_day)
    monkeypatch.setattr(pt_adapter, "start_day", _refused)

    text, kb = await pipeline._handle_start_safe(OWNER_ID, INTENT)

    assert "BACKEND_UNAVAILABLE" in text
    assert kb is None
    assert legacy_writer_guard == []


async def test_adapter_failure_fails_closed_without_a_legacy_start(monkeypatch, legacy_writer_guard):
    async def _broken(owner_id, **kwargs):
        raise RuntimeError("paper_trading service unavailable")

    monkeypatch.setattr(pt_adapter, "get_current_state", _broken)

    text, kb = await pipeline._handle_start_safe(OWNER_ID, INTENT)

    assert "BACKEND_UNAVAILABLE" in text
    assert kb is None
    assert legacy_writer_guard == []
    # the refusal text says explicitly that the old path is not a fallback
    assert "trading_sessions" in text


async def test_error_card_never_claims_a_session_was_started(monkeypatch):
    async def _broken(owner_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pt_adapter, "get_current_state", _broken)

    text, _ = await pipeline._handle_start_safe(OWNER_ID, INTENT)

    assert "Торговый день запущен" not in text
    assert "сессия НЕ запущена" in text


async def test_running_day_is_reported_from_the_domain(monkeypatch, legacy_status_guard,
                                                       legacy_writer_guard):
    async def _running(owner_id, **kwargs):
        return session_state_payload(running=True)

    async def _unused_start(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("second day must not be created while one is running")

    monkeypatch.setattr(pt_adapter, "get_current_state", _running)
    monkeypatch.setattr(pt_adapter, "start_day", _unused_start)

    text, kb = await pipeline._handle_start_safe(OWNER_ID, INTENT)

    assert "уже идёт" in text
    assert "<code>faaf2226</code>" in text
    assert "998.45 USDT" in text          # balance comes from the domain verbatim
    # the state is the domain's enum value, never its type name (live-probe find)
    assert "RUNNING" in text
    assert "DayState" not in text
    assert kb is not None
    assert legacy_writer_guard == []


def test_start_path_contains_no_legacy_session_import():
    """Static guard: the fallthrough is gone from the code, not just at runtime.

    The docstring is allowed to *name* the retired path; the executable body is
    not, so the check runs on the source with the docstring removed.
    """
    src = inspect.getsource(pipeline._handle_start_safe)
    node = ast.parse(src).body[0]
    doc_node = node.body[0]
    assert isinstance(doc_node, ast.Expr) and isinstance(doc_node.value, ast.Constant)

    lines = src.splitlines(keepends=True)
    # drop the docstring block by line range: it may *name* the retired path
    code = "".join(lines[: doc_node.lineno - 1] + lines[doc_node.end_lineno :])
    assert "session_manager" not in code
    assert "create_session" not in code
    assert "get_active_session" not in code
    assert "_build_status_response" not in code
    assert "paper_trading.adapter" in code


def test_error_text_is_exposed_as_a_single_domain_constant():
    assert pipeline.ERR_DOMAIN_UNAVAILABLE.startswith("🔴")
    assert "paper_trading" in pipeline.ERR_DOMAIN_UNAVAILABLE


# ─── Q-24 (срез P4b): управление дня говорит только с доменом ────────────

#: единственные доменные вызовы экрана; новый вход сюда добавляется только
#: вместе с проверкой, что у него есть runner-исполнитель и карточка
_DAY_ADAPTER_CALLS = {
    "load_day_limits", "get_current_state", "pause_auto", "resume_auto",
    "close_all_positions", "finish_day", "set_day_limits",
}


def _day_control_modules():
    from handlers import day_control as day_handler
    from services import day_control as day_service

    return day_service, day_handler


def test_day_control_surface_has_no_legacy_session_path():
    """Ни экран, ни сервис не пишут в легаси: имена и импорты, не подстроки.

    Docstring имеет право назвать удалённый путь (и называет), поэтому
    проверяется дерево исходника, а не текст файла.
    """
    retired = {"session_manager", "apply_revision_command", "session_closeout",
               "get_active_session", "create_session"}
    for module in _day_control_modules():
        tree = ast.parse(inspect.getsource(module))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imports = {str(a.name) for a in ast.walk(tree) if isinstance(a, ast.alias)}

        assert not retired & names, (module.__name__, retired & names)
        assert not any("session_manager" in name for name in imports)


def test_day_control_reaches_the_domain_only_through_the_adapter():
    """Все записи экрана идут через ``paper_trading.adapter`` — одним списком."""
    service, _handler = _day_control_modules()
    tree = ast.parse(inspect.getsource(service))

    used = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "pt_adapter"
    }

    assert used <= _DAY_ADAPTER_CALLS, used - _DAY_ADAPTER_CALLS
    assert {"pause_auto", "resume_auto", "close_all_positions", "finish_day",
            "set_day_limits"} <= used
