"""Market-data boundary tests for simulated perpetual execution."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from webui.paper_api import router
from webui.paper_market import parse_live_snapshot


def live_payload(*, source_at: datetime | None = None) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    market_time = source_at or now
    return {
        "schema_version": 1,
        "venue": "htx",
        "market_type": "linear-swap",
        "contract_code": "BTC-USDT",
        "bid": "100",
        "ask": "100.2",
        "last": "100.1",
        "mark": "100.08",
        "index": "100.05",
        "funding_rate": "0.0001",
        "next_funding_at": (now + timedelta(hours=4)).isoformat(),
        "contract_size": "0.001",
        "price_tick": "0.1",
        "quantity_step": "0.001",
        "source_at": market_time.isoformat(),
        "received_at": now.isoformat(),
        "component_times": {
            "ticker": market_time.isoformat(),
            "index": now.isoformat(),
            "mark": now.isoformat(),
            "funding": now.isoformat(),
        },
        "source": "htx-public-api",
    }


def test_live_snapshot_selects_executable_side_prices() -> None:
    snapshot = parse_live_snapshot(live_payload(), expected_contract="BTC-USDT")
    assert str(snapshot.entry_price("buy")) == "100.2"
    assert str(snapshot.entry_price("sell")) == "100"
    assert str(snapshot.close_price("long")) == "100"
    assert str(snapshot.close_price("short")) == "100.2"
    assert snapshot.public_dict()["funding_rate"] == "0.0001"


@pytest.mark.parametrize(
    "mutation", ["stale", "stale_mark", "crossed", "missing_funding"]
)
def test_live_snapshot_rejects_untrustworthy_data(mutation: str) -> None:
    payload = live_payload()
    if mutation == "stale":
        payload["source_at"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    elif mutation == "stale_mark":
        payload["component_times"]["mark"] = (
            datetime.now(timezone.utc) - timedelta(seconds=100)
        ).isoformat()
    elif mutation == "crossed":
        payload["bid"] = "101"
    else:
        payload["funding_rate"] = None
    with pytest.raises(ValueError):
        parse_live_snapshot(payload, expected_contract="BTC-USDT", max_age_seconds=5)


class FakeRedis:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload

    async def get(self, key: str) -> str | None:
        assert key == "market:perpetual:htx:BTC-USDT"
        return json.dumps(self.payload) if self.payload is not None else None


def live_app(payload: dict[str, object] | None) -> FastAPI:
    app = FastAPI()
    app.state.paper_market_mode = "live"
    app.state.redis_client = FakeRedis(payload)
    app.include_router(router)
    app.add_middleware(SessionMiddleware, secret_key="paper-market-test-secret")
    return app


async def test_live_preview_uses_ask_and_exposes_market_evidence() -> None:
    app = live_app(live_payload())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.post("/api/trading-day/start", json={})).status_code == 202
        response = await client.post(
            "/api/paper/accounts/proto-manual/orders/preview",
            json={
                "instrument": "btcusdt",
                "side": "buy",
                "order_type": "market",
                "risk": "1",
                "stop_price": "99",
                "leverage": 10,
            },
        )
    assert response.status_code == 200
    result = response.json()
    assert result["entry_price"] == "100.2"
    assert result["funding_status"] == "known"
    assert result["market"]["quality"] == "live"
    assert result["market"]["contract_code"] == "BTC-USDT"


async def test_live_mode_blocks_missing_snapshot_without_fallback() -> None:
    app = live_app(None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        started = await client.post("/api/trading-day/start", json={})
        current = (await client.get("/api/trading-day/current")).json()
        response = await client.post(
            "/api/paper/accounts/proto-manual/orders/preview",
            json={
                "instrument": "btcusdt",
                "side": "buy",
                "order_type": "market",
                "risk": "1",
                "stop_price": "99",
            },
        )
    assert started.status_code == 409
    assert current["day"] is None
    assert current["fresh"] is False
    assert current["capabilities"]["can_start"] is False
    assert response.status_code == 409
    assert "Нет perpetual-снимка BTC-USDT" in response.json()["detail"]


async def test_live_long_opens_at_ask_and_closes_at_bid() -> None:
    app = live_app(live_payload())
    redis = app.state.redis_client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.post("/api/trading-day/start", json={})
        opened = await client.post(
            "/api/paper/accounts/proto-manual/orders",
            json={
                "idempotency_key": "live-open",
                "instrument": "btcusdt",
                "side": "buy",
                "order_type": "market",
                "risk": "1",
                "stop_price": "99",
                "leverage": 10,
            },
        )
        assert opened.status_code == 202
        current = (await client.get("/api/trading-day/current")).json()
        assert current["positions"][0]["entry_price"] == "100.2"

        redis.payload = live_payload()
        redis.payload.update({"bid": "101", "ask": "101.2", "last": "101.1", "mark": "101.08"})
        closed = await client.post(
            f"/api/paper/positions/{opened.json()['position_id']}/actions",
            json={"idempotency_key": "live-close", "action": "close"},
        )
        state = (await client.get("/api/trading-day/current")).json()

    assert closed.status_code == 202
    assert closed.json()["close_price"] == "101"
    assert float(state["accounts"][0]["cash"]) > 1000
    assert state["closed_positions"][0]["exit_reason"] == "manual_close"
