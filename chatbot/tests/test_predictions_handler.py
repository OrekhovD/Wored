from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_format_prediction_detail_renders_hit_miss_scorecard():
    from handlers.predictions import format_prediction_detail

    detail = {
        "id": 17,
        "symbol": "ethusdt",
        "horizon_hours": 1,
        "status": "completed",
        "source": "telegram",
        "requested_by": "tester",
        "base_price": 2310.51,
        "completed_models": 2,
        "failed_models": 0,
        "evaluated_points": 2,
        "total_points": 2,
        "avg_accuracy": 55.5,
        "avg_failure": 44.5,
        "top_model": {"model_name": "Strategist / Qwen Reasoning", "avg_accuracy": 77.7},
        "models": [
            {
                "id": 1,
                "model_name": "Analyst / Qwen Reasoning Auto",
                "role_name": "Analyst",
                "short_name": "Qwen Reasoning Auto",
                "status": "completed",
                "avg_accuracy": 33.3,
                "avg_failure": 66.7,
                "points": [
                    {
                        "forecast_hour": 1,
                        "target_time_display": "2026-04-26 01:00 UTC",
                        "predicted_price": 2312.0,
                        "predicted_change_pct": 0.06,
                        "actual_price": 2305.0,
                        "actual_change_pct": -0.24,
                        "accuracy_score": 0.0,
                        "direction_match": False,
                    }
                ],
            },
            {
                "id": 2,
                "model_name": "Strategist / Qwen Reasoning",
                "role_name": "Strategist",
                "short_name": "Qwen Reasoning",
                "status": "completed",
                "avg_accuracy": 77.7,
                "avg_failure": 22.3,
                "points": [
                    {
                        "forecast_hour": 1,
                        "target_time_display": "2026-04-26 01:00 UTC",
                        "predicted_price": 2306.0,
                        "predicted_change_pct": -0.2,
                        "actual_price": 2305.0,
                        "actual_change_pct": -0.24,
                        "accuracy_score": 77.7,
                        "direction_match": True,
                    }
                ],
            },
        ],
    }

    text = format_prediction_detail(detail)

    assert "hit <b>55.5%</b>" in text
    assert "miss <b>44.5%</b>" in text
    assert "A $2312.00 (+0.06%) 0%❌" in text
    assert "S $2306.00 (-0.20%) 78%✅" in text


@pytest.mark.asyncio
async def test_cb_prediction_run_acknowledges_before_internal_api():
    from handlers.predictions import cb_prediction_run

    message = SimpleNamespace(edit_text=AsyncMock())
    call = SimpleNamespace(
        data="prediction_run:ethusdt:4",
        answer=AsyncMock(),
        message=message,
        from_user=SimpleNamespace(username="alice", id=42),
    )

    async def fake_create_prediction_request(symbol, horizon_hours, requested_by):
        assert call.answer.await_count == 1
        assert symbol == "ethusdt"
        assert horizon_hours == 4
        assert requested_by == "alice"
        return {
            "id": 99,
            "symbol": "ethusdt",
            "horizon_hours": 4,
            "status": "active",
            "source": "telegram",
            "requested_by": "alice",
            "base_price": 2300.0,
            "completed_models": 2,
            "failed_models": 0,
            "evaluated_points": 0,
            "total_points": 8,
            "avg_accuracy": None,
            "avg_failure": None,
            "top_model": None,
            "models": [],
        }

    with patch("handlers.predictions.create_prediction_request", side_effect=fake_create_prediction_request):
        await cb_prediction_run(call)

    call.answer.assert_awaited_once()
    assert message.edit_text.await_count >= 2
