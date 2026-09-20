"""BaselineV1 trading strategy for the paper-trading prototype.

Three-timeframe breakout/pullback model:
  1h  — regime filter: EMA20 > EMA50 and close > EMA20
  15m — confirmation: close > EMA20
  1m  — trigger: previous low <= previous EMA20, last close > last EMA20
         and last close > previous high

All monetary arithmetic uses Decimal.  Python 3.9 compatible.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────

TWO = Decimal(2)


def _dec(value: Any) -> Decimal:
    """Coerce *value* to a finite Decimal, raising ValueError on bad input."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"cannot convert {value!r} to Decimal") from exc
    if not d.is_finite():
        raise ValueError(f"non-finite Decimal: {value!r}")
    return d


# ─── Indicators ────────────────────────────────────────────────────────

class EMA:
    """Exponential moving average.

    alpha = 2 / (N + 1).  The first value is seeded with the SMA of the
    first *period* closes.  Subsequent values are updated incrementally.
    """

    __slots__ = ("_seed_buf", "_seeded", "_value", "_prev_value", "alpha", "period")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("EMA period must be >= 1")
        self.period = period
        self.alpha = TWO / Decimal(period + 1)
        self._value: Decimal | None = None
        self._prev_value: Decimal | None = None
        self._seed_buf: list[Decimal] = []
        self._seeded = False

    # -- public API --------------------------------------------------------

    @property
    def value(self) -> Decimal | None:
        """Current EMA value, or ``None`` if not yet seeded."""
        return self._value

    @property
    def prev_value(self) -> Decimal | None:
        """EMA value as of the *previous* update, or ``None`` if unavailable.

        Tracked explicitly (D11) rather than recovered by inverting the EMA
        recurrence, which is unstable as ``alpha`` approaches 1.
        """
        return self._prev_value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, price: Decimal) -> Decimal:
        """Push a new close *price* and return the updated EMA."""
        price = _dec(price)
        if not self._seeded:
            self._seed_buf.append(price)
            if len(self._seed_buf) < self.period:
                self._value = None
                self._prev_value = None
                return self._value  # type: ignore[return-value]
            sma = sum(self._seed_buf, Decimal(0)) / Decimal(self.period)
            self._value = sma
            self._prev_value = None
            self._seeded = True
            self._seed_buf = []
            return self._value
        # EMA recurrence:  E = alpha*price + (1-alpha)*prev
        previous = self._value
        if previous is None:
            raise RuntimeError("EMA seeded without a previous value")
        self._prev_value = previous
        self._value = self.alpha * price + (Decimal(1) - self.alpha) * previous
        return self._value

    def reset(self) -> None:
        self._value = None
        self._prev_value = None
        self._seed_buf = []
        self._seeded = False


class ATR14:
    """ATR(14) using Wilder's smoothing method.

    The first TR value seeds the ATR as a simple average of the first 14
    true ranges.  Subsequent values use Wilder's recurrence:
        ATR = (ATR_prev * (N-1) + TR) / N
    """

    __slots__ = ("_prev_close", "_seeded", "_tr_buf", "_value", "period")

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self.period = period
        self._value: Decimal | None = None
        self._tr_buf: list[Decimal] = []
        self._prev_close: Decimal | None = None
        self._seeded = False

    @property
    def value(self) -> Decimal | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def true_range(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal:
        """True range for a bar given the previous close."""
        high = _dec(high)
        low = _dec(low)
        close = _dec(close)
        if self._prev_close is None:
            return high - low
        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )
        return tr

    def update(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal:
        """Push a new bar and return the updated ATR."""
        high = _dec(high)
        low = _dec(low)
        close = _dec(close)
        tr = self.true_range(high, low, close)
        if not self._seeded:
            self._tr_buf.append(tr)
            self._prev_close = close
            if len(self._tr_buf) < self.period:
                self._value = None
                return self._value  # type: ignore[return-value]
            total = sum(self._tr_buf, Decimal(0))
            self._value = total / Decimal(self.period)
            self._seeded = True
            self._tr_buf = []
            return self._value
        n = Decimal(self.period)
        previous = self._value
        if previous is None:
            raise RuntimeError("ATR seeded without a previous value")
        self._value = (previous * (n - Decimal(1)) + tr) / n
        self._prev_close = close
        return self._value

    def reset(self) -> None:
        self._value = None
        self._tr_buf = []
        self._prev_close = None
        self._seeded = False


# ─── Strategy data structures ──────────────────────────────────────────

@dataclass(frozen=True)
class Bar:
    """A single OHLC bar."""
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)


