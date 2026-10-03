"""Unit + API tests for the V3 forecast *command* (G2, ТЗ §55/§57/§59/§47).

Everything here runs against a recording fake pool: no Postgres, no HTX, no
mounted ``webui.app``.  The suite is deliberately organised around the promises
``forecast_command_v3`` makes:

  * **Pre-request guards** — the horizon↔period matrix and the step budget are
    answered *before* any history is read (ТЗ §59), and an unregistered/spot key
    is a 404 that never falls back to spot (ТЗ §51).
  * **Fact-history integrity** — insufficient history, a gapped series, a foreign
    venue row or a broken candle fail closed with 503 + ``last_good_end_at``; the
    command must never quietly shorten or substitute the series.
  * **Acknowledgement semantics** — 202 means ``accepted``, never ``completed``
    (ТЗ §47), and the base is the last *closed* candle (ТЗ §57).
  * **Idempotency** — same key + same intent replays the stored request; same key
    + different intent is a 409.
  * **Status contract** — normalized execution state, ``terminal`` and
    ``forecast_id`` only once the domain actually says completed.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request

import forecast_command_v3 as cmd
from forecast_queue import SCHEMA as FORECAST_QUEUE_SCHEMA
from forecast_schema import PREDICTION_TABLES_SQL
from forecast_command_v3 import router

INSTR = "htx:linear-swap:BTC-USDT"
SPOT_KEY = "htx:spot:BTC-USDT"
BASE_TIME = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)


# ── fake perpetual store ──────────────────────────────────────────────────────


def _rows(count: int, *, timeframe: str, step_seconds: int, source: str = "htx-perp-ws",
          venue: str = "htx", start: datetime = BASE_TIME, skip_at: int | None = None,
          bad_ohlc_at: int | None = None) -> list[dict[str, Any]]:
    """Build closed candles, returned newest-first like ``ORDER BY ... DESC``.

    ``skip_at`` models a real outage: that interval is simply absent, so the
    series has a hole rather than an over-long candle.
    """
    rows: list[dict[str, Any]] = []
    for i in range(count):
        if i == skip_at:
            continue
        open_time = start + timedelta(seconds=step_seconds * i)
        close_time = open_time + timedelta(seconds=step_seconds)
        base = 100 + i
        high, low = base + 1, base - 1
        if bad_ohlc_at is not None and i == bad_ohlc_at:
            high = base - 5  # violates OHLC containment
        rows.append({
            "open_time": open_time,
            "close_time": close_time,
            "open": str(base),
            "high": str(high),
            "low": str(low),
            "close": str(base + 0.5),
            "volume": "12.5",
            "source": source,
            "venue": venue,
            "timeframe": timeframe,
        })
    rows.reverse()
    return rows


class _Ctx:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Connection:
    def __init__(self, pool: "FakePool") -> None:
        self._pool = pool

    def transaction(self) -> _Ctx:
        return _Ctx(self)

    async def execute(self, sql: str, *args: Any) -> None:
        self._pool.statements.append(sql)
        if "INSERT INTO forecast_jobs" in sql:
            self._pool.jobs[int(args[0])] = {
                "payload": json.loads(args[1]),
                "idempotency_key": args[2],
            }
            if args[2] is not None:
                self._pool.by_key[str(args[2])] = int(args[0])

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "FROM forecast_jobs" in sql:
            request_id = self._pool.by_key.get(str(args[0]))
            if request_id is None:
                return None
            job = self._pool.jobs[request_id]
            return {
                "request_id": request_id,
                "payload": job["payload"],
                "status": self._pool.request_status,
                "execution_state": self._pool.request_execution_state,
            }
        if "INSERT INTO forecast_requests" in sql:
            self._pool.writes.append((sql, args))
            self._pool.next_id += 1
            self._pool.inserts.append(args)
            return {"id": self._pool.next_id}
        return None


class FakePool:
    """Records every query so a test can prove a guard fired *before* any read."""

    def __init__(self, rows: list[dict[str, Any]] | None = None,
                 status_row: dict[str, Any] | None = None) -> None:
        self.rows = rows if rows is not None else _rows(36, timeframe="15min", step_seconds=900)
        self.status_row = status_row
        self.jobs: dict[int, dict[str, Any]] = {}
        self.by_key: dict[str, int] = {}
        self.inserts: list[tuple] = []
        self.writes: list[tuple[str, tuple]] = []
        self.reads: list[tuple[str, tuple]] = []
        self.history_queries: list[tuple] = []
        self.statements: list[str] = []
        self.next_id = 500
        self.fail_history = False
        self.request_status = "pending"
        self.request_execution_state = "queued"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if cmd.HISTORY_TABLE in sql:
            self.history_queries.append((sql, args))
            if self.fail_history:
                raise RuntimeError("connection reset by peer")
            timeframe, limit = args[2], int(args[4])
            return [r for r in self.rows if r.get("timeframe") == timeframe][:limit]
        return []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "FROM forecast_requests" in sql:
            self.reads.append((sql, args))
            return self.status_row
        return None

    def acquire(self) -> _Ctx:
        return _Ctx(_Connection(self))


def _app(pool: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.pg_pool = pool
    return app


class _Client:
    def __init__(self, app: FastAPI) -> None:
        self._transport = httpx.ASGITransport(app=app)

    async def __aenter__(self) -> httpx.AsyncClient:
        self._cm = httpx.AsyncClient(transport=self._transport, base_url="http://t")
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc: Any) -> bool:
        return await self._cm.__aexit__(*exc)


async def _post(client: httpx.AsyncClient, body: dict[str, Any], key: str | None = None):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return await client.post("/api/v3/forecasts", json=body, headers=headers)


def _detail(response: httpx.Response) -> dict[str, Any]:
    return response.json()["detail"]


def _status_row(**over: Any) -> dict[str, Any]:
    row = {
        "id": 777, "status": "pending", "execution_state": "queued", "evaluation_state": None,
        "failure_code": None, "as_of": None, "valid_until": None, "deadline_at": None,
        "job_error_code": None, "job_state": "queued",
        # V3 identity columns (additive migration 20260930_forecast_request_v3_identity.sql)
        "instrument_key": INSTR, "period": "15m", "horizon": "1h",
        "base_snapshot_id": f"{INSTR}|15m|2026-09-28T09:00:00+00:00|htx-perp-ws",
        "market_data_source": "trader_v1_perp_candles",
    }
    row.update(over)
    return row


# ── pre-request guards ────────────────────────────────────────────────────────


class TestPreRequestGuards:
    async def test_incompatible_pair_rejected_before_history_read(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "1h", "horizon": "15m"})
        assert r.status_code == 400
        d = _detail(r)
        assert d["reason_code"] == "incompatible_horizon_period"
        assert "1m" in d["suggested_periods"]
        assert d["exact_candles_if_compatible"] is None
        # ТЗ §59: the answer arrives before any candle query is issued
        assert pool.history_queries == []
        assert pool.inserts == []

    async def test_horizon_step_budget_exceeded(self, monkeypatch):
        monkeypatch.setattr(cmd, "MAX_HORIZON_STEPS", 2)
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 400
        d = _detail(r)
        assert d["reason_code"] == "horizon_step_budget_exceeded"
        assert d["horizon_steps"] == 4 and d["max_horizon_steps"] == 2
        assert pool.inserts == []

    async def test_depth_out_of_range_400(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            bad_zero = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h", "depth": 0})
            bad_str = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h", "depth": "3"})
            bad_bool = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h", "depth": True})
        for r in (bad_zero, bad_str, bad_bool):
            assert r.status_code == 400
            assert _detail(r)["reason_code"] == "invalid_depth"

    async def test_body_must_be_object(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await c.post("/api/v3/forecasts", json=[1, 2, 3])
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "invalid_body"

    async def test_instrument_key_required(self):
        async with _Client(_app(FakePool())) as c:
            r = await _post(c, {"period": "15m", "horizon": "1h"})
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "instrument_key_required"

    async def test_spot_key_is_404_and_never_substituted(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": SPOT_KEY, "period": "15m", "horizon": "1h"})
        assert r.status_code == 404
        assert _detail(r)["reason_code"] == "instrument_not_registered"
        assert pool.history_queries == []

    async def test_unsupported_period_for_instrument(self):
        async with _Client(_app(FakePool())) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "99m", "horizon": "1h"})
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "unsupported_period"

    async def test_unsupported_horizon(self):
        async with _Client(_app(FakePool())) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "1h", "horizon": "8h"})
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "unsupported_horizon"

    async def test_no_pool_is_503(self):
        async with _Client(_app(None)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        assert _detail(r)["reason_code"] == "history_unavailable"


# ── fact-history integrity ────────────────────────────────────────────────────


class TestHistoryIntegrity:
    async def test_insufficient_history_fails_closed(self):
        pool = FakePool(_rows(10, timeframe="15min", step_seconds=900))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        d = _detail(r)
        assert d["reason_code"] == "insufficient_history"
        assert d["required_candles"] == cmd.MIN_HISTORY_CANDLES
        assert d["available_candles"] == 10
        assert pool.inserts == []

    async def test_gap_is_reported_not_hidden(self):
        # 37 bars with one interval missing → exactly the 36 requested, but broken
        pool = FakePool(_rows(37, timeframe="15min", step_seconds=900, skip_at=20))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        d = _detail(r)
        assert d["reason_code"] == "missing_intervals"
        assert d["gaps"], "the missing interval must be named, not silently skipped"
        assert d["last_good_end_at"] is not None
        assert pool.inserts == []

    async def test_foreign_venue_row_is_rejected(self):
        pool = FakePool(_rows(36, timeframe="15min", step_seconds=900, venue="bybit"))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        assert _detail(r)["reason_code"] == "venue_mixture"

    async def test_broken_candle_is_rejected(self):
        pool = FakePool(_rows(36, timeframe="15min", step_seconds=900, bad_ohlc_at=5))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        assert _detail(r)["reason_code"] == "invalid_candle_ohlc"

    async def test_history_query_failure_is_503_not_spot_fallback(self):
        pool = FakePool()
        pool.fail_history = True
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 503
        d = _detail(r)
        assert d["reason_code"] == "history_query_failed"
        assert d["last_good_end_at"] is None

    async def test_five_minute_period_reads_one_minute_perp_rows(self):
        pool = FakePool(_rows(180, timeframe="1min", step_seconds=60))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "5m", "horizon": "15m"})
        assert r.status_code == 202, r.text
        sql, args = pool.history_queries[0]
        assert "trader_v1_perp_candles" in sql
        assert args[1] == "BTC-USDT"
        assert args[2] == "1min"            # derived from the stored base timeframe
        assert args[4] == cmd.MIN_HISTORY_CANDLES * 5  # 5m buckets need 5x base rows


# ── accepted command semantics ────────────────────────────────────────────────


class TestAcceptedCommand:
    async def test_two_oh_two_is_acknowledgement_not_result(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 202
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert body["accepted"] is True
        assert body["completed"] is False          # ТЗ §47
        assert body["execution_state"] == "queued"
        assert body["idempotent_replay"] is False
        assert body["forecast_candle_count"] == 4  # 1h at 15m
        assert body["market_data_source"] == "trader_v1_perp_candles"
        assert body["result_path"] == f"/api/v3/forecast/{body['request_id']}"

    async def test_base_is_last_closed_candle(self):
        pool = FakePool(_rows(36, timeframe="15min", step_seconds=900))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        body = r.json()
        # the newest 15m bar of the fixture closes at 09:00 UTC with close 135.5
        expected_end = (BASE_TIME + timedelta(minutes=15 * 36)).isoformat()
        assert body["base_time"] == expected_end
        assert body["base_price"] == "135.5"
        assert body["base_snapshot_id"].startswith(f"{INSTR}|15m|{expected_end}|")
        # the stored request carries the same base, not a live ticker price
        assert pool.inserts[-1][4] == pytest.approx(135.5)
        job = pool.jobs[int(body["request_id"])]["payload"]
        assert job["v3"]["base_price"] == "135.5"
        assert job["base_price"] == pytest.approx(135.5)

    async def test_job_payload_keeps_exact_v3_parameters(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h", "depth": 5})
        body = r.json()
        job = pool.jobs[int(body["request_id"])]["payload"]
        assert job["symbol"] == "btcusdt"
        assert job["base_timeframe"] == "15min"
        assert job["horizon_steps"] == 4
        assert job["depth"] == 5
        assert job["source"] == cmd.COMMAND_SOURCE
        v3 = job["v3"]
        assert v3["schema"] == "ForecastRequestV3"
        assert (v3["instrument_key"], v3["period"], v3["horizon"]) == (INSTR, "15m", "1h")
        assert v3["horizon_steps"] == 4 and v3["forecast_candle_count"] == 4
        assert v3["market_data_source"] == "trader_v1_perp_candles"

    async def test_legacy_horizon_hours_mapping_is_documented_lossy(self):
        pool = FakePool(_rows(cmd.MIN_HISTORY_CANDLES, timeframe="1min", step_seconds=60))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "1m", "horizon": "15m"})
        assert r.status_code == 202
        # forecast_requests.horizon_hours is an integer hour column: a 15m horizon
        # floors to 1 there, while the exact step count lives in the job payload.
        assert pool.inserts[-1][1] == 1
        job = pool.jobs[int(r.json()["request_id"])]["payload"]
        assert job["v3"]["horizon"] == "15m" and job["horizon_steps"] == 15

    async def test_request_is_enqueued_within_one_transaction(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        request_id = int(r.json()["request_id"])
        assert len(pool.inserts) == 1
        assert request_id in pool.jobs
        assert any("INSERT INTO forecast_jobs" in s for s in pool.statements)
        assert any("UPDATE forecast_requests SET execution_state='queued'" in s for s in pool.statements)

    async def test_default_period_and_horizon_are_the_workspace_pair(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR})
        assert r.status_code == 202
        assert (r.json()["period"], r.json()["horizon"]) == ("15m", "1h")

    async def test_stored_row_carries_v3_identity(self):
        """Additive DDL payoff: the *row*, not only the job payload, proves identity."""
        pool = FakePool(_rows(180, timeframe="1min", step_seconds=60))
        async with _Client(_app(pool)) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "5m", "horizon": "15m"})
        assert r.status_code == 202
        body = r.json()
        args = pool.inserts[-1]
        # $9..$14 of the INSERT are the additive V3 columns
        assert args[8] == INSTR
        assert args[9] == "5m"
        assert args[10] == "15m"
        assert args[11] == 3                        # horizon_steps == exact candle count
        assert args[12] == body["base_snapshot_id"]
        assert args[13] == "trader_v1_perp_candles"

    async def test_session_owner_is_recorded_on_the_request(self):
        """``requested_by`` comes from the web session when one exists (audit)."""
        from starlette.middleware.sessions import SessionMiddleware

        pool = FakePool()
        app = _app(pool)
        app.add_middleware(SessionMiddleware, secret_key="test-only-secret")

        async def _sessioned(request: Request) -> Any:
            request.scope["session"]["username"] = "captain"
            return await cmd.create_forecast(request)

        app.post("/sessioned/forecasts")(_sessioned)

        async with _Client(app) as c:
            r = await c.post("/sessioned/forecasts",
                             json={"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert r.status_code == 202
        # INSERT ... requested_by is the 7th bind parameter
        assert pool.inserts[-1][6] == "captain"
        assert pool.jobs[int(r.json()["request_id"])]["payload"]["requested_by"] == "captain"


# ── idempotency ───────────────────────────────────────────────────────────────


class TestIdempotency:
    async def test_same_key_same_intent_replays_original(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            first = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"}, key="K-1")
            stored = pool.jobs[int(first.json()["request_id"])]["payload"]
            stored["v3"]["base_price"] = "999.5"      # pretend the original base
            stored["v3"]["base_time"] = "2026-09-28T08:45:00+00:00"
            second = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"}, key="K-1")
        assert first.status_code == 202
        assert second.status_code == 202
        body = second.json()
        assert body["idempotent_replay"] is True
        assert body["request_id"] == first.json()["request_id"]
        assert len(pool.inserts) == 1                 # no duplicate request row
        # a replay must echo the stored base, not a re-derived one
        assert body["base_price"] == "999.5"
        assert body["base_time"] == "2026-09-28T08:45:00+00:00"

    async def test_same_key_different_intent_is_409(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"}, key="K-2")
            conflict = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "4h"}, key="K-2")
        assert conflict.status_code == 409
        d = _detail(conflict)
        assert d["reason_code"] == "idempotency_conflict"
        assert len(pool.inserts) == 1

    async def test_advisory_lock_taken_before_replay_lookup(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"}, key="K-3")
        assert any("pg_advisory_xact_lock" in s for s in pool.statements)

    async def test_malformed_key_is_400(self):
        async with _Client(_app(FakePool())) as c:
            r = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"}, key="bad key!!")
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "invalid_idempotency_key"

    async def test_without_key_each_post_creates_a_request(self):
        pool = FakePool()
        async with _Client(_app(pool)) as c:
            a = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
            b = await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        assert a.json()["request_id"] != b.json()["request_id"]
        assert len(pool.inserts) == 2


# ── status contract ───────────────────────────────────────────────────────────


class TestRequestStatus:
    async def test_queued_is_not_terminal_and_has_no_forecast_id(self):
        pool = FakePool(status_row=_status_row())
        async with _Client(_app(pool)) as c:
            r = await c.get("/api/v3/forecasts/requests/777")
        assert r.status_code == 200
        body = r.json()
        assert body["execution_state"] == "queued"
        assert body["completed"] is False
        assert body["terminal"] is False
        assert body["forecast_id"] is None
        assert body["result_path"] is None

    async def test_completed_exposes_forecast_id_and_result_path(self):
        row = _status_row(status="completed", execution_state="completed", evaluation_state="evaluated",
                          as_of=datetime(2026, 9, 28, 9, 0))
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["completed"] is True
        assert body["terminal"] is True
        assert body["forecast_id"] == "777"
        assert body["result_path"] == "/api/v3/forecast/777"
        assert body["as_of"] == "2026-09-28T09:00:00+00:00"

    async def test_failed_is_terminal_with_failure_code(self):
        row = _status_row(status="failed", execution_state=None, failure_code=None,
                          job_error_code="ProviderExhausted")
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["execution_state"] == "failed"     # legacy status mapping
        assert body["terminal"] is True
        assert body["completed"] is False
        assert body["failure_code"] == "ProviderExhausted"
        assert body["forecast_id"] is None

    async def test_expired_is_terminal(self):
        row = _status_row(status="failed", execution_state="expired", failure_code="deadline_exceeded")
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["execution_state"] == "expired"
        assert body["terminal"] is True

    async def test_running_without_completion_is_not_result(self):
        row = _status_row(status="active", execution_state=None, job_state="running")
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["execution_state"] == "running"
        assert body["terminal"] is False and body["completed"] is False

    async def test_unknown_request_404(self):
        async with _Client(_app(FakePool(status_row=None))) as c:
            r = await c.get("/api/v3/forecasts/requests/999999")
        assert r.status_code == 404
        assert _detail(r)["reason_code"] == "request_not_found"

    async def test_non_numeric_request_id_400(self):
        async with _Client(_app(FakePool())) as c:
            r = await c.get("/api/v3/forecasts/requests/abc")
        assert r.status_code == 400
        assert _detail(r)["reason_code"] == "invalid_request_id"

    async def test_status_never_reads_candles(self):
        pool = FakePool(status_row=_status_row())
        async with _Client(_app(pool)) as c:
            await c.get("/api/v3/forecasts/requests/777")
        assert pool.history_queries == []

    async def test_status_reports_identity_from_row(self):
        row = _status_row()
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["instrument_key"] == INSTR
        assert (body["period"], body["horizon"]) == ("15m", "1h")
        assert body["market_data_source"] == "trader_v1_perp_candles"
        assert body["identity_provenance"] == "row"

    async def test_legacy_row_admits_it_has_no_v3_identity(self):
        """A request predating the migration is NULL, never a guessed instrument."""
        row = _status_row(instrument_key=None, period=None, horizon=None,
                          base_snapshot_id=None, market_data_source=None)
        async with _Client(_app(FakePool(status_row=row))) as c:
            body = (await c.get("/api/v3/forecasts/requests/777")).json()
        assert body["instrument_key"] is None
        assert body["identity_provenance"] == "legacy_row_no_v3_identity"


# ── additive DDL contract (step 3) ────────────────────────────────────────────

V3_IDENTITY_COLUMNS = {
    "instrument_key", "period", "horizon", "horizon_steps", "base_snapshot_id", "market_data_source",
}
_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = _ROOT / "db" / "migrations" / "20260930_forecast_request_v3_identity.sql"


def _statements(script: str) -> list[str]:
    """Split exactly the way ``app.ensure_prediction_schema`` does."""
    return [item.strip() for item in script.split(";") if item.strip()]


def _strip_comments(script: str) -> str:
    """Drop ``--`` line comments so structural assertions see SQL only."""
    return "\n".join(line.split("--", 1)[0] for line in script.splitlines())


def _split_statements(script: str) -> list[str]:
    """Split on ``;`` **outside** ``$$ … $$`` bodies.

    The psql migration wraps its CHECK constraints in DO blocks that contain
    their own semicolons, so the naive startup splitter is the wrong parser here.
    """
    text = _strip_comments(script)
    out: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = text[i]
        buf.append(ch)
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
        i += 1
    tail = "".join(buf).strip().rstrip(";").strip()
    if tail:
        out.append(tail)
    return out


_ADDITIVE_SHAPE = re.compile(
    r"^ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS \w+ (TEXT|INTEGER)$"
)


class TestAdditiveDdlContract:
    """Source-level guards: the DDL must stay additive and startup-safe."""

    def test_migration_file_declares_every_identity_column(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for col in sorted(V3_IDENTITY_COLUMNS):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, col

    def test_startup_schema_declares_every_identity_column(self):
        for col in sorted(V3_IDENTITY_COLUMNS):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in PREDICTION_TABLES_SQL, col

    def test_startup_script_stays_single_statement_per_chunk(self):
        # ensure_prediction_schema splits on ";" — a DO block or multi-statement
        # chunk would silently break the startup path.
        for chunk in _statements(PREDICTION_TABLES_SQL):
            assert "$$" not in chunk, chunk[:80]
            assert chunk.count(";") == 0, chunk[:80]

    def test_migration_statements_are_only_additive_forms(self):
        stmts = _split_statements(MIGRATION_PATH.read_text(encoding="utf-8"))
        assert len(stmts) >= 8, f"expected columns + index + guards, got {len(stmts)}"
        for stmt in stmts:
            assert (
                _ADDITIVE_SHAPE.match(stmt)
                or stmt.startswith("CREATE INDEX IF NOT EXISTS")
                or stmt.startswith("DO $$")
            ), f"unexpected DDL form: {stmt[:90]}"
        # DO blocks may only ADD a CHECK constraint; nothing destructive anywhere.
        for stmt in stmts:
            upper = stmt.upper()
            assert "ADD CONSTRAINT" not in upper or "CHECK" in upper, stmt[:90]
            for forbidden in ("DROP ", "TRUNCATE", "DELETE ", "UPDATE ", "SET NOT NULL", "ALTER COLUMN"):
                assert forbidden not in upper, f"ТЗ §117 violation: {forbidden!r}"

    def test_new_startup_columns_are_nullable(self):
        # asserted per column, not per comment block: comments are stripped for
        # the structural checks, so the V3 marker text is not available here.
        stmts = {
            stmt.split(" EXISTS ")[1].split(" ")[0]: stmt
            for stmt in _statements(_strip_comments(PREDICTION_TABLES_SQL))
            if "ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS" in stmt
        }
        for col in sorted(V3_IDENTITY_COLUMNS):
            assert col in stmts, col
            stmt = stmts[col]
            assert "NOT NULL" not in stmt, stmt
            assert "DEFAULT" not in stmt, f"no backfill default expected: {stmt}"

    def test_status_query_columns_exist_in_ddl(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for col in ("instrument_key", "period", "horizon", "base_snapshot_id", "market_data_source"):
            assert f"ADD COLUMN IF NOT EXISTS {col} TEXT" in sql, col


# ── module SQL ↔ DDL column contract (L1, no database required) ──────────────

async def _recorded_queries() -> tuple[tuple[str, tuple], tuple[str, tuple]]:
    """The **exact** SQL (and binds) the module emits, captured from the fake pool.

    Both the L1 column-contract tests and the disposable-PG rehearsal use this,
    so a test can never drift away from the statement the route really runs (a
    hand-copied query would keep passing while the module broke).
    """
    pool = FakePool(status_row=_status_row())
    async with _Client(_app(pool)) as c:
        await _post(c, {"instrument_key": INSTR, "period": "15m", "horizon": "1h"})
        await c.get("/api/v3/forecasts/requests/777")
    assert len(pool.writes) == 1 and len(pool.reads) == 1
    return pool.writes[0], pool.reads[0]


_CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (?P<name>\w+) \((?P<body>.*?)\n\);", re.S)
_ADD_COLUMN = re.compile(
    r"ALTER TABLE (?P<name>\w+) ADD COLUMN IF NOT EXISTS (?P<col>\w+)")
_INSERT_SHAPE = re.compile(
    r"INSERT INTO forecast_requests\s*\((?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>.*)\)", re.S)
_TABLE_CONSTRAINT_WORDS = {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"}


def _table_columns(*scripts: str) -> dict[str, set[str]]:
    """Every column name the given DDL scripts give each table (CREATE + ALTER)."""
    tables: dict[str, set[str]] = {}
    for script in scripts:
        text = _strip_comments(script)
        for match in _CREATE_TABLE.finditer(text):
            cols = tables.setdefault(match.group("name"), set())
            for line in match.group("body").splitlines():
                token = line.strip().split(" ")[0].rstrip(",")
                if token and token.upper() not in _TABLE_CONSTRAINT_WORDS:
                    cols.add(token)
        for match in _ADD_COLUMN.finditer(text):
            tables.setdefault(match.group("name"), set()).add(match.group("col"))
    return tables


class TestModuleQueriesMatchTheDdl:
    """Bridge the SQL the module writes to the columns the migration declares.

    The recording fake pool accepts *any* statement string, so a misspelled column
    or one the migration never added stays green at L1 and only explodes against a
    real database. These tests parse the captured statement and diff it against the
    DDL — the strongest proof available without a Postgres.
    """

    async def test_insert_touches_only_columns_the_ddl_declares(self):
        (insert_sql, args), _read = await _recorded_queries()
        match = _INSERT_SHAPE.search(" ".join(insert_sql.split()))
        assert match, insert_sql
        cols = [item.strip() for item in match.group("cols").split(",")]
        vals = [item.strip() for item in match.group("vals").split(",")]
        known = _table_columns(PREDICTION_TABLES_SQL, FORECAST_QUEUE_SCHEMA)["forecast_requests"]
        assert set(cols) <= known, f"module writes unknown columns: {sorted(set(cols) - known)}"
        assert V3_IDENTITY_COLUMNS <= set(cols), "identity columns must be stored, not only queued"
        # arity: one VALUES item per column, and one bind per non-literal column
        assert len(vals) == len(cols)
        literals = [v for v in vals if not v.startswith("$")]
        assert literals == ["'pending'"], literals
        assert len(args) == len(cols) - len(literals)

    async def test_status_select_reads_only_columns_the_ddl_declares(self):
        _write, (select_sql, _binds) = await _recorded_queries()
        known = _table_columns(PREDICTION_TABLES_SQL, FORECAST_QUEUE_SCHEMA)["forecast_requests"]
        referenced = set(re.findall(r"\br\.(\w+)", select_sql))
        assert referenced, "no r.<column> references parsed — the regex needs review"
        assert referenced <= known, f"status query reads unknown columns: {sorted(referenced - known)}"
        for col in V3_IDENTITY_COLUMNS - {"horizon_steps"}:
            assert col in referenced, f"{col} must be readable from the row"

    def test_ddl_column_extraction_is_itself_sane(self):
        """Guard the guard: if the parser lost the known columns, the tests above
        would pass vacuously, so pin a few columns that must always be found."""
        known = _table_columns(PREDICTION_TABLES_SQL, FORECAST_QUEUE_SCHEMA)["forecast_requests"]
        for col in ("symbol", "horizon_hours", "base_price", "status", "as_of",
                    "execution_state", "instrument_key", "market_data_source"):
            assert col in known, col


class TestMountedInRealApp:
    """``webui/app.py`` must actually expose the command (G2 mount).

    No host test imports the production ``app`` object (it needs env, Redis and
    Postgres), and the UI fixture deliberately does not mount this router — so the
    mount is pinned at source level here, while the running service is re-probed
    through its own OpenAPI document as the L4 step.
    """

    APP_PATH = _ROOT / "webui" / "app.py"

    def test_app_imports_and_includes_the_command_router(self):
        source = self.APP_PATH.read_text(encoding="utf-8")
        # the router is imported (now as part of a multi-symbol block that also
        # pulls _closed_candles/HISTORY_TABLE for the perpetual worker path)
        assert "router as forecast_command_v3_router" in source
        assert "from forecast_command_v3 import" in source
        assert "app.include_router(forecast_command_v3_router)" in source

    def test_mount_order_keeps_the_read_model_owner_of_result_path(self):
        source = self.APP_PATH.read_text(encoding="utf-8")
        assert (source.index("app.include_router(forecast_v3_router)")
                < source.index("app.include_router(forecast_command_v3_router)"))

    def test_command_routes_do_not_shadow_the_read_model(self):
        """Sharing the ``/api/v3`` prefix is fine; an identical method+path would
        silently win by registration order instead of being rejected."""
        from fastapi.routing import APIRoute

        from forecast_workspace import router as read_router

        def signature(other) -> set[str]:
            return {
                f"{sorted(route.methods)[0]} {route.path}"
                for route in other.routes
                if isinstance(route, APIRoute)
            }

        mine, theirs = signature(router), signature(read_router)
        assert "POST /api/v3/forecasts" in mine
        assert "GET /api/v3/forecasts/requests/{request_id}" in mine
        assert not (mine & theirs), f"route collision: {sorted(mine & theirs)}"


# ── disposable-Postgres rehearsal (opt-in, never touches production) ──────────

_DSN = os.getenv("WORED_TEST_DATABASE_URL", "")


def _dsn_allowed() -> bool:
    return bool(_DSN) and urlsplit(_DSN).path == "/wored_qa"


class TestDdlOnDisposablePostgres:
    """Applies the real DDL to a throwaway schema and proves the contract holds.

    Skipped unless ``WORED_TEST_DATABASE_URL`` points at the disposable
    ``wored_qa`` database — the same guard the P5/P6 PG suites use.
    """

    async def _connect(self):
        import asyncpg

        if not _dsn_allowed():
            pytest.skip("WORED_TEST_DATABASE_URL must point at the disposable wored_qa database")
        admin = await asyncpg.connect(_DSN, timeout=5)
        schema = f"fcid_{uuid4().hex[:8]}"
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        conn = await asyncpg.connect(_DSN, timeout=5)
        await conn.execute(f'SET search_path TO "{schema}"')
        return admin, conn, schema

    async def _close(self, admin, conn, schema) -> None:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()
        await admin.close()

    async def _prepare(self, conn) -> None:
        """Recreate the *pre-migration* table exactly as the live DB has it.

        The V3 DDL only ALTERs ``forecast_requests``, so the rehearsal has to
        stand up the legacy table (startup script) and the lifecycle columns +
        ``forecast_jobs`` (``forecast_queue.SCHEMA``) first — otherwise a passing
        test would prove nothing about the real database state.
        """
        for stmt in _statements(PREDICTION_TABLES_SQL):
            await conn.execute(stmt)
        for stmt in _statements(FORECAST_QUEUE_SCHEMA):
            await conn.execute(stmt)

    async def test_startup_path_and_migration_create_the_columns_and_are_idempotent(self):
        admin, conn, schema = await self._connect()
        try:
            # 1) exactly what webui does at startup
            for stmt in _statements(PREDICTION_TABLES_SQL):
                await conn.execute(stmt)
            # the same ALTERs the queue module already runs at startup must not
            # clash with the V3 script
            for stmt in _statements(FORECAST_QUEUE_SCHEMA):
                await conn.execute(stmt)
            # 2) the psql migration, applied twice, must not raise
            text = MIGRATION_PATH.read_text(encoding="utf-8")
            await conn.execute(text)
            await conn.execute(text)
            cols = await conn.fetch(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = 'forecast_requests'", schema)
            found = {r["column_name"]: r["is_nullable"] for r in cols}
            for col in V3_IDENTITY_COLUMNS:
                assert col in found, f"{col} missing after the startup + migration path"
                assert found[col] == "YES", f"{col} must stay nullable (legacy rows)"
            idx = await conn.fetchval(
                "SELECT indexname FROM pg_indexes WHERE schemaname=$1 AND indexname=$2",
                schema, "idx_forecast_requests_v3_identity")
            assert idx == "idx_forecast_requests_v3_identity"
        finally:
            await self._close(admin, conn, schema)

    async def test_domain_checks_reject_values_the_v3_matrix_cannot_produce(self):
        admin, conn, schema = await self._connect()
        try:
            await self._prepare(conn)
            await conn.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
            insert = (
                "INSERT INTO forecast_requests (symbol, horizon_hours, base_price, status, "
                "instrument_key, period, horizon, horizon_steps) VALUES "
                "('btcusdt', 1, 100.0, 'pending', $1, $2, $3, $4)"
            )
            await conn.execute(insert, INSTR, "5m", "15m", 3)          # valid V3 shape
            await conn.execute(insert, None, None, None, None)         # legacy row: all NULL
            bad = [
                ("BTCUSDT", "5m", "15m", 3),   # not the registry grammar (no venue:type:pair)
                (INSTR, "99m", "15m", 3),        # period outside the supported set
                (INSTR, "5m", "8h", 3),          # horizon outside the matrix
                (INSTR, "5m", "15m", 60),        # beyond the runner step budget
            ]
            for key, period, horizon, steps in bad:
                with pytest.raises(Exception) as exc:
                    await conn.execute(insert, key, period, horizon, steps)
                assert exc.type.__name__ == "CheckViolationError", (key, period, horizon, steps, exc)
        finally:
            await self._close(admin, conn, schema)

    async def test_instrument_check_is_a_grammar_guard_not_a_venue_gate(self):
        """Honest limit: the DB cannot tell perpetual from spot.

        ``htx:spot:BTC-USDT`` satisfies the key grammar, so no CHECK rejects it.
        ТЗ §51 is enforced one layer up — the registry resolves the key and
        ``forecast_command_v3`` reads history only from ``trader_v1_perp_candles``
        and 404s an unregistered/spot key before any insert. This test records
        that fact instead of pretending the constraint covers it.
        """
        admin, conn, schema = await self._connect()
        try:
            await self._prepare(conn)
            await conn.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO forecast_requests (symbol, horizon_hours, base_price, status, "
                "instrument_key, period, horizon, horizon_steps) "
                "VALUES ('btcusdt', 1, 100.0, 'pending', $1, '5m', '15m', 3)", SPOT_KEY)
            stored = await conn.fetchval(
                "SELECT instrument_key FROM forecast_requests WHERE instrument_key = $1", SPOT_KEY)
            assert stored == SPOT_KEY
        finally:
            await self._close(admin, conn, schema)

    async def test_status_select_shape_matches_the_real_table(self):
        """The module's status query must run unchanged against the migrated table."""
        admin, conn, schema = await self._connect()
        try:
            await self._prepare(conn)
            await conn.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
            # use the module's own captured statement, not a hand-written copy
            _write, (select_sql, _binds) = await _recorded_queries()
            request_id = await conn.fetchval(
                "INSERT INTO forecast_requests (symbol, horizon_hours, base_price, status, "
                "instrument_key, period, horizon, horizon_steps, market_data_source) "
                "VALUES ('btcusdt', 1, 100.0, 'pending', $1, '15m', '1h', 4, "
                "'trader_v1_perp_candles') RETURNING id", INSTR)
            row = await conn.fetchrow(select_sql, request_id)
            assert row["instrument_key"] == INSTR
            assert (row["period"], row["horizon"]) == ("15m", "1h")
            assert row["job_state"] is None      # no job row yet — LEFT JOIN keeps the request
        finally:
            await self._close(admin, conn, schema)


