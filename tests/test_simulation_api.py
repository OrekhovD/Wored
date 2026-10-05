"""P3 tests — SimulationPlanV1 normalize/validate/hash + simulation API.

Covers the non-browser half of MC-09 (values preserved exactly), MC-10
(negative gates: unexecuted strategy, wrong limits, missing replay history,
stale quote — each with an exact reason code) and MC-11 (approve pins the
plan_hash; approved versions are immutable; idempotent session start).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
import httpx

from simulation_api import (
    MemorySimulationStore,
    PLAN_FIELDS,
    SESSION_TRANSITIONS,
    check_plan_semantics,
    estimate_worst_case_risk,
    normalize_plan,
    plan_hash,
    router,
)

INSTR = "htx:linear-swap:BTC-USDT"
# anchored to the real clock: the endpoints gate live starts against "now"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
CSRF = "tok123"


def _plan(mode: str = "live_paper", **overrides) -> dict:
    if mode == "live_paper":
        start, end = NOW + timedelta(minutes=5), NOW + timedelta(hours=8)
    else:
        start, end = NOW - timedelta(hours=3), NOW - timedelta(hours=1)
    base = {
        "instrument_key": INSTR,
        "mode": mode,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": "Asia/Bangkok",
        "manual_budget": "1000",
        "auto_budget": "500",
        "strategy_id": "baseline_v1",
        "strategy_version": "baseline_v1",
        "style": "default",
        "allowed_sides": ["long", "short"],
        "max_leverage": 10,
        "max_risk_per_order": "10",
        "max_daily_loss": "50",
        "max_session_loss": "100",
        "max_total_exposure": "200",
        "max_open_positions": 2,
        "cooldown_minutes": 5,
        "trading_hours": "any",
        "stop_policy": "mark",
        "take_profit_policy": "mark",
        "close_at_end": True,
        "fee_schedule_version": "taker_6bps_v1",
        "slippage_model_version": "adverse_2bps_v1",
        "funding_model_version": "htx_mark_funding_v1",
        "market_data_policy": "closed_candles_only",
    }
    if mode == "historical_replay":
        base["seed"] = 42
    base.update(overrides)
    return base


# ── normalize ───────────────────────────────────────────────────────────────


class TestNormalizePlan:
    def test_valid_live_plan(self):
        plan, violations = normalize_plan(_plan())
        assert violations == []
        assert plan is not None
        assert plan["manual_budget"] == "1000"  # Decimal string preserved
        assert plan["allowed_sides"] == ["long", "short"]
        assert set(plan.keys()) == set(PLAN_FIELDS)

    def test_unknown_field_rejected(self):
        plan, violations = normalize_plan(_plan(guaranteed_profit="yes"))
        assert plan is None
        codes = {v.code for v in violations}
        assert "unknown_field" in codes

    def test_spot_instrument_rejected(self):
        plan, violations = normalize_plan(_plan(instrument_key="htx:spot:BTC-USDT"))
        assert plan is None
        assert any(v.field == "instrument_key" and v.code == "unknown_instrument" for v in violations)

    def test_unimplemented_style_rejected(self):
        plan, violations = normalize_plan(_plan(style="moon_laser_v9"))
        assert plan is None
        assert any(v.code == "field_enum" and v.field == "style" for v in violations)

    def test_scenario_mode_not_selectable(self):
        plan, violations = normalize_plan(_plan(mode="scenario"))
        assert plan is None
        assert any(v.code == "field_enum" for v in violations)

    def test_close_at_end_false_blocked(self):
        plan, violations = normalize_plan(_plan(close_at_end=False))
        assert plan is None
        assert any(v.field == "close_at_end" for v in violations)

    def test_naive_datetime_rejected(self):
        raw = _plan(start_at="2026-09-29T15:05:00")  # no offset
        plan, violations = normalize_plan(raw)
        assert plan is None
        assert any(v.field == "start_at" and v.code == "field_type" for v in violations)

    def test_empty_datetime_is_required_not_malformed(self):
        """A blank window field is a missing-required, not a format error: the
        message the user sees must not say "ISO-8601" when nothing was entered."""
        raw = _plan(start_at="", end_at=None)
        plan, violations = normalize_plan(raw)
        assert plan is None
        by = {v.field: v for v in violations}
        assert by["start_at"].code == "field_required"
        assert by["end_at"].code == "field_required"
        assert "required" in by["start_at"].message.lower()

    def test_replay_requires_seed(self):
        raw = _plan("historical_replay")
        del raw["seed"]
        plan, violations = normalize_plan(raw)
        assert plan is None
        assert any(v.field == "seed" and v.code == "field_required" for v in violations)

    def test_budget_out_of_range(self):
        plan, violations = normalize_plan(_plan(manual_budget="999999"))
        assert plan is None
        assert any(v.field == "manual_budget" and v.code == "field_range" for v in violations)

    def test_zero_budget_blocked(self):
        plan, violations = normalize_plan(_plan(auto_budget="0"))
        assert plan is None
        assert any(v.field == "auto_budget" for v in violations)

    def test_bad_timezone(self):
        plan, violations = normalize_plan(_plan(timezone="Mars/Olympus"))
        assert plan is None
        assert any(v.field == "timezone" for v in violations)


# ── semantics / worst-case ──────────────────────────────────────────────────


class TestPlanSemantics:
    def test_live_window_too_long(self):
        plan, _ = normalize_plan(_plan(
            start_at=NOW.isoformat(), end_at=(NOW + timedelta(hours=200)).isoformat()))
        assert plan is not None
        v = check_plan_semantics(plan, now=NOW)
        assert any(x.code == "window_range" for x in v)

    def test_replay_open_window_blocked(self):
        raw = _plan("historical_replay",
                    start_at=(NOW - timedelta(hours=2)).isoformat(),
                    end_at=(NOW + timedelta(hours=1)).isoformat())
        plan, _ = normalize_plan(raw)
        assert plan is not None
        v = check_plan_semantics(plan, now=NOW)
        assert any(x.code == "replay_window_open" for x in v)

    def test_loss_hierarchy(self):
        plan, _ = normalize_plan(_plan(max_session_loss="20"))  # < daily 50
        assert plan is not None
        v = check_plan_semantics(plan, now=NOW)
        assert any(x.code == "loss_hierarchy" for x in v)

    def test_risk_exceeds_budget(self):
        plan, _ = normalize_plan(_plan(max_risk_per_order="800"))  # > auto 500
        assert plan is not None
        v = check_plan_semantics(plan, now=NOW)
        assert any(x.code == "risk_exceeds_budget" for x in v)

    def test_worst_case_estimate_binds_positions_x_risk(self):
        plan, _ = normalize_plan(_plan())
        est = estimate_worst_case_risk(plan)
        assert est["worst_allowed_loss_usdt"] == "20"  # 2 positions x 10 risk
        assert "method" in est


# ── hash ────────────────────────────────────────────────────────────────────


class TestPlanHash:
    def test_deterministic_and_order_independent(self):
        plan, _ = normalize_plan(_plan())
        shuffled = {k: plan[k] for k in reversed(PLAN_FIELDS)}
        assert plan_hash(plan) == plan_hash(shuffled)

    def test_single_field_change_flips_hash(self):
        a, _ = normalize_plan(_plan())
        b, _ = normalize_plan(_plan(max_leverage=5))
        assert plan_hash(a) != plan_hash(b)
        assert plan_hash(a).startswith("sha256:")

    def test_canon_decimal_plain_and_fractional(self):
        from simulation_api import _dec_str
        # canonical form: numerically exact, trailing zeros folded (hash stable)
        assert _dec_str("1000") == "1000"
        assert _dec_str("1000.00") == "1000"
        assert _dec_str("0.50") == "0.5"
        assert _dec_str("65000.30") == "65000.3"


# ── store semantics (memory == PG contract) ─────────────────────────────────


@pytest.mark.asyncio
class TestMemoryStore:
    async def test_approve_pins_and_blocks_edits(self):
        store = MemorySimulationStore()
        plan, _ = normalize_plan(_plan())
        rec = await store.create_plan(owner_id="o1", plan=plan,
                                      plan_hash=plan_hash(plan), violations=[])
        rec = await store.approve_plan(plan_id=rec.plan_id, owner_id="o1")
        assert rec.status == "approved" and rec.approved_at is not None
        with pytest.raises(Exception):
            await store.replace_draft(plan_id=rec.plan_id, owner_id="o1",
                                      plan=plan, plan_hash="x", violations=[])

    async def test_illegal_session_transition(self):
        from simulation_api import SessionRecord
        store = MemorySimulationStore()
        now = datetime.now(timezone.utc)
        rec = SessionRecord("s1", "o1", "p1", "h", "live_paper", INSTR,
                            now, now + timedelta(hours=2), "Etc/UTC", "starting",
                            "k", "fp", [], now, now)
        await store.create_session(rec)
        with pytest.raises(Exception):
            await store.transition_session(session_id="s1", owner_id="o1",
                                           to="closed", reason="skip")
        out = await store.transition_session(session_id="s1", owner_id="o1",
                                             to="running", reason="runner_up")
        assert out.status == "running"
        assert out.state_log[-1]["reason"] == "runner_up"
        # every edge of the declared machine is reachable one step at a time
        assert "closing" in SESSION_TRANSITIONS["running"]


# ── API harness ─────────────────────────────────────────────────────────────


def _snapshot_payload(*, stale: bool) -> str:
    now = datetime.now(timezone.utc)
    age = timedelta(seconds=300) if stale else timedelta(seconds=1)
    t = (now - age).isoformat()
    return json.dumps({
        "venue": "htx", "market_type": "linear-swap", "contract_code": "BTC-USDT",
        "bid": "65000.1", "ask": "65000.9", "last": "65000.5",
        "mark": "65000.3", "index": "65000.7", "funding_rate": "0.0001",
        "source_at": t, "received_at": now.isoformat(),
        "component_times": {"ticker": t, "mark": t, "index": t, "funding": t},
    })


class FakeRedis:
    def __init__(self, stale: bool = False) -> None:
        self.stale = stale

    async def get(self, key: str) -> str:
        return _snapshot_payload(stale=self.stale)


class FakePool:
    def __init__(self, coverage_complete: bool = True) -> None:
        self.coverage_complete = coverage_complete

    async def fetch(self, sql: str, *args, **kwargs):
        low = sql.lower()
        if "count(*)" in low and "trader_v1_perp_candles" in low:
            start, end = args[2], args[3]
            required = int((end - start).total_seconds() // 60)
            n = required if self.coverage_complete else max(0, required - 5)
            return [{"n": n}]
        return []


def _app(*, store=None, redis=None, pool=None):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/test-login")
    async def _login(request: Request) -> dict:
        request.session["authenticated"] = True
        request.session["auth_type"] = "password"
        request.session["username"] = "qa-eng"
        request.session["csrf_token"] = CSRF
        return {"ok": True}

    app.include_router(router)
    app.state.simulation_store = store
    app.state.redis_client = redis
    app.state.pg_pool = pool
    return app


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


HDRS = {"X-CSRF-Token": CSRF}


@pytest.mark.asyncio
class TestSimulationEndpoints:
    async def test_unauthenticated_401(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()})
            assert r.status_code == 401

    async def test_csrf_missing_403(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()})
            assert r.status_code == 403
            assert r.json()["detail"]["reason_code"] == "csrf_invalid"

    async def test_store_unavailable_503(self):
        async for c in _client(_app(store=None, redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.get("/api/v3/simulation/plans", headers=HDRS)
            assert r.status_code == 503

    async def test_validate_live_fresh(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans/validate",
                             json={"plan": _plan()}, headers=HDRS)
            assert r.status_code == 200
            body = r.json()
            assert body["can_approve"] is True
            assert body["plan_hash"].startswith("sha256:")
            assert body["market"]["can_enter"] is True

    async def test_validate_stale_quote_blocks(self):
        """MC-10: stale quote blocks with exact reason code."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis(stale=True))):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans/validate",
                             json={"plan": _plan()}, headers=HDRS)
            body = r.json()
            assert body["can_approve"] is False
            codes = {v["code"] for v in body["violations"]}
            assert "stale_quote" in codes

    async def test_validate_missing_intervals_blocks_replay(self):
        """MC-10: incomplete replay history blocks with missing_intervals."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis(),
                                    pool=FakePool(coverage_complete=False))):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans/validate",
                             json={"plan": _plan("historical_replay")}, headers=HDRS)
            body = r.json()
            assert body["can_approve"] is False
            codes = {v["code"] for v in body["violations"]}
            assert "missing_intervals" in codes

    async def test_replay_full_coverage_ok(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis(),
                                    pool=FakePool(coverage_complete=True))):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans/validate",
                             json={"plan": _plan("historical_replay")}, headers=HDRS)
            assert r.json()["can_approve"] is True

    async def test_draft_roundtrip_preserves_values(self):
        """MC-09 at API level: stored exactly, recovered by GET."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            raw = _plan(manual_budget="1000.00")
            r = await c.post("/api/v3/simulation/plans", json={"plan": raw}, headers=HDRS)
            assert r.status_code == 201
            pid = r.json()["plan"]["plan_id"]
            g = await c.get(f"/api/v3/simulation/plans/{pid}")
            plan = g.json()["plan"]["plan"]
            assert plan["manual_budget"] == "1000"  # canonical, numerically exact
            assert plan["timezone"] == "Asia/Bangkok"
            assert plan["max_risk_per_order"] == "10"

    async def test_approve_flow_and_immutability(self):
        """MC-11: approve pins the hash; approved plan cannot be edited."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            a = await c.post(f"/api/v3/simulation/plans/{pid}/approve", json={}, headers=HDRS)
            assert a.status_code == 200
            assert a.json()["plan"]["status"] == "approved"
            pinned = a.json()["plan"]["plan_hash"]
            e = await c.post(f"/api/v3/simulation/plans/{pid}", json={"plan": _plan(max_leverage=3)}, headers=HDRS)
            assert e.status_code == 409
            assert e.json()["detail"]["reason_code"] == "plan_immutable"
            g = await c.get(f"/api/v3/simulation/plans/{pid}")
            assert g.json()["plan"]["plan_hash"] == pinned  # unchanged

    async def test_approve_refuses_violations(self):
        """MC-10 at approve: stale quote refuses with exact code."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis(stale=True))):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            a = await c.post(f"/api/v3/simulation/plans/{pid}/approve", json={}, headers=HDRS)
            assert a.status_code == 422
            codes = {v["code"] for v in a.json()["violations"]}
            assert "stale_quote" in codes

    async def test_session_requires_approved_plan(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            s = await c.post("/api/v3/simulation/sessions",
                             json={"plan_id": pid},
                             headers={**HDRS, "Idempotency-Key": "sess-key-0001"})
            assert s.status_code == 409
            assert s.json()["detail"]["reason_code"] == "plan_not_approved"

    async def test_session_idempotency_replay_and_conflict(self):
        """Same key+payload replays; same key+other payload 409s (ТЗ §6)."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r1 = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid1 = r1.json()["plan"]["plan_id"]
            await c.post(f"/api/v3/simulation/plans/{pid1}/approve", json={}, headers=HDRS)
            r2 = await c.post("/api/v3/simulation/plans", json={"plan": _plan(max_leverage=5)}, headers=HDRS)
            pid2 = r2.json()["plan"]["plan_id"]
            await c.post(f"/api/v3/simulation/plans/{pid2}/approve", json={}, headers=HDRS)

            s = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid1},
                             headers={**HDRS, "Idempotency-Key": "sess-key-0002"})
            assert s.status_code == 201
            sid = s.json()["session"]["session_id"]
            assert s.json()["session"]["status"] == "starting"

            again = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid1},
                                 headers={**HDRS, "Idempotency-Key": "sess-key-0002"})
            assert again.status_code == 200  # replayed, not newly created
            assert again.json()["session"]["session_id"] == sid
            assert again.json()["replayed"] is True

            conflict = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid2},
                                    headers={**HDRS, "Idempotency-Key": "sess-key-0002"})
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["reason_code"] == "idempotency_conflict"

    async def test_session_frozen_against_plan_hash(self):
        """MC-11: session carries the pinned hash — what ran is what was approved."""
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            pinned_hash = r.json()["plan"]["plan_hash"]
            await c.post(f"/api/v3/simulation/plans/{pid}/approve", json={}, headers=HDRS)
            s = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid},
                             headers={**HDRS, "Idempotency-Key": "sess-key-0003"})
            assert s.json()["session"]["plan_hash"] == pinned_hash
            g = await c.get(f"/api/v3/simulation/sessions/{s.json()['session']['session_id']}")
            assert g.json()["session"]["state_log"][0]["to"] == "starting"

    async def test_session_blocked_by_stale_quote(self):
        """MC-10 at start: even an approved plan cannot start on a stale feed."""
        store = MemorySimulationStore()
        async for c in _client(_app(store=store, redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            await c.post(f"/api/v3/simulation/plans/{pid}/approve", json={}, headers=HDRS)
            # feed degrades between approval and start
            store_redis = FakeRedis(stale=True)
            c_app = c._transport.app  # type: ignore[attr-defined]
            c_app.state.redis_client = store_redis
            s = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid},
                             headers={**HDRS, "Idempotency-Key": "sess-key-0004"})
            assert s.status_code == 409
            assert s.json()["detail"]["reason_code"] == "stale_quote"

    async def test_transitions_endpoint(self):
        async for c in _client(_app(store=MemorySimulationStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plans", json={"plan": _plan()}, headers=HDRS)
            pid = r.json()["plan"]["plan_id"]
            await c.post(f"/api/v3/simulation/plans/{pid}/approve", json={}, headers=HDRS)
            s = await c.post("/api/v3/simulation/sessions", json={"plan_id": pid},
                             headers={**HDRS, "Idempotency-Key": "sess-key-0005"})
            sid = s.json()["session"]["session_id"]
            ok = await c.post(f"/api/v3/simulation/sessions/{sid}/transitions",
                              json={"to": "running", "reason": "runner up"}, headers=HDRS)
            assert ok.status_code == 200
            bad = await c.post(f"/api/v3/simulation/sessions/{sid}/transitions",
                               json={"to": "closed", "reason": "skip"}, headers=HDRS)
            assert bad.status_code == 409
            assert bad.json()["detail"]["reason_code"] == "illegal_transition"
