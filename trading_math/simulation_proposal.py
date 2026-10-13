"""V3 plan-proposal domain: questionnaire → agent draft → deterministic veto.

Extracted verbatim from ``webui/simulation_proposal.py`` by phase P3d so that the
Telegram console and the dashboard run **one** proposal pipeline (ТЗ §2, TB-03).
The contract (ТЗ §5, MC-12/MC-13) is unchanged:

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
    adapter is opt-in and injectable by the host).

This module owns no transport: no FastAPI, no Redis, no asyncpg import.  The
host injects what it has — a pool for :class:`PostgresProposalStore`, its own
market reader as ``MarketProvider``, its own source callable — which is exactly
how :mod:`trading_math.plan_validation` already works for the plan flow.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from trading_math.plan_contract import (
    check_plan_semantics,
    normalize_plan,
    plan_hash,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
GOALS = ("learning", "comparison", "risk_control")
SIDES_PREF = ("long", "short", "both")
INVOLVEMENT = ("manual", "supervised", "autonomous")
PROPOSAL_STATES = ("pending", "completed", "failed")
# hard wall on the agent call: a slow provider must land in `failed`,
# never hold the questionnaire hostage (MC-13)
PROPOSAL_TIMEOUT_SECONDS = 20.0

RULES_MODEL_VERSION = "wored-rules-v1"

#: instrument used when a questionnaire does not name one; the registry, not
#: this module, decides whether it is real
DEFAULT_INSTRUMENT_KEY = "htx:linear-swap:BTC-USDT"


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
    #: set by the host when this proposal became a plan; the id, not a copy of
    #: it, so a second "adopt" can be answered with the draft that already exists
    adopted_plan_id: str | None = None


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
            "adopted_plan_id": rec.adopted_plan_id,
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
        "instrument_key": ctx.get("instrument_key", DEFAULT_INSTRUMENT_KEY),
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
#: a host reads its own market cache and returns ``build_market_state`` output
MarketProvider = Callable[[str], Awaitable[Any]]


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

    async def list_recent(self, owner_id: str, limit: int = 5) -> list[ProposalRecord]:
        rows = [r for r in self.items.values() if r.owner_id == owner_id]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:max(1, int(limit))]

    async def mark_adopted(self, proposal_id: str, plan_id: str, owner_id: str) -> bool:
        rec = await self.get(proposal_id, owner_id)
        if rec is None:
            return False
        rec.adopted_plan_id = plan_id
        rec.updated_at = datetime.now(timezone.utc)
        return True


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
    # the bound plan id is merged into the same jsonb document: no schema change,
    # and an adopt never overwrites the draft the agent proposed
    _MARK_ADOPTED = """
        UPDATE simulation_plan_proposals
        SET result = coalesce(result, '{}'::jsonb)
                   || jsonb_build_object('adopted_plan_id', $2::text),
            updated_at = now()
        WHERE id = $1 AND owner_id = $3
    """
    _LIST_RECENT = """
        SELECT * FROM simulation_plan_proposals
        WHERE owner_id = $1
        ORDER BY created_at DESC
        LIMIT $2
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
            adopted_plan_id=result.get("adopted_plan_id"),
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
            "validator": rec.validator, "adopted_plan_id": rec.adopted_plan_id,
        }
        await self.pool.execute(self._UPDATE, UUID(rec.proposal_id), rec.status,
                                json.dumps(result), rec.reason_code, rec.reason_message)

    async def list_recent(self, owner_id: str, limit: int = 5) -> list[ProposalRecord]:
        rows = await self.pool.fetch(self._LIST_RECENT, owner_id, max(1, int(limit)))
        return [self._row(r) for r in rows]

    async def mark_adopted(self, proposal_id: str, plan_id: str, owner_id: str) -> bool:
        status = await self.pool.execute(self._MARK_ADOPTED, UUID(proposal_id), plan_id,
                                         owner_id)
        # asyncpg returns "UPDATE n"; a foreign or already-bound row answers UPDATE 0
        return status.endswith(" 1")


# ── runner: agent call + deterministic veto ─────────────────────────────────


def validate_draft(draft_plan: Any) -> dict[str, Any]:
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


