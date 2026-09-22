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

    summary, points = parse_prediction_payload(raw_text, horizon_steps=3, base_price=100.0)

    assert "Range-bound" in summary
    assert [point.step_index for point in points] == [1, 2, 3]
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

    _, points = parse_prediction_payload(raw_text, horizon_steps=2, base_price=100.0)

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
        parse_prediction_payload(raw_text, horizon_steps=2, base_price=100.0)


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

    summary, points = parse_prediction_payload(raw_text, horizon_steps=1, base_price=100.0)

    assert summary == "Neutral drift."
    assert points[0].predicted_price == 100.4


@pytest.mark.parametrize("role", ["worker", "analyst", "premium"])
def test_runtime_candidates_honor_configured_ollama_chain(role):
    prefix = "OLLAMA_" + role.upper()
    with patch.dict(os.environ, {prefix+"_MODEL": "test-primary", prefix+"_FALLBACK_MODEL": "test-fallback"}, clear=True):
        candidates = _build_runtime_candidates(MODEL_CONFIGS[role])
    assert [item.model_id for item in candidates] == ["test-primary", "test-fallback"]
    assert all(item.api_key_env == "OLLAMA_API_KEY" for item in candidates)


def test_nvidia_fallback_requires_its_own_credential():
    config = {"OLLAMA_WORKER_MODEL": "primary", "NVIDIA_WORKER_MODEL": "fallback"}
    # Opt-in tier: even with the credential, the chain stops at Ollama by default,
    # because integrate.api.nvidia.com answers 410 Gone for these models.
    with patch.dict(os.environ, {**config, "NVIDIA_DEEPSEEK_V4_FLASH_API_KEY": "test-key"}, clear=True):
        assert [_item.provider for _item in _build_runtime_candidates(MODEL_CONFIGS["worker"])] == ["ollama"]
    with patch.dict(os.environ, {**config, "NVIDIA_NIM_ENABLED": "1"}, clear=True):
        assert len(_build_runtime_candidates(MODEL_CONFIGS["worker"])) == 1
    with patch.dict(
        os.environ,
        {**config, "NVIDIA_NIM_ENABLED": "1", "NVIDIA_DEEPSEEK_V4_FLASH_API_KEY": "test-key"},
        clear=True,
    ):
        candidates = _build_runtime_candidates(MODEL_CONFIGS["worker"])
    assert candidates[-1].provider == "nvidia"
    assert candidates[-1].model_id == "fallback"


@pytest.mark.parametrize("key,available", [("", False), ("test-key", True)])
def test_model_availability_requires_active_provider_key(key, available):
    with patch.dict(os.environ, {"OLLAMA_API_KEY": key, "GLM_API_KEY": "legacy-key"}, clear=True):
        items = list_prediction_models()
    assert all(item["available"] is available for item in items)


@pytest.mark.parametrize("points", [
    [{"step": 1, "price": 101}, {"step": 1, "price": 102}],
    [{"step": 1, "price": 101}, {"step": 3, "price": 102}],
])
def test_prediction_steps_cannot_be_duplicated_or_out_of_range(points):
    import json
    with pytest.raises(ValueError):
        parse_prediction_payload(json.dumps({"points": points}), horizon_steps=2, base_price=100)


def test_prediction_model_list_hides_worker_slot():
    keys = [item["key"] for item in list_prediction_models()]

    assert "worker" not in keys
    assert keys == ["analyst", "premium", "minimax"]
