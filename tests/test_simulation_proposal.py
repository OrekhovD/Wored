"""P4 tests — AI proposal (questionnaire → draft → deterministic veto).

Covers the non-browser half of MC-12 (proposal carries rationale/sources and is
only a *draft*; an invalid draft is vetoed by the shared validator, never
silently fixed) and MC-13 (timeout / quota / invalid-response land in
``failed`` with an exact reason code while the manual validate endpoint stays
available — no paid fallback is ever triggered; the default source is the free
deterministic rule engine).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
import httpx

from simulation_api import PLAN_FIELDS, normalize_plan
from simulation_proposal import (  # the webui adapter must keep re-exporting the domain
    AdoptRefused,
    DEFAULT_INSTRUMENT_KEY,
    InvalidAgentEnvelope,
    MemoryProposalStore,
    ProposalRecord,
    adopt_plan_body,
    build_proposal_ctx,
    normalize_questionnaire,
    proposal_to_dict,
    router,
    rules_source,
    run_proposal,
    validate_draft,
)
from test_simulation_api import FakeRedis, CSRF, INSTR  # shared QA harness bits
from trading_math.plan_contract import plan_hash


def _q(**overrides) -> dict:
    base = {
        "goal": "learning",
        "duration_hours": 24,
        "side_preference": "both",
        "involvement": "supervised",
        "manual_budget": "100",
        "auto_budget": "50",
        "max_acceptable_loss": "30",
        "trading_hours": "any",
    }
    base.update(overrides)
    return base


# ── questionnaire normalizer ────────────────────────────────────────────────


class TestNormalizeQuestionnaire:
    def test_valid(self):
        q, v = normalize_questionnaire(_q())
        assert v == []
        assert q["manual_budget"] == "100" and q["auto_budget"] == "50"

    def test_bad_enums(self):
        q, v = normalize_questionnaire(_q(goal="get_rich", involvement="telepathy",
                                         side_preference="sideways"))
        assert q is None
        fields = {x["field"] for x in v}
        assert fields == {"goal", "side_preference", "involvement"}

    def test_duration_range(self):
        _, v = normalize_questionnaire(_q(duration_hours=0))
        assert any(x["field"] == "duration_hours" for x in v)
        _, v = normalize_questionnaire(_q(duration_hours=169))
        assert any(x["field"] == "duration_hours" for x in v)

    def test_total_budget_explicit_split(self):
        q, v = normalize_questionnaire(_q(manual_budget=None, auto_budget=None,
                                         total_budget="100"))
        assert v == []
        assert q["manual_budget"] == "60.00" and q["auto_budget"] == "40.00"
        assert q["total_budget"] == "100"  # the split is recorded, not hidden

    def test_budgets_required(self):
        _, v = normalize_questionnaire(_q(manual_budget=None, auto_budget=None))
        assert any(x["field"] == "total_budget" and x["code"] == "field_required" for x in v)

    def test_max_loss_required(self):
        _, v = normalize_questionnaire(_q(max_acceptable_loss=None))
        assert any(x["field"] == "max_acceptable_loss" for x in v)


# ── deterministic rule source ───────────────────────────────────────────────


class TestRulesSource:
    async def test_draft_passes_shared_validator(self):
        q, _ = normalize_questionnaire(_q())
        now = datetime.now(timezone.utc)
        result = await rules_source(q, {"instrument_key": INSTR, "now": now})
        plan, violations = normalize_plan(result["plan"])
        assert violations == [], [v.as_dict() for v in violations]
        assert plan is not None
        assert set(result["plan"].keys()) == set(PLAN_FIELDS)

    async def test_rationale_and_versions_present(self):
        q, _ = normalize_questionnaire(_q())
        result = await rules_source(q, {"instrument_key": INSTR,
                                        "now": datetime.now(timezone.utc)})
        assert result["model_version"] == "wored-rules-v1"
        for key in ("leverage", "risk_per_order", "max_daily_loss", "allowed_sides"):
            assert key in result["rationale"]
        # no live snapshot in ctx → declared honestly as missing data
        assert any("snapshot" in m for m in result["missing_data"])

    async def test_autonomous_lowers_leverage(self):
        now = datetime.now(timezone.utc)
        q, _ = normalize_questionnaire(_q(involvement="autonomous"))
        r1 = await rules_source(q, {"instrument_key": INSTR, "now": now})
        q2, _ = normalize_questionnaire(_q(involvement="manual"))
        r2 = await rules_source(q2, {"instrument_key": INSTR, "now": now})
        assert int(r1["plan"]["max_leverage"]) < int(r2["plan"]["max_leverage"])

    async def test_short_preference(self):
        q, _ = normalize_questionnaire(_q(side_preference="short"))
        result = await rules_source(q, {"instrument_key": INSTR,
                                        "now": datetime.now(timezone.utc)})
        assert result["plan"]["allowed_sides"] == ["short"]


# ── API harness ─────────────────────────────────────────────────────────────


def _app(*, store=None, source=None, timeout=None, redis=None):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/test-login")
    async def _login(request: Request) -> dict:
        request.session["authenticated"] = True
        request.session["auth_type"] = "password"
        request.session["username"] = "qa-eng"
        request.session["csrf_token"] = CSRF
        return {"ok": True}

    # the manual P3 validate endpoint mounted alongside, to prove MC-13:
    # after any agent failure the manual path must still answer 200.
    from simulation_api import MemorySimulationStore, router as sim_router
    app.include_router(sim_router)
    app.include_router(router)
    app.state.simulation_proposal_store = store
    app.state.simulation_store = MemorySimulationStore()
    app.state.redis_client = redis
    app.state.pg_pool = None
    if source is not None:
        app.state.simulation_proposal_source = source
    if timeout is not None:
        app.state.simulation_proposal_timeout = timeout
    return app


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


HDRS = {"X-CSRF-Token": CSRF}


async def _await_settled(c, pid, *, timeout=3.0):
    """Poll until the background runner leaves `pending` (never sleeps blindly)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await c.get(f"/api/v3/simulation/plan-proposals/{pid}")
        assert r.status_code == 200
        body = r.json()["proposal"]
        if body["status"] != "pending":
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"proposal {pid} stuck in pending")


