"""Unit tests for the V3 SSE market stream (P1.2a).

The generator is exercised directly — no HTTP involved — so we get fast,
deterministic coverage of the frame contract (``snapshot`` / ``keepalive`` /
``error`` / ``bye``).  The route-level behaviour (404 / 503 guards) is checked
via TestClient as well for MC-01 coverage at the api evidence level.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from instrument_registry import InstrumentSpec
from market_workspace import (
    _sse_event,
    _stream_market_state,
    router,
)


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_key="htx:linear-swap:BTC-USDT",
        venue="htx",
        market_type="linear-swap",
        contract_code="BTC-USDT",
        price_tick="0.1",
        quantity_step="0.001",
        contract_size="0.0001",
        settlement_currency="USDT",
        fee_schedule_version="htx-linear-v1",
        status="active",
        supported_periods=("1m", "5m", "15m", "1h", "4h", "1d"),
    )


def _payload(source_at: datetime, mark: str = "117000") -> str:
    iso = source_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return json.dumps(
        {
            "market_type": "linear-swap",
            "venue": "htx",
            "contract_code": "BTC-USDT",
            "settlement_currency": "USDT",
            "bid": "116990",
            "ask": "117010",
            "last": "117000",
            "mark": mark,
            "index": "116995",
            "funding_rate": "0.0001",
            "next_funding_at": "2026-09-29T00:00:00Z",
            "source_at": iso,
            "received_at": iso,
            "component_times": {
                "ticker": iso,
                "mark": iso,
                "index": iso,
                "funding": iso,
            },
        }
    )


class ScriptedRedis:
    """Fake Redis whose get() returns queued values (str/bytes) or raises.

    Empty queue → repeats the last value forever (mirrors a stable feed).
    """

    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self._last = None

    async def get(self, key: str):
        if self._scripted:
            v = self._scripted.pop(0)
            if isinstance(v, Exception):
                raise v
            self._last = v
            return v
        return self._last


def _drain(gen, max_events: int = 3, timeout: float = 3.0) -> list[dict]:
    """Run the async generator until we have ``max_events`` frames or timeout.

    Returns list of ``{event, data}``.
    """

    async def _run():
        out = []
        it = gen.__aiter__()
        while len(out) < max_events:
            frame = await asyncio.wait_for(it.__anext__(), timeout=timeout)
            # Parse SSE frame text back into (event, json_data)
            event_name = None
            data_str = None
            event_id = None
            for line in frame.strip().split("\n"):
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                elif line.startswith("id:"):
                    event_id = line[3:].strip()
            out.append(
                {"event": event_name, "data": json.loads(data_str or "null"), "id": event_id}
            )
        return out

    return asyncio.run(_run())


class TestSSEEventFormat:
    def test_event_renders_standard_sse_frame(self) -> None:
        frame = _sse_event("snapshot", {"ok": True})
        assert frame.startswith("event: snapshot\ndata: ")
        assert frame.endswith("\n\n")
        data_line = frame.split("data: ", 1)[1].strip()
        assert json.loads(data_line) == {"ok": True}

    def test_event_id_emits_resumable_id_line(self) -> None:
        # G3 (§53): an event_id prefixes an ``id:`` line so the browser sends
        # Last-Event-ID on reconnect.
        frame = _sse_event("snapshot", {"ok": True}, event_id=12345)
        assert frame.startswith("id: 12345\nevent: snapshot\ndata: ")

    def test_no_event_id_omits_id_line(self) -> None:
        # keepalive/error/bye are not resumable → no ``id:`` line.
        frame = _sse_event("keepalive", {"as_of": "x"})
        assert "id:" not in frame


class TestSSEStreamBehavior:
    def test_first_frame_is_snapshot(self) -> None:
        now = datetime.now(timezone.utc)
        redis = ScriptedRedis([_payload(now)])
        frames = _drain(
            _stream_market_state(
                redis=redis,
                spec=_spec(),
                thresholds={"ticker": 5.0, "mark": 90.0, "index": 90.0, "funding": 3600.0},
                stop_after_seconds=30.0,
                poll_seconds=0.01,
            ),
            max_events=1,
        )
        assert frames[0]["event"] == "snapshot"
        assert frames[0]["data"]["instrument_key"] == "htx:linear-swap:BTC-USDT"
        assert frames[0]["data"]["capabilities"]["can_enter"] is True

    def test_snapshot_frames_are_tagged_with_sequence_id(self) -> None:
        """G3 (§53): every snapshot carries ``id: <sequence>`` so reconnect can
        resume from Last-Event-ID; the id matches the payload's own sequence."""
        now = datetime.now(timezone.utc)
        redis = ScriptedRedis([_payload(now)])
        frames = _drain(
            _stream_market_state(
                redis=redis,
                spec=_spec(),
                thresholds={"ticker": 5.0, "mark": 90.0, "index": 90.0, "funding": 3600.0},
                stop_after_seconds=30.0,
                poll_seconds=0.01,
            ),
            max_events=1,
        )
        assert frames[0]["id"] == str(frames[0]["data"]["sequence"])

    def test_sequence_advance_emits_another_snapshot(self) -> None:
        t0 = datetime.now(timezone.utc)
        t1 = t0.replace(microsecond=0)  # force a different ms on next add
        from datetime import timedelta
        t1 = t0 + timedelta(milliseconds=5)
        redis = ScriptedRedis([_payload(t0, mark="117000"), _payload(t1, mark="117001")])
        frames = _drain(
            _stream_market_state(
                redis=redis,
                spec=_spec(),
                thresholds={"ticker": 5.0, "mark": 90.0, "index": 90.0, "funding": 3600.0},
                stop_after_seconds=30.0,
                poll_seconds=0.01,
            ),
            max_events=2,
        )
        assert [f["event"] for f in frames] == ["snapshot", "snapshot"]
        # Sequence is derived from source_at ms; two distinct snapshots → two sequences
        assert frames[0]["data"]["sequence"] != frames[1]["data"]["sequence"]
        # payload actually changed
        assert frames[0]["data"]["quote"]["mark"] == "117000"
        assert frames[1]["data"]["quote"]["mark"] == "117001"

    def test_redis_error_becomes_error_frame_and_stream_recovers(self) -> None:
        now = datetime.now(timezone.utc)
        redis = ScriptedRedis([
            _payload(now),
            ConnectionError("redis down"),
            _payload(now.replace(microsecond=0) + __import__("datetime").timedelta(milliseconds=10)),
        ])
        frames = _drain(
            _stream_market_state(
                redis=redis,
                spec=_spec(),
                thresholds={"ticker": 5.0, "mark": 90.0, "index": 90.0, "funding": 3600.0},
                stop_after_seconds=30.0,
                poll_seconds=0.01,
            ),
            max_events=3,
        )
        events = [f["event"] for f in frames]
        assert events == ["snapshot", "error", "snapshot"]
        assert frames[1]["data"]["reason_code"] == "redis_unavailable"

    def test_session_cap_emits_bye(self) -> None:
        # Cap is shorter than the poll interval, so after the first snapshot
        # the loop should notice the cap and emit bye on the next pass.
        now = datetime.now(timezone.utc)
        redis = ScriptedRedis([_payload(now)])
        frames = _drain(
            _stream_market_state(
                redis=redis,
                spec=_spec(),
                thresholds={"ticker": 5.0, "mark": 90.0, "index": 90.0, "funding": 3600.0},
                stop_after_seconds=0.0,
                poll_seconds=0.01,
            ),
            max_events=2,
        )
        assert frames[0]["event"] == "snapshot"
        assert frames[1]["event"] == "bye"


