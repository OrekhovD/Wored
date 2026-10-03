"""V3 AI-proposal layer (P4): questionnaire → draft SimulationPlanV1 → deterministic veto.

Contract (ТЗ §5, MC-12/MC-13):

  * the questionnaire is the only input the "agent" sees;
  * the agent returns a *draft* plan plus per-parameter rationale, the data it
    used (snapshot/forecast ids), assumptions, missing data, and a model/policy
    version — it never approves or starts anything;
  * the draft is pushed through the SAME ``normalize_plan``/``check_plan_semantics``
    validator as the manual flow; invalid fields are reported, never silently
    corrected (validator veto, adopt button stays disabled);
  * timeout / quota / invalid-response → ``failed`` with an exact reason code,
    and the manual form remains fully available — no paid fallback is triggered
    (the default source is the deterministic zero-cost rule engine; an LLM
    adapter is opt-in and injectable via ``app.state.simulation_proposal_source``).

Storage mirrors simulation_api: QA injects ``app.state.simulation_proposal_store``
(MemoryProposalStore); the real app writes ``simulation_plan_proposals`` (additive
migration).  No pool and no store → 503 fail-closed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from market_workspace import MARKET_KEY_PREFIX, build_market_state
from simulation_api import (
    _owner_id,
    _require_csrf,
    check_plan_semantics,
    normalize_plan,
    plan_hash,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/simulation", tags=["simulation-v3-proposal"])

SCHEMA_VERSION = 1
GOALS = ("learning", "comparison", "risk_control")
SIDES_PREF = ("long", "short", "both")
INVOLVEMENT = ("manual", "supervised", "autonomous")
PROPOSAL_STATES = ("pending", "completed", "failed")
# hard wall on the agent call: a slow provider must land in `failed`,
# never hold the questionnaire hostage (MC-13)
PROPOSAL_TIMEOUT_SECONDS = 20.0

RULES_MODEL_VERSION = "wored-rules-v1"


@dataclass
class ProposalRecord:
    proposal_id: str
    owner_id: str
    status: str  # pending|completed|failed
    questionnaire: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    draft: dict[str, Any] | None = None
    plan_hash: str | None = None
    rationale: dict[str, str] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    model_version: str | None = None
    validator: dict[str, Any] | None = None
    reason_code: str | None = None
    reason_message: str | None = None


def proposal_to_dict(rec: ProposalRecord) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": rec.proposal_id,
        "status": rec.status,
        "questionnaire": rec.questionnaire,
        "created_at": rec.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": rec.updated_at.astimezone(timezone.utc).isoformat(),
        "model_version": rec.model_version,
    }
    if rec.status == "completed":
        out.update({
            "draft": rec.draft,
            "plan_hash": rec.plan_hash,
            "rationale": rec.rationale,
            "sources": rec.sources,
            "assumptions": rec.assumptions,
            "missing_data": rec.missing_data,
            "validator": rec.validator,
        })
    if rec.status == "failed":
        out.update({"reason_code": rec.reason_code, "reason_message": rec.reason_message})
    return out


# ── questionnaire ───────────────────────────────────────────────────────────


def normalize_questionnaire(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if not isinstance(raw, dict):
        return None, [{"field": "_q", "code": "bad_payload", "message": "questionnaire must be an object"}]
    v: list[dict[str, str]] = []

    def bad(f: str, code: str, msg: str) -> None:
        v.append({"field": f, "code": code, "message": msg})

    q: dict[str, Any] = {}
    goal = raw.get("goal")
    if goal in GOALS:
        q["goal"] = goal
    else:
        bad("goal", "field_enum", f"goal must be one of {list(GOALS)}")

    dur = raw.get("duration_hours")
    try:
        dur_i = int(str(dur))
    except (TypeError, ValueError):
        dur_i = -1
    if 1 <= dur_i <= 168:
        q["duration_hours"] = dur_i
    else:
        bad("duration_hours", "field_range", "duration_hours must be 1..168 (live paper window)")

    side = raw.get("side_preference")
    if side in SIDES_PREF:
        q["side_preference"] = side
    else:
        bad("side_preference", "field_enum", f"must be one of {list(SIDES_PREF)}")

    inv = raw.get("involvement")
    if inv in INVOLVEMENT:
        q["involvement"] = inv
    else:
        bad("involvement", "field_enum", f"must be one of {list(INVOLVEMENT)}")

    manual = raw.get("manual_budget")
    auto = raw.get("auto_budget")
    total = raw.get("total_budget")
    def _dec_pos(x: Any) -> Decimal | None:
        try:
            d = Decimal(str(x))
        except Exception:
            return None
        return d if d.is_finite() and d > 0 else None
    md, ad, td = _dec_pos(manual), _dec_pos(auto), _dec_pos(total)
    if md is not None and ad is not None:
        q["manual_budget"], q["auto_budget"] = str(md), str(ad)
    elif td is not None:
        # explicit split proposal: 60/40 manual/auto, surfaced as an assumption
        q["manual_budget"] = str((td * Decimal("0.6")).quantize(Decimal("0.01")))
        q["auto_budget"] = str((td * Decimal("0.4")).quantize(Decimal("0.01")))
        q["total_budget"] = str(td)
    else:
        bad("total_budget", "field_required",
            "provide manual_budget+auto_budget or a single total_budget (a split is proposed explicitly)")

    mal = _dec_pos(raw.get("max_acceptable_loss"))
    if mal is not None:
        q["max_acceptable_loss"] = str(mal)
    else:
        bad("max_acceptable_loss", "field_required", "maximum acceptable loss (USDT) is required")

    hours = raw.get("trading_hours", "any")
    if isinstance(hours, str) and hours.strip():
        q["trading_hours"] = hours.strip()
    else:
        bad("trading_hours", "field_type", "trading_hours must be 'any' or 'HH:MM-HH:MM'")

    if v:
        return None, v
    return q, []


# ── deterministic rule source (default; zero-cost, no paid fallback ever) ───


async def rules_source(q: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Translate the questionnaire into a catalog-valid SimulationPlanV1 draft.
    Every choice is bounded by the same catalogs the server enforces; the draft
    still goes through the full validator — this is a proposer, not an approver."""
    manual = Decimal(q["manual_budget"])
    auto = Decimal(q["auto_budget"])
    duration = int(q["duration_hours"])
    mal = Decimal(q["max_acceptable_loss"])
    goal = q["goal"]
    involvement = q["involvement"]

    leverage = {"risk_control": 3, "learning": 5, "comparison": 5}[goal]
    if involvement == "autonomous":
        leverage = max(1, leverage - 1)  # less human oversight → less leverage
    risk_per_order = max(Decimal("1"), min(manual, auto) * Decimal("0.01")).quantize(Decimal("0.01"))
    days = max(1, -(-duration // 24))
    daily = min(mal / days, (manual + auto) * Decimal("0.1")).quantize(Decimal("0.01"))
    session_loss = mal
    positions = {"manual": 1, "supervised": 2, "autonomous": 2}[involvement]
    cooldown = {"manual": 0, "supervised": 5, "autonomous": 15}[involvement]
    sides = {"long": ["long"], "short": ["short"], "both": ["long", "short"]}[q["side_preference"]]

    now = ctx.get("now") or datetime.now(timezone.utc)
    start = now + timedelta(minutes=10)
    end = start + timedelta(hours=duration)

    rationale = {
        "goal": f"цель «{goal}»: профиль лимитов выбран из каталога реализованных стратегий",
        "duration_hours": f"окно {duration} ч начинается через 10 минут (live paper)",
        "leverage": f"плечо {leverage}: цель {goal} + уровень участия {involvement}",
        "risk_per_order": f"1% от меньшего бюджета, минимум 1 USDT",
        "max_daily_loss": f"приемлемый убыток / {days} дн., не более 10% от суммарного капитала",
        "max_session_loss": f"ровно запрошенный приемлемый убыток {mal} USDT",
        "allowed_sides": f"предпочтение сторон: {q['side_preference']}",
        "cooldown_minutes": f"уровень участия {involvement} → пауза {cooldown} мин",
        "budgets": "бюджеты manual/auto независимы, переноса средств нет",
    }
    assumptions = [
        "комиссии/проскальзывание/funding взяты из реализованного каталога (taker_6bps_v1, adverse_2bps_v1, htx_mark_funding_v1)",
        "источник данных — только закрытые свечи (closed_candles_only)",
    ]
    missing: list[str] = []
    if ctx.get("snapshot_id") is None:
        missing.append("рыночный snapshot недоступен — числа не привязаны к живой котировке")

    plan = {
        "instrument_key": ctx.get("instrument_key", "htx:linear-swap:BTC-USDT"),
        "mode": "live_paper",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": "Etc/UTC",
        "manual_budget": q["manual_budget"],
        "auto_budget": q["auto_budget"],
        "strategy_id": "baseline_v1",
        "strategy_version": "baseline_v1",
        "style": "default",
        "allowed_sides": sides,
        "max_leverage": leverage,
        "max_risk_per_order": str(risk_per_order),
        "max_daily_loss": str(daily),
        "max_session_loss": str(session_loss),
        "max_total_exposure": str((risk_per_order * positions).quantize(Decimal("0.01"))),
        "max_open_positions": positions,
        "cooldown_minutes": cooldown,
        "trading_hours": q["trading_hours"],
        "stop_policy": "mark",
        "take_profit_policy": "mark",
        "close_at_end": True,
        "fee_schedule_version": "taker_6bps_v1",
        "slippage_model_version": "adverse_2bps_v1",
        "funding_model_version": "htx_mark_funding_v1",
        "market_data_policy": "closed_candles_only",
        "seed": None,
    }
    return {
        "plan": plan,
        "rationale": rationale,
        "assumptions": assumptions,
        "missing_data": missing,
        "model_version": RULES_MODEL_VERSION,
    }


ProposalSource = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


# ── stores ──────────────────────────────────────────────────────────────────


class MemoryProposalStore:
    def __init__(self) -> None:
        self.items: dict[str, ProposalRecord] = {}

    async def create(self, rec: ProposalRecord) -> None:
        self.items[rec.proposal_id] = rec

    async def get(self, proposal_id: str, owner_id: str) -> ProposalRecord | None:
        rec = self.items.get(proposal_id)
        return rec if rec and rec.owner_id == owner_id else None

    async def save(self, rec: ProposalRecord) -> None:
        self.items[rec.proposal_id] = rec


class PostgresProposalStore:
    _INSERT = """
        INSERT INTO simulation_plan_proposals
          (id, owner_id, status, questionnaire, result, reason_code, reason_message)
        VALUES ($1, $2, $3, $4::jsonb, NULL, NULL, NULL)
    """
    _SELECT = "SELECT * FROM simulation_plan_proposals WHERE id=$1 AND owner_id=$2"
    _UPDATE = """
        UPDATE simulation_plan_proposals
        SET status=$2, result=$3::jsonb, reason_code=$4, reason_message=$5, updated_at=now()
        WHERE id=$1
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @staticmethod
    def _row(row: Any) -> ProposalRecord:
        result = row["result"] if isinstance(row["result"], dict) or row["result"] is None \
            else json.loads(row["result"] or "null")
        result = result or {}
        q = row["questionnaire"]
        if isinstance(q, str):
            q = json.loads(q)
        return ProposalRecord(
            proposal_id=str(row["id"]), owner_id=row["owner_id"], status=row["status"],
            questionnaire=q, created_at=row["created_at"], updated_at=row["updated_at"],
            draft=result.get("draft"), plan_hash=result.get("plan_hash"),
            rationale=result.get("rationale") or {}, sources=result.get("sources") or {},
            assumptions=result.get("assumptions") or [],
            missing_data=result.get("missing_data") or [],
            model_version=result.get("model_version"),
            validator=result.get("validator"),
            reason_code=row["reason_code"], reason_message=row["reason_message"],
        )

    async def create(self, rec: ProposalRecord) -> None:
        await self.pool.execute(self._INSERT, UUID(rec.proposal_id), rec.owner_id,
                                rec.status, json.dumps(rec.questionnaire))

    async def get(self, proposal_id: str, owner_id: str) -> ProposalRecord | None:
        row = await self.pool.fetchrow(self._SELECT, UUID(proposal_id), owner_id)
        return self._row(row) if row else None

    async def save(self, rec: ProposalRecord) -> None:
        result = {
            "draft": rec.draft, "plan_hash": rec.plan_hash, "rationale": rec.rationale,
            "sources": rec.sources, "assumptions": rec.assumptions,
            "missing_data": rec.missing_data, "model_version": rec.model_version,
            "validator": rec.validator,
        }
        await self.pool.execute(self._UPDATE, UUID(rec.proposal_id), rec.status,
                                json.dumps(result), rec.reason_code, rec.reason_message)


# ── runner: agent call + deterministic veto ─────────────────────────────────


def _validate_draft(draft_plan: Any) -> dict[str, Any]:
    """Same validator as the manual flow — the veto is shared, not duplicated."""
    plan, violations = normalize_plan(draft_plan)
    codes = [v.as_dict() for v in violations]
    if plan is not None:
        codes += [v.as_dict() for v in check_plan_semantics(plan, now=datetime.now(timezone.utc))]
    return {
        "violations": codes,
        "adoptable": plan is not None and not codes,
        "plan_hash": plan_hash(plan) if plan is not None and not codes else None,
    }


async def _run_proposal(store, rec: ProposalRecord, source: ProposalSource,
                        ctx: dict[str, Any], timeout: float) -> None:
    try:
        result = await asyncio.wait_for(source(rec.questionnaire, ctx), timeout=timeout)
    except asyncio.TimeoutError:
        rec.status, rec.reason_code = "failed", "llm_timeout"
        rec.reason_message = f"agent did not answer within {timeout:.0f}s; manual form stays available"
        rec.updated_at = datetime.now(timezone.utc)
        await store.save(rec)
        return
    except Exception as exc:  # provider quota / auth / transport
        msg = str(exc).lower()
        rec.status = "failed"
        rec.reason_code = "llm_quota" if ("quota" in msg or "credit" in msg or "402" in msg or "429" in msg) else "llm_unavailable"
        rec.reason_message = f"agent unavailable ({rec.reason_code}); manual form stays available, no paid fallback was triggered"
        rec.updated_at = datetime.now(timezone.utc)
        await store.save(rec)
        return

    if not isinstance(result, dict) or "plan" not in result:
        rec.status, rec.reason_code = "failed", "llm_invalid_response"
        rec.reason_message = "agent response is not a SimulationPlanV1 draft; manual form stays available"
        rec.updated_at = datetime.now(timezone.utc)
        await store.save(rec)
        return

    rec.status = "completed"
    rec.draft = result.get("plan")
    rec.rationale = result.get("rationale") or {}
    rec.assumptions = result.get("assumptions") or []
    rec.missing_data = result.get("missing_data") or []
    rec.model_version = result.get("model_version") or RULES_MODEL_VERSION
    rec.sources = {"market": ctx.get("market"), "instrument_key": ctx.get("instrument_key")}
    rec.validator = _validate_draft(result.get("plan"))
    rec.plan_hash = rec.validator["plan_hash"]
    if rec.validator["adoptable"] is False:
        rec.missing_data.append("часть полей предложения не прошла детерминированный валидатор — они не будут исправлены молча")
    rec.updated_at = datetime.now(timezone.utc)
    await store.save(rec)


# ── endpoints ───────────────────────────────────────────────────────────────


async def _proposal_ctx(request: Request, instrument_key: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {"instrument_key": instrument_key, "now": datetime.now(timezone.utc)}
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return ctx
    try:
        from instrument_registry import load_registry
        spec = load_registry().get(instrument_key)
        raw = await redis_client.get(f"{MARKET_KEY_PREFIX}:{spec.contract_code}")
        payload = json.loads(raw) if raw else None
        state = build_market_state(spec, payload if isinstance(payload, dict) else None,
                                   now=ctx["now"])
        if "identity_ok" not in state:
            ctx["snapshot_id"] = state["snapshot_id"]
            ctx["market"] = {"snapshot_id": state["snapshot_id"], "as_of": state["as_of"],
                             "quality_worst": state["quality"]["worst"],
                             "base_price": (state.get("quote") or {}).get("mark")}
    except Exception as exc:  # noqa: BLE001 — context must never break the flow
        log.debug("proposal market context unavailable: %s", exc)
    return ctx


def _json(obj: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


async def _get_proposal_store(request: Request):
    store = getattr(request.app.state, "simulation_proposal_store", None)
    if store is not None:
        return store
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail={"reason_code": "proposal_store_unavailable",
                                                     "message": "no proposal store configured"})
    return PostgresProposalStore(pool)


def _get_source(request: Request) -> ProposalSource:
    """Opt-in agent adapter. Default is the deterministic rule engine — an
    explicit, free, reproducible source; a paid LLM is wired ONLY by injecting
    app.state.simulation_proposal_source (ТЗ: no paid fallback by default)."""
    return getattr(request.app.state, "simulation_proposal_source", None) or rules_source


@router.post("/plan-proposals")
async def create_proposal(request: Request) -> JSONResponse:
    body = await request.json()
    owner = _owner_id(request)
    _require_csrf(request, body if isinstance(body, dict) else {})
    q, violations = normalize_questionnaire(body.get("questionnaire") if isinstance(body, dict) else None)
    if violations:
        return _json({"reason_code": "questionnaire_invalid", "violations": violations}, status=422)
    store = await _get_proposal_store(request)
    now = datetime.now(timezone.utc)
    rec = ProposalRecord(str(uuid4()), owner, "pending", q, now, now)
    await store.create(rec)
    ctx = await _proposal_ctx(request, q.get("instrument_key", "htx:linear-swap:BTC-USDT"))
    timeout = float(getattr(request.app.state, "simulation_proposal_timeout", PROPOSAL_TIMEOUT_SECONDS))
    task = asyncio.create_task(_run_proposal(store, rec, _get_source(request), ctx, timeout))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return _json({"schema_version": SCHEMA_VERSION, "proposal": proposal_to_dict(rec)}, status=201)


@router.get("/plan-proposals/{proposal_id}")
async def get_proposal(request: Request, proposal_id: str) -> JSONResponse:
    owner = _owner_id(request)
    store = await _get_proposal_store(request)
    try:
        UUID(proposal_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"reason_code": "proposal_not_found", "message": "no such proposal"})
    rec = await store.get(proposal_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail={"reason_code": "proposal_not_found", "message": "no such proposal"})
    return _json({"schema_version": SCHEMA_VERSION, "proposal": proposal_to_dict(rec)})
