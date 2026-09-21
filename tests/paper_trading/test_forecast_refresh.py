"""Session forecast auto-refresh tests (Trader review, item B2).

Covers the pure policy (``decide_refresh``) and one DB cycle with a stub pool.
No live Postgres/Redis/AI: the enqueue step is a recorded callback.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

import forecast_refresh  # noqa: E402
from forecast_refresh import AUTO_SOURCE, decide_refresh, run_forecast_refresh_cycle  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def decide(**overrides):
    args = dict(
        now=NOW,
        last_target=NOW + timedelta(hours=2),
        in_flight=0,
        auto_requests_24h=0,
        max_per_day=6,
        last_auto_at=None,
        cooldown_seconds=900,
        enabled=True,
    )
    args.update(overrides)
    return decide_refresh(**args)


class TestDecideRefresh:
    def test_skips_when_disabled(self):
        d = decide(enabled=False, last_target=None)
        assert (d.should_run, d.reason) == (False, "disabled")

    def test_skips_while_a_job_is_in_flight(self):
        d = decide(in_flight=1, last_target=None)
        assert (d.should_run, d.reason) == (False, "job_in_flight")

    def test_skips_when_daily_budget_spent(self):
        d = decide(auto_requests_24h=6, max_per_day=6, last_target=None)
        assert (d.should_run, d.reason) == (False, "budget_exhausted")

    def test_skips_during_cooldown(self):
        d = decide(last_auto_at=NOW - timedelta(seconds=300), last_target=None)
        assert (d.should_run, d.reason) == (False, "cooldown")

    def test_enqueues_when_no_forecast_exists(self):
        d = decide(last_target=None)
        assert (d.should_run, d.reason) == (True, "no_forecast")

    def test_enqueues_when_latest_target_is_in_the_past(self):
        """The exact regression: a completed forecast whose horizon elapsed."""
        d = decide(last_target=NOW - timedelta(minutes=30))
        assert (d.should_run, d.reason) == (True, "expired")

    def test_covers_a_future_step_and_waits(self):
        d = decide(last_target=NOW + timedelta(minutes=5))
        assert (d.should_run, d.reason) == (False, "covered")

    def test_naive_db_timestamps_are_treated_as_utc(self):
        naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        assert decide(last_target=naive).reason == "expired"


class _StubConn:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append((sql.split()[0], args))
        return self._rows.pop(0) if self._rows else None


class _StubPool:
    def __init__(self, rows):
        self.conn = _StubConn(rows)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _cycle(pool, calls, **overrides):
    async def enqueue(symbol, horizon_steps, base_timeframe, depth):
        calls.append({"symbol": symbol, "horizon_steps": horizon_steps,
                      "base_timeframe": base_timeframe, "depth": depth})
        return {"id": 136, "status": "pending"}

    kwargs = dict(symbol="btcusdt", horizon_hours=4, base_timeframe="60min",
                  depth=3, max_per_day=6, cooldown_seconds=900, enabled=True, now=NOW)
    kwargs.update(overrides)
    return run_forecast_refresh_cycle(pool, enqueue, **kwargs)


class TestRefreshCycle:
    def _rows(self, last_target):
        # latest completed target, in-flight count, 24h auto budget
        return [
            {"id": 135, "last_target": last_target} if last_target else None,
            {"n": 0},
            {"n": 0, "last_at": None},
        ]

    def test_expired_forecast_is_replenished(self):
        calls = []
        pool = _StubPool(self._rows(NOW - timedelta(hours=20)))
        reason = asyncio.run(_cycle(pool, calls))
        assert reason == "enqueued"
        assert calls == [{"symbol": "btcusdt", "horizon_steps": 4,
                          "base_timeframe": "60min", "depth": 3}]

    def test_covered_forecast_creates_nothing(self):
        calls = []
        pool = _StubPool(self._rows(NOW + timedelta(hours=1)))
        reason = asyncio.run(_cycle(pool, calls))
        assert reason == "covered"
        assert calls == []

    def test_horizon_is_converted_to_steps_of_the_timeframe(self):
        calls = []
        pool = _StubPool(self._rows(None))
        asyncio.run(_cycle(pool, calls, base_timeframe="4hour", horizon_hours=8))
        assert calls[0]["horizon_steps"] == 2  # 8h / 4h bars
        assert calls[0]["base_timeframe"] == "4hour"

    def test_budget_guard_is_queried_with_the_auto_source(self):
        calls = []
        pool = _StubPool(self._rows(None))
        asyncio.run(_cycle(pool, calls))
        budget_queries = [q for q in pool.conn.queries if q[0] == "SELECT" and q[1]]
        assert any(AUTO_SOURCE in str(args) for _, args in budget_queries)

    def test_missing_row_shapes_degrade_to_no_forecast(self):
        calls = []
        pool = _StubPool([None, None, None])
        reason = asyncio.run(_cycle(pool, calls))
        assert reason == "enqueued"

    def test_timestamp_params_are_naive_utc(self):
        """forecast_requests.created_at is naive UTC; asyncpg rejects aware values."""
        calls = []
        pool = _StubPool(self._rows(None))
        asyncio.run(_cycle(pool, calls))
        budget_args = pool.conn.queries[-1][1]
        since = budget_args[-1]
        assert since.tzinfo is None
        assert since == (NOW - timedelta(hours=24)).replace(tzinfo=None)
        assert budget_args[1] == AUTO_SOURCE


def test_loop_module_surface_exists():
    """app.py wires these names; fail early if the contract drifts."""
    assert callable(forecast_refresh.forecast_refresh_loop)
    assert AUTO_SOURCE == "auto-trader-session"
