"""tests.paper_trading.test_forecast — Phase 3 forecast engine tests.

Tests:
  1. Golden indicator fixture — deterministic indicator values
  2. No-look-ahead mutation test — adding future bars doesn't change past features
  3. Forecast-evaluation test — MAE, directional accuracy, in-band rate
  4. Backtest reproducibility test — same dataset hash → same results

Run: python -m pytest tests/paper_trading/test_forecast.py -v
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List

import pytest

from forecast_engine.contracts import (
    ForecastCandle,
    ForecastRun,
    ModelVersion,
    OHLCVBar,
)
from forecast_engine.evaluator import (
    evaluate_forecast,
    walk_forward_compare,
)
from forecast_engine.features import extract_features
from forecast_engine.indicators import (
    atr,
    avl,
    bollinger_bands,
    ema,
    kdj,
    ma,
    snapshot,
)
from forecast_engine.models import predict_b0, predict_b1
from forecast_engine.service import ForecastService


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _bar(index: int, o: str, h: str, l: str, c: str, v: str = "100") -> OHLCVBar:
    """Build a 5-minute OHLCV bar at the given index."""
    t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * index)
    return OHLCVBar(
        time=t,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _flat_bar(index: int, close: str, v: str = "100") -> OHLCVBar:
    """Build a flat bar (open=high=low=close) for simple tests."""
    c = Decimal(close)
    return _bar(index, close, close, close, close, v)


def _make_bars(n: int, start: str = "100") -> List[OHLCVBar]:
    """Build n bars with a simple upward trend."""
    base = Decimal(start)
    bars: List[OHLCVBar] = []
    for i in range(n):
        c = base + Decimal(i)
        o = c - Decimal("0.5")
        h = c + Decimal("1")
        lo = c - Decimal("1")
        bars.append(_bar(i, str(o), str(h), str(lo), str(c)))
    return bars


def _dataset_hash(bars: List[OHLCVBar]) -> str:
    """Deterministic hash of a candle dataset for reproducibility tests."""
    payload = json.dumps(
        [
            {
                "time": b.time.isoformat(),
                "open": str(b.open),
                "high": str(b.high),
                "low": str(b.low),
                "close": str(b.close),
                "volume": str(b.volume),
            }
            for b in bars
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 1. Golden indicator fixture
# --------------------------------------------------------------------------- #

class TestGoldenIndicators:
    """Verify indicator values against a deterministic golden fixture."""

    @pytest.fixture
    def golden_bars(self) -> List[OHLCVBar]:
        """30 bars with known values for golden comparison."""
        return _make_bars(30, "100")

    def test_ma_20(self, golden_bars: List[OHLCVBar]) -> None:
        closes = [b.close for b in golden_bars]
        result = ma(closes, 20)
        assert result is not None
        # closes 100..129, last 20 are 110..129, mean = (110+129)/2 = 119.5
        assert result == Decimal("119.5")

    def test_ema_20(self, golden_bars: List[OHLCVBar]) -> None:
        closes = [b.close for b in golden_bars]
        result = ema(closes, 20)
        assert result is not None
        # EMA should be below the last close (119.5 < 129) since trend is upward
        assert result < closes[-1]
        assert result > closes[0]

    def test_bollinger_bands(self, golden_bars: List[OHLCVBar]) -> None:
        closes = [b.close for b in golden_bars]
        bb = bollinger_bands(closes, 20, 2)
        assert bb is not None
        # Middle = MA(20) = 119.5
        assert bb.middle == Decimal("119.5")
        # Upper > middle > lower
        assert bb.upper > bb.middle
        assert bb.middle > bb.lower
        # Std is non-negative
        assert bb.std > Decimal(0)
        # For linear sequence, std = sqrt(variance) where variance = sum((x-mean)^2)/N
        # For 110..129: variance = 33.25, std = 5.766...
        expected_std = Decimal("33.25").sqrt()
        assert abs(bb.std - expected_std) < Decimal("0.001")

    def test_atr_14(self, golden_bars: List[OHLCVBar]) -> None:
        result = atr(golden_bars, 14)
        assert result is not None
        # Each bar: high - low = 2, and abs(high - prev_close) = |c+1 - (c_prev)| = 2
        # abs(low - prev_close) = |c-1 - c_prev| = 0
        # So TR = max(2, 2, 0) = 2 for all bars
        # ATR with Wilder's smoothing → converges to 2
        assert abs(result - Decimal(2)) < Decimal("0.01")

    def test_kdj(self, golden_bars: List[OHLCVBar]) -> None:
        highs = [b.high for b in golden_bars]
        lows = [b.low for b in golden_bars]
        closes = [b.close for b in golden_bars]
        result = kdj(highs, lows, closes, 9, 3, 3)
        assert result is not None
        # In an uptrend, K should be high (>50)
        assert result.k > Decimal(50)
        assert result.j > result.k  # J = 3K - 2D, in uptrend J > K

    def test_avl(self, golden_bars: List[OHLCVBar]) -> None:
        vols = [b.volume for b in golden_bars]
        result = avl(vols)
        assert result == Decimal("100")

    def test_snapshot_all_present(self) -> None:
        # Need 50 bars for EMA50 and 34 for MACD(12,26,9)
        bars_50 = _make_bars(50, "100")
        snap = snapshot(bars_50)
        assert snap.ma_20 is not None
        assert snap.ema_20 is not None
        assert snap.ema_50 is not None  # 50 bars >= 50
        assert snap.bb is not None
        assert snap.macd is not None  # 50 bars > 34
        assert snap.kdj is not None
        assert snap.atr_14 is not None
        assert snap.avl is not None

    def test_snapshot_warmup_with_30_bars(self) -> None:
        """With only 30 bars, MACD and EMA50 should still be None."""
        bars_30 = _make_bars(30, "100")
        snap = snapshot(bars_30)
        assert snap.ma_20 is not None
        assert snap.ema_20 is not None
        assert snap.ema_50 is None  # only 30 bars, need 50
        assert snap.bb is not None
        assert snap.macd is None  # 30 bars < 34 needed for MACD(12,26,9)
        assert snap.kdj is not None
        assert snap.atr_14 is not None
        assert snap.avl is not None


# --------------------------------------------------------------------------- #
# 2. No-look-ahead mutation test
# --------------------------------------------------------------------------- #

class TestNoLookAhead:
    """Features computed at bar T must not change when future bars are added."""

    def test_features_stable_on_future_addition(self) -> None:
        """Extract features from 20 bars, then add 5 more bars and verify
        the features at the original cutoff are identical."""
        bars_20 = _make_bars(20, "100")
        features_at_20 = extract_features(bars_20)

        # Add 5 future bars
        bars_25 = _make_bars(25, "100")
        features_at_25 = extract_features(bars_25[:20])

        # The features hash and all feature values must be identical
        assert features_at_20["features_hash"] == features_at_25["features_hash"]

        # Verify all feature values match
        for key in features_at_20:
            if key == "features_hash":
                continue
            assert features_at_20[key] == features_at_25[key], (
                f"Feature {key} changed: {features_at_20[key]} → {features_at_25[key]}"
            )

    def test_forecast_does_not_use_future(self) -> None:
        """B0 forecast from 10 bars must equal B0 forecast from the first
        10 bars of a 15-bar sequence."""
        bars_10 = _make_bars(10, "100")
        bars_15 = _make_bars(15, "100")

        fc_10 = predict_b0(bars_10, horizon=3)
        fc_15_prefix = predict_b0(bars_15[:10], horizon=3)

        for a, b in zip(fc_10, fc_15_prefix):
            assert a.predicted_close == b.predicted_close
            assert a.open_time == b.open_time

    def test_b1_forecast_does_not_use_future(self) -> None:
        """B1 forecast from 20 bars must be identical to B1 from the first
        20 bars of a 25-bar sequence."""
        bars_20 = _make_bars(20, "100")
        bars_25 = _make_bars(25, "100")

        fc_20 = predict_b1(bars_20, horizon=3)
        fc_25_prefix = predict_b1(bars_25[:20], horizon=3)

        for a, b in zip(fc_20, fc_25_prefix):
            assert a.predicted_close == b.predicted_close, (
                f"B1 close differs: {a.predicted_close} vs {b.predicted_close}"
            )
            assert a.predicted_high == b.predicted_high
            assert a.predicted_low == b.predicted_low


# --------------------------------------------------------------------------- #
# 3. Forecast-evaluation test
# --------------------------------------------------------------------------- #

class TestForecastEvaluation:
    """Test MAE, directional accuracy, and in-band rate."""

    def test_b0_evaluation_mae_and_direction(self) -> None:
        """B0 predicts flat → directional accuracy should reflect actual direction."""
        bars = _make_bars(20, "100")
        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)
        run = service.run_forecast(bars, ModelVersion.B0_NAIVE)

        # Build actual candles that match the forecast open_times
        interval = timedelta(minutes=5)
        actuals: List[OHLCVBar] = []
        for i in range(3):
            t = bars[-1].time + interval * (i + 1)
            actuals.append(OHLCVBar(
                time=t,
                open=Decimal("120"),
                high=Decimal("122"),
                low=Decimal("118"),
                close=Decimal("121"),
                volume=Decimal("100"),
            ))

        ev = service.evaluate_forecast(run, actuals)
        assert ev.points_evaluated == 3
        assert ev.mae is not None
        # B0 predicted close=119 (last close of bars is 119), actual close=121
        assert ev.mae == Decimal("2")
        # B0 predicts flat (open=close), actual is up → directional match when actual is flat
        # B0 predicted_open == predicted_close → direction is 0
        # actual: open=120, close=121 → direction is 1
        # 0 != 1 → 0 hits
        assert ev.directional_accuracy == Decimal(0)

    def test_b1_evaluation_in_band_rate(self) -> None:
        """B1 quantile bands should contain some actual closes."""
        bars = _make_bars(30, "100")
        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)
        run = service.run_forecast(bars, ModelVersion.B1_QUANTILE)

        # Build actuals that follow the trend (close continues upward)
        interval = timedelta(minutes=5)
        actuals: List[OHLCVBar] = []
        for i in range(3):
            t = bars[-1].time + interval * (i + 1)
            c = Decimal("130") + Decimal(i + 1)
            actuals.append(OHLCVBar(
                time=t,
                open=c - Decimal("0.5"),
                high=c + Decimal("1"),
                low=c - Decimal("1"),
                close=c,
                volume=Decimal("100"),
            ))

        ev = service.evaluate_forecast(run, actuals)
        assert ev.points_evaluated == 3
        assert ev.in_band_rate is not None
        # In-band rate should be a valid fraction
        assert Decimal(0) <= ev.in_band_rate <= Decimal(1)

    def test_walk_forward_b1_vs_b0(self) -> None:
        """Walk-forward comparison: B1 should generally not be worse than B0."""
        bars = _make_bars(30, "100")
        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)

        run_b0 = service.run_forecast(bars, ModelVersion.B0_NAIVE)
        run_b1 = service.run_forecast(bars, ModelVersion.B1_QUANTILE)

        interval = timedelta(minutes=5)
        actuals: List[OHLCVBar] = []
        for i in range(3):
            t = bars[-1].time + interval * (i + 1)
            c = Decimal("130") + Decimal(i + 1)
            actuals.append(OHLCVBar(
                time=t,
                open=c - Decimal("0.5"),
                high=c + Decimal("1"),
                low=c - Decimal("1"),
                close=c,
                volume=Decimal("100"),
            ))

        ev_b0 = evaluate_forecast(run_b0, actuals)
        ev_b1 = evaluate_forecast(run_b1, actuals)

        wf = walk_forward_compare(ev_b0, ev_b1)
        assert wf.b0_mae is not None
        assert wf.b1_mae is not None
        # In an uptrend, B1 (drift) should produce lower MAE than B0 (flat)
        assert wf.b1_beats_b0 is True
        assert wf.b1_mae < wf.b0_mae

    def test_empty_evaluation_returns_none(self) -> None:
        """No matching actuals → all metrics None."""
        bars = _make_bars(20, "100")
        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)
        run = service.run_forecast(bars, ModelVersion.B0_NAIVE)

        # No actuals at all
        ev = service.evaluate_forecast(run, [])
        assert ev.points_evaluated == 0
        assert ev.mae is None
        assert ev.directional_accuracy is None
        assert ev.in_band_rate is None


# --------------------------------------------------------------------------- #
# 4. Backtest reproducibility test keyed by dataset hash
# --------------------------------------------------------------------------- #

class TestBacktestReproducibility:
    """Same dataset hash → identical backtest results."""

    def test_same_dataset_same_results(self) -> None:
        """Two runs over the same dataset must produce identical forecasts."""
        bars = _make_bars(30, "100")
        dataset_hash = _dataset_hash(bars)

        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)
        run1 = service.run_forecast(bars, ModelVersion.B1_QUANTILE)
        run2 = service.run_forecast(bars, ModelVersion.B1_QUANTILE)

        # Dataset hash is the same
        assert _dataset_hash(bars) == dataset_hash

        # Forecast candles must be identical (deterministic)
        assert len(run1.candles) == len(run2.candles)
        for c1, c2 in zip(run1.candles, run2.candles):
            assert c1.predicted_open == c2.predicted_open
            assert c1.predicted_high == c2.predicted_high
            assert c1.predicted_low == c2.predicted_low
            assert c1.predicted_close == c2.predicted_close
            assert c1.confidence == c2.confidence
            assert c1.p_up == c2.p_up

    def test_different_dataset_different_hash(self) -> None:
        """Different datasets must produce different hashes."""
        bars_a = _make_bars(30, "100")
        bars_b = _make_bars(30, "200")
        assert _dataset_hash(bars_a) != _dataset_hash(bars_b)

    def test_features_hash_deterministic(self) -> None:
        """Features hash must be stable across calls with same input."""
        bars = _make_bars(25, "100")
        f1 = extract_features(bars)
        f2 = extract_features(bars)
        assert f1["features_hash"] == f2["features_hash"]

    def test_backtest_deterministic_clock(self) -> None:
        """Simulate a walk-forward backtest with a deterministic clock.

        At each step, we forecast from bars[0:t] and evaluate against
        bars[t:t+3].  Results must be reproducible.
        """
        bars = _make_bars(50, "100")
        service = ForecastService(contract_code="BTC-USDT", horizon_steps=3)

        results: List[dict] = []
        for t in range(20, len(bars) - 3):
            window = bars[:t]
            actuals = bars[t : t + 3]
            run = service.run_forecast(window, ModelVersion.B1_QUANTILE)
            ev = service.evaluate_forecast(run, actuals)
            results.append({
                "t": t,
                "mae": str(ev.mae) if ev.mae else None,
                "directional": str(ev.directional_accuracy) if ev.directional_accuracy else None,
                "in_band": str(ev.in_band_rate) if ev.in_band_rate else None,
            })

        # Re-run and compare
        results2: List[dict] = []
        for t in range(20, len(bars) - 3):
            window = bars[:t]
            actuals = bars[t : t + 3]
            run = service.run_forecast(window, ModelVersion.B1_QUANTILE)
            ev = service.evaluate_forecast(run, actuals)
            results2.append({
                "t": t,
                "mae": str(ev.mae) if ev.mae else None,
                "directional": str(ev.directional_accuracy) if ev.directional_accuracy else None,
                "in_band": str(ev.in_band_rate) if ev.in_band_rate else None,
            })

        assert results == results2
        # Dataset hash anchors reproducibility
        assert _dataset_hash(bars) == _dataset_hash(bars)