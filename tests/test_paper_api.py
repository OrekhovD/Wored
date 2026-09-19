"""Interaction contract for the browser-session paper trading prototype."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from webui.paper_api import router
from webui.paper_store import MemoryPaperStore


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.state.paper_market_mode = "demo"
    application.state.redis_client = None
    application.include_router(router)
    application.add_middleware(SessionMiddleware, secret_key="paper-prototype-test-secret")
    return application


@pytest.fixture
async def client(app: FastAPI):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as session:
        yield session


async def test_settings_and_start_persist_one_day_with_two_accounts(client: httpx.AsyncClient):
    settings = await client.post("/api/trading-day/settings", json={
        "end_time_local": "22:30", "timezone": "Asia/Bangkok",
        "opening_capital": "2500", "max_daily_loss_usdt": "100",
        "max_risk_per_order_usdt": "20", "max_leverage": 5,
    })
    assert settings.status_code == 200

    started = await client.post("/api/trading-day/start", json={"idempotency_key": "start-1"})
    assert started.status_code == 202
    current = (await client.get("/api/trading-day/current")).json()
    assert current["day"]["state"] == "active"
    assert [account["kind"] for account in current["accounts"]] == ["manual", "auto"]
    assert {account["cash"] for account in current["accounts"]} == {"2500"}

    repeated = await client.post("/api/trading-day/start", json={"idempotency_key": "start-1"})
    assert repeated.status_code == 200
    assert repeated.json()["day"]["id"] == current["day"]["id"]


async def test_manual_preview_rejects_wrong_stop_and_uses_decimal_strings(client: httpx.AsyncClient):
    await client.post("/api/trading-day/start", json={})
    invalid = await client.post("/api/paper/accounts/proto-manual/orders/preview", json={
        "side": "buy", "order_type": "market", "risk": "10", "stop_price": "65000",
    })
    assert invalid.status_code == 422

    valid = await client.post("/api/paper/accounts/proto-manual/orders/preview", json={
        "side": "buy", "order_type": "market", "risk": "10",
        "stop_price": "63750", "take_profit": "65250",
    })
    assert valid.status_code == 200
    payload = valid.json()
    assert payload["allowed"] is True
    assert payload["quantity"] == "0.02"
    assert payload["account_label"] == "Ручной счёт"
    assert payload["funding_status"] == "unknown"
    assert payload["leverage"] == 10
    assert payload["liquidation_price"] == "57825"

    too_high = await client.post("/api/paper/accounts/proto-manual/orders/preview", json={
        "side": "buy", "order_type": "market", "risk": "10", "leverage": 11,
        "stop_price": "63750",
    })
    assert too_high.status_code == 422


async def test_manual_fill_close_and_report_use_actual_demo_state(client: httpx.AsyncClient):
    started = (await client.post("/api/trading-day/start", json={})).json()
    day_id = started["day"]["id"]
    order_body = {
        "side": "buy", "order_type": "market", "risk": "10",
        "stop_price": "63750", "take_profit": "65250", "instrument": "btcusdt",
        "idempotency_key": "manual-order-1",
    }
    order = await client.post("/api/paper/accounts/proto-manual/orders", json=order_body)
    assert order.status_code == 202
    repeated_order = await client.post(
        "/api/paper/accounts/proto-manual/orders", json=order_body
    )
    assert repeated_order.status_code == 200
    assert repeated_order.json()["position_id"] == order.json()["position_id"]
    current = (await client.get("/api/trading-day/current")).json()
    assert len(current["positions"]) == 1
    assert current["accounts"][0]["open_positions"] == 1
    assert current["accounts"][1]["cash"] == "1000"

    position_id = current["positions"][0]["id"]
    closed = await client.post(f"/api/paper/positions/{position_id}/actions", json={"action": "close"})
    assert closed.status_code == 202

    finished = await client.post(f"/api/trading-day/{day_id}/finish", json={})
    assert finished.status_code == 202
    report = (await client.get("/api/trading-day/current")).json()
    assert report["day"]["state"] == "reconciled"
    assert report["report"]["manual"]["realized_net"] == "-1.542"
    assert report["report"]["manual"]["trades_count"] == 1
    assert report["report"]["auto"]["realized_net"] == "0"
    assert report["report"]["closed_trades"][0]["exit_reason"] == "manual_close"
    assert report["report"]["learning"]["sample_size"] == 1
    assert report["report"]["learning"]["promotion"]["applied_rules"] == []

    reset = await client.post("/api/trading-day/next")
    assert reset.status_code == 200
    assert (await client.get("/api/trading-day/current")).json()["day"] is None


async def test_auto_pause_does_not_change_manual_account(client: httpx.AsyncClient):
    started = (await client.post("/api/trading-day/start", json={})).json()
    day_id = started["day"]["id"]
    paused = await client.post(f"/api/trading-day/{day_id}/automation", json={"action": "pause"})
    assert paused.status_code == 202
    current = (await client.get("/api/trading-day/current")).json()
    assert current["auto_state"] == "paused"
    assert current["accounts"][0]["cash"] == "1000"


async def test_store_replays_original_result_after_revision_race():
    store = MemoryPaperStore()
    row = await store.load("owner")
    original = {"position_id": "position-one"}
    assert await store.save(
        "owner", settings={}, state={"events": []}, expected_revision=row.revision,
        event_type="manual_order_filled", payload=original, idempotency_key="same-key",
    ) is None

    replay = await store.save(
        "owner", settings={}, state={"events": []}, expected_revision=row.revision,
        event_type="manual_order_filled", payload={"position_id": "position-two"},
        idempotency_key="same-key",
    )
    assert replay == original
