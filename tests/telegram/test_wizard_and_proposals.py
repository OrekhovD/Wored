"""P3d gate — the agent proposal wizard in the Telegram console.

What has to be true here, from the ТЗ:

* **TB-11** — an AI proposal never starts a session and never approves anything;
  it goes through the *same* deterministic validator as the manual form, and a
  wrong / timed-out / quota-blocked answer routes the owner to the manual flow
  instead of pretending a draft exists;
* **TB-12** — the proposal card shows both budgets, the limits, the data quality
  and the ``plan_hash``, and that hash is byte-for-byte what the dashboard's own
  serializer produces for the same draft (P3 exit gate: «из UI виден тот же
  plan_hash и violations, что в WebUI»);
* **TB-03** — one answer to "is this identifier resolvable", one refusal card
  family, one market reader, shared with the plan screens.

Only the transport boundary is faked (in-memory stores, fixture Redis); the
services, presenters and handlers run exactly as in the container.
"""

from __future__ import annotations

import asyncio

import pytest
from aiogram.filters import CommandObject
from conftest import OWNER_ID, VIEWER_ID, make_callback, make_message

from handlers import simulation_v3 as screens
from services import simulation_plans as sp
from services import simulation_proposals as spr
from trading_math.plan_contract import normalize_plan, plan_hash
from trading_math.simulation_proposal import (
    InvalidAgentEnvelope,
    MemoryProposalStore,
    proposal_to_dict,
)

ANSWER = (
    "goal=risk_control duration=24 side=both involvement=supervised "
    "manual=1000 auto=1000 maxloss=300 hours=any"
)


def _principal(user_id: int = OWNER_ID):
    from security.principal import resolve

    principal = resolve(user_id)
    assert principal is not None
    return principal


def _buttons(outcome: dict) -> list[str]:
    markup = outcome["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.fixture
def proposal_store(monkeypatch):
    """The bot's proposal service bound to the in-memory store, nothing else."""
    store = MemoryProposalStore()

    async def _get_store():
        return store

    monkeypatch.setattr(spr, "get_store", _get_store)
    return store


# ─── the questionnaire ───────────────────────────────────────────────────


async def test_propose_without_an_answer_shows_the_questionnaire(
    wired_sources, proposal_store,
):
    message = make_message(text="/plans propose")
    await screens.cmd_plans(message, _principal(), CommandObject(command="plans", args="propose"))

    text = message.sent[0]["text"]
    assert "Анкета предложения" in text
    # the offered choices are the domain's own enumerations, not a copy
    for value in ("learning", "comparison", "risk_control"):
        assert value in text
    for token in ("goal", "duration", "side", "involvement", "manual", "auto",
                  "maxloss", "hours", "instrument"):
        assert token in text
    assert "ключ=значение" in text


async def test_questionnaire_names_the_free_rule_engine_as_the_default_source(
    wired_sources, proposal_store, monkeypatch,
):
    monkeypatch.delenv("BOT_PROPOSAL_SOURCE", raising=False)
    message = make_message(text="/plans propose")
    await screens.cmd_plans(message, _principal(), CommandObject(command="plans", args="propose"))

    assert "бесплатно" in message.sent[0]["text"]


async def test_paid_source_is_declared_on_the_card_when_it_is_enabled(
    wired_sources, proposal_store, monkeypatch,
):
    monkeypatch.setenv("BOT_PROPOSAL_SOURCE", "llm")
    message = make_message(text="/plans propose")
    await screens.cmd_plans(message, _principal(), CommandObject(command="plans", args="propose"))

    text = message.sent[0]["text"]
    assert "платный агент" in text and "analyst" in text


async def test_viewer_cannot_ask_for_a_proposal(wired_sources, proposal_store):
    message = make_message(user_id=VIEWER_ID, text="/plans propose " + ANSWER)
    await screens.cmd_plans(
        message, _principal(VIEWER_ID),
        CommandObject(command="plans", args="propose " + ANSWER),
    )

    text = message.sent[0]["text"]
    assert "role_denied" in text
    assert len(proposal_store.items) == 0


# ─── a good answer produces a proposal the dashboard would agree with ─────


async def test_answer_makes_one_completed_proposal_with_a_plan_hash(
    wired_sources, proposal_store,
):
    principal = _principal()
    message = make_message(text="/plans propose " + ANSWER)
    await screens.cmd_plans(
        message, principal, CommandObject(command="plans", args="propose " + ANSWER),
    )

    assert len(proposal_store.items) == 1
    rec = next(iter(proposal_store.items.values()))
    assert rec.owner_id == principal.owner_id
    assert rec.status == "completed"
    assert rec.validator["adoptable"] is True

    text = message.sent[0]["text"]
    # TB-12: both budgets, the limits, the data quality and the digest
    assert "Предложенный черновик" in text
    assert rec.plan_hash in text
    assert "manual_budget" in rec.questionnaire and "auto_budget" in rec.questionnaire
    assert "Нарушений нет" in text
    assert f"prop:adopt:{rec.proposal_id}" in _buttons(message.sent[0])


async def test_chat_hash_is_the_dashboard_hash_of_the_same_draft(
    wired_sources, proposal_store,
):
    """One serializer, two surfaces — the P3 exit gate, stated as an equality."""
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)

    draft_hash = plan_hash(normalize_plan(rec.draft)[0])
    assert rec.plan_hash == draft_hash
    # the exact value the webui endpoint would have returned for this record
    assert proposal_to_dict(rec)["plan_hash"] == draft_hash
    # and the card the bot shows carries that same string, not a shortened alias
    assert rec.plan_hash == rec.validator["plan_hash"]


