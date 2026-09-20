"""Block E.4 — bounded rules consumer (``paper_trading.rules``).

Acceptance: activating a whitelisted rule observably changes strategy behaviour,
hard bounds are enforced, and non-strategy / unknown parameters are ignored.
"""
from __future__ import annotations

from decimal import Decimal

from paper_trading.rules import apply_rules_to_config, build_strategy
from paper_trading.strategy import Bar, BaselineV1Config


# --------------------------------------------------------------------------- #
# whitelist / bounds
# --------------------------------------------------------------------------- #
def test_applies_whitelisted_parameter():
    rules = {"adjustments": [{"parameter": "min_net_rr", "new": 1.8}]}
    cfg, applied = apply_rules_to_config(BaselineV1Config(), rules)
    assert cfg.min_net_rr == Decimal("1.8")
    assert applied and applied[0]["parameter"] == "min_net_rr"


def test_hard_bounds_clamp_out_of_range():
    # ema_fast_period ceiling is 60 — a candidate asking for 5000 is clamped.
    cfg, _ = apply_rules_to_config(
        BaselineV1Config(), {"adjustments": [{"parameter": "ema_fast_period", "new": 5000}]}
    )
    assert cfg.ema_fast_period == 60
    cfg2, _ = apply_rules_to_config(
        BaselineV1Config(), {"adjustments": [{"parameter": "cooldown_seconds", "new": -1}]}
    )
    assert cfg2.cooldown_seconds == 60.0  # lower bound


def test_unknown_and_unparseable_are_ignored():
    base = BaselineV1Config()
    rules = {"adjustments": [
        {"parameter": "max_leverage", "new": 50},      # risk-layer, not strategy
        {"parameter": "tp_rr", "new": "not-a-number"},  # unparseable
        {"parameter": "status", "new": "active"},       # not a knob
    ]}
    cfg, applied = apply_rules_to_config(base, rules)
    assert cfg.tp_rr == base.tp_rr
    assert applied == []


def test_none_rules_is_noop():
    base = BaselineV1Config()
    cfg, applied = apply_rules_to_config(base, None)
    assert cfg == base and applied == []


# --------------------------------------------------------------------------- #
# replay-observable behaviour change (acceptance)
# --------------------------------------------------------------------------- #
def _bar(minute: int, close) -> Bar:
    value = Decimal(str(close))
    return Bar(
        timestamp=f"2026-01-01T00:{minute % 60:02d}:00+00:00",
        open=value, high=value + Decimal("1"), low=value - Decimal("1"),
        close=value, volume=Decimal("1"),
    )


def _ramp(n: int, start: str, end: str) -> list[Bar]:
    a, b = Decimal(start), Decimal(end)
    step = (b - a) / Decimal(max(n - 1, 1))
    return [Bar(timestamp=f"t{i:05d}", open=a + step * i, high=a + step * i,
                low=a + step * i, close=a + step * i, volume=Decimal("1"))
            for i in range(n)]


def _bearish_1m() -> list[Bar]:
    bars = [_bar(i, 100) for i in range(20)]
    bars.append(_bar(20, 103))
    bars.append(_bar(21, 105))
    bars.append(_bar(22, 99))
    return bars


def _seeded(strategy):
    strategy.warm_up(_ramp(60, "120", "60"), _ramp(60, "120", "60"), _bearish_1m()[:-1])
    return strategy


def test_active_rule_changes_replay_behaviour():
    bars = _bearish_1m()

    base_strategy, _ = build_strategy(None)
    _seeded(base_strategy)
    signal_base = base_strategy.evaluate(
        bars_1m=bars, close_1h=Decimal("60"), close_15m=Decimal("60"), now_epoch=1000.0,
    )

    # A rule that disables the short side must observably suppress that signal.
    disabled, _ = build_strategy(
        {"adjustments": [{"parameter": "enable_short", "new": False}]}
    )
    _seeded(disabled)
    signal_rule = disabled.evaluate(
        bars_1m=bars, close_1h=Decimal("60"), close_15m=Decimal("60"), now_epoch=1000.0,
    )

    assert signal_base is not None and signal_base.side == "short"
    assert signal_rule is None
