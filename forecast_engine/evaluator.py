"""forecast_engine.evaluator — forecast evaluation and walk-forward comparison.

Evaluates predicted candles against subsequently closed actual candles.
Metrics:
  - MAE (mean absolute error of close price)
  - directional_accuracy (fraction of correct up/down direction)
  - in_band_rate (fraction of actual closes within [predicted_low, predicted_high])

Walk-forward comparison: B1 vs B0 on MAE — B1 must beat B0 (lower MAE)
before auto entry is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence

from forecast_engine.contracts import ForecastCandle, ForecastRun, OHLCVBar

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class ForecastEval:
    """Evaluation result for a single forecast run."""

    run_id: object  # UUID
    model_version: str
    points_evaluated: int
    mae: Optional[Decimal]
    directional_accuracy: Optional[Decimal]
    in_band_rate: Optional[Decimal]
    metadata: dict = field(default_factory=dict)


def _direction(open_price: Decimal, close_price: Decimal) -> int:
    """1 if up, -1 if down, 0 if flat."""
    if close_price > open_price:
        return 1
    if close_price < open_price:
        return -1
    return 0


def _get_open_time(bar) -> datetime:
    """Get open_time from either OHLCVBar (.time) or Candle (.open_time)."""
    return getattr(bar, "time", None) or getattr(bar, "open_time", None)


def _get_close_time(bar) -> datetime:
    """Get close_time from either OHLCVBar or Candle."""
    return getattr(bar, "close_time", None) or (
        getattr(bar, "time", None) + timedelta(0)  # fallback
    )


def evaluate_forecast(
    run: ForecastRun,
    actual_candles: Sequence,
) -> ForecastEval:
    """Evaluate a forecast run against actual closed candles.

    Only actual candles whose open_time matches a predicted candle's
    open_time and whose close_time is at or after the run's data_cutoff
    are considered.  This ensures no look-ahead.

    Backward-compatible: accepts both new ``ForecastRun`` (with ``.candles``
    of ``ForecastCandle``) and old ``core.ForecastRun`` (with ``.points``
    of ``ForecastPoint``), and both ``OHLCVBar`` and ``core.Candle`` for
    actuals.
    """
    # Detect old-style run (core.ForecastRun has .points, no .candles)
    forecast_items = getattr(run, "candles", None)
    if forecast_items is None:
        forecast_items = getattr(run, "points", ())

    actual_by_open = {}
    for bar in actual_candles:
        ot = _get_open_time(bar)
        ct = getattr(bar, "close_time", None)
        if ct is not None and ct > run.data_cutoff:
            actual_by_open[ot] = bar
        elif ct is None and ot >= run.data_cutoff:
            actual_by_open[ot] = bar

    abs_errors: List[Decimal] = []
    directional_hits = 0
    in_band = 0
    count = 0

    for fc in forecast_items:
        fc_open = _get_open_time(fc)
        actual = actual_by_open.get(fc_open)
        if actual is None:
            continue
        count += 1
        # MAE on close
        pred_close = getattr(fc, "predicted_close", None) or getattr(fc, "close", None)
        abs_errors.append(abs(pred_close - actual.close))
        # Directional accuracy
        pred_open = getattr(fc, "predicted_open", None) or getattr(fc, "open", None)
        pred_dir = _direction(pred_open, pred_close)
        actual_dir = _direction(actual.open, actual.close)
        if pred_dir == actual_dir:
            directional_hits += 1
        # In-band rate (only for ForecastCandle with predicted_high/low)
        pred_high = getattr(fc, "predicted_high", None)
        pred_low = getattr(fc, "predicted_low", None)
        if pred_high is not None and pred_low is not None:
            if pred_low <= actual.close <= pred_high:
                in_band += 1
        else:
            # Old-style ForecastPoint: use high/low
            fc_high = getattr(fc, "high", None)
            fc_low = getattr(fc, "low", None)
            if fc_high is not None and fc_low is not None:
                if fc_low <= actual.close <= fc_high:
                    in_band += 1

    if count == 0:
        return ForecastEval(
            run_id=getattr(run, "run_id", None),
            model_version=str(run.model_version.value if hasattr(run.model_version, "value") else run.model_version),
            points_evaluated=0,
            mae=None,
            directional_accuracy=None,
            in_band_rate=None,
        )

    return ForecastEval(
        run_id=getattr(run, "run_id", None),
        model_version=str(run.model_version.value if hasattr(run.model_version, "value") else run.model_version),
        points_evaluated=count,
        mae=sum(abs_errors, ZERO) / Decimal(count),
        directional_accuracy=Decimal(directional_hits) / Decimal(count),
        in_band_rate=Decimal(in_band) / Decimal(count),
    )


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-forward comparison of B1 vs B0."""

    b0_mae: Optional[Decimal]
    b1_mae: Optional[Decimal]
    b0_directional: Optional[Decimal]
    b1_directional: Optional[Decimal]
    b0_in_band: Optional[Decimal]
    b1_in_band: Optional[Decimal]
    b1_beats_b0: bool
    points_evaluated: int


def walk_forward_compare(
    b0_eval: ForecastEval,
    b1_eval: ForecastEval,
) -> WalkForwardResult:
    """Compare B1 vs B0 on the walk-forward statistic (MAE).

    B1 beats B0 if its MAE is strictly lower.  If either MAE is ``None``
    (no evaluable points), ``b1_beats_b0`` is ``False``.
    """
    b1_beats = False
    if b0_eval.mae is not None and b1_eval.mae is not None:
        b1_beats = b1_eval.mae < b0_eval.mae

    return WalkForwardResult(
        b0_mae=b0_eval.mae,
        b1_mae=b1_eval.mae,
        b0_directional=b0_eval.directional_accuracy,
        b1_directional=b1_eval.directional_accuracy,
        b0_in_band=b0_eval.in_band_rate,
        b1_in_band=b1_eval.in_band_rate,
        b1_beats_b0=b1_beats,
        points_evaluated=max(
            b0_eval.points_evaluated, b1_eval.points_evaluated
        ),
    )