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
from forecast_engine.metrics import (
    brier_score as _brier_score,
    interval_coverage as _interval_coverage,
    pinball_loss as _pinball_loss,
    skill_score as _skill_score,
)

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
    pinball_loss: Optional[float] = None
    coverage: Optional[float] = None
    brier: Optional[float] = None
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
    # Quantile / probabilistic accumulators (block C, D8)
    quantile_triples: List[List] = []
    actual_closes: List[Decimal] = []
    band_lows: List[Decimal] = []
    band_highs: List[Decimal] = []
    p_ups: List[Decimal] = []
    actual_ups: List[int] = []

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
        # Band edges: ForecastCandle (predicted_*) else legacy ForecastPoint (high/low)
        pred_high = getattr(fc, "predicted_high", None)
        pred_low = getattr(fc, "predicted_low", None)
        if pred_high is None:
            pred_high = getattr(fc, "high", None)
        if pred_low is None:
            pred_low = getattr(fc, "low", None)
        if pred_high is not None and pred_low is not None:
            if pred_low <= actual.close <= pred_high:
                in_band += 1
            # q10/q50/q90 triple for pinball loss
            quantile_triples.append([pred_low, pred_close, pred_high])
            actual_closes.append(actual.close)
            band_lows.append(pred_low)
            band_highs.append(pred_high)
        # Probabilistic direction (only when the model exposes p_up)
        p_up = getattr(fc, "p_up", None)
        if p_up is not None:
            p_ups.append(p_up)
            actual_ups.append(1 if actual.close > actual.open else 0)

    model_version = str(
        run.model_version.value if hasattr(run.model_version, "value") else run.model_version
    )

    if count == 0:
        return ForecastEval(
            run_id=getattr(run, "run_id", None),
            model_version=model_version,
            points_evaluated=0,
            mae=None,
            directional_accuracy=None,
            in_band_rate=None,
        )

    return ForecastEval(
        run_id=getattr(run, "run_id", None),
        model_version=model_version,
        points_evaluated=count,
        mae=sum(abs_errors, ZERO) / Decimal(count),
        directional_accuracy=Decimal(directional_hits) / Decimal(count),
        in_band_rate=Decimal(in_band) / Decimal(count),
        pinball_loss=_pinball_loss(actual_closes, quantile_triples) if quantile_triples else None,
        coverage=_interval_coverage(actual_closes, band_lows, band_highs) if band_lows else None,
        brier=_brier_score(p_ups, actual_ups) if p_ups else None,
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
    # Block C additions — distributional skill against the B0 baseline
    b0_pinball: Optional[float] = None
    b1_pinball: Optional[float] = None
    b0_brier: Optional[float] = None
    b1_brier: Optional[float] = None
    skill_mae: Optional[float] = None
    skill_pinball: Optional[float] = None
    b1_beats_b0_pinball: bool = False


def walk_forward_compare(
    b0_eval: ForecastEval,
    b1_eval: ForecastEval,
) -> WalkForwardResult:
    """Compare B1 vs B0 on the walk-forward statistics.

    ``b1_beats_b0`` keeps its historic MAE semantics (point-error regression),
    while ``b1_beats_b0_pinball`` and the ``skill_*`` fields judge the *quantile*
    forecast with proper scoring rules (block C, D8).  A skill score is
    ``1 - b1/b0`` for a lower-is-better metric; positive means B1 is better.
    """
    b1_beats = False
    if b0_eval.mae is not None and b1_eval.mae is not None:
        b1_beats = b1_eval.mae < b0_eval.mae

    skill_mae = None
    if b0_eval.mae is not None and b1_eval.mae is not None:
        skill_mae = _skill_score(float(b1_eval.mae), float(b0_eval.mae))

    b1_beats_pinball = False
    skill_pinball = None
    if b0_eval.pinball_loss is not None and b1_eval.pinball_loss is not None:
        b1_beats_pinball = b1_eval.pinball_loss < b0_eval.pinball_loss
        skill_pinball = _skill_score(b1_eval.pinball_loss, b0_eval.pinball_loss)

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
        b0_pinball=b0_eval.pinball_loss,
        b1_pinball=b1_eval.pinball_loss,
        b0_brier=b0_eval.brier,
        b1_brier=b1_eval.brier,
        skill_mae=skill_mae,
        skill_pinball=skill_pinball,
        b1_beats_b0_pinball=b1_beats_pinball,
    )