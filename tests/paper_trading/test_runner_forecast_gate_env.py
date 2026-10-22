"""Forecast-gate env parsing in ``_strategy_config_from_env`` (④b).

These pin the same three behaviours the operator depends on for every other
entry knob: unset keeps the contour off, a valid value is honoured, and a broken
value fails at startup rather than silently trading under the wrong policy.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_trading.runner import _strategy_config_from_env

GATE_ENVS = (
    "PAPER_STRATEGY_FORECAST_GATE",
    "PAPER_STRATEGY_FORECAST_MODEL",
    "PAPER_STRATEGY_FORECAST_HORIZON_STEPS",
    "PAPER_STRATEGY_FORECAST_MIN_EV",
    "PAPER_STRATEGY_FORECAST_EXIT",
    "PAPER_STRATEGY_FORECAST_EXIT_MIN_EV",
    "PAPER_STRATEGY_FORECAST_EXIT_MIN_HOLD_SECONDS",
)


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in GATE_ENVS:
        monkeypatch.delenv(key, raising=False)


def test_gate_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    cfg = _strategy_config_from_env()
    assert cfg.forecast_gate_enabled is False
    assert cfg.forecast_gate_model == "B1_QUANTILE"
    assert cfg.forecast_gate_horizon_steps == 3
    assert cfg.forecast_gate_min_ev == Decimal("0")


def test_gate_on_and_all_knobs_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_GATE", "on")
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_MODEL", "B0_NAIVE")
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_HORIZON_STEPS", "10")
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_MIN_EV", "0.05")
    cfg = _strategy_config_from_env()
    assert cfg.forecast_gate_enabled is True
    assert cfg.forecast_gate_model == "B0_NAIVE"
    assert cfg.forecast_gate_horizon_steps == 10
    assert cfg.forecast_gate_min_ev == Decimal("0.05")


def test_gate_flag_is_case_and_space_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_GATE", "  ON  ")
    assert _strategy_config_from_env().forecast_gate_enabled is True


def test_model_is_normalised_upper(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_MODEL", "  b1_quantile  ")
    assert _strategy_config_from_env().forecast_gate_model == "B1_QUANTILE"


def test_unknown_gate_flag_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_GATE", "enabled")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_GATE"):
        _strategy_config_from_env()


def test_unknown_model_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_MODEL", "GLM-5.2")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_MODEL"):
        _strategy_config_from_env()


def test_horizon_out_of_range_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_HORIZON_STEPS", "0")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_HORIZON_STEPS"):
        _strategy_config_from_env()
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_HORIZON_STEPS", "49")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_HORIZON_STEPS"):
        _strategy_config_from_env()


# ── Forecast-driven exit (⑤) env parsing ─────────────────────────────────


def test_exit_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    cfg = _strategy_config_from_env()
    assert cfg.forecast_exit_enabled is False
    assert cfg.forecast_exit_min_ev == Decimal("0")
    assert cfg.forecast_exit_min_hold_seconds == 60.0


def test_exit_on_and_knobs_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT", "on")
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT_MIN_EV", "0.25")
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT_MIN_HOLD_SECONDS", "180")
    cfg = _strategy_config_from_env()
    assert cfg.forecast_exit_enabled is True
    assert cfg.forecast_exit_min_ev == Decimal("0.25")
    assert cfg.forecast_exit_min_hold_seconds == 180.0


def test_exit_flag_is_case_and_space_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT", "  ON  ")
    assert _strategy_config_from_env().forecast_exit_enabled is True


def test_unknown_exit_flag_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT", "enabled")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_EXIT"):
        _strategy_config_from_env()


def test_negative_min_hold_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Negative hold means "close on the first cycle", which would defeat the
    # min-hold guard entirely; startup refuses.
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_FORECAST_EXIT_MIN_HOLD_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_FORECAST_EXIT_MIN_HOLD_SECONDS"):
        _strategy_config_from_env()
