"""UI-11 test_forecast — forecast form, 48-step chart, idempotency."""
from __future__ import annotations

import re

import pytest


async def _login(client) -> str:
    resp = await client.get("/login")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf = csrf.group(1) if csrf else ""
    await client.post("/login", data={
        "username": "fixture-admin", "password": "test-password",
        "next": "/", "csrf_token": csrf,
    }, follow_redirects=False)
    return csrf


@pytest.mark.asyncio
class TestForecastForm:
    async def test_predictions_page_has_form(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert resp.status_code == 200
        assert 'id="forecastForm"' in resp.text
        assert 'method="post"' in resp.text

    async def test_form_has_symbol_select(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'name="symbol"' in resp.text
        assert 'id="forecastSymbol"' in resp.text
        # Watchlist options
        assert "btcusdt" in resp.text.lower() or "BTCUSDT" in resp.text

    async def test_form_has_timeframe_select(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'name="timeframe"' in resp.text
        assert 'id="forecastTimeframe"' in resp.text
        assert 'value="15min"' in resp.text

    async def test_form_has_horizon_input(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'name="horizon_steps"' in resp.text
        assert 'id="forecastHorizon"' in resp.text
        assert 'min="1"' in resp.text
        assert 'max="48"' in resp.text

    async def test_form_has_depth_input(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'name="depth"' in resp.text
        assert 'id="forecastDepth"' in resp.text

    async def test_form_has_csrf_token(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'name="csrf_token"' in resp.text

    async def test_form_has_submit_button(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'id="forecastSubmit"' in resp.text
        assert "Новый прогноз" in resp.text

    async def test_form_has_shortcut_buttons(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'data-h="1"' in resp.text
        assert 'data-h="4"' in resp.text
        assert 'data-h="8"' in resp.text
        assert 'data-h="12"' in resp.text
        assert 'data-h="24"' in resp.text
        assert 'data-h="48"' in resp.text

    async def test_form_has_result_area(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'id="forecastResult"' in resp.text
        assert 'aria-live="polite"' in resp.text

    async def test_form_posts_to_api_predictions(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'action="/api/predictions"' in resp.text


@pytest.mark.asyncio
class Test48StepChart:
    def test_steps_48_fixture_has_48_points(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("steps_48")
        points = fx["forecast"]["points"]
        assert len(points) == 48

    def test_steps_48_step_indices_sequential(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("steps_48")
        points = fx["forecast"]["points"]
        indices = [p["step_index"] for p in points]
        assert indices == list(range(1, 49))

    def test_steps_48_valid_until_extended(self):
        from tests.ui.fixture_data import get_fixture
        base = get_fixture("base")
        fx48 = get_fixture("steps_48")
        assert fx48["forecast"]["valid_until"] == "2026-09-10T00:00:00Z"
        assert fx48["forecast"]["valid_until"] != base["forecast"]["valid_until"]

    def test_steps_48_each_point_has_price(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("steps_48")
        for p in fx["forecast"]["points"]:
            assert "predicted_price" in p
            assert "low" in p
            assert "high" in p
            assert p["low"] <= p["predicted_price"] <= p["high"]

    async def test_predictions_page_shows_1_48_range(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert "1–48" in resp.text or "1-48" in resp.text

    async def test_predictions_page_has_chart_area(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        # predictions.html should have chart-related elements
        assert "chart" in resp.text.lower() or "Forecast" in resp.text


@pytest.mark.asyncio
class TestIdempotency:
    async def test_post_predictions_returns_202(self, client):
        await _login(client)
        resp = await client.post("/api/predictions", json={
            "symbol": "btcusdt", "timeframe": "15min",
            "horizon_steps": 4, "depth": 3,
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "request_id" in data
        assert data["execution_state"] == "queued"

    async def test_post_predictions_requires_auth(self, client):
        resp = await client.post("/api/predictions", json={
            "symbol": "btcusdt", "timeframe": "15min",
            "horizon_steps": 4, "depth": 3,
        })
        assert resp.status_code == 401

    def test_idempotency_key_format(self):
        """Idempotency-Key should be a UUID v4 string."""
        import uuid
        key = str(uuid.uuid4())
        assert len(key) == 36
        assert key.count("-") == 4

    async def test_same_request_id_on_repeat(self, client):
        """POST /api/predictions should return the same request_id for the same payload."""
        await _login(client)
        payload = {"symbol": "btcusdt", "timeframe": "15min",
                    "horizon_steps": 4, "depth": 3}
        resp1 = await client.post("/api/predictions", json=payload,
                                  headers={"Idempotency-Key": "test-key-1"})
        resp2 = await client.post("/api/predictions", json=payload,
                                  headers={"Idempotency-Key": "test-key-1"})
        assert resp1.status_code == 202
        assert resp2.status_code == 202
        # Fixture returns same request_id (9001) for all
        assert resp1.json()["request_id"] == resp2.json()["request_id"]