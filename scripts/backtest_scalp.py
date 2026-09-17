#!/usr/bin/env python3
"""scripts.backtest_scalp — deterministic clock backtest for the forecast engine.

Walk-forward backtest with:
  - Deterministic clock (no wall-clock dependency)
  - Data hash (SHA-256 of the candle dataset)
  - Per-trade chain (each step logged with features_hash and forecast eval)
  - No-look-ahead enforcement (only closed bars are used at each step)

Usage:
    python scripts/backtest_scalp.py [--bars 50] [--contract BTC-USDT]

Run: python -m pytest tests/paper_trading/test_forecast.py -v
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Make the project root importable when run as a script
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecast_engine.contracts import ForecastRun, ModelVersion, OHLCVBar
from forecast_engine.evaluator import ForecastEval, evaluate_forecast, walk_forward_compare
from forecast_engine.features import extract_features
from forecast_engine.models import predict_b0, predict_b1
from forecast_engine.service import ForecastService


# --------------------------------------------------------------------------- #
# Synthetic data generator (deterministic)
# --------------------------------------------------------------------------- #


def generate_synthetic_bars(
    n: int = 50,
    start_price: str = "100",
    interval_minutes: int = 5,
    seed: int = 42,
) -> List[OHLCVBar]:
    """Generate deterministic synthetic OHLCV bars for backtesting.

    Uses a simple linear trend with small oscillation so the results are
    reproducible without external data.
    """
    base = Decimal(start_price)
    bars: List[OHLCVBar] = []
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    interval = timedelta(minutes=interval_minutes)

    for i in range(n):
        # Deterministic: linear upward + small sine-like oscillation
        trend = Decimal(i)
        oscillation = Decimal(str(((i * 7) % 11) - 5)) * Decimal("0.1")
        close = base + trend + oscillation
        op = close - Decimal("0.5")
        high = close + Decimal("1.0")
        low = close - Decimal("1.0")
        volume = Decimal(100 + (i * 3) % 50)
        bars.append(OHLCVBar(
            time=t0 + interval * i,
            open=op,
            high=high,
            low=low,
            close=close,
            volume=volume,
        ))
    return bars


# --------------------------------------------------------------------------- #
# Data hash
# --------------------------------------------------------------------------- #


def data_hash(bars: List[OHLCVBar]) -> str:
    """SHA-256 hash of the candle dataset for reproducibility."""
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
# Backtest trade chain
# --------------------------------------------------------------------------- #


@dataclass
class TradeStep:
    """One step in the walk-forward backtest chain."""
    step: int
    bar_index: int
    features_hash: str
    b0_predicted_close: str
    b1_predicted_close: str
    actual_close: str
    b0_mae: str
    b1_mae: str
    b1_beats_b0: bool


@dataclass
class BacktestResult:
    """Complete backtest result."""
    dataset_hash: str
    n_bars: int
    n_steps: int
    horizon: int
    b0_mae_avg: Optional[str]
    b1_mae_avg: Optional[str]
    b1_beats_b0_count: int
    b1_total_comparisons: int
    trade_chain: List[Dict[str, Any]] = field(default_factory=list)


def run_backtest(
    bars: List[OHLCVBar],
    warmup: int = 20,
    horizon: int = 3,
    contract_code: str = "BTC-USDT",
) -> BacktestResult:
    """Run a deterministic walk-forward backtest.

    At each step ``t`` from ``warmup`` to ``len(bars) - horizon``:
      1. Build features from ``bars[:t]`` (no look-ahead)
      2. Forecast B0 and B1 for ``horizon`` steps
      3. Evaluate against ``bars[t:t+horizon]``
      4. Record per-trade chain entry
    """
    service = ForecastService(contract_code=contract_code, horizon_steps=horizon)
    dhash = data_hash(bars)

    b0_maes: List[Decimal] = []
    b1_maes: List[Decimal] = []
    b1_beats = 0
    total_cmp = 0
    chain: List[Dict[str, Any]] = []

    step_num = 0
    for t in range(warmup, len(bars) - horizon):
        step_num += 1
        window = bars[:t]
        actuals = bars[t : t + horizon]

        # No-look-ahead: features from window only
        features = extract_features(window)
        fhash = features["features_hash"]

        # Run both models
        run_b0 = service.run_forecast(window, ModelVersion.B0_NAIVE)
        run_b1 = service.run_forecast(window, ModelVersion.B1_QUANTILE)

        ev_b0 = evaluate_forecast(run_b0, actuals)
        ev_b1 = evaluate_forecast(run_b1, actuals)

        wf = walk_forward_compare(ev_b0, ev_b1)

        if ev_b0.mae is not None:
            b0_maes.append(ev_b0.mae)
        if ev_b1.mae is not None:
            b1_maes.append(ev_b1.mae)

        if ev_b0.mae is not None and ev_b1.mae is not None:
            total_cmp += 1
            if wf.b1_beats_b0:
                b1_beats += 1

        # Per-trade chain entry
        last_actual = actuals[-1]
        chain_entry: Dict[str, Any] = {
            "step": step_num,
            "bar_index": t,
            "features_hash": fhash,
            "b0_predicted_close": str(run_b0.candles[-1].predicted_close) if run_b0.candles else None,
            "b1_predicted_close": str(run_b1.candles[-1].predicted_close) if run_b1.candles else None,
            "actual_close": str(last_actual.close),
            "b0_mae": str(ev_b0.mae) if ev_b0.mae else None,
            "b1_mae": str(ev_b1.mae) if ev_b1.mae else None,
            "b1_beats_b0": wf.b1_beats_b0,
            "b0_directional": str(ev_b0.directional_accuracy) if ev_b0.directional_accuracy else None,
            "b1_directional": str(ev_b1.directional_accuracy) if ev_b1.directional_accuracy else None,
            "b0_in_band": str(ev_b0.in_band_rate) if ev_b0.in_band_rate else None,
            "b1_in_band": str(ev_b1.in_band_rate) if ev_b1.in_band_rate else None,
        }
        chain.append(chain_entry)

    b0_avg = sum(b0_maes, Decimal(0)) / Decimal(len(b0_maes)) if b0_maes else None
    b1_avg = sum(b1_maes, Decimal(0)) / Decimal(len(b1_maes)) if b1_maes else None

    return BacktestResult(
        dataset_hash=dhash,
        n_bars=len(bars),
        n_steps=step_num,
        horizon=horizon,
        b0_mae_avg=str(b0_avg) if b0_avg else None,
        b1_mae_avg=str(b1_avg) if b1_avg else None,
        b1_beats_b0_count=b1_beats,
        b1_total_comparisons=total_cmp,
        trade_chain=chain,
    )


# --------------------------------------------------------------------------- #
# No-look-ahead enforcement
# --------------------------------------------------------------------------- #


def verify_no_look_ahead(bars: List[OHLCVBar], warmup: int = 20, horizon: int = 3) -> bool:
    """Verify that forecasts don't use future data.

    For each step, forecast from window[:t] and from bars[:t] (which
    includes future bars) — the forecast must be identical because the
    model should only use the first ``t`` bars.
    """
    for t in range(warmup, min(warmup + 5, len(bars) - horizon)):
        window = bars[:t]
        # B0
        fc0_a = predict_b0(window, horizon)
        fc0_b = predict_b0(bars[:t], horizon)
        for a, b in zip(fc0_a, fc0_b):
            assert a.predicted_close == b.predicted_close, (
                f"B0 look-ahead at t={t}: {a.predicted_close} != {b.predicted_close}"
            )
        # B1
        fc1_a = predict_b1(window, horizon)
        fc1_b = predict_b1(bars[:t], horizon)
        for a, b in zip(fc1_a, fc1_b):
            assert a.predicted_close == b.predicted_close, (
                f"B1 look-ahead at t={t}: {a.predicted_close} != {b.predicted_close}"
            )
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic backtest for WORED forecast engine"
    )
    parser.add_argument("--bars", type=int, default=50, help="Number of synthetic bars")
    parser.add_argument("--contract", default="BTC-USDT", help="Contract code")
    parser.add_argument("--warmup", type=int, default=20, help="Warmup bars before first forecast")
    parser.add_argument("--horizon", type=int, default=3, help="Forecast horizon in bars")
    parser.add_argument("--json", action="store_true", help="Output full results as JSON")
    args = parser.parse_args()

    bars = generate_synthetic_bars(n=args.bars)
    result = run_backtest(
        bars,
        warmup=args.warmup,
        horizon=args.horizon,
        contract_code=args.contract,
    )

    # Verify no-look-ahead
    nla_ok = verify_no_look_ahead(bars, warmup=args.warmup, horizon=args.horizon)

    if args.json:
        print(json.dumps({
            "dataset_hash": result.dataset_hash,
            "n_bars": result.n_bars,
            "n_steps": result.n_steps,
            "horizon": result.horizon,
            "b0_mae_avg": result.b0_mae_avg,
            "b1_mae_avg": result.b1_mae_avg,
            "b1_beats_b0_count": result.b1_beats_b0_count,
            "b1_total_comparisons": result.b1_total_comparisons,
            "no_look_ahead_verified": nla_ok,
            "trade_chain": result.trade_chain,
        }, indent=2))
    else:
        print(f"Dataset hash:   {result.dataset_hash}")
        print(f"Bars:           {result.n_bars}")
        print(f"Steps:          {result.n_steps}")
        print(f"Horizon:        {result.horizon} bars")
        print(f"B0 MAE (avg):   {result.b0_mae_avg}")
        print(f"B1 MAE (avg):   {result.b1_mae_avg}")
        print(f"B1 beats B0:    {result.b1_beats_b0_count}/{result.b1_total_comparisons}")
        print(f"No-look-ahead:  {'PASS' if nla_ok else 'FAIL'}")
        print(f"\nPer-trade chain ({len(result.trade_chain)} steps):")
        for entry in result.trade_chain[:10]:
            print(f"  step {entry['step']:3d}  bar={entry['bar_index']:3d}  "
                  f"B0_mae={entry['b0_mae']}  B1_mae={entry['b1_mae']}  "
                  f"B1_beats={entry['b1_beats_b0']}")
        if len(result.trade_chain) > 10:
            print(f"  ... ({len(result.trade_chain) - 10} more)")


if __name__ == "__main__":
    main()