# ── MC-12: proposal flow + validator veto ───────────────────────────────────


@pytest.mark.asyncio
class TestProposalEndpoints:
    async def test_unauthenticated_401(self):
        async for c in _client(_app(store=MemoryProposalStore(), redis=FakeRedis())):
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()})
            assert r.status_code == 401

    async def test_csrf_missing_403(self):
        async for c in _client(_app(store=MemoryProposalStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()})
            assert r.status_code == 403

    async def test_store_unavailable_503(self):
        async for c in _client(_app(store=None, redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()}, headers=HDRS)
            assert r.status_code == 503
            assert r.json()["detail"]["reason_code"] == "proposal_store_unavailable"

    async def test_questionnaire_invalid_422(self):
        async for c in _client(_app(store=MemoryProposalStore(), redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q(goal="get_rich")}, headers=HDRS)
            assert r.status_code == 422
            assert r.json()["reason_code"] == "questionnaire_invalid"

    async def test_rules_proposal_completes_with_draft(self):
        store = MemoryProposalStore()
        app = _app(store=store, redis=FakeRedis())
        async for c in _client(app):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()}, headers=HDRS)
            assert r.status_code == 201
            pid = r.json()["proposal"]["proposal_id"]
            assert r.json()["proposal"]["status"] == "pending"
            body = await _await_settled(c, pid)
            assert body["status"] == "completed"
            assert body["model_version"] == "wored-rules-v1"
            assert set(body["draft"].keys()) == set(PLAN_FIELDS)
            assert body["rationale"] and body["assumptions"]
            # MC-12: proposal is only a draft — nothing was persisted as a plan
            # P3b: the plan domain keys owners with the canonical uuid5 namespace
            # (same value paper_api / positions_workspace / the bot derive), so a
            # proposal written in Telegram is the same owner in the dashboard.
            assert store.items[pid].owner_id == str(uuid5(NAMESPACE_URL, "wored:owner:qa-eng"))
            assert app.state.simulation_store.plans == {}

    async def test_bad_draft_vetoed_not_silently_fixed(self):
        """MC-12 veto: an agent returning an off-catalog version must surface
        as adoptable=false with the exact violation; the field is NOT fixed."""
        async def bad_source(q, ctx):
            result = await rules_source(q, ctx)
            result["plan"]["strategy_version"] = "moon_laser_v9"
            return result

        store = MemoryProposalStore()
        async for c in _client(_app(store=store, source=bad_source, redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()}, headers=HDRS)
            pid = r.json()["proposal"]["proposal_id"]
            body = await _await_settled(c, pid)
            assert body["status"] == "completed"  # agent answered; veto is separate
            assert body["validator"]["adoptable"] is False
            codes = [(x["field"], x["code"]) for x in body["validator"]["violations"]]
            assert ("strategy_version", "field_enum") in codes
            assert body["plan_hash"] is None
            assert any("валидатор" in m for m in body["missing_data"])

    async def test_get_unknown_or_foreign_404(self):
        store = MemoryProposalStore()
        async for c in _client(_app(store=store, redis=FakeRedis())):
            await c.get("/test-login")
            r = await c.get("/api/v3/simulation/plan-proposals/"
                            "11111111-1111-1111-1111-111111111111")
            assert r.status_code == 404
            r = await c.get("/api/v3/simulation/plan-proposals/not-a-uuid")
            assert r.status_code == 404