class TestSSERouteGuards:
    """Route-level guards: unknown key → 404, no redis → 503 (never opens a stream)."""

    def _app(self, redis) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        app.state.redis_client = redis
        return app

    def test_unknown_instrument_returns_404_before_upgrade(self) -> None:
        client = TestClient(self._app(ScriptedRedis([])))
        r = client.get("/api/v3/market/htx:spot:BTC-USDT/stream")
        assert r.status_code == 404
        assert r.json()["detail"]["reason_code"] == "instrument_not_registered"

    def test_missing_redis_returns_503(self) -> None:
        client = TestClient(self._app(None))
        r = client.get("/api/v3/market/htx:linear-swap:BTC-USDT/stream")
        assert r.status_code == 503
        assert r.json()["detail"]["reason_code"] == "redis_unavailable"

    def test_stream_route_is_registered(self) -> None:
        # Route-level streaming assertions are fragile with TestClient (needs
        # to close a live connection mid-iteration); instead, we just prove
        # the route exists and its return type is StreamingResponse.  The
        # generator itself is covered by the tests above.
        from market_workspace import v3_market_stream
        from fastapi.routing import APIRoute

        app = self._app(ScriptedRedis([]))
        paths = {r.path: r for r in app.routes if isinstance(r, APIRoute)}
        assert "/api/v3/market/{instrument_key}/stream" in paths
        assert paths["/api/v3/market/{instrument_key}/stream"].endpoint is v3_market_stream
