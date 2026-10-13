"""The paid proposal agent — opt-in, and the only proposal path that dials a provider.

Default source in WORED is the free, reproducible rule engine
(:func:`trading_math.simulation_proposal.rules_source`).  Nothing in this module
runs unless the host says ``BOT_PROPOSAL_SOURCE=llm``, and the service resolves
that lazily, so an installation with no AI key still answers questionnaires and
no chat command can silently become a billable call.

What the agent is allowed to be, and where the guard rails are:

* **proposer, never approver** — it returns a draft envelope and the shared
  deterministic validator in :mod:`trading_math.simulation_proposal` decides
  whether that draft may become a plan; a veto here looks exactly like a veto in
  the dashboard, because it *is* the same code (ТЗ TB-03, P3 exit gate);
* **one transport, not a second one** — calls go through
  :func:`ai.router.call_raw`, i.e. the same client cache, resilience handler and
  local-Bonsai branch that every chat answer uses; ``ai_providers`` stays the
  only place that knows how to reach a model;
* **the chain is the bot's own** — ``BOT_PROPOSAL_TIER`` names the preferred
  tier and :func:`ai.models.expand_fallback_tiers` orders the rest, so a proposal
  cannot invent a cheaper private fallback route that the rest of the product
  would not have chosen;
* **failure keeps its real name** — an answer that is not an envelope raises
  :class:`InvalidAgentEnvelope` (``llm_invalid_response``), a quota block is
  detected *before* spending on the call, and everything else surfaces with the
  provider's own message so the runner can tell ``llm_quota`` from
  ``llm_unavailable``.  In all three cases the card sends the owner to
  ``/plans new`` — the ТЗ definition of "AI failure safe" (TB-11).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from trading_math.simulation_proposal import InvalidAgentEnvelope

log = logging.getLogger(__name__)

#: envelope shape from the domain contract; ``plan`` is required, the rest must
#: have the type the record stores them as
_ENVELOPE_TYPES: tuple[tuple[str, type, bool], ...] = (
    ("plan", dict, True),
    ("rationale", dict, False),
    ("assumptions", list, False),
    ("missing_data", list, False),
)

#: tier asked for when the host does not name one; the same tier the free-form
#: ``trade_plan`` intent has always used for planning work
DEFAULT_TIER = "analyst"

INVALID_ENVELOPE = "agent response is not a SimulationPlanV1 draft; manual form stays available"


def preferred_tier() -> str:
    raw = os.getenv("BOT_PROPOSAL_TIER", "").strip()
    return raw or DEFAULT_TIER


def _agent_view(ctx: dict[str, Any]) -> dict[str, Any]:
    """What the agent is allowed to see: the instrument, the clock, the snapshot.

    The market block is the same reduced view the dashboard's proposal card
    shows (snapshot id, as-of, worst data quality, mark price) — nothing else
    from Redis is handed over, so an agent answer can never be "explained" by
    data the owner could not have seen in the card.
    """
    moment = ctx.get("now")
    if isinstance(moment, datetime):
        now_utc = moment.astimezone(timezone.utc).isoformat()
    else:
        now_utc = datetime.now(timezone.utc).isoformat()
    return {
        "instrument_key": ctx.get("instrument_key"),
        "now_utc": now_utc,
        "market": ctx.get("market"),
    }


def _extract_envelope(raw: str) -> dict[str, Any]:
    """Parse one machine answer, or say that this answer is not an envelope."""
    from ai.router import strip_code_fence

    text = strip_code_fence(raw)
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        # a model may wrap the object in a sentence; the outermost braces are
        # still the answer, and if they are not, the type check below refuses
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(text[start:end + 1])
            except (ValueError, TypeError):
                parsed = None
    if not isinstance(parsed, dict):
        raise InvalidAgentEnvelope(INVALID_ENVELOPE)
    for key, expected, required in _ENVELOPE_TYPES:
        value = parsed.get(key)
        if value is None:
            if required:
                raise InvalidAgentEnvelope(INVALID_ENVELOPE)
            continue
        if not isinstance(value, expected):
            raise InvalidAgentEnvelope(
                f"{INVALID_ENVELOPE}: field «{key}» has the wrong type"
            )
    return parsed


async def _check_owner_quota(ctx: dict[str, Any], model_key: str) -> None:
    """Daily cap for this owner, asked *before* the first provider call.

    A quota block is its own failure kind (``llm_quota``), so the bot detects it
    while it still has the owner's id in hand rather than reading a provider's
    402 back after the money is spent.  Without a Telegram id in the context
    there is nothing to meter here — the provider's own error still lands in the
    runner's taxonomy.
    """
    raw = ctx.get("telegram_user_id")
    if raw is None:
        return
    from ai.models import MODELS
    from ai.quota import check_quota

    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return
    tier = MODELS[model_key].tier
    status = await check_quota(user_id, tier)
    if not status.get("allowed", True):
        raise RuntimeError(
            f"quota blocked for tier '{tier}' ({status.get('warning')}); "
            "manual form stays available"
        )


async def llm_source(questionnaire: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Build one proposal draft from a paid model; raises to explain a refusal."""
    from ai.models import MODELS, expand_fallback_tiers
    from ai.prompts import get_prompt
    from ai.router import call_raw

    chain = [key for key in expand_fallback_tiers(preferred_tier()) if key in MODELS]
    if not chain:
        # no configured model is not a network problem and not a made-up answer
        raise RuntimeError(
            "agent unavailable: no AI model is configured for proposals; "
            "manual form stays available"
        )

    await _check_owner_quota(ctx, chain[0])

    payload = json.dumps(
        {"questionnaire": questionnaire, "context": _agent_view(ctx)},
        ensure_ascii=False,
    )
    prompt = get_prompt("plan_proposal_agent")
    last_error: Exception | None = None

    for key in chain:
        cfg = MODELS[key]
        try:
            raw = await call_raw(key, prompt, payload)
            envelope = _extract_envelope(raw)
        except InvalidAgentEnvelope as exc:
            last_error = exc
            log.warning("proposal agent '%s' (%s) answered outside the envelope: %s",
                        key, cfg.model_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — transport/auth/quota, named by the runner
            last_error = exc
            log.warning("proposal agent '%s' (%s) failed: %s", key, cfg.model_id, exc)
            continue

        # who answered is part of the record: a draft without a model version
        # would be indistinguishable from the free rule engine in the card
        envelope.setdefault("model_version", cfg.model_id)
        envelope["assumptions"] = list(envelope.get("assumptions") or []) + [
            f"черновик предложил агент {cfg.model_id} (tier {cfg.tier}) — "
            "числа проверит детерминированный валидатор",
        ]
        log.info(
            "PROPOSAL_AGENT_ANSWERED model=%s tier=%s actor=%s",
            cfg.model_id, cfg.tier, str(ctx.get("telegram_user_id") or "-"),
        )
        return envelope

    if isinstance(last_error, InvalidAgentEnvelope):
        # every candidate did answer; none of them answered *in shape*. That is
        # not an outage and must not be reported as one.
        raise last_error
    detail = f" last error: {last_error}" if last_error else ""
    # the provider's own words stay in the message: that is how the shared
    # runner tells llm_quota (402/429/credit) from llm_unavailable
    raise RuntimeError(
        "agent unavailable; manual form stays available, no paid fallback was"
        f" triggered beyond the configured chain.{detail}"
    )