# ── worker perpetual-context wiring (G2 residual: ТЗ §51/§57) ─────────────


class TestWorkerPerpetualContext:
    """The queue worker must build a V3 job's pattern context from perpetual
    candles, never spot.

    Same rule as ``TestMountedInRealApp``: no host test imports the production
    ``app`` object (it needs env/Redis/Postgres), so the wiring is pinned at
    source level.  The *executed* perpetual run is proven live at L4, not here.
    """

    APP_PATH = _ROOT / "webui" / "app.py"

    def _source(self) -> str:
        return self.APP_PATH.read_text(encoding="utf-8")

    def _builder_body(self, source: str) -> str:
        start = source.index("async def build_prediction_context_perpetual(")
        end = source.index("async def build_prediction_context(", start)
        return source[start:end]

    def test_app_imports_the_perpetual_reader_and_decimal(self):
        src = self._source()
        assert "from decimal import Decimal" in src
        assert "_closed_candles as v3_closed_candles" in src
        assert "HISTORY_TABLE as V3_PERP_TABLE" in src
        assert "from instrument_registry import load_registry" in src
        assert "_require_spec as v3_require_spec" in src
        assert "CandleSourceError" in src

    def test_worker_signature_accepts_v3(self):
        """Fixes the latent TypeError: the runner forwards ``**payload`` and a V3
        payload carries a ``v3`` key, so the signature must accept it."""
        assert "v3: dict[str, Any] | None = None" in self._source()

    def test_worker_routes_perpetual_and_pins_as_of_to_base_candle(self):
        src = self._source()
        assert 'is_perpetual = bool(v3) and str(v3.get("market_data_source")) == V3_PERP_TABLE' in src
        assert "build_prediction_context_perpetual(" in src
        # §57: steps counted from the CLOSED base candle, not wall-clock now()
        assert 'as_of = datetime.fromisoformat(str(v3["base_time"]))' in src
        # legacy default path still uses the spot builder
        assert "context_payload = await build_prediction_context(" in src

    def test_perpetual_builder_reads_perp_source_and_never_spot(self):
        body = self._builder_body(self._source())
        assert "v3_closed_candles(" in body
        assert "float(Decimal(str(v3[\"base_price\"])))" in body  # §57 pinned base
        assert "perpetual_history" in body                        # fail-closed
        assert "\"market_snapshot\"" in body
        # §51: the perpetual context path must not touch the spot series or key
        assert "fetch_klines" not in body
        assert "spot_snapshot" not in body

    def test_period_uses_the_v3_aware_normalizer(self):
        """Regression pin: app.normalize_period only accepts "15min" and 400s on the
        V3 workspace form "15m". The perpetual path must use the alias-aware
        prediction_timeframes.normalize_period (imported as normalize_period_v3)."""
        src = self._source()
        assert "normalize_period as normalize_period_v3" in src
        assert 'normalize_period_v3(str(v3["period"]))' in src
        body = self._builder_body(src)
        assert "normalize_period_v3(period)" in body
        assert "normalize_period(period)" not in body
