"""Smoke test — verify all modules integrate correctly."""
import pytest


def test_service_container_imports():
    from core.services import ServiceContainer, UserSessionState
    assert ServiceContainer is not None

def test_bootstrap_imports():
    from core.bootstrap import build_service_container
    assert build_service_container is not None

def test_admin_service_imports():
    from admin.service import AdminService
    assert AdminService is not None

def test_all_handlers_importable():
    from bot.handlers import (
        cmd_start, cmd_help, cmd_ask, cmd_mode, cmd_models,
        cmd_providers, cmd_usage, cmd_quota, cmd_context,
        cmd_switch_model, cmd_health, cmd_reload, cmd_admin_stats,
    )
    assert callable(cmd_ask)

def test_htx_adapter_async_ratelimit():
    """Verify _wait_ratelimit is async."""
    import inspect
    from providers.htx_adapter import HTXMarketDataAdapter
    adapter = HTXMarketDataAdapter()
    assert inspect.iscoroutinefunction(adapter._wait_ratelimit)

def test_no_stale_directories():
    from pathlib import Path
    assert not Path("core/context").exists(), "core/context/ should be deleted"
    assert not Path("core/providers").exists(), "core/providers/ should be deleted"
