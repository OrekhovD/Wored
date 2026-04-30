import sys
from pathlib import Path

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    app,
    build_prediction_comparison_payload,
    compute_rsi_series,
    get_internal_api_token,
    compute_sma_series,
    normalize_klines,
    normalize_prediction_horizon,
    to_db_timestamp,
)


def test_normalize_klines_reverses_into_oldest_first():
    raw = [
        {"id": 3, "open": 12, "high": 15, "low": 11, "close": 14, "vol": 1200},
        {"id": 2, "open": 11, "high": 13, "low": 10, "close": 12, "vol": 900},
        {"id": 1, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 800},
    ]

    candles = normalize_klines(raw)

    assert [item["time"] for item in candles] == [1, 2, 3]
    assert candles[0]["close"] == 11.0


def test_indicator_helpers_return_points():
    candles = [
        {"time": idx, "open": 100 + idx, "high": 101 + idx, "low": 99 + idx, "close": 100 + idx, "volume": 1000 + idx}
        for idx in range(1, 40)
    ]

    sma = compute_sma_series(candles, 5)
    rsi = compute_rsi_series(candles, 14)

    assert sma
    assert rsi
    assert sma[-1]["time"] == candles[-1]["time"]
    assert 0 <= rsi[-1]["value"] <= 100


def test_prediction_horizon_validator_accepts_supported_values():
    assert normalize_prediction_horizon(4) == 4


def test_prediction_horizon_validator_rejects_unsupported_values():
    with pytest.raises(HTTPException):
        normalize_prediction_horizon(5)


def test_to_db_timestamp_strips_timezone_info():
    value = datetime(2026, 4, 25, 18, 7, tzinfo=timezone.utc)

    result = to_db_timestamp(value)

    assert result.tzinfo is None
    assert result.hour == 18


def test_build_prediction_comparison_payload_groups_rows_and_marks_best_model():
    models = [
        {
            "id": 1,
            "model_key": "worker",
            "model_name": "Worker / GLM-4 Flash",
            "model_id": "glm-4-flash",
            "status": "completed",
            "error_message": "",
            "avg_accuracy": 91.2,
            "points": [
                {
                    "forecast_hour": 1,
                    "target_time": "2026-04-26T01:00:00+00:00",
                    "target_time_display": "2026-04-26 01:00 UTC",
                    "predicted_price": 100.25,
                    "predicted_change_pct": 0.25,
                    "actual_price": 100.4,
                    "actual_change_pct": 0.4,
                    "evaluated_at": "2026-04-26T01:03:00+00:00",
                    "evaluated_at_display": "2026-04-26 01:03 UTC",
                    "accuracy_score": 94.0,
                }
            ],
        },
        {
            "id": 2,
            "model_key": "analyst",
            "model_name": "Analyst / GLM-5.1",
            "model_id": "glm-5.1",
            "status": "completed",
            "error_message": "",
            "avg_accuracy": 84.0,
            "points": [
                {
                    "forecast_hour": 1,
                    "target_time": "2026-04-26T01:00:00+00:00",
                    "target_time_display": "2026-04-26 01:00 UTC",
                    "predicted_price": 99.9,
                    "predicted_change_pct": -0.1,
                    "actual_price": 100.4,
                    "actual_change_pct": 0.4,
                    "evaluated_at": "2026-04-26T01:03:00+00:00",
                    "evaluated_at_display": "2026-04-26 01:03 UTC",
                    "accuracy_score": 82.0,
                }
            ],
        },
        {
            "id": 3,
            "model_key": "oracle",
            "model_name": "Oracle / MiniMax M2.7",
            "model_id": "minimaxai/minimax-m2.7",
            "status": "failed",
            "error_message": "rate limited",
            "avg_accuracy": None,
            "points": [],
        },
    ]

    comparison_models, comparison_rows, top_model = build_prediction_comparison_payload(2, models)

    assert len(comparison_models) == 3
    assert comparison_models[0]["short_name"] == "GLM-4 Flash"
    assert comparison_rows[0]["best_models"] == ["GLM-4 Flash"]
    assert comparison_rows[0]["model_cells"][0]["is_best"] is True
    assert comparison_rows[0]["model_cells"][2]["cell_state"] == "failed"
    assert comparison_rows[1]["actual_price"] is None
    assert top_model["short_name"] == "GLM-4 Flash"


def test_index_renders_control_room_when_auth_disabled(monkeypatch):
    monkeypatch.delenv("WEBUI_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("WEBUI_ADMIN_PASSWORD", raising=False)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "WORED Control Room" in response.text


def test_predictions_page_renders_when_auth_disabled(monkeypatch):
    monkeypatch.delenv("WEBUI_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("WEBUI_ADMIN_PASSWORD", raising=False)

    with TestClient(app) as client:
        response = client.get("/predictions")

    assert response.status_code == 200
    assert "Prediction Lab" in response.text


def test_alerts_redirect_to_login_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("WEBUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("WEBUI_ADMIN_PASSWORD", "secret-pass")

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/alerts")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_page_renders_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("WEBUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("WEBUI_ADMIN_PASSWORD", "secret-pass")

    with TestClient(app) as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "WORED Web UI" in response.text


def test_internal_prediction_api_rejects_invalid_token():
    with TestClient(app) as client:
        response = client.post(
            "/api/internal/predictions",
            json={"symbol": "ethusdt", "horizon_hours": 4, "requested_by": "bot"},
            headers={"X-Internal-Token": "bad-token"},
        )

    assert response.status_code == 403


def test_internal_prediction_api_accepts_valid_token(monkeypatch):
    async def fake_run_prediction_request(request, background_tasks, symbol, horizon_hours, requested_by, source):
        return {
            "id": 321,
            "symbol": symbol,
            "horizon_hours": horizon_hours,
            "requested_by": requested_by,
            "source": source,
            "status": "active",
        }

    monkeypatch.setattr("app.run_prediction_request", fake_run_prediction_request)

    with TestClient(app) as client:
        response = client.post(
            "/api/internal/predictions",
            json={"symbol": "ethusdt", "horizon_hours": 4, "requested_by": "bot-runner", "source": "telegram"},
            headers={"X-Internal-Token": get_internal_api_token()},
        )

    assert response.status_code == 200
    assert response.json()["id"] == 321
    assert response.json()["source"] == "telegram"