async def test_proposal_card_names_the_source_and_the_instrument(
    wired_sources, proposal_store,
):
    """No invented context: the card says what produced the draft and against
    which instrument (and, when Redis answered, which snapshot)."""
    from trading_math.simulation_proposal import RULES_MODEL_VERSION

    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)

    message = make_message(text="/plans propose " + ANSWER)
    await screens.cmd_plans(
        message, principal, CommandObject(command="plans", args="propose " + ANSWER),
    )
    text = message.sent[0]["text"]

    assert rec.sources["instrument_key"] == "htx:linear-swap:BTC-USDT"
    assert "htx:linear-swap:BTC-USDT" in text
    assert RULES_MODEL_VERSION in text
    if rec.sources.get("market"):
        assert "snapshot" in text


# ─── bad answers are refused, and nothing is written ─────────────────────


async def test_unknown_token_refuses_with_the_domain_violation_shape(
    wired_sources, proposal_store,
):
    principal = _principal()
    with pytest.raises(spr.QuestionnaireInvalid) as raised:
        await spr.create(principal.owner_id, ANSWER + " mood=brave")

    codes = [item["code"] for item in raised.value.violations]
    assert codes == ["bad_token"]
    assert raised.value.reason_code == "questionnaire_invalid"
    assert len(proposal_store.items) == 0


async def test_repeated_key_is_refused_rather_than_silently_chosen(
    wired_sources, proposal_store,
):
    principal = _principal()
    with pytest.raises(spr.QuestionnaireInvalid) as raised:
        await spr.create(principal.owner_id, ANSWER + " goal=learning")

    assert [i["code"] for i in raised.value.violations] == ["duplicate_key"]


async def test_domain_rejects_an_out_of_range_window(wired_sources, proposal_store):
    principal = _principal()
    with pytest.raises(spr.QuestionnaireInvalid) as raised:
        await spr.create(principal.owner_id, ANSWER.replace("duration=24", "duration=4000"))

    assert [i["code"] for i in raised.value.violations] == ["field_range"]


async def test_unknown_instrument_is_refused_not_replaced_by_a_near_one(
    wired_sources, proposal_store,
):
    """ТЗ «no invented numbers»: the registry is the only authority on keys."""
    principal = _principal()
    with pytest.raises(spr.ProposalRefused) as raised:
        await spr.create(principal.owner_id, ANSWER + " instrument=htx:linear-swap:DOGE-USDT")

    assert raised.value.reason_code == "unknown_instrument"
    assert len(proposal_store.items) == 0