# ── MC-13: failure modes keep the manual path alive ─────────────────────────


@pytest.mark.asyncio
class TestProposalFailures:
    async def _failed(self, c, store, *, expect_code):
        await c.get("/test-login")
        r = await c.post("/api/v3/simulation/plan-proposals",
                         json={"questionnaire": _q()}, headers=HDRS)
        pid = r.json()["proposal"]["proposal_id"]
        body = await _await_settled(c, pid)
        assert body["status"] == "failed", body
        assert body["reason_code"] == expect_code, body
        # the failed record must NOT expose a draft
        assert "draft" not in body
        # MC-13 core: manual validate endpoint still answers after the failure
        plan_raw = {
            "instrument_key": INSTR, "mode": "live_paper",
            "start_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "end_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "manual_budget": "100", "auto_budget": "50",
            "strategy_id": "baseline_v1", "strategy_version": "baseline_v1",
            "style": "default", "allowed_sides": ["long"],
            "max_leverage": 5, "max_risk_per_order": "1", "max_daily_loss": "10",
            "max_session_loss": "30", "max_total_exposure": "2",
            "max_open_positions": 1, "trading_hours": "any",
        }
        mv = await c.post("/api/v3/simulation/plans/validate", json={"plan": plan_raw})
        assert mv.status_code == 200, mv.text
        return body

    async def test_timeout(self):
        async def slow(q, ctx):
            await asyncio.sleep(5)
            return await rules_source(q, ctx)

        store = MemoryProposalStore()
        async for c in _client(_app(store=store, source=slow, timeout=0.2,
                                    redis=FakeRedis())):
            body = await self._failed(c, store, expect_code="llm_timeout")
            assert "manual form" in body["reason_message"]

    async def test_quota(self):
        async def quota(q, ctx):
            raise RuntimeError("HTTP 402 payment required: quota exhausted")

        store = MemoryProposalStore()
        async for c in _client(_app(store=store, source=quota, redis=FakeRedis())):
            await self._failed(c, store, expect_code="llm_quota")

    async def test_unavailable(self):
        async def down(q, ctx):
            raise RuntimeError("connection reset by peer")

        store = MemoryProposalStore()
        async for c in _client(_app(store=store, source=down, redis=FakeRedis())):
            await self._failed(c, store, expect_code="llm_unavailable")

    async def test_invalid_response(self):
        async def garbage(q, ctx):
            return {"nope": 1}

        store = MemoryProposalStore()
        async for c in _client(_app(store=store, source=garbage, redis=FakeRedis())):
            await self._failed(c, store, expect_code="llm_invalid_response")

    async def test_default_source_is_free_rules_engine(self):
        """No source injected ⇒ the deterministic zero-cost engine runs; a paid
        adapter is reachable ONLY via explicit app.state injection (ТЗ §5:
        timeout/quota must never trigger a paid fallback)."""
        store = MemoryProposalStore()
        async for c in _client(_app(store=store, redis=FakeRedis())):  # source=None
            await c.get("/test-login")
            r = await c.post("/api/v3/simulation/plan-proposals",
                             json={"questionnaire": _q()}, headers=HDRS)
            pid = r.json()["proposal"]["proposal_id"]
            body = await _await_settled(c, pid)
            assert body["status"] == "completed"
            assert body["model_version"] == "wored-rules-v1"


# ── P3d: the domain surface both UIs share ─────────────────────────────────


def _record(status="completed", *, draft=None, validator=None,
            reason_code=None, reason_message=None) -> ProposalRecord:
    now = datetime.now(timezone.utc)
    return ProposalRecord(
        "22222222-2222-2222-2222-222222222222", "owner-1", status, _q(), now, now,
        draft=draft, validator=validator or {},
        reason_code=reason_code, reason_message=reason_message,
    )


@pytest.mark.asyncio
async def test_http_proposal_hash_is_the_contract_hash_of_the_same_draft():
    """The dashboard's endpoint answers with the digest the contract yields.

    This is the web half of the P3 exit gate: the chat card and this response are
    equal because both come out of the one runner in
    :mod:`trading_math.simulation_proposal`.
    """
    store = MemoryProposalStore()
    async for c in _client(_app(store=store, redis=FakeRedis())):
        await c.get("/test-login")
        r = await c.post("/api/v3/simulation/plan-proposals",
                         json={"questionnaire": _q()}, headers=HDRS)
        body = await _await_settled(c, r.json()["proposal"]["proposal_id"])
        assert body["plan_hash"] == plan_hash(normalize_plan(body["draft"])[0])
        assert body["validator"]["adoptable"] is True


