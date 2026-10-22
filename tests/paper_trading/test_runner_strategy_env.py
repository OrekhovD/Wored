"""Deployment-level strategy knobs read by ``PaperTradingRunner.from_env``.

The engine's entry frequency was code-only before these keys existed, which
meant "make the simulation trade sooner" required a code change and a rebuild.
These tests pin the three behaviours the operator relies on: nothing set keeps
the historical defaults, a value is honoured, and a broken value fails at
startup instead of quietly running the other policy.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_trading.runner import _strategy_config_from_env

#: Every environment key :func:`_strategy_config_from_env` reads.
STRATEGY_ENVS = (
    "PAPER_STRATEGY_COOLDOWN_SECONDS",
    "PAPER_STRATEGY_MIN_NET_RR",
    "PAPER_STRATEGY_SL_ATR_FRACTION",
    "PAPER_STRATEGY_TP_RR",
    "PAPER_STRATEGY_TRIGGER_MODE",
    "PAPER_SIM_FEE_RATE",
    "PAPER_STRATEGY_FORECAST_GATE",
    "PAPER_STRATEGY_FORECAST_MODEL",
    "PAPER_STRATEGY_FORECAST_HORIZON_STEPS",
    "PAPER_STRATEGY_FORECAST_MIN_EV",
)


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in STRATEGY_ENVS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_unchanged_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    cfg = _strategy_config_from_env()
    assert cfg.cooldown_seconds == 600.0
    assert cfg.min_net_rr == Decimal("1.2")
    assert cfg.sl_atr_fraction == Decimal("0.25")
    assert cfg.tp_rr == Decimal(2)
    assert cfg.trigger_mode == "breakout"
    # unset fee falls back to the HTX Prime-0 code default, not the operator's 0.0006
    assert cfg.fee_rate == Decimal("0.0005")
    # the forecast gate is OFF unless the deployment opts in, so an unset
    # contour trades exactly as before item ④b
    assert cfg.forecast_gate_enabled is False
    assert cfg.forecast_gate_model == "B1_QUANTILE"
    assert cfg.forecast_gate_horizon_steps == 3
    assert cfg.forecast_gate_min_ev == Decimal("0")


def test_environment_overrides_every_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("PAPER_STRATEGY_MIN_NET_RR", "0.3")
    monkeypatch.setenv("PAPER_STRATEGY_SL_ATR_FRACTION", "1.5")
    monkeypatch.setenv("PAPER_STRATEGY_TP_RR", "3")
    monkeypatch.setenv("PAPER_STRATEGY_TRIGGER_MODE", "ema_cross")
    monkeypatch.setenv("PAPER_SIM_FEE_RATE", "0.0006")
    cfg = _strategy_config_from_env()
    assert cfg.cooldown_seconds == 60.0
    assert cfg.min_net_rr == Decimal("0.3")
    assert cfg.sl_atr_fraction == Decimal("1.5")
    assert cfg.tp_rr == Decimal("3")
    assert cfg.trigger_mode == "ema_cross"
    assert cfg.fee_rate == Decimal("0.0006")


def test_trigger_mode_is_case_and_space_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_TRIGGER_MODE", "  EMA_Cross  ")
    assert _strategy_config_from_env().trigger_mode == "ema_cross"


def test_trend_side_is_an_accepted_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_TRIGGER_MODE", "trend_side")
    assert _strategy_config_from_env().trigger_mode == "trend_side"


def test_ema_only_is_an_accepted_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_TRIGGER_MODE", "ema_only")
    assert _strategy_config_from_env().trigger_mode == "ema_only"


def test_unknown_trigger_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo here decides whether the bot trades at all — it must not default."""
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_TRIGGER_MODE", "degen")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_TRIGGER_MODE"):
        _strategy_config_from_env()


def test_unparsable_value_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_MIN_NET_RR", "aggressive")
    with pytest.raises(RuntimeError, match="PAPER_STRATEGY_MIN_NET_RR"):
        _strategy_config_from_env()


def test_negative_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A negative cooldown would mean "never cool down" — that is a misconfig."""
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_COOLDOWN_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="non-negative"):
        _strategy_config_from_env()


def test_zero_stop_fraction_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero-width stop buffer would put the stop exactly on the swing low."""
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_SL_ATR_FRACTION", "0")
    with pytest.raises(RuntimeError, match="positive"):
        _strategy_config_from_env()

