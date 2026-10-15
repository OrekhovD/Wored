"""Fill microstructure knobs read by ``PaperTradingRunner.from_env``.

These keys used to sit in ``.env`` with no consumer, so the profile an operator
declared was not the profile that filled. They are now read by
:func:`_sim_settings_from_env` and threaded into ``execute_market_order`` and the
risk gate. The three behaviours pinned here mirror the strategy-config tests:
nothing set keeps the module defaults, a value is honoured, a broken value fails
at startup. The taker fee is intentionally NOT in this struct — it lives on the
strategy config so the emit gate and the booked fill share one rate.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from paper_trading.runner import _sim_settings_from_env

SIM_ENVS = (
    "PAPER_SIM_SLIPPAGE_BPS",
    "PAPER_ORDER_MAX_SPREAD_BPS",
    "PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION",
)


def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in SIM_ENVS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_match_the_module_constants_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean(monkeypatch)
    slippage_bps, available_fraction, max_spread_bps = _sim_settings_from_env()
    assert slippage_bps == 2  # market.DEFAULT_SLIPPAGE_BPS
    assert available_fraction == Decimal("0.1")
    assert max_spread_bps == 10  # market.DEFAULT_SPREAD_MAX_BPS


def test_environment_overrides_every_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_SIM_SLIPPAGE_BPS", "5")
    monkeypatch.setenv("PAPER_ORDER_MAX_SPREAD_BPS", "20")
    monkeypatch.setenv("PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION", "0.25")
    slippage_bps, available_fraction, max_spread_bps = _sim_settings_from_env()
    assert slippage_bps == 5
    assert available_fraction == Decimal("0.25")
    assert max_spread_bps == 20


def test_zero_liquidity_fraction_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero visible depth would make every order notional-only — a misconfig."""
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION", "0")
    with pytest.raises(RuntimeError, match="positive"):
        _sim_settings_from_env()


def test_unparsable_value_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean(monkeypatch)
    monkeypatch.setenv("PAPER_SIM_SLIPPAGE_BPS", "wide")
    with pytest.raises(RuntimeError, match="PAPER_SIM_SLIPPAGE_BPS"):
        _sim_settings_from_env()
