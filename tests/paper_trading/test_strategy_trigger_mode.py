"""Trigger strictness: ``breakout``, ``ema_cross``, ``trend_side``, ``ema_only``.

The BaselineV1 gate only fires when the trigger bar both crosses the 1m EMA20
*and* pushes beyond the previous bar's extreme — a real swing.  In a flat market
that second clause never aligns, so an operator testing the signal→order→fill
chain sees nothing.  ``ema_cross`` drops the swing clause and keeps the cross;
``trend_side`` also drops the cross event and fires whenever the 1m close sits on
the trend side of the EMA20 (so a sustained move already past the EMA still
trades).  ``ema_only`` is a deliberate stress knob that also drops the higher-
timeframe alignment (1h regime + 15m confirm) and trades the 1m EMA20 side in any
regime.  Every mode except ``ema_only`` keeps the higher-TF alignment, so no
loosened trigger fights the trend unless the operator asks for exactly that.
These tests feed crafted scenarios to each mode and assert they differ and that
the regime gate still holds.
"""
from __future__ import annotations

from decimal import Decimal

from paper_trading.strategy import Bar, BaselineV1Config, BaselineV1Strategy


def _mk(minute: int, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(
        timestamp=f"2026-01-01T00:{minute % 60:02d}:00+00:00",
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=Decimal("1"),
    )


def _ramp(n: int, start: str, end: str) -> list[Bar]:
    step = (Decimal(end) - Decimal(start)) / Decimal(max(n - 1, 1))
    bars = []
    for i in range(n):
        v = Decimal(start) + step * Decimal(i)
        bars.append(Bar(timestamp=f"t{i:05d}", open=v, high=v, low=v, close=v,
                        volume=Decimal("1")))
    return bars


def _chop_1m() -> list[Bar]:
    """Flat seed, a pin bar that closes back at the EMA, then a soft cross.

    The last bar closes below EMA20 (a genuine downside cross) but *above* the
    previous bar's low, so it is not a swing breakdown: ``breakout`` must reject
    it, ``ema_cross`` must accept it.
    """
    bars = [_mk(i, "100", "101", "99", "100") for i in range(20)]  # seed EMA20=100
    bars.append(_mk(20, "100", "100", "90", "100"))  # pin: deep low, close back at EMA
    bars.append(_mk(21, "97", "97", "95", "96"))     # cross below EMA, no new low
    return bars


def _below_ema_1m() -> list[Bar]:
    """Two consecutive closes below a flat-seeded EMA20 — a sustained move.

    Neither of the last two bars closes above the EMA, so no fresh cross exists:
    ``breakout`` and ``ema_cross`` both reject it, while ``trend_side`` (position,
    not cross) accepts it as a short in the bearish regime.
    """
    bars = [_mk(i, "100", "101", "99", "100") for i in range(20)]  # seed EMA20=100
    bars.append(_mk(20, "100", "100", "98", "99"))   # first close below EMA
    bars.append(_mk(21, "99", "99", "97", "98"))     # consecutive close, still below
    return bars


def _strategy(cfg: BaselineV1Config, bars=None) -> BaselineV1Strategy:
    bars_1h = _ramp(60, "120", "60")
    bars_15m = _ramp(60, "120", "60")
    strategy = BaselineV1Strategy(cfg)
    seed = bars if bars is not None else _chop_1m()
    strategy.warm_up(bars_1h, bars_15m, seed[:-1])
    return strategy


def _eval(cfg: BaselineV1Config, bars=None):
    seed = bars if bars is not None else _chop_1m()
    strategy = _strategy(cfg, seed)
    return strategy.evaluate(
        bars_1m=seed,
        close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("60"),
        now_epoch=1000.0,
    )


def test_breakout_mode_rejects_the_soft_cross() -> None:
    cfg = BaselineV1Config(trigger_mode="breakout", min_net_rr=Decimal("0.1"))
    assert _eval(cfg) is None, "no swing beyond the prior low must not fire"


def test_ema_cross_mode_fires_on_the_same_bars() -> None:
    cfg = BaselineV1Config(trigger_mode="ema_cross", min_net_rr=Decimal("0.1"))
    signal = _eval(cfg)
    assert signal is not None, "ema_cross should trade the cross the prior bar held"
    assert signal.side == "short"
    assert signal.stop_loss > signal.close_price
    assert signal.take_profit < signal.close_price


def test_default_config_stays_breakout() -> None:
    assert BaselineV1Config().trigger_mode == "breakout"


def test_ema_cross_still_respects_regime() -> None:
    """A bullish 1h regime must not emit a short even in the loosened mode."""
    cfg = BaselineV1Config(trigger_mode="ema_cross", min_net_rr=Decimal("0.1"))
    strategy = BaselineV1Strategy(cfg)
    bars_1h = _ramp(60, "60", "120")   # bullish regime
    bars_15m = _ramp(60, "60", "120")
    strategy.warm_up(bars_1h, bars_15m, _chop_1m()[:-1])
    assert strategy.regime_bearish() is False
    signal = strategy.evaluate(
        bars_1m=_chop_1m(),
        close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("120"),
        now_epoch=1000.0,
    )
    assert signal is None or signal.side != "short"


def test_trend_side_fires_when_already_below_ema() -> None:
    """The tape this mode is for: price sustained below EMA, no fresh cross bar."""
    cfg = BaselineV1Config(trigger_mode="trend_side", min_net_rr=Decimal("0.1"))
    signal = _eval(cfg, _below_ema_1m())
    assert signal is not None, "trend_side must trade a close already on the trend side"
    assert signal.side == "short"
    assert signal.stop_loss > signal.close_price
    assert signal.take_profit < signal.close_price


def test_cross_modes_reject_a_sustained_below_move() -> None:
    """The same bars that fire trend_side must not fire a cross detector."""
    bars = _below_ema_1m()
    assert _eval(BaselineV1Config(trigger_mode="breakout", min_net_rr=Decimal("0.1")), bars) is None
    assert _eval(BaselineV1Config(trigger_mode="ema_cross", min_net_rr=Decimal("0.1")), bars) is None


def test_trend_side_still_respects_regime() -> None:
    cfg = BaselineV1Config(trigger_mode="trend_side", min_net_rr=Decimal("0.1"))
    strategy = BaselineV1Strategy(cfg)
    bars_1h = _ramp(60, "60", "120")   # bullish regime, but 1m below EMA
    bars_15m = _ramp(60, "60", "120")
    strategy.warm_up(bars_1h, bars_15m, _below_ema_1m()[:-1])
    assert strategy.regime_bearish() is False
    signal = strategy.evaluate(
        bars_1m=_below_ema_1m(),
        close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("120"),
        now_epoch=1000.0,
    )
    assert signal is None or signal.side != "short"


def _above_ema_1m() -> list[Bar]:
    """Two consecutive closes above a flat-seeded EMA20 (a bearish-regime bounce).

    ``trend_side`` will not go long here because the 1h regime is bearish; only
    the ``ema_only`` stress knob drops that alignment and takes the long.
    """
    bars = [_mk(i, "100", "101", "99", "100") for i in range(20)]  # seed EMA20=100
    bars.append(_mk(20, "100", "101", "100", "101"))  # first close above EMA
    bars.append(_mk(21, "101", "102", "101", "102"))  # consecutive close, still above
    return bars


def _eval_regime(cfg: BaselineV1Config, bars, bullish: bool):
    """Evaluate *bars* under a chosen 1h/15m regime with a matching 15m close."""
    lo, hi = ("60", "120") if bullish else ("120", "60")
    strategy = BaselineV1Strategy(cfg)
    strategy.warm_up(_ramp(60, lo, hi), _ramp(60, lo, hi), bars[:-1])
    close_15m = Decimal("120") if bullish else Decimal("60")
    return strategy.evaluate(
        bars_1m=bars,
        close_1h=strategy._ema50_1h.value,
        close_15m=close_15m,
        now_epoch=1000.0,
    )


def test_ema_only_goes_long_against_bearish_regime() -> None:
    """ema_only trades the 1m EMA20 side even when the 1h regime disagrees."""
    bars = _above_ema_1m()
    trend = _eval_regime(BaselineV1Config(trigger_mode="trend_side", min_net_rr=Decimal("0.1")),
                         bars, bullish=False)
    assert trend is None, "trend_side must not long against a bearish 1h regime"
    only = _eval_regime(BaselineV1Config(trigger_mode="ema_only", min_net_rr=Decimal("0.1")),
                        bars, bullish=False)
    assert only is not None and only.side == "long"


def test_ema_only_goes_short_against_bullish_regime() -> None:
    bars = _below_ema_1m()
    only = _eval_regime(BaselineV1Config(trigger_mode="ema_only", min_net_rr=Decimal("0.1")),
                        bars, bullish=True)
    assert only is not None and only.side == "short"
    trend = _eval_regime(BaselineV1Config(trigger_mode="trend_side", min_net_rr=Decimal("0.1")),
                         bars, bullish=True)
    assert trend is None, "trend_side must not short against a bullish 1h regime"


def test_ema_only_is_an_explicit_mode() -> None:
    """ema_only must be in the canonical set so the runner accepts it."""
    from paper_trading.strategy import TRIGGER_MODES
    assert "ema_only" in TRIGGER_MODES
    assert "ema_only" not in ("breakout",)  # default contour excludes it
    assert BaselineV1Config().trigger_mode == "breakout"