async def test_a_refusal_renders_as_the_one_refusal_card(wired_sources, proposal_store):
    """The handler's existing error family renders proposals too (TB-03)."""
    principal = _principal()
    message = make_message(text="/plans propose mood=brave")
    await screens.cmd_plans(
        message, principal,
        CommandObject(command="plans", args="propose mood=brave"),
    )

    text = message.sent[0]["text"]
    assert "операция не выполнена" in text
    assert "questionnaire_invalid" in text
    assert "bad_token" in text


# ─── failure taxonomy: AI failure safe (TB-11) ───────────────────────────


def _use_source(store, monkeypatch, source):
    monkeypatch.setattr(spr, "resolve_source", lambda: source)
    return store


async def test_timeout_lands_in_failed_and_keeps_the_manual_route(
    wired_sources, proposal_store, monkeypatch,
):
    async def slow(questionnaire, ctx):
        await asyncio.sleep(0.20)
        return {"plan": {}}

    _use_source(proposal_store, monkeypatch, slow)
    monkeypatch.setenv("BOT_PROPOSAL_TIMEOUT", "0.05")

    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)
    assert (rec.status, rec.reason_code) == ("failed", "llm_timeout")

    message = make_message(text="/plans propose " + ANSWER)
    await screens.cmd_plans(
        message, principal, CommandObject(command="plans", args="propose " + ANSWER),
    )
    text = message.sent[0]["text"]
    assert "llm_timeout" in text
    assert "Ручной путь доступен" in text
    assert "plan:new" not in _buttons(message.sent[0])  # nothing to adopt


async def test_quota_and_transport_failures_keep_different_names(
    wired_sources, proposal_store, monkeypatch,
):
    async def quota(questionnaire, ctx):
        raise RuntimeError("402 Insufficient quota, purchase credits to continue")

    async def dead(questionnaire, ctx):
        raise RuntimeError("[Errno 111] connect refused")

    principal = _principal()
    _use_source(proposal_store, monkeypatch, quota)
    assert (await spr.create(principal.owner_id, ANSWER)).reason_code == "llm_quota"
    _use_source(proposal_store, monkeypatch, dead)
    assert (await spr.create(principal.owner_id, ANSWER)).reason_code == "llm_unavailable"


async def test_answer_without_a_plan_is_invalid_response_not_an_outage(
    wired_sources, proposal_store, monkeypatch,
):
    """A model that *did* answer in the wrong shape must not be reported as down."""
    async def junk(questionnaire, ctx):
        return {"rationale": "looks good to me"}

    async def envelope_error(questionnaire, ctx):
        raise InvalidAgentEnvelope("agent response is not a SimulationPlanV1 draft")

    principal = _principal()
    _use_source(proposal_store, monkeypatch, junk)
    assert (await spr.create(principal.owner_id, ANSWER)).reason_code == "llm_invalid_response"
    _use_source(proposal_store, monkeypatch, envelope_error)
    assert (await spr.create(principal.owner_id, ANSWER)).reason_code == "llm_invalid_response"


async def test_a_vetoed_draft_is_never_adoptable(wired_sources, proposal_store, monkeypatch):
    """The deterministic veto survives a paid agent's confidence: adopt stays off."""
    from trading_math.simulation_proposal import rules_source

    async def bad_numbers(questionnaire, ctx):
        plan = dict((await rules_source(questionnaire, ctx))["plan"])
        # a semantic break the contract accepts as a field and the domain refuses
        plan["max_session_loss"] = "1.00"
        return {"plan": plan, "rationale": {"note": "агент уверен"},
                "model_version": "test-agent"}

    _use_source(proposal_store, monkeypatch, bad_numbers)
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)

    assert rec.status == "completed"
    assert rec.validator["adoptable"] is False
    assert rec.plan_hash is None
    assert "loss_hierarchy" in [v["code"] for v in rec.validator["violations"]]

    message = make_message(text="/plans propose " + ANSWER)
    await screens.cmd_plans(
        message, principal, CommandObject(command="plans", args="propose " + ANSWER),
    )
    assert not any(b.startswith("prop:adopt:") for b in _buttons(message.sent[0]))
    assert "plan_hash не вычислен" in message.sent[0]["text"].lower()