class InvalidAgentEnvelope(Exception):
    """The agent answered, but the answer is not a proposal envelope.

    A host adapter raises this when the reply carries no parseable JSON object or
    no ``plan`` inside it.  It is a *different* condition from "the provider never
    answered": reporting a malformed answer as ``llm_unavailable`` would send the
    owner looking for a network problem that does not exist, so the runner keeps
    the two apart (ТЗ §6, TB-11).
    """

    reason_code = "llm_invalid_response"


async def run_proposal(store, rec: ProposalRecord, source: ProposalSource,
                       ctx: dict[str, Any], timeout: float) -> None:
    try:
        result = await asyncio.wait_for(source(rec.questionnaire, ctx), timeout=timeout)
    except asyncio.TimeoutError:
        rec.status, rec.reason_code = "failed", "llm_timeout"
        rec.reason_message = f"agent did not answer within {timeout:.0f}s; manual form stays available"
        rec.updated_at = datetime.now(timezone.utc)
        await store.save(rec)
        return
    except InvalidAgentEnvelope as exc:
        rec.status, rec.reason_code = "failed", "llm_invalid_response"
        rec.reason_message = str(exc) or (
            "agent response is not a SimulationPlanV1 draft; manual form stays available"
        )
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
    rec.validator = validate_draft(result.get("plan"))
    rec.plan_hash = rec.validator["plan_hash"]
    if rec.validator["adoptable"] is False:
        rec.missing_data.append("часть полей предложения не прошла детерминированный валидатор — они не будут исправлены молча")
    rec.updated_at = datetime.now(timezone.utc)
    await store.save(rec)


# ── adopt: the one place that decides whether a proposal may become a draft ─


class AdoptRefused(Exception):
    """A proposal cannot become a plan, in the domain's own words.

    ``violations`` is the shared validator's list, verbatim: the host renders it
    instead of the numbers it would have written, and never fixes the draft
    silently (MC-12: the veto belongs to the validator, not to a UI).
    """

    reason_code = "proposal_not_adoptable"

    def __init__(self, message: str, *, reason_code: str | None = None,
                 violations: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.violations = list(violations or [])
        if reason_code:
            self.reason_code = reason_code


def adopt_plan_body(rec: ProposalRecord) -> dict[str, Any]:
    """Return the draft body this proposal may become, or refuse.

    The check is the *stored* validator verdict — the same one the proposal card
    displayed — so an adoption can never carry a draft the veto rejected.  The
    host still re-runs the full pipeline before the plan becomes approvable; a
    proposal is a suggestion, and a plan hash is only ever produced by the plan
    contract.
    """
    if rec.status == "pending":
        raise AdoptRefused(
            "предложение ещё формируется — закройте карточку позже",
            reason_code="proposal_pending",
        )
    if rec.status == "failed":
        raise AdoptRefused(
            rec.reason_message or "агент не смог сформировать предложение",
            reason_code=rec.reason_code or "proposal_failed",
        )
    if not rec.draft:
        raise AdoptRefused(
            "предложение не содержит черновика плана",
            reason_code="proposal_without_draft",
        )
    validator = rec.validator or validate_draft(rec.draft)
    if validator.get("adoptable") is not True:
        raise AdoptRefused(
            "детерминированный валидатор отклонил предложение — оно не станет планом",
            reason_code="proposal_vetoed",
            violations=list(validator.get("violations") or []),
        )
    return dict(rec.draft)


async def build_proposal_ctx(market: MarketProvider | None, instrument_key: str,
                             *, now: datetime | None = None) -> dict[str, Any]:
    """Assemble the agent's market context through the host's own reader.

    Reading the quote must never break the flow: any failure simply leaves the
    context without a snapshot, and ``rules_source`` then records the fact in
    ``missing_data`` instead of inventing a price.
    """
    ctx: dict[str, Any] = {
        "instrument_key": instrument_key,
        "now": now or datetime.now(timezone.utc),
    }
    if market is None:
        return ctx
    try:
        state = await market(instrument_key)
    except Exception as exc:  # noqa: BLE001 — context is advisory, not critical
        log.debug("proposal market context unavailable: %s", exc)
        return ctx
    if isinstance(state, dict) and "identity_ok" not in state:
        ctx["snapshot_id"] = state["snapshot_id"]
        ctx["market"] = {
            "snapshot_id": state["snapshot_id"],
            "as_of": state["as_of"],
            "quality_worst": state["quality"]["worst"],
            "base_price": (state.get("quote") or {}).get("mark"),
        }
    return ctx