@dataclass(frozen=True)
class Signal:
    """A BaselineV1 trigger signal."""
    side: str                       # "long" | "short"
    close_price: Decimal            # close of the trigger bar
    bar_timestamp: str
    ema20_at_trigger: Decimal
    atr_at_trigger: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    net_rr: Decimal
    created_at_epoch: float         # wall-clock for TTL
    strategy_version: str = "baseline_v1"

    @property
    def risk(self) -> Decimal:
        if self.side == "long":
            return self.close_price - self.stop_loss
        return self.stop_loss - self.close_price

    def is_expired(self, now_epoch: float, ttl_seconds: float = 60.0) -> bool:
        return (now_epoch - self.created_at_epoch) > ttl_seconds

    def price_in_entry_zone(self, price: Decimal) -> bool:
        return self.entry_zone_low <= price <= self.entry_zone_high


@dataclass
class BaselineV1Config:
    """Tunable parameters for BaselineV1Strategy."""
    ema_fast_period: int = 20
    ema_slow_period: int = 50
    atr_period: int = 14
    signal_ttl_seconds: float = 60.0
    entry_atr_fraction: Decimal = field(default_factory=lambda: Decimal("0.25"))
    sl_atr_fraction: Decimal = field(default_factory=lambda: Decimal("0.25"))
    tp_rr: Decimal = field(default_factory=lambda: Decimal(2))
    min_net_rr: Decimal = field(default_factory=lambda: Decimal("1.2"))
    cooldown_seconds: float = 600.0         # 10 min
    sl_lookback_bars: int = 5
    no_entry_last_minutes_of_day: int = 5
    warmup_bars_1h: int = 50
    warmup_bars_15m: int = 50
    warmup_bars_1m: int = 50
    # Self-learn block E: enable the mirrored short side (bearish regime).
    enable_long: bool = True
    enable_short: bool = True


# ─── Strategy ──────────────────────────────────────────────────────────

