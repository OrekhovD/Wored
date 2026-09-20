"""Block E.1 — mirrored short side in BaselineV1Strategy.

The short path is the exact mirror of the long breakout/pullback trigger:
  1h regime bearish (EMA20 < EMA50), 15m confirm down (close < EMA20),
  1m breakdown (prev.high >= prev_ema20, close < ema20, close < prev.low),
with SL above the swing high and TP below entry (mirrored net-RR).
"""
from __future__ import annotations

from decimal import Decimal

from paper_trading.strategy import Bar, BaselineV1Config, BaselineV1Strategy


def _bar(minute: int, close) -> Bar:
    value = Decimal(str(close))
    return Bar(
        timestamp=f"2026-01-01T00:{minute % 60:02d}:00+00:00",
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume=Decimal("1"),
    )


def _ramp(n: int, start: Decimal, end: Decimal) -> list[Bar]:
    step = (end - start) / Decimal(max(n - 1, 1))
    return [Bar(timestamp=f"t{i:05d}", open=start + step * Decimal(i),
                high=start + step * Decimal(i), low=start + step * Decimal(i),
                close=start + step * Decimal(i), volume=Decimal("1"))
            for i in range(n)]


def _downtrend_tf(n: int, start: str, end: str) -> list[Bar]:
    return _ramp(n, Decimal(start), Decimal(end))


def _bearish_1m() -> list[Bar]:
    """20 flat bars seed EMA20/ATR14, then a rise and a sharp breakdown bar."""
    bars = [_bar(i, 100) for i in range(20)]
    bars.append(_bar(20, 103))
    bars.append(_bar(21, 105))
    bars.append(_bar(22, 99))   # breakdown: below prev low (104) and below EMA20
    return bars


def _strategy_bearish() -> BaselineV1Strategy:
    bars_1h = _downtrend_tf(60, "120", "60")
    bars_15m = _downtrend_tf(60, "120", "60")
    strategy = BaselineV1Strategy()
    strategy.warm_up(bars_1h, bars_15m, _bearish_1m()[:-1])
    return strategy


def test_short_signal_emits_on_breakdown() -> None:
    strategy = _strategy_bearish()
    bars = _bearish_1m()
    signal = strategy.evaluate(
        bars_1m=bars,
        close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("60"),
        now_epoch=1000.0,
    )
    assert signal is not None, "expected a short signal on the mirror breakdown"
    assert signal.side == "short"
    # Stop is ABOVE entry, TP is BELOW entry for a short (mirror of long).
    assert signal.stop_loss > signal.close_price
    assert signal.take_profit < signal.close_price
    assert signal.risk == signal.stop_loss - signal.close_price
    assert signal.net_rr >= strategy.cfg.min_net_rr


def test_enable_short_false_suppresses_short() -> None:
    cfg = BaselineV1Config(enable_short=False)
    bars_1h = _downtrend_tf(60, "120", "60")
    bars_15m = _downtrend_tf(60, "120", "60")
    strategy = BaselineV1Strategy(cfg)
    strategy.warm_up(bars_1h, bars_15m, _bearish_1m()[:-1])
    signal = strategy.evaluate(
        bars_1m=_bearish_1m(),
        close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("60"),
        now_epoch=1000.0,
    )
    assert signal is None


def test_short_dedup_is_side_scoped() -> None:
    strategy = _strategy_bearish()
    bars = _bearish_1m()
    first = strategy.evaluate(
        bars_1m=bars, close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("60"), now_epoch=1000.0,
    )
    second = strategy.evaluate(
        bars_1m=bars, close_1h=strategy._ema50_1h.value,
        close_15m=Decimal("60"), now_epoch=1001.0,
    )
    assert first is not None and first.side == "short"
    assert second is None, "same bar/side must not re-emit"


def test_regime_bearish_predicate() -> None:
    strategy = _strategy_bearish()
    assert strategy.regime_bearish() is True
    assert strategy.regime_bullish() is False
