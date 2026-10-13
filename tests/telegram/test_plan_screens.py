"""P3c gate — the plan screens over the shared store and the shared pipeline.

The point of these tests is not that a card renders, but that the card cannot
disagree with the dashboard: the bot's preview must be the output of
:func:`trading_math.plan_validation.validate_plan` and nothing else, an edit must
be re-hashed by the contract, and an approval must be impossible against a version
the user was not shown.

Transport boundary only is faked (in-memory store, fixture Redis), exactly as the
P2 gate does it: ``services.simulation_plans`` → ``application.presenters`` →
``handlers.simulation_v3`` run for real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.filters import CommandObject
from conftest import CONTRACT, OWNER_ID, VIEWER_ID, make_callback, make_message, snapshot_payload

from handlers import simulation_v3 as screens
from services import simulation_plans as sp
from trading_math.plan_contract import plan_hash
from trading_math.plan_validation import validate_plan


def _principal(user_id: int = OWNER_ID):
    from security.principal import resolve

    principal = resolve(user_id)
    assert principal is not None
    return principal


def _token(digest: str | None) -> str:
    assert digest and digest.startswith("sha256:")
    return digest.split(":", 1)[1][:12]


def _buttons(outcome: dict) -> list[str]:
    """Callback data of a rendered keyboard.

    The fake transport stores the markup in the recorded call (Telegram sends it
    that way), so a test must read it there rather than from the inbound object.
    """
    markup = outcome["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ─── list screen ─────────────────────────────────────────────────────────


async def test_plans_list_is_empty_until_the_owner_creates_one(wired_sources, plan_store):
    message = make_message(text="/plans")
    await screens.show_plans(message, _principal())

    text = message.sent[0]["text"]
    assert "Мои планы" in text
    assert "Своих планов ещё нет" in text
    # the empty screen still offers the write affordance only to a writer
    assert "plan:new" in _buttons(message.sent[0])


async def test_plans_list_shows_stored_fields_and_hash_prefix(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)

    message = make_message(text="/plans")
    await screens.show_plans(message, principal)
    text = message.sent[0]["text"]

    assert rec.plan_id[:8] in text
    assert "черновик" in text
    assert "live_paper" in text
    assert rec.plan_hash.replace("sha256:", "")[:8] in text
    # the honest footer: no fake «start» affordance in the chat
    assert "запуск сессии — только WebUI" in text


async def test_list_callback_carries_real_plan_rows(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    call = make_callback(data="plan:list")

    await screens.cb_plans_list(call, principal)

    assert f"plan:show:{rec.plan_id}" in _buttons(call.message.edited[0])


# ─── draft creation and the shared hash ──────────────────────────────────


async def test_new_draft_is_contract_valid_and_carries_a_plan_hash(wired_sources, plan_store):
    message = make_message(text="/plans new")
    await screens.cmd_plans(message, _principal(), CommandObject(command="plans", args="new"))

    text = message.sent[0]["text"]
    assert "Новый черновик" in text
    assert "sha256:" in text
    assert "Нарушений нет" in text
    assert len(plan_store.plans) == 1


async def test_bot_hash_is_the_contract_hash_of_the_same_body(wired_sources, plan_store):
    """No bot-side hashing: the stored digest is exactly ``plan_hash(plan)``."""
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)

    assert rec.plan_hash == plan_hash(rec.plan)
    result = await sp.preview(rec)
    assert result["plan_hash"] == rec.plan_hash
    assert result["can_approve"] is True


async def test_preview_matches_the_pipeline_for_the_same_body(wired_sources, plan_store):
    """The card is a rendering of ``validate_plan``, violations included."""
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    # a *semantic* break: the contract stores it, the domain refuses to approve it
    body = dict(rec.plan)
    body["max_session_loss"] = "10.00"  # below max_daily_loss 50

    direct = await validate_plan(
        body, market=sp.market_state, coverage=sp._coverage,
        now=datetime.now(timezone.utc),
    )
    edited = await sp.set_field(principal.owner_id, rec.plan_id, "max_session_loss", "10.00")
    card = await sp.preview(edited)

    assert [v["code"] for v in card["violations"]] == [v["code"] for v in direct["violations"]]
    assert "loss_hierarchy" in [v["code"] for v in card["violations"]]
    assert card["can_approve"] is False
    assert card["plan_hash"] is None


# ─── write policy ────────────────────────────────────────────────────────


async def test_viewer_cannot_create_a_draft(wired_sources, plan_store):
    message = make_message(user_id=VIEWER_ID, text="/plans new")
    await screens.cmd_plans(message, _principal(VIEWER_ID), CommandObject(command="plans", args="new"))

    text = message.sent[0]["text"]
    assert "только владелец аккаунта" in text
    assert "role_denied" in text
    assert not plan_store.plans


async def test_refused_edit_writes_nothing(wired_sources, plan_store):
    """A value the contract cannot even parse never reaches the stored draft.

    The line is drawn at *parsing*: a field outside its declared range is not
    saved, while a cross-field violation (loss hierarchy) is saved and then
    blocks approval — exactly as the dashboard form does, so chat and web store
    the same draft for the same input.
    """
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    before = dict(rec.plan)
    before_hash = rec.plan_hash

    with pytest.raises(sp.ContractRefused):
        await sp.set_field(principal.owner_id, rec.plan_id, "max_leverage", 99)

    stored = plan_store.plans[rec.plan_id]
    assert stored.plan == before
    assert stored.plan_hash == before_hash


async def test_semantic_violation_is_stored_and_blocks_approval(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    updated = await sp.set_field(principal.owner_id, rec.plan_id, "max_daily_loss", "400.00")

    # stored in the contract's canonical form (trailing zeros folded), like the
    # web form does — and then it refuses itself at approval
    assert updated.plan["max_daily_loss"] == "400"
    result = await sp.preview(updated)
    assert "loss_hierarchy" in [v["code"] for v in result["violations"]]
    assert result["can_approve"] is False


async def test_refused_edit_renders_the_domain_violation(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    message = make_message(
        text=f"/plans set {rec.plan_id[:8]} max_leverage 99",
    )
    await screens.cmd_plans(
        message, principal,
        CommandObject(command="plans", args=f"set {rec.plan_id[:8]} max_leverage 99"),
    )

    text = message.sent[0]["text"]
    assert "операция не выполнена" in text
    assert "plan_invalid" in text
    assert "field_range" in text
    assert "max_leverage" in text


async def test_valid_edit_bumps_version_and_rehashes(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    before_hash = rec.plan_hash
    updated = await sp.set_field(principal.owner_id, rec.plan_id, "max_leverage", 2)

    assert updated.plan["max_leverage"] == 2
    assert updated.plan_hash != before_hash
    assert updated.plan_hash == plan_hash(updated.plan)


async def test_unknown_field_is_named_not_invented(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)

    with pytest.raises(sp.FieldUnknown):
        await sp.set_field(principal.owner_id, rec.plan_id, "magic_indicator", "on")


async def test_foreign_plan_is_not_found_rather_than_not_authorized(wired_sources, plan_store):
    """Absence is not disclosed: someone else's id answers as *no such plan*."""
    owner = _principal()
    rec = await sp.create_draft(owner.owner_id)
    other = _principal(VIEWER_ID)

    with pytest.raises(sp.PlanNotFound):
        await sp.get_mine(other.owner_id, rec.plan_id)


