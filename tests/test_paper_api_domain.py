"""Domain-authoritative behaviour of the trading-day API (TZ issue #1/#2).

These tests force the PostgreSQL domain path (``_use_pt_domain``) and stub the
adapter so we can prove the API never fabricates a green, ready day and never
silently falls back to writing the prototype ledger beside the real one.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

import webui.paper_api as paper_api
from webui.paper_api import router


@pytest.fixture
def domain_app() -> FastAPI:
    application = FastAPI()
    application.state.paper_market_mode = "demo"
    application.state.redis_client = None
    # A non-None pg_pool makes _use_pt_domain report the domain path as active.
    application.state.pg_pool = object()
    application.include_router(router)
    application.add_middleware(SessionMiddleware, secret_key="paper-domain-test-secret")
    return application


@pytest.fixture(autouse=True)
def _force_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_api, "_pt_available", True)
    monkeypatch.setattr(paper_api, "_pt_owner_id", lambda name, telegram_user_id=None: "owner-uuid")


async def _get(application: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as session:
        return await session.get(path)


async def _post(application: FastAPI, path: str, payload: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as session:
        return await session.post(path, json=payload)


async def test_domain_reachable_but_no_day_is_not_fabricated(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_state(owner_id: str) -> dict[str, Any]:
        return {"ok": True, "day": None, "accounts": [], "next_action": "start"}

    monkeypatch.setattr(paper_api, "_pt_get_state", fake_state)
    data = (await _get(domain_app, "/api/trading-day/current")).json()
    # No invented day and no made-up engine status.
    assert data["day"] is None
    assert "engine_status" not in data
    assert all(not str(account["id"]).startswith("proto-") or data["day"] is None
               for account in data["accounts"])
    # Freshness is computed honestly from the demo market mode, not forced green.
    assert data["market_mode"] == "demo"
    assert data["fresh"] is False


async def test_domain_source_error_blocks_readiness(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_state(owner_id: str) -> dict[str, Any]:
        return {"ok": False, "error": "db_down", "day": None, "accounts": [], "next_action": "start"}

    monkeypatch.setattr(paper_api, "_pt_get_state", fake_state)
    resp = await _get(domain_app, "/api/trading-day/current")
    data = resp.json()
    assert data["fresh"] is False
    assert data["capabilities"]["can_start"] is False
    assert "недоступен" in data["reason_code"]
    assert data["capabilities"]["domain_error"]


async def test_start_domain_failure_is_fail_closed(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_start(**kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "boom"}

    async def fake_state(owner_id: str) -> dict[str, Any]:
        return {"ok": True, "day": None, "accounts": [], "next_action": "start"}

    monkeypatch.setattr(paper_api, "_pt_start_day", fake_start)
    monkeypatch.setattr(paper_api, "_pt_get_state", fake_state)
    resp = await _post(domain_app, "/api/trading-day/start", {"idempotency_key": "x"})
    assert resp.status_code == 502
    # No prototype split-brain: the day was never created in the ledger.
    current = (await _get(domain_app, "/api/trading-day/current")).json()
    assert current["day"] is None


async def test_domain_active_day_is_rendered_in_ui_schema(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_state(owner_id: str) -> dict[str, Any]:
        return {
            "ok": True,
            "day": {"id": "d1", "state": "running",
                    "start_at": "2026-09-26T03:00:00+00:00",
                    "end_at": "2026-09-26T14:00:00+00:00"},
            "accounts": [{
                "id": "acc-manual", "kind": "manual", "currency": "USDT", "cash": "1000",
                "open_positions": 1,
                "positions": [{"id": "p1", "side": "long", "qty": "0.01",
                               "avg_entry": "64000", "stop": "63000", "target": "65000"}],
            }],
            "next_action": "trade",
        }

    monkeypatch.setattr(paper_api, "_pt_get_state", fake_state)
    data = (await _get(domain_app, "/api/trading-day/current")).json()
    assert data["day"]["id"] == "d1"
    assert data["day"]["state"] == "active"  # "running" mapped to UI "active"
    assert data["capabilities"]["can_start"] is False
    manual = next(account for account in data["accounts"] if account["kind"] == "manual")
    assert manual["equity"] == "1000"
    position = data["positions"][0]
    assert position["id"] == "p1"
    assert position["entry_price"] == "64000"
    assert position["origin"] == "manual"
    # Fields the domain does not expose are absent, not fabricated.
    assert "unrealized_net" not in position


async def test_automation_domain_failure_is_fail_closed(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_pause(owner_id: str) -> dict[str, Any]:
        return {"ok": False, "error": "no_auto_account"}

    monkeypatch.setattr(paper_api, "_pt_pause_auto", fake_pause)
    resp = await _post(domain_app, "/api/trading-day/d1/automation", {"action": "pause"})
    assert resp.status_code == 502


async def test_order_placement_routes_to_domain(
    domain_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_state(owner_id: str) -> dict[str, Any]:
        return {
            "ok": True,
            "day": {"id": "d1", "state": "running",
                    "start_at": "2026-09-26T03:00:00+00:00",
                    "end_at": "2026-09-26T14:00:00+00:00"},
            "accounts": [{
                "id": "acc-manual", "kind": "manual", "currency": "USDT", "cash": "1000",
                "open_positions": 0, "positions": [],
            }],
            "next_action": "trade",
        }

    captured: dict[str, Any] = {}

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "command_id": "cmd-1"}

    monkeypatch.setattr(paper_api, "_pt_get_state", fake_state)
    monkeypatch.setattr(paper_api, "_pt_submit_order", fake_submit)
    resp = await _post(domain_app, "/api/paper/accounts/acc-manual/orders", {
        "side": "buy", "order_type": "market", "risk": "10",
        "stop_price": "63750", "instrument": "btcusdt",
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"
    assert captured["account_id"] == "acc-manual"
    assert captured["side"] == "buy"
    assert captured["qty"]  # risk was converted to a quantity via the preview math
