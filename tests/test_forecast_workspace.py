"""Unit + API tests for the V3 forecast read-model (P2.1a).

Covers the deterministic core the browser and DB layers will lean on:
  * ТЗ §5 horizon<->period compatibility matrix and exact candle counts;
  * MC-06 line+band rendering — ``has_valid_ohlc=False``, no invented/zero wick;
  * fail-closed "no verified forecast" instead of a made-up path;
  * MC-08 accuracy with a baseline and the N/A guard on a small sample;
  * the ``/api/v3/forecasts`` + ``/api/v3/forecast/{id}`` HTTP contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from forecast_workspace import (
    build_forecast_prediction,
    compute_forecast_accuracy,
    forecast_candle_count,
    is_compatible,
    replay_forecast_holdout,
    router,
    summarize_holdout,
)

INSTR = "htx:linear-swap:BTC-USDT"
BASE_TIME = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _req_row(**over):
    row = {
        "id": 7,
        "symbol": "btcusdt",
        "base_price": "100.0",
        "horizon_hours": 1,
        "base_timeframe": "15min",
        "created_at": BASE_TIME,
        "execution_state": "completed",
        "plan_version": "v3",
        "rationale": "ensemble median",
    }
    row.update(over)
    return row


def _point(step, price, role, run_id, *, high=None, low=None, conf=60.0, chg=1.0, tt_off=None, **over):
    p = {
        "step_index": step,
        "target_time": BASE_TIME + (tt_off if tt_off is not None else timedelta(minutes=15 * (step + 1))),
        "predicted_price": str(price),
        "predicted_high": None if high is None else str(high),
        "predicted_low": None if low is None else str(low),
        "confidence": conf,
        "predicted_change_pct": str(chg),
        "run_id": run_id,
        "agent_role": role,
        "model_id": f"m-{role}",
    }
    p.update(over)
    return p


def _three_role_points(step, prices, **kw):
    bull, bear, arb = prices
    return [
        _point(step, bull, "bull", 100 + step, **kw),
        _point(step, bear, "bear", 200 + step, **kw),
        _point(step, arb, "arbiter", 300 + step, **kw),
    ]


class TestHorizonPeriodMatrix:
    def test_allowed_combos(self):
        for h, p in [
            ("15m", "1m"), ("15m", "5m"), ("15m", "15m"),
            ("1h", "5m"), ("1h", "15m"), ("1h", "1h"),
            ("4h", "15m"), ("4h", "1h"), ("4h", "4h"),
            ("24h", "1h"), ("24h", "4h"), ("24h", "1d"),
        ]:
            assert is_compatible(h, p), (h, p)

    def test_rejected_combos(self):
        for h, p in [("15m", "1h"), ("24h", "15m"), ("1h", "1m"), ("4h", "5m"), ("1h", "1d")]:
            assert not is_compatible(h, p), (h, p)

    def test_exact_candle_counts(self):
        assert forecast_candle_count("24h", "1h") == 24
        assert forecast_candle_count("15m", "5m") == 3
        assert forecast_candle_count("1h", "15m") == 4
        assert forecast_candle_count("4h", "1h") == 4
        assert forecast_candle_count("24h", "1d") == 1
        assert forecast_candle_count("15m", "1h") is None  # incompatible


class TestBuildLineBand:
    def test_no_invented_wick_line_plus_band_MC06(self):
        points = _three_role_points(0, (100, 90, 95)) + _three_role_points(1, (110, 100, 105))
        out = build_forecast_prediction(
            instrument_key=INSTR, contract_code="BTC-USDT",
            request_row=_req_row(), points=points, horizon="1h", period="15m",
            now=BASE_TIME,
        )
        assert out["error_code"] is None
        assert out["execution_state"] == "completed"
        assert out["forecast_id"] == "7"
        assert out["expected_candles"] == 4
        assert out["coverage"] == 0.5  # only 2 of 4 steps present
        iv = out["intervals"]
        assert len(iv) == 2
        for c in iv:
            # ensemble path is a band, NOT a proven candle (MC-06)
            assert c["has_valid_ohlc"] is False
            assert c["predicted_high"] is None
            assert c["predicted_low"] is None
            assert c["band_low"] is not None and c["band_high"] is not None
            assert float(c["band_low"]) > 0 and float(c["band_high"]) > 0
        # median of {90,95,100} = 95
        assert float(iv[0]["predicted_close"]) == pytest.approx(95.0)
        # first bar opens at the base price; second opens at the prior close
        assert float(iv[0]["predicted_open"]) == pytest.approx(100.0)
        assert float(iv[1]["predicted_open"]) == pytest.approx(95.0)

    def test_missing_wick_never_zeroes_the_band(self):
        # high/low NULL in storage → band must reflect the votes, not a 0 wick
        points = _three_role_points(0, (100, 90, 95), high=None, low=None)
        out = build_forecast_prediction(
            instrument_key=INSTR, contract_code="BTC-USDT",
            request_row=_req_row(), points=points, horizon="1h", period="15m", now=BASE_TIME,
        )
        c = out["intervals"][0]
        assert c["has_valid_ohlc"] is False
        assert float(c["band_low"]) == pytest.approx(90.0)
        assert float(c["band_high"]) == pytest.approx(100.0)

    def test_incompatible_returns_reason(self):
        out = build_forecast_prediction(
            instrument_key=INSTR, contract_code="BTC-USDT",
            request_row=_req_row(), points=_three_role_points(0, (100, 90, 95)),
            horizon="15m", period="1h", now=BASE_TIME,
        )
        assert out["error_code"] == "incompatible_horizon_period"
        assert out["intervals"] == []

    def test_no_data_fail_closed(self):
        out = build_forecast_prediction(
            instrument_key=INSTR, contract_code="BTC-USDT",
            request_row=None, points=[], horizon="1h", period="15m", now=BASE_TIME,
        )
        assert out["error_code"] == "no_verified_forecast"
        assert out["intervals"] == []
        assert "Нет проверенного прогноза" in out["message"]


class TestAccuracyMC08:
    def test_insufficient_sample_is_na(self):
        pts = [_point(0, 100, "bull", 1, actual_price="101", price_error_pct="1.0")]
        acc = compute_forecast_accuracy(pts)
        assert acc["verdict"] == "N/A"
        assert acc["mape"] is None and acc["skill_vs_baseline"] is None
        assert acc["reason"] == "insufficient_sample"

    def test_enough_sample_computes_metrics(self):
        pts = []
        for i in range(6):
            pts.append(_point(
                i, 100, "bull", i + 1,
                actual_price="101", price_error_pct=str(2.0 + i), skill_vs_baseline="0.5",
                in_range=True,
            ))
        acc = compute_forecast_accuracy(pts)
        assert acc["sample_n"] == 6
        assert acc["mape"] == pytest.approx(4.5)  # mean of 2..7
        assert acc["coverage"] == pytest.approx(1.0)
        assert acc["skill_vs_baseline"] == pytest.approx(0.5)
        assert acc["verdict"] == "beats_baseline"


def _settled_steps(n):
    """n steps, 3 roles each; median price = 95, realised fact = 95 (in band,
    above the flat base_price=100 baseline → the model should beat persistence)."""
    pts = []
    for i in range(n):
        pts.extend(_three_role_points(i, (100, 90, 95), actual_price="95"))
    return pts


class TestHoldoutReplayMC08:
    def test_deterministic_beats_baseline(self):
        h = replay_forecast_holdout(_settled_steps(6), "100.0")
        assert h["replay"] == "deterministic"
        assert h["baseline_method"] == "persistence_base_price"
        assert h["sample_n"] == 6
        assert h["mape"] == pytest.approx(0.0)          # median == actual
        assert h["baseline_mape"] == pytest.approx(5.2632, abs=1e-3)  # |100-95|/95
        assert h["skill_vs_baseline"] == pytest.approx(1.0)
        assert h["coverage"] == pytest.approx(1.0)       # 95 inside q10..q90
        assert h["verdict"] == "beats_baseline"

    def test_insufficient_sample_is_na(self):
        h = replay_forecast_holdout(_settled_steps(3), "100.0")
        assert h["verdict"] == "N/A"
        assert h["reason"] == "insufficient_sample"
        assert h["mape"] is None and h["coverage"] is None
        assert h["sample_n"] == 3

    def test_missing_realised_facts_is_na(self):
        # unsettled (no actual_price) → no realised sample at all
        pts = _three_role_points(0, (100, 90, 95))
        h = replay_forecast_holdout(pts, "100.0")
        assert h["verdict"] == "N/A"
        assert h["sample_n"] == 0

    def test_no_baseline_when_base_price_unusable(self):
        h = replay_forecast_holdout(_settled_steps(6), "0")
        assert h["verdict"] == "N/A"
        assert h["reason"] == "no_baseline"

    def test_worse_than_baseline_when_prediction_off(self):
        # median 95 but actual far (120): model error |95-120|/120=20.83%,
        # baseline 100 error |100-120|/120=16.67% → model worse than persistence
        pts = []
        for i in range(6):
            pts.extend(_three_role_points(i, (100, 90, 95), actual_price="120"))
        h = replay_forecast_holdout(pts, "100.0")
        assert h["sample_n"] == 6
        assert h["skill_vs_baseline"] is not None and h["skill_vs_baseline"] < 0
        assert h["verdict"] == "worse_than_baseline"

    def test_summarize_empty_is_na(self):
        assert summarize_holdout([])["verdict"] == "N/A"


# ── endpoints ─────────────────────────────────────────────────────────────────


class FakePool:
    def __init__(self, requests, points):
        self._requests = requests
        self._points = points

    async def fetch(self, sql, *args, **kwargs):
        if "forecast_requests" in sql:
            return list(self._requests)
        if "forecast_points" in sql:
            return list(self._points)
        return []

    async def fetchrow(self, sql, *args, **kwargs):
        if "forecast_requests" in sql:
            return self._requests[0] if self._requests else None
        return None


def _app(pool):
    app = FastAPI()
    app.include_router(router)
    app.state.pg_pool = pool
    return app


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.mark.asyncio
class TestForecastEndpoints:
    async def test_list_ok(self):
        points = _three_role_points(0, (100, 90, 95)) + _three_role_points(1, (110, 100, 105))
        app = _app(FakePool([_req_row()], points))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts?instrument_key={INSTR}&horizon=1h&period=15m")
            assert r.status_code == 200
            body = r.json()
            assert body["expected_candles"] == 4
            assert body["forecasts"][0]["intervals"][0]["has_valid_ohlc"] is False

    async def test_incompatible_period_400(self):
        app = _app(FakePool([_req_row()], []))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts?instrument_key={INSTR}&horizon=15m&period=1h")
            assert r.status_code == 400
            assert r.json()["detail"]["reason_code"] == "incompatible_horizon_period"
            assert "1m" in r.json()["detail"]["suggested_periods"]

    async def test_unsupported_horizon_400(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts?instrument_key={INSTR}&horizon=8h&period=1h")
            assert r.status_code == 400
            assert r.json()["detail"]["reason_code"] == "unsupported_horizon"

    async def test_spot_key_404(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get("/api/v3/forecasts?instrument_key=htx:spot:BTC-USDT&horizon=1h&period=15m")
            assert r.status_code == 404
            assert r.json()["detail"]["reason_code"] == "instrument_not_registered"

    async def test_no_pool_503(self):
        app = _app(None)
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts?instrument_key={INSTR}&horizon=1h&period=15m")
            assert r.status_code == 503

    async def test_empty_store_fail_closed(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts?instrument_key={INSTR}&horizon=1h&period=15m")
            assert r.status_code == 200
            assert r.json()["forecasts"][0]["error_code"] == "no_verified_forecast"

    async def test_detail_includes_accuracy(self):
        pts = [
            _point(i, 100, "bull", i + 1, actual_price="101", price_error_pct="2.0",
                   skill_vs_baseline="0.5", in_range=True)
            for i in range(6)
        ]
        app = _app(FakePool([_req_row(contract_code="BTC-USDT", base_timeframe="15m")], pts))
        async for c in _client(app):
            r = await c.get("/api/v3/forecast/7")
            assert r.status_code == 200
            body = r.json()
            assert body["forecast_id"] == "7"
            assert body["accuracy"]["sample_n"] == 6
            assert body["accuracy"]["verdict"] == "beats_baseline"

    async def test_detail_includes_holdout(self):
        pts = _three_role_points(0, (100, 90, 95), actual_price="95")
        app = _app(FakePool([_req_row(contract_code="BTC-USDT", base_price="100.0", base_timeframe="15m")], pts))
        async for c in _client(app):
            r = await c.get("/api/v3/forecast/7")
            assert r.status_code == 200
            h = r.json()["holdout"]
            # only 1 realised step < MIN → honest N/A, never a spurious number
            assert h["verdict"] == "N/A"
            assert h["reason"] == "insufficient_sample"
            assert h["replay"] == "deterministic"

    async def test_accuracy_endpoint_numeric(self):
        pts = _settled_steps(6)
        app = _app(FakePool([_req_row(base_price="100.0")], pts))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts/accuracy?instrument_key={INSTR}&horizon=1h&period=15m")
            assert r.status_code == 200
            body = r.json()
            assert body["forecasts_evaluated"] == 1
            assert body["holdout"]["verdict"] == "beats_baseline"
            assert body["holdout"]["sample_n"] == 6

    async def test_accuracy_endpoint_incompatible_400(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts/accuracy?instrument_key={INSTR}&horizon=15m&period=1h")
            assert r.status_code == 400
            assert r.json()["detail"]["reason_code"] == "incompatible_horizon_period"

    async def test_accuracy_endpoint_unsupported_horizon_400(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts/accuracy?instrument_key={INSTR}&horizon=8h&period=1h")
            assert r.status_code == 400
            assert r.json()["detail"]["reason_code"] == "unsupported_horizon"

    async def test_accuracy_endpoint_spot_404(self):
        app = _app(FakePool([], []))
        async for c in _client(app):
            r = await c.get("/api/v3/forecasts/accuracy?instrument_key=htx:spot:BTC-USDT&horizon=1h&period=15m")
            assert r.status_code == 404

    async def test_accuracy_endpoint_no_pool_503(self):
        app = _app(None)
        async for c in _client(app):
            r = await c.get(f"/api/v3/forecasts/accuracy?instrument_key={INSTR}&horizon=1h&period=15m")
            assert r.status_code == 503
