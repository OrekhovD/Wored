"""V3 AI-proposal layer (P4) — HTTP adapter over the shared proposal domain.

The questionnaire, the rule engine, the stores, the agent runner and the
deterministic veto all live in :mod:`trading_math.simulation_proposal` since
phase P3d, because the Telegram console must run *the same* pipeline as this
dashboard (ТЗ §2, TB-03: one action, one implementation; P3 exit gate: the same
``plan_hash`` out of both UIs).  What stays here is the web half only:
session-derived owner, CSRF, the two JSON endpoints and the Redis read that
belongs to this process.

QA injects ``app.state.simulation_proposal_store`` (MemoryProposalStore); the
real app writes ``simulation_plan_proposals``.  No pool and no store → 503
fail-closed.  Moved names are re-exported below so existing importers
(``tests/test_simulation_proposal.py``, ``tests/ui``, ``webui.app``) keep working
unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from market_workspace import MARKET_KEY_PREFIX, build_market_state
from simulation_api import _owner_id, _require_csrf
from trading_math.simulation_proposal import (  # noqa: F401  (re-exported domain)
    DEFAULT_INSTRUMENT_KEY,
    GOALS,
    INVOLVEMENT,
    PROPOSAL_STATES,
    PROPOSAL_TIMEOUT_SECONDS,
    RULES_MODEL_VERSION,
    SCHEMA_VERSION,
    SIDES_PREF,
    AdoptRefused,
    InvalidAgentEnvelope,
    MarketProvider,
    MemoryProposalStore,
    PostgresProposalStore,
    ProposalRecord,
    ProposalSource,
    adopt_plan_body,
    build_proposal_ctx,
    normalize_questionnaire,
    proposal_to_dict,
    rules_source,
    run_proposal,
    validate_draft,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/simulation", tags=["simulation-v3-proposal"])

__all__ = [
    "router",
    "SCHEMA_VERSION", "GOALS", "SIDES_PREF", "INVOLVEMENT", "PROPOSAL_STATES",
    "PROPOSAL_TIMEOUT_SECONDS", "RULES_MODEL_VERSION", "DEFAULT_INSTRUMENT_KEY",
    "ProposalRecord", "ProposalSource", "MarketProvider", "MemoryProposalStore",
    "PostgresProposalStore", "AdoptRefused", "InvalidAgentEnvelope",
    "proposal_to_dict", "normalize_questionnaire", "rules_source",
    "validate_draft", "run_proposal", "adopt_plan_body", "build_proposal_ctx",
]


# ── endpoints ───────────────────────────────────────────────────────────────


def _market_provider(request: Request, now: datetime):
    """This process's Redis read, handed to the shared context builder.

    ``None`` means there is no market source here at all, which the domain turns
    into a ``missing_data`` note rather than a price it made up.
    """
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None

    async def _read(instrument_key: str) -> Any:
        from instrument_registry import load_registry
        spec = load_registry().get(instrument_key)
        raw = await redis_client.get(f"{MARKET_KEY_PREFIX}:{spec.contract_code}")
        payload = json.loads(raw) if raw else None
        return build_market_state(spec, payload if isinstance(payload, dict) else None,
                                  now=now)

    return _read


async def _proposal_ctx(request: Request, instrument_key: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return await build_proposal_ctx(_market_provider(request, now), instrument_key, now=now)


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
    ctx = await _proposal_ctx(request, q.get("instrument_key", DEFAULT_INSTRUMENT_KEY))
    timeout = float(getattr(request.app.state, "simulation_proposal_timeout", PROPOSAL_TIMEOUT_SECONDS))
    task = asyncio.create_task(run_proposal(store, rec, _get_source(request), ctx, timeout))
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