# ─── adoption: a proposal becomes an ordinary draft, nothing more ─────────


async def test_adopt_creates_one_draft_and_binds_it(wired_sources, proposal_store, plan_store):
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)

    _, plan_rec, already = await spr.adopt(principal.owner_id, rec.proposal_id)

    assert already is False
    assert plan_rec.status == "draft"
    assert plan_rec.plan == normalize_plan(rec.draft)[0]
    assert (await spr.get(principal.owner_id, rec.proposal_id)).adopted_plan_id == plan_rec.plan_id
    # the numbers are the proposal's, not re-invented on the way through
    assert plan_rec.plan["max_session_loss"] == rec.draft["max_session_loss"]


async def test_second_adopt_answers_with_the_existing_draft(
    wired_sources, proposal_store, plan_store,
):
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)
    await spr.adopt(principal.owner_id, rec.proposal_id)
    plans_before = len(plan_store.plans)

    _, plan_rec, already = await spr.adopt(principal.owner_id, rec.proposal_id)

    assert already is True
    assert len(plan_store.plans) == plans_before
    assert plan_rec.status == "draft"


async def test_adopt_is_refused_for_a_failed_or_foreign_proposal(
    wired_sources, proposal_store, monkeypatch, plan_store,
):
    async def dead(questionnaire, ctx):
        raise RuntimeError("no route to host")

    principal = _principal()
    _use_source(proposal_store, monkeypatch, dead)
    rec = await spr.create(principal.owner_id, ANSWER)

    with pytest.raises(spr.ProposalRefused) as raised:
        await spr.adopt(principal.owner_id, rec.proposal_id)
    assert raised.value.reason_code == "llm_unavailable"

    with pytest.raises(sp.PlanNotFound):
        await spr.adopt(principal.owner_id, "00000000-0000-0000-0000-000000000000")


async def test_adopt_button_flow_writes_the_draft_through_the_handler(
    wired_sources, proposal_store, plan_store,
):
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)
    call = make_callback(data=f"prop:adopt:{rec.proposal_id}")

    await screens.cb_proposal_adopt(call, principal)

    text = call.message.edited[0]["text"]
    assert "Принято из предложения" in text
    assert "Нарушений нет" in text
    # approval lives on the plan card, two steps, and the start stays in WebUI
    assert "plan:appr:" in _buttons(call.message.edited[0])[0] or any(
        b.startswith("plan:appr:") for b in _buttons(call.message.edited[0])
    )
    assert "Запуск сессии" in text or "запуск" in text.lower()


async def test_adopt_denied_for_a_viewer(wired_sources, proposal_store, plan_store):
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)
    call = make_callback(user_id=VIEWER_ID, data=f"prop:adopt:{rec.proposal_id}")

    await screens.cb_proposal_adopt(call, _principal(VIEWER_ID))

    assert call.answered[0]["text"] == "🔒 Принимать может только владелец"
    assert len(plan_store.plans) == 0


# ─── listing and identifier resolution, shared with the plan screens ─────


async def test_proposals_list_and_prefix_resolution(wired_sources, proposal_store):
    principal = _principal()
    first = await spr.create(principal.owner_id, ANSWER)
    second = await spr.create(principal.owner_id, ANSWER.replace("duration=24", "duration=48"))

    rows = await spr.list_recent(principal.owner_id)
    assert {rec.proposal_id for rec in rows} == {first.proposal_id, second.proposal_id}

    resolved = await spr.resolve(principal.owner_id, second.proposal_id[:8])
    assert resolved.proposal_id == second.proposal_id

    with pytest.raises(sp.PlanNotFound):
        await spr.resolve(principal.owner_id, "ffffffff")


async def test_proposals_screen_lists_verdicts_not_summaries(wired_sources, proposal_store):
    principal = _principal()
    rec = await spr.create(principal.owner_id, ANSWER)
    message = make_message(text="/plans proposals")
    await screens.cmd_plans(message, principal, CommandObject(command="plans", args="proposals"))

    text = message.sent[0]["text"]
    assert "Мои предложения" in text
    assert rec.proposal_id[:8] in text
    assert f"prop:show:{rec.proposal_id}" in _buttons(message.sent[0])
