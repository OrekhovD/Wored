"""forecast_engine.service — ForecastService orchestrator.

Runs forecasts, persists to DB (stub), and evaluates against actuals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from forecast_engine.contracts import (
    ForecastCandle,
    ForecastRun,
    ModelVersion,
    OHLCVBar,
    new_run_id,
)
from forecast_engine.evaluator import ForecastEval, evaluate_forecast
from forecast_engine.features import extract_features
from forecast_engine.models import predict


# Default horizon: 3 steps × 5 min = 15 minutes
DEFAULT_HORIZON_STEPS = 3
DEFAULT_CONTRACT_CODE = "BTC-USDT"


class ForecastService:
    """Orchestrates forecast runs and evaluation.

    The ``persist_*`` methods are stubs — they record what *would* be
    written to the database but do not execute SQL.  Replace with a real
    repository when the DB layer is connected.
    """

    def __init__(
        self,
        contract_code: str = DEFAULT_CONTRACT_CODE,
        horizon_steps: int = DEFAULT_HORIZON_STEPS,
    ) -> None:
        self.contract_code = contract_code
        self.horizon_steps = horizon_steps

    # ----------------------------------------------------------------- #
    # Run forecast
    # ----------------------------------------------------------------- #

    def run_forecast(
        self,
        candles: Sequence[OHLCVBar],
        model_version: ModelVersion,
        horizon_steps: Optional[int] = None,
    ) -> ForecastRun:
        """Run a forecast and return a completed ForecastRun.

        ``candles`` must be closed, chronological OHLCV bars.  The
        data_cutoff is the open time of the last bar.
        """
        if not candles:
            raise ValueError("candles: at least one closed candle required")
        steps = horizon_steps or self.horizon_steps
        if steps < 1 or steps > 48:
            raise ValueError("horizon_steps: expected 1..48")

        # Extract features (also validates no-look-ahead by construction)
        features = extract_features(candles)

        # Predict
        forecast_candles = predict(candles, model_version, steps)

        # Data cutoff: the close time of the last input candle
        last = candles[-1]
        # Infer interval
        if len(candles) >= 2:
            interval = candles[-1].time - candles[-2].time
        else:
            interval = type(candles[-1].time).__new__(
                type(candles[-1].time), 5, 0, 0
            ) if hasattr(candles[-1].time, 'minute') else None
        # Use close_time = last open_time + interval (bar closes at open+interval)
        # For OHLCVBar we only have `time` (open time); close = open + interval
        if len(candles) >= 2:
            interval = candles[-1].time - candles[-2].time
            data_cutoff = candles[-1].time + interval
        else:
            data_cutoff = candles[-1].time

        run = ForecastRun(
            run_id=new_run_id(),
            contract_code=self.contract_code,
            horizon_minutes=int(
                interval.total_seconds() / 60
            ) if len(candles) >= 2 else 5,
            model_version=model_version,
            data_cutoff=data_cutoff,
            status="completed",
            candles=forecast_candles,
        )

        # Persist (stub)
        self._persist_run(run, features)

        return run

    # ----------------------------------------------------------------- #
    # Evaluate
    # ----------------------------------------------------------------- #

    def evaluate_forecast(
        self,
        run: ForecastRun,
        actual_candles: Sequence[OHLCVBar],
    ) -> ForecastEval:
        """Evaluate a completed forecast run against actual closed candles."""
        eval_result = evaluate_forecast(run, actual_candles)
        self._persist_eval(eval_result)
        return eval_result

    # ----------------------------------------------------------------- #
    # DB stubs
    # ----------------------------------------------------------------- #

    def _persist_run(
        self,
        run: ForecastRun,
        features: dict,
    ) -> None:
        """Stub: persist forecast run, candles, and indicators to DB.

        In production this writes to ``trader_v1_forecast_runs``,
        ``trader_v1_forecast_candles``, and ``trader_v1_forecast_indicators``.
        """
        # No-op stub — override with a real repository.
        pass

    def _persist_eval(self, eval_result: ForecastEval) -> None:
        """Stub: persist evaluation to ``trader_v1_forecast_eval``."""
        pass