@pytest.mark.asyncio
class TestAdoptGate:
    async def test_completed_and_validated_draft_is_adoptable(self):
        env = await rules_source(_q(), {"instrument_key": INSTR})
        rec = _record(draft=env["plan"])
        rec.validator = validate_draft(rec.draft)
        assert adopt_plan_body(rec)["instrument_key"] == INSTR

    async def test_pending_and_failed_never_become_a_plan(self):
        with pytest.raises(AdoptRefused) as raised:
            adopt_plan_body(_record("pending"))
        assert raised.value.reason_code == "proposal_pending"

        failed = _record("failed", reason_code="llm_timeout", reason_message="slow")
        with pytest.raises(AdoptRefused) as raised:
            adopt_plan_body(failed)
        assert raised.value.reason_code == "llm_timeout"
        assert "slow" in raised.value.message

    async def test_a_vetoed_proposal_is_refused_with_its_violations(self):
        env = await rules_source(_q(), {})
        draft = dict(env["plan"])
        draft["max_session_loss"] = "0.01"  # below max_daily_loss
        with pytest.raises(AdoptRefused) as raised:
            adopt_plan_body(_record(draft=draft))
        assert raised.value.reason_code == "proposal_vetoed"
        assert "loss_hierarchy" in [v["code"] for v in raised.value.violations]

    async def test_completed_without_a_draft_is_refused(self):
        with pytest.raises(AdoptRefused) as raised:
            adopt_plan_body(_record(draft=None))
        assert raised.value.reason_code == "proposal_without_draft"

    async def test_binding_survives_the_store_and_shows_up_in_the_payload(self):
        store = MemoryProposalStore()
        env = await rules_source(_q(), {})
        rec = _record(draft=env["plan"])
        rec.validator = validate_draft(rec.draft)
        await store.create(rec)

        assert await store.mark_adopted(rec.proposal_id, "plan-9", "someone-else") is False
        assert await store.mark_adopted(rec.proposal_id, "plan-9", "owner-1") is True
        loaded = await store.get(rec.proposal_id, "owner-1")
        assert loaded.adopted_plan_id == "plan-9"
        assert [r.proposal_id for r in await store.list_recent("owner-1", 5)] == [rec.proposal_id]
        assert proposal_to_dict(loaded)["adopted_plan_id"] == "plan-9"


@pytest.mark.asyncio
class TestProposalContext:
    async def test_missing_market_reader_still_produces_a_context(self):
        ctx = await build_proposal_ctx(None, INSTR)
        assert ctx["instrument_key"] == INSTR
        assert "market" not in ctx

    async def test_a_failing_reader_is_not_an_error_for_a_proposal(self):
        async def broken(key):
            raise RuntimeError("redis is down")

        ctx = await build_proposal_ctx(broken, INSTR)
        assert ctx["instrument_key"] == INSTR and "market" not in ctx

    async def test_a_live_snapshot_is_carried_into_the_context(self):
        async def alive(key):
            return {
                "snapshot_id": "snap-1", "as_of": "2026-10-13T00:00:00+00:00",
                "quality": {"worst": "live"}, "quote": {"mark": "82656.6"},
            }

        ctx = await build_proposal_ctx(alive, INSTR)
        assert ctx["snapshot_id"] == "snap-1"
        assert ctx["market"]["quality_worst"] == "live"

    async def test_rules_engine_takes_the_instrument_from_the_context(self):
        """No silent default when the host names an instrument, and an explicit
        default when it does not — the card and the draft must agree."""
        env = await rules_source(_q(), {"instrument_key": INSTR})
        assert env["plan"]["instrument_key"] == INSTR
        assert (await rules_source(_q(), {}))["plan"]["instrument_key"] == DEFAULT_INSTRUMENT_KEY


@pytest.mark.asyncio
async def test_typed_agent_refusal_keeps_its_own_reason_code():
    """``InvalidAgentEnvelope`` is a shape problem, not an outage.

    Without the typed branch the generic handler would report ``llm_unavailable``
    and send the owner looking for a network fault that does not exist (ТЗ §6).
    """
    async def malformed(q, ctx):
        raise InvalidAgentEnvelope("agent response is not a SimulationPlanV1 draft")

    store = MemoryProposalStore()
    rec = _record("pending")
    await store.create(rec)
    await run_proposal(store, rec, malformed, {}, 5.0)
    assert rec.status == "failed"
    assert rec.reason_code == "llm_invalid_response"
    assert "SimulationPlanV1" in rec.reason_message

    async def silent(q, ctx):
        raise InvalidAgentEnvelope("")

    rec2 = _record("pending")
    await run_proposal(store, rec2, silent, {}, 5.0)
    assert rec2.reason_message.startswith("agent response is not")