class BaselineV1Strategy:
    """BaselineV1 multi-timeframe strategy.

    Call :meth:`evaluate` with the latest closed bars on each timeframe to
    obtain an optional :class:`Signal`.  The strategy is stateless across
    evaluations except for the indicator objects it maintains internally.
    """

    VERSION = "baseline_v1"

    def __init__(self, config: BaselineV1Config | None = None) -> None:
        self.cfg = config or BaselineV1Config()

        # 1h indicators (regime)
        self._ema20_1h = EMA(self.cfg.ema_fast_period)
        self._ema50_1h = EMA(self.cfg.ema_slow_period)

        # 15m indicators (confirmation)
        self._ema20_15m = EMA(self.cfg.ema_fast_period)

        # 1m indicators (trigger)
        self._ema20_1m = EMA(self.cfg.ema_fast_period)
        self._atr14_1m = ATR14(self.cfg.atr_period)

        # cooldown
        self._last_sl_epoch: float = 0.0
        self._last_signal_bar_ts: str | None = None
        self._dedup_key: str | None = None
        self._last_1h_bar_ts: str | None = None
        self._last_15m_bar_ts: str | None = None
        self._last_1m_bar_ts: str | None = None

    # -- public -----------------------------------------------------------

    def reset(self) -> None:
        self._ema20_1h.reset()
        self._ema50_1h.reset()
        self._ema20_15m.reset()
        self._ema20_1m.reset()
        self._atr14_1m.reset()
        self._last_sl_epoch = 0.0
        self._last_signal_bar_ts = None
        self._dedup_key = None
        self._last_1h_bar_ts = None
        self._last_15m_bar_ts = None
        self._last_1m_bar_ts = None

    def on_stop_loss_hit(self, epoch: float) -> None:
        """Record that SL was hit, starting the cooldown window."""
        self._last_sl_epoch = epoch

    def in_cooldown(self, now_epoch: float) -> bool:
        if self._last_sl_epoch <= 0:
            return False
        return (now_epoch - self._last_sl_epoch) < self.cfg.cooldown_seconds

    def warm_up(self, bars_1h: Sequence[Bar], bars_15m: Sequence[Bar], bars_1m: Sequence[Bar]) -> None:
        """Pre-seed all indicators from historical bars."""
        self.update_higher_timeframes(bars_1h, bars_15m)
        self._update_1m(bars_1m)

    def update_higher_timeframes(
        self,
        bars_1h: Sequence[Bar],
        bars_15m: Sequence[Bar],
    ) -> None:
        """Consume each closed higher-timeframe candle exactly once."""
        for b in bars_1h:
            if self._last_1h_bar_ts is not None and b.timestamp <= self._last_1h_bar_ts:
                continue
            self._ema20_1h.update(b.close)
            self._ema50_1h.update(b.close)
            self._last_1h_bar_ts = b.timestamp
        for b in bars_15m:
            if self._last_15m_bar_ts is not None and b.timestamp <= self._last_15m_bar_ts:
                continue
            self._ema20_15m.update(b.close)
            self._last_15m_bar_ts = b.timestamp

    def _update_1m(self, bars_1m: Sequence[Bar]) -> None:
        for b in bars_1m:
            if self._last_1m_bar_ts is not None and b.timestamp <= self._last_1m_bar_ts:
                continue
            self._ema20_1m.update(b.close)
            self._atr14_1m.update(b.high, b.low, b.close)
            self._last_1m_bar_ts = b.timestamp

    def regime_bullish(self) -> bool:
        """1h regime: EMA20 > EMA50 (both must be ready)."""
        e20 = self._ema20_1h.value
        e50 = self._ema50_1h.value
        if e20 is None or e50 is None:
            return False
        return e20 > e50

    def confirm_15m(self, close_15m: Decimal) -> bool:
        """15m confirmation: close > EMA20."""
        e20 = self._ema20_15m.value
        if e20 is None:
            return False
        return _dec(close_15m) > e20

    def regime_bearish(self) -> bool:
        """1h regime: EMA20 < EMA50 (both must be ready) — short side."""
        e20 = self._ema20_1h.value
        e50 = self._ema50_1h.value
        if e20 is None or e50 is None:
            return False
        return e20 < e50

    def confirm_15m_short(self, close_15m: Decimal) -> bool:
        """15m confirmation for shorts: close < EMA20."""
        e20 = self._ema20_15m.value
        if e20 is None:
            return False
        return _dec(close_15m) < e20

    def evaluate(
        self,
        *,
        bars_1m: Sequence[Bar],
        close_1h: Decimal | None = None,
        close_15m: Decimal | None = None,
        now_epoch: float,
        day_end_epoch: float | None = None,
        account_id: str = "default",
        dedup_context: str | None = None,
    ) -> Signal | None:
        """Evaluate the strategy on a freshly closed 1m bar.

        *bars_1m* must contain at least the last two closed 1m bars (prev and
        current).  Indicators are updated from *bars_1m* before evaluation.

        Returns a :class:`Signal` if all conditions are met, else ``None``.
        """
        if len(bars_1m) < 2:
            return None

        # Overlapping history windows are normal in the live runner.  Consume
        # only candles newer than the last processed timestamp.
        self._update_1m(bars_1m)

        # Cooldown
        if self.in_cooldown(now_epoch):
            log.debug("strategy: in cooldown until %.0f", self._last_sl_epoch + self.cfg.cooldown_seconds)
            return None

        # No entries in last N minutes of the trading day
        if day_end_epoch is not None:
            minutes_left = (day_end_epoch - now_epoch) / 60.0
            if minutes_left <= self.cfg.no_entry_last_minutes_of_day:
                log.debug("strategy: no entries in last %d min of day", self.cfg.no_entry_last_minutes_of_day)
                return None

        # 1m trigger: need prev and current bar + EMA20 ready
        prev_bar = bars_1m[-2]
        last_bar = bars_1m[-1]
        ema20_1m = self._ema20_1m.value
        atr_1m = self._atr14_1m.value
        if ema20_1m is None or atr_1m is None or atr_1m <= 0:
            return None

        # We need the *previous* EMA20 value (EMA20 as of prev_bar close).
        # It is tracked explicitly by the accumulator (D11) rather than recovered
        # by inverting the EMA recurrence, which is numerically unstable.
        prev_ema20 = self._ema20_1m.prev_value
        if prev_ema20 is None:
            return None

        # ── LONG: 1h bullish regime + 15m up-confirm + 1m breakout trigger ──
        if (self.cfg.enable_long and self.regime_bullish()
                and (close_15m is None or self.confirm_15m(close_15m))
                and prev_bar.low <= prev_ema20
                and last_bar.close > ema20_1m
                and last_bar.close > prev_bar.high):
            return self._maybe_emit(
                "long", last_bar, ema20_1m, atr_1m, bars_1m,
                now_epoch, account_id, dedup_context,
            )

        # ── SHORT (block E): 1h bearish regime + 15m down-confirm + 1m
        #    breakdown trigger — the exact mirror of the long conditions ──
        if (self.cfg.enable_short and self.regime_bearish()
                and (close_15m is None or self.confirm_15m_short(close_15m))
                and prev_bar.high >= prev_ema20
                and last_bar.close < ema20_1m
                and last_bar.close < prev_bar.low):
            return self._maybe_emit(
                "short", last_bar, ema20_1m, atr_1m, bars_1m,
                now_epoch, account_id, dedup_context,
            )

        return None

    def _maybe_emit(
        self, side, trigger_bar, ema20_at, atr_at, bars_1m, now_epoch, account_id, dedup_context,
    ) -> Signal | None:
        """Build a signal for *side* and apply the per-bar/account/side dedup."""
        signal = self._build_signal(
            side=side,
            trigger_bar=trigger_bar,
            ema20_at_trigger=ema20_at,
            atr_at_trigger=atr_at,
            bars_1m=bars_1m,
            now_epoch=now_epoch,
            account_id=account_id,
            dedup_context=dedup_context,
        )
        if signal is None:
            return None

        # Dedup: don't re-emit for the same bar/account/version/side
        dedup_key = f"{account_id}:{self.VERSION}:{signal.bar_timestamp}:{signal.side}"
        if dedup_context:
            dedup_key += f":{dedup_context}"
        if self._dedup_key == dedup_key:
            return None
        self._dedup_key = dedup_key
        self._last_signal_bar_ts = signal.bar_timestamp
        return signal

    # -- internals --------------------------------------------------------

    def _build_signal(
        self,
        *,
        side: str,
        trigger_bar: Bar,
        ema20_at_trigger: Decimal,
        atr_at_trigger: Decimal,
        bars_1m: Sequence[Bar],
        now_epoch: float,
        account_id: str,
        dedup_context: str | None,
    ) -> Signal | None:
        cfg = self.cfg
        close_price = trigger_bar.close
        atr = atr_at_trigger
        frac = cfg.entry_atr_fraction
        sl_frac = cfg.sl_atr_fraction
        fee_rate = Decimal("0.0006")

        lookback = bars_1m[-cfg.sl_lookback_bars:] if len(bars_1m) >= cfg.sl_lookback_bars else bars_1m
        if not lookback:
            return None

        if side == "long":
            # Stop below the recent swing low; TP at 2R above entry.
            extreme = min((b.low for b in lookback), key=lambda x: x)
            stop_loss = extreme - sl_frac * atr
            risk = close_price - stop_loss
            if risk <= 0:
                log.debug("strategy: long risk <= 0, skip")
                return None
            take_profit = close_price + cfg.tp_rr * risk
        else:  # short — mirror image
            extreme = max((b.high for b in lookback), key=lambda x: x)
            stop_loss = extreme + sl_frac * atr
            risk = stop_loss - close_price
            if risk <= 0:
                log.debug("strategy: short risk <= 0, skip")
                return None
            take_profit = close_price - cfg.tp_rr * risk

        # Entry zone: within ``entry_atr_fraction`` ATR of the signal close.
        entry_zone_low = close_price - frac * atr
        entry_zone_high = close_price + frac * atr

        # Net RR (fee-aware round-trip on a hypothetical taker fill at ``close``).
        approx_entry = close_price
        fee_cost = approx_entry * fee_rate * Decimal(2)
        if side == "long":
            gross_profit = take_profit - approx_entry
            net_risk = approx_entry - stop_loss
        else:
            gross_profit = approx_entry - take_profit
            net_risk = stop_loss - approx_entry
        net_profit = gross_profit - fee_cost
        if net_risk <= 0:
            return None
        net_rr = net_profit / net_risk
        if net_rr < cfg.min_net_rr:
            log.debug("strategy: net RR %s < min %s, cancel", net_rr, cfg.min_net_rr)
            return None

        return Signal(
            side=side,
            close_price=close_price,
            bar_timestamp=trigger_bar.timestamp,
            ema20_at_trigger=ema20_at_trigger,
            atr_at_trigger=atr,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            net_rr=net_rr,
            created_at_epoch=now_epoch,
            strategy_version=self.VERSION,
        )
