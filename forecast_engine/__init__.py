"""forecast_engine — Phase 3 deterministic forecast engine for WORED Trader V0.1.

B0 naive persistence and B1 deterministic quantile baseline.
All monetary values use ``decimal.Decimal``.  No external dependencies.
"""
from forecast_engine.contracts import (
    ForecastCandle,
    ForecastRun,
    ModelVersion,
    OHLCVBar,
    new_run_id,
)
from forecast_engine.evaluator import (
    ForecastEval,
    WalkForwardResult,
    evaluate_forecast,
    walk_forward_compare,
)
from forecast_engine.features import extract_features
from forecast_engine.indicators import (
    BollingerBands,
    IndicatorSnapshot,
    KDJResult,
    MACDResult,
    atr,
    avl,
    bollinger_bands,
    ema,
    ema_series,
    kdj,
    ma,
    ma_series,
    snapshot,
)
from forecast_engine.models import predict, predict_b0, predict_b1
from forecast_engine.service import ForecastService

__all__ = [
    "BollingerBands",
    "ForecastCandle",
    "ForecastEval",
    "ForecastRun",
    "ForecastService",
    "IndicatorSnapshot",
    "KDJResult",
    "MACDResult",
    "ModelVersion",
    "OHLCVBar",
    "WalkForwardResult",
    "atr",
    "avl",
    "bollinger_bands",
    "ema",
    "ema_series",
    "evaluate_forecast",
    "extract_features",
    "kdj",
    "ma",
    "ma_series",
    "new_run_id",
    "predict",
    "predict_b0",
    "predict_b1",
    "snapshot",
    "walk_forward_compare",
]