# ─── approval ladder ─────────────────────────────────────────────────────


async def test_approve_requires_two_steps_and_pins_the_version(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    result = await sp.preview(rec)
    token = _token(result["plan_hash"])

    first = make_callback(data=f"plan:appr:{rec.plan_id}:{token}")
    await screens.cb_plan_approve(first, principal)
    assert plan_store.plans[rec.plan_id].status == "draft"
    assert "Подтвердите утверждение" in first.message.edited[0]["text"]

    second = make_callback(data=f"plan:confirm:{rec.plan_id}:{token}")
    await screens.cb_plan_confirm(second, principal)
    assert plan_store.plans[rec.plan_id].status == "approved"
    assert "План утверждён" in second.message.edited[0]["text"]
    # the chat never starts a session (Q-10)
    assert "WebUI" in second.message.edited[0]["text"]


async def test_double_confirmation_cannot_approve_twice(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    token = _token((await sp.preview(rec))["plan_hash"])

    await screens.approve_step1(make_callback(data=f"plan:appr:{rec.plan_id}:{token}"), principal,
                                rec.plan_id, token)
    await screens.approve_step2(make_callback(data=f"plan:confirm:{rec.plan_id}:{token}"), principal,
                                rec.plan_id, token)
    third = make_callback(data=f"plan:confirm:{rec.plan_id}:{token}")
    await screens.approve_step2(third, principal, rec.plan_id, token)

    assert plan_store.plans[rec.plan_id].status == "approved"
    assert "операция не выполнена" in third.message.edited[0]["text"]


async def test_stale_token_is_refused_after_the_draft_changed(wired_sources, plan_store):
    """MC-11: an old card cannot pin a version nobody was shown."""
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    token = _token((await sp.preview(rec))["plan_hash"])

    await sp.set_field(principal.owner_id, rec.plan_id, "max_leverage", 2)

    call = make_callback(data=f"plan:confirm:{rec.plan_id}:{token}")
    await screens.approve_step2(call, principal, rec.plan_id, token)

    assert plan_store.plans[rec.plan_id].status == "draft"
    assert "plan_version_changed" in call.message.edited[0]["text"]


async def test_viewer_cannot_approve(wired_sources, plan_store):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    token = _token((await sp.preview(rec))["plan_hash"])

    call = make_callback(user_id=VIEWER_ID, data=f"plan:appr:{rec.plan_id}:{token}")
    await screens.cb_plan_approve(call, _principal(VIEWER_ID))

    assert plan_store.plans[rec.plan_id].status == "draft"
    assert call.answered[0]["text"].startswith("🔒")


async def test_degraded_feed_blocks_approval_in_the_chat(wired_sources, plan_store):
    """The gate that matters for P3: the bot uses the domain's freshness verdict."""
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    wired_sources["redis"].values.clear()
    wired_sources["redis"].values[f"market:perpetual:htx:{CONTRACT}"] = snapshot_payload(
        age_seconds=400.0,
    )

    message = make_message(text="/plans")
    await screens.show_plan(message, principal, rec.plan_id[:8])
    text = message.sent[0]["text"]

    assert "stale_quote" in text
    assert "can_approve: <b>false</b>" in text
    assert "plan_hash не вычислен" in text
    # and the button is not offered at all
    assert not any((b or "").startswith("plan:appr:") for b in _buttons(message.sent[0]))


async def test_absent_snapshot_is_a_stale_quote_violation(wired_sources, plan_store):
    """No key in Redis is *missing data*, which the domain renders as a blocker."""
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    wired_sources["redis"].values.clear()

    result = await sp.preview(rec)
    assert [v["code"] for v in result["violations"]] == ["stale_quote"]
    assert result["can_approve"] is False


async def test_unreachable_market_source_is_a_violation_not_an_absence(
    wired_sources, plan_store, monkeypatch
):
    """A dead transport must not read as «no problem found» (fail-closed)."""
    import storage.redis_client as redis_module

    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    monkeypatch.setattr(redis_module, "get_redis", lambda: None)

    result = await sp.preview(rec)
    assert [v["code"] for v in result["violations"]] == ["quote_source_unavailable"]
    assert result["can_approve"] is False


# ─── draft TTL (TB-10 / BOT_WIZARD_TTL_HOURS) ────────────────────────────


async def test_old_draft_refuses_writes_but_stays_in_history(wired_sources, plan_store, monkeypatch):
    principal = _principal()
    rec = await sp.create_draft(principal.owner_id)
    plan_store.plans[rec.plan_id].created_at = datetime.now(timezone.utc) - timedelta(hours=30)

    with pytest.raises(sp.DraftExpired):
        await sp.set_field(principal.owner_id, rec.plan_id, "max_leverage", 2)
    assert rec.plan_id in plan_store.plans  # expiry is a write policy, not a delete

    with pytest.raises(sp.DraftExpired):
        await sp.approve(principal.owner_id, rec.plan_id, rec.plan_hash or "")


def test_wizard_ttl_default_and_override(monkeypatch):
    monkeypatch.delenv("BOT_WIZARD_TTL_HOURS", raising=False)
    assert sp.wizard_ttl_hours() == 24
    monkeypatch.setenv("BOT_WIZARD_TTL_HOURS", "6")
    assert sp.wizard_ttl_hours() == 6
    monkeypatch.setenv("BOT_WIZARD_TTL_HOURS", "not-a-number")
    assert sp.wizard_ttl_hours() == 24
    monkeypatch.setenv("BOT_WIZARD_TTL_HOURS", "0")
    assert sp.wizard_ttl_hours() == 24


# ─── store availability (fail-closed, never memory) ─────────────────────


async def test_store_outage_is_named_and_writes_nothing(monkeypatch):
    async def _unavailable():
        raise sp.StoreUnavailable("хранилище планов недоступно: Postgres не подключён")

    monkeypatch.setattr(sp, "get_store", _unavailable)
    message = make_message(text="/plans")
    await screens.show_plans(message, _principal())

    assert "simulation_store_unavailable" in message.sent[0]["text"]


# ─── redirect identity (TB-03) ───────────────────────────────────────────


async def test_plan_command_and_plans_command_open_the_same_screen(wired_sources, plan_store):
    """``/plan`` is a redirect to the one implementation, not a second screen."""
    from handlers import deprecated

    principal = _principal()
    await sp.create_draft(principal.owner_id)

    legacy = make_message(user_id=OWNER_ID, text="/plan")
    await deprecated.cmd_plan(legacy, principal)
    canonical = make_message(user_id=OWNER_ID, text="/plans")
    await screens.show_plans(canonical, principal)

    assert "/plans" in legacy.sent[0]["text"] and "устарела" in legacy.sent[0]["text"]
    assert legacy.sent[1]["text"].split("req ")[0] == canonical.sent[0]["text"].split("req ")[0]


# ─── replay coverage path uses the same pipeline ─────────────────────────


async def test_replay_draft_reports_coverage_gap(wired_sources, plan_store, monkeypatch):
    principal = _principal()
    body = sp.skeleton_body(now=datetime.now(timezone.utc))
    body.update({
        "mode": "historical_replay",
        "start_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "end_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "seed": 7,
    })
    rec = await sp.create_draft(principal.owner_id, body)

    async def _thin(key, start, end):
        return {"required_intervals": 1440, "available_intervals": 900,
                "reason_code": "missing_intervals"}

    monkeypatch.setattr(sp, "_coverage", _thin)
    result = await sp.preview(rec)

    assert "missing_intervals" in [v["code"] for v in result["violations"]]
    assert result["can_approve"] is False
