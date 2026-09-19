from decimal import Decimal

from paper_trading.strategy import Bar, BaselineV1Strategy


def _bar(minute: int, close: str) -> Bar:
    value = Decimal(close)
    return Bar(
        timestamp=f"2026-01-01T00:{minute:02d}:00+00:00",
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume=Decimal("1"),
    )


def test_overlapping_windows_do_not_reapply_indicator_history() -> None:
    history = [_bar(i, str(100 + i)) for i in range(1, 22)]
    strategy = BaselineV1Strategy()

    strategy.warm_up([], [], history[:20])
    ema_before = strategy._ema20_1m.value
    strategy.evaluate(bars_1m=history[:20], now_epoch=1.0)

    assert strategy._ema20_1m.value == ema_before

    strategy.evaluate(bars_1m=history, now_epoch=2.0)
    ema_after_new_bar = strategy._ema20_1m.value
    strategy.evaluate(bars_1m=history, now_epoch=3.0)

    assert strategy._ema20_1m.value == ema_after_new_bar


def test_higher_timeframe_windows_are_consumed_once() -> None:
    bars = [_bar(i, str(100 + i)) for i in range(1, 6)]
    strategy = BaselineV1Strategy()

    strategy.update_higher_timeframes(bars, bars)
    ema_1h = strategy._ema20_1h.value
    ema_15m = strategy._ema20_15m.value
    strategy.update_higher_timeframes(bars, bars)

    assert strategy._ema20_1h.value == ema_1h
    assert strategy._ema20_15m.value == ema_15m
