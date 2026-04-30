import sys
from pathlib import Path
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prediction_engine import (
    MODEL_CONFIGS,
    _build_runtime_candidates,
    list_prediction_models,
    parse_prediction_payload,
)


def test_parse_prediction_payload_accepts_complete_json():
    raw_text = """
    {
      "summary": "Range-bound with slight upside bias.",
      "points": [
        {"hour": 1, "price": 101.5, "change_pct": 1.5, "confidence": 60, "rationale": "Short squeeze cooled."},
        {"hour": 2, "price": 102.0, "change_pct": 2.0, "confidence": 58, "rationale": "Momentum still positive."},
        {"hour": 3, "price": 101.2, "change_pct": 1.2, "confidence": 54, "rationale": "Resistance overhead."}
      ]
    }
    """

    summary, points = parse_prediction_payload(raw_text, horizon_hours=3, base_price=100.0)

    assert "Range-bound" in summary
    assert [point.hour for point in points] == [1, 2, 3]
    assert points[1].predicted_price == 102.0


def test_parse_prediction_payload_backfills_change_pct_from_price():
    raw_text = """
    {
      "points": [
        {"hour": 1, "price": 99.0},
        {"hour": 2, "price": 98.5}
      ]
    }
    """

    _, points = parse_prediction_payload(raw_text, horizon_hours=2, base_price=100.0)

    assert points[0].predicted_change_pct == -1.0
    assert points[1].predicted_change_pct == -1.5


def test_parse_prediction_payload_rejects_missing_hours():
    raw_text = """
    {
      "points": [
        {"hour": 1, "price": 101.0, "change_pct": 1.0}
      ]
    }
    """

    with pytest.raises(ValueError):
        parse_prediction_payload(raw_text, horizon_hours=2, base_price=100.0)


def test_parse_prediction_payload_strips_think_blocks_before_json():
    raw_text = """
    <think>internal reasoning that should never be parsed</think>
    {
      "summary": "Neutral drift.",
      "points": [
        {"hour": 1, "price": 100.4, "change_pct": 0.4, "confidence": 57, "rationale": "Small upside."}
      ]
    }
    """

    summary, points = parse_prediction_payload(raw_text, horizon_hours=1, base_price=100.0)

    assert summary == "Neutral drift."
    assert points[0].predicted_price == 100.4


def test_worker_runtime_candidates_include_qwen_chain_and_glm_fallback():
    with patch.dict(
        os.environ,
        {
            "WORKER_QWEN_MODEL": "qwen3.6-flash",
            "WORKER_QWEN_FALLBACKS": "qwen3.5-flash,qwen-flash",
            "WORKER_GLM_FALLBACK_MODEL": "glm-4-flash",
            "WORKER_GEMINI_FALLBACK_MODEL": "gemini-3-flash-preview",
        },
        clear=False,
    ):
        candidates = _build_runtime_candidates(MODEL_CONFIGS["worker"])

    assert [candidate.model_id for candidate in candidates] == [
        "qwen3.6-flash",
        "qwen3.5-flash",
        "qwen-flash",
        "glm-4-flash",
        "gemini-3-flash-preview",
    ]


def test_analyst_runtime_candidates_include_reasoning_chain_and_glm_fallback():
    with patch.dict(
        os.environ,
        {
            "ANALYST_QWEN_MODEL": "qwen3.6-35b-a3b",
            "ANALYST_QWEN_FALLBACKS": "qwen3.6-27b",
            "ANALYST_GLM_FALLBACK_MODEL": "glm-5.1",
        },
        clear=False,
    ):
        candidates = _build_runtime_candidates(MODEL_CONFIGS["analyst"])

    assert [candidate.model_id for candidate in candidates] == [
        "qwen3.6-35b-a3b",
        "qwen3.6-27b",
        "glm-5.1",
    ]


def test_premium_runtime_candidates_include_qwen_reasoning_chain_and_glm_fallback():
    with patch.dict(
        os.environ,
        {
            "PREMIUM_QWEN_MODEL": "qwen3.6-27b",
            "PREMIUM_QWEN_FALLBACKS": "qwen3.6-35b-a3b",
            "PREMIUM_GLM_FALLBACK_MODEL": "glm-5.1",
        },
        clear=False,
    ):
        candidates = _build_runtime_candidates(MODEL_CONFIGS["premium"])

    assert [candidate.model_id for candidate in candidates] == [
        "qwen3.6-27b",
        "qwen3.6-35b-a3b",
        "glm-5.1",
    ]


def test_worker_runtime_candidates_include_dashscope_and_gemini_fallbacks():
    with patch.dict(
        os.environ,
        {
            "DASHSCOPE_API_KEY": "dashscope-key",
            "GLM_API_KEY": "",
            "GOOGLE_API_KEY": "google-key",
            "WORKER_QWEN_MODEL": "qwen3.6-flash",
            "WORKER_QWEN_FALLBACKS": "qwen3.5-flash,qwen-flash",
            "WORKER_GLM_FALLBACK_MODEL": "glm-4-flash",
            "WORKER_GEMINI_FALLBACK_MODEL": "gemini-3-flash-preview",
        },
        clear=False,
    ):
        candidates = _build_runtime_candidates(MODEL_CONFIGS["worker"])

    assert candidates[0].model_id == "qwen3.6-flash"
    assert candidates[-1].model_id == "gemini-3-flash-preview"


def test_list_prediction_models_marks_analyst_available_with_glm_fallback_only():
    with patch.dict(
        os.environ,
        {
            "DASHSCOPE_API_KEY": "",
            "GLM_API_KEY": "glm-key",
            "ANALYST_QWEN_MODEL": "qwen3.6-35b-a3b",
            "ANALYST_QWEN_FALLBACKS": "qwen3.6-27b",
            "ANALYST_GLM_FALLBACK_MODEL": "glm-5.1",
        },
        clear=False,
    ):
        analyst = next(item for item in list_prediction_models() if item["key"] == "analyst")

    assert analyst["available"] is True
    assert "qwen3.6-35b-a3b" in analyst["model_id"]
    assert "glm-5.1" in analyst["model_id"]


def test_list_prediction_models_marks_premium_available_with_glm_fallback_only():
    with patch.dict(
        os.environ,
        {
            "DASHSCOPE_API_KEY": "",
            "GLM_API_KEY": "glm-key",
            "PREMIUM_QWEN_MODEL": "qwen3.6-27b",
            "PREMIUM_QWEN_FALLBACKS": "qwen3.6-35b-a3b",
            "PREMIUM_GLM_FALLBACK_MODEL": "glm-5.1",
        },
        clear=False,
    ):
        premium = next(item for item in list_prediction_models() if item["key"] == "premium")

    assert premium["available"] is True
    assert "qwen3.6-27b" in premium["model_id"]
    assert "glm-5.1" in premium["model_id"]


def test_prediction_model_list_hides_worker_slot():
    keys = [item["key"] for item in list_prediction_models()]

    assert "worker" not in keys
    assert keys == ["analyst", "premium", "minimax"]
