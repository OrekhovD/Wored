"""V3 simulation plans in Telegram: list, preview, draft, edit, approve, proposals.

The screen surface is deliberately thin.  Everything that decides *whether a plan
is valid* lives in :mod:`trading_math` and is reached through
:mod:`services.simulation_plans` (plans) and :mod:`services.simulation_proposals`
(agent proposals), so the violations and the ``plan_hash`` on this card are the
output of the same pipeline the dashboard calls (ТЗ P3 exit gate).

Three rules shape the UX:

* **no invented numbers.**  A card shows stored fields and domain-pipeline
  output only.  Absence is ``N/A``; a plan that never normalized shows its
  violation list instead of a field table;
* **authority is derived, never parsed.**  Callback data carries a plan id and a
  hash prefix, and that is *only* a pointer: the handler re-resolves the
  Principal, re-reads the plan scoped to that owner, re-runs the full validation
  at that moment, and refuses if the hash no longer matches what the user was
  shown.  A stale card can therefore never approve a different version, and a
  foreign id never resolves at all;
* **an action has one implementation.**  A slash command and the button of the
  same intent call the same coroutine below; the historical ``/plan`` and
  ``/plans`` names are redirected here and never re-implemented (ТЗ §2, TB-03).

Session start is absent from this module by design (curator decision Q-10): an
approved plan is started in the WebUI, and the card says so instead of pretending
otherwise.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from application import presenters as ui
from handlers import telegram_surface as surface
from security import guards
from security import principal as principal_mod
from security.principal import Principal
from services import simulation_plans as plans
from services import simulation_proposals as proposals

log = logging.getLogger("chatbot.plans")
router = Router(name="simulation_v3")

#: how many plans one card lists
LIST_LIMIT = 10

#: fields the help line invites the owner to edit; the contract accepts every
#: name in ``PLAN_FIELDS``, so this is a hint list, not a whitelist
_EDIT_HINTS = (
    "mode", "start_at", "end_at", "timezone", "instrument_key",
    "manual_budget", "auto_budget", "max_leverage", "max_risk_per_order",
    "max_daily_loss", "max_session_loss", "max_total_exposure",
    "max_open_positions", "allowed_sides", "strategy_id", "strategy_version",
)

#: one tap per second per actor for write intents; the store's immutability rule
#: is what actually prevents a double approval, this only protects against
#: accidental double taps
_write_limiter = guards.RateLimiter(1.0)


# ─── keyboards ──────────────────────────────────────────────────────────────


def _row(*pairs: tuple[str, str]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data) for text, data in pairs]


def plans_keyboard(*, can_write: bool, plan_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = list(plan_rows or [])
    if can_write:
        rows.append(_row(("🆕 Новый черновик", "plan:new")))
    rows.append(_row(("⬅️ Сессия", "nav:session"), ("🏠 Сейчас", "nav:now")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_keyboard(rec: Any, result: dict[str, Any], *, can_write: bool) -> InlineKeyboardMarkup:
    """Detail-card buttons: re-check always, approve only for an own draft."""
    pid = rec.plan_id
    rows = [_row(("🔄 Перепроверить", f"plan:show:{pid}"))]
    if can_write and rec.status == "draft" and result.get("can_approve"):
        token = _hash_token(result.get("plan_hash"))
        if token:
            rows.append(_row(("✅ Утвердить эту версию", f"plan:appr:{pid}:{token}")))
    rows.append(_row(("⬅️ Список", "plan:list"), ("🏠 Сейчас", "nav:now")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(plan_id: str, hash_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        _row(("📌 Да, утвердить эту версию", f"plan:confirm:{plan_id}:{hash_token}")),
        _row(("↩️ Отмена", f"plan:show:{plan_id}")),
    ])


def proposals_keyboard(
    *, can_write: bool, proposal_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = list(proposal_rows or [])
    if can_write:
        rows.append(_row(("🧭 Новая анкета", "prop:help")))
    rows.append(_row(("📋 Мои планы", "plan:list"), ("🏠 Сейчас", "nav:now")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proposal_keyboard(rec: Any, *, can_write: bool) -> InlineKeyboardMarkup:
    """Refresh always; «Принять» only for an own, validated, not-yet-adopted draft."""
    pid = rec.proposal_id
    rows = [_row(("🔄 Обновить", f"prop:show:{pid}"))]
    validator = rec.validator or {}
    if (
        can_write
        and rec.status == "completed"
        and validator.get("adoptable")
        and not getattr(rec, "adopted_plan_id", None)
    ):
        rows.append(_row(("📥 Принять как черновик", f"prop:adopt:{pid}")))
    rows.append(_row(("⬅️ Список", "prop:list"), ("🏠 Сейчас", "nav:now")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hash_token(plan_hash_value: Any) -> str:
    """Short binding token of a ``sha256:`` digest, empty when there is none."""
    if not isinstance(plan_hash_value, str) or not plan_hash_value.startswith("sha256:"):
        return ""
    return plan_hash_value.split(":", 1)[1][:12]


# ─── reply helpers ──────────────────────────────────────────────────────────


async def _reply(target: Message | CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> bool:
    """Send a card, or refresh the one already on screen.

    Pressing «Перепроверить» on a plan that has not changed is a no-op edit: the
    repaint rule lives in :mod:`handlers.telegram_surface` (Q-16).  Here it means
    a successful re-check with nothing to repaint, not an error; left unhandled it
    would abort the callback and the owner would see a dead button.
    """
    try:
        if isinstance(target, Message):
            await target.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if not surface.is_no_op_edit(exc):
            raise
        log.info("plan card already up to date (%s)", getattr(target, "id", "message"))
        return False
    return True


async def _refused(target: Message | CallbackQuery, exc: plans.PlanServiceError) -> None:
    card = ui.build_plan_refused_card(
        exc.reason_code, exc.message, violations=getattr(exc, "violations", None),
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
        plans_keyboard(can_write=False),
    )


def _tz(principal: Principal) -> str:
    return principal_mod.default_timezone()


# ─── id resolution ──────────────────────────────────────────────────────────


async def _resolve_plan(principal: Principal, token: str) -> Any:
    """Full uuid or a unique prefix of this owner's own plans.

    The rule itself lives in :func:`services.simulation_plans.resolve_prefixed`,
    which proposals use too: one answer to "what counts as a resolvable id"
    across the console (TB-03), and a foreign or ambiguous prefix can never be
    used to probe somebody else's data.
    """

    async def _own_plan_ids() -> list[str]:
        recents = await plans.list_mine(principal.owner_id, None, 50)
        return [rec.plan_id for rec in recents]

    resolved = await plans.resolve_prefixed(
        token, _own_plan_ids,
        entity_label="плана", entity_dative="планам",
        not_found="такого идентификатора среди ваших планов нет",
    )
    return await plans.get_mine(principal.owner_id, resolved)


def _denied_write(target: Message | CallbackQuery, principal: Principal, what: str) -> Any:
    """A non-owner role never reaches a write use case (ТЗ §3 roles)."""
    return plans.PlanServiceError(
        f"{what} может только владелец аккаунта", reason_code="role_denied"
    )


# ─── use cases: command and button both call these ──────────────────────────


async def show_plans(target: Message | CallbackQuery, principal: Principal, *, note: str = "") -> None:
    """«Мои планы» — the screen the ТЗ reaches from ``Сессия``."""
    try:
        rows = await plans.list_mine(principal.owner_id, None, LIST_LIMIT)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_plan_list_card(rows, tz_name=_tz(principal), store_note=note)
    plan_rows = [
        _row((f"🔍 {rec.plan_id[:8]} · {ui.esc(rec.status)}", f"plan:show:{rec.plan_id}"))
        for rec in rows
    ] or None
    keyboard = plans_keyboard(can_write=principal.may_mutate, plan_rows=plan_rows)
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · "
        f"{ui.quality_badge(card.quality)} хранилище планов v3 · {ui.zone_label(_tz(principal))}</i>",
        keyboard,
    )


async def show_plan(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """Preview card: fields, limits, worst loss, coverage/quote, violations, hash."""
    try:
        rec = await _resolve_plan(principal, token)
        result = await plans.preview(rec)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_plan_preview_card(rec, result, tz_name=_tz(principal))
    footer = (
        f"<i>req <code>{card.request_id}</code> · правка поля: "
        f"<code>/plans set {rec.plan_id[:8]} &lt;поле&gt; &lt;значение&gt;</code></i>\n"
        f"<i>частые поля: {ui.esc(', '.join(_EDIT_HINTS[:8]))} …</i>"
    )
    await _reply(
        target,
        f"{card.render()}\n\n{footer}",
        plan_keyboard(rec, result, can_write=principal.may_mutate),
    )


async def new_draft(target: Message | CallbackQuery, principal: Principal) -> None:
    """Create a draft whose fields already pass the contract, then show it."""
    try:
        rec = await plans.create_draft(principal.owner_id)
        result = await plans.preview(rec)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_plan_preview_card(
        rec, result, tz_name=_tz(principal), title_prefix="🆕 Новый черновик",
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · "
        f"правка: <code>/plans set {rec.plan_id[:8]} &lt;поле&gt; &lt;значение&gt;</code></i>",
        plan_keyboard(rec, result, can_write=True),
    )


async def set_plan_field(
    target: Message | CallbackQuery, principal: Principal, token: str, field: str, value: str
) -> None:
    try:
        rec = await _resolve_plan(principal, token)
        updated = await plans.set_field(principal.owner_id, rec.plan_id, field, value)
        result = await plans.preview(updated)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_plan_preview_card(
        updated, result, tz_name=_tz(principal),
        title_prefix=f"✏️ {ui.esc(field)} обновлено",
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · значение записано, "
        "потому что контракт его разобрал; нарушение кросс-полей останется в "
        "списке ниже и заблокирует утверждение</i>",
        plan_keyboard(updated, result, can_write=principal.may_mutate),
    )


async def approve_step1(
    target: CallbackQuery, principal: Principal, plan_id: str, hash_token: str
) -> None:
    """Show what is about to be pinned, and ask for an explicit confirmation."""
    try:
        rec = await plans.get_mine(principal.owner_id, plan_id)
        result = await plans.preview(rec)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    if rec.status != "draft":
        await _refused(target, plans.NotDraft(f"план уже в состоянии «{rec.status}»"))
        return
    actual = _hash_token(result.get("plan_hash"))
    if not result.get("can_approve"):
        card = ui.build_plan_refused_card(
            "plan_not_approvable",
            "между карточкой и этим нажатием изменились рынок, время или поля плана — "
            "требуется свежая проверка",
            violations=list(result.get("violations") or []),
        )
        await _reply(
            target,
            f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
            plan_keyboard(rec, result, can_write=True),
        )
        return
    if actual != hash_token:
        await _refused(target, plans.VersionChanged(
            "версия плана изменилась с момента карточки — откройте предпросмотр заново"
        ))
        return
    worst = (result.get("worst_case_risk") or {}).get("worst_allowed_loss_usdt")
    text = (
        "📌 <b>Подтвердите утверждение</b>\n"
        f"План <code>{ui.esc(rec.plan_id[:8])}</code> v{rec.version} · "
        f"<code>{ui.esc(result.get('plan_hash'))}</code>\n"
        f"Худший допустимый убыток: {ui.plain(worst)} USDT\n"
        "Утверждение закрепляет эту версию: изменить её можно будет только как новую версию.\n"
        "<i>Запуск сессии остаётся в WebUI (решение Q-10).</i>"
    )
    await _reply(target, text, confirm_keyboard(rec.plan_id, hash_token))


async def approve_step2(
    target: CallbackQuery, principal: Principal, plan_id: str, hash_token: str
) -> None:
    """The only place a plan changes state, and it re-validates immediately."""
    try:
        rec = await plans.get_mine(principal.owner_id, plan_id)
        if _hash_token(rec.plan_hash) != hash_token:
            raise plans.VersionChanged(
                "версия плана изменилась с момента карточки — откройте предпросмотр заново"
            )
        approved = await plans.approve(principal.owner_id, plan_id, rec.plan_hash or "")
    except plans.ApprovalRefused as exc:
        card = ui.build_plan_refused_card(exc.reason_code, exc.message, violations=exc.violations)
        await _reply(
            target,
            f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
            plans_keyboard(can_write=True),
        )
        return
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    when = ui.NA
    if approved.approved_at is not None:
        clock, zone = ui.format_time(approved.approved_at.isoformat(), _tz(principal))
        when = f"{clock} ({zone})"
    text = (
        "✅ <b>План утверждён</b>\n"
        f"<code>{ui.esc(approved.plan_id[:8])}</code> v{approved.version} · "
        f"<code>{ui.esc(approved.plan_hash)}</code>\n"
        f"утверждён: {ui.esc(when)} · {ui.esc(principal_mod.default_timezone())}\n"
        "<i>Дальше — WebUI: запуск сессии из этого плана в чат не выносится.</i>"
    )
    await _reply(target, text, plans_keyboard(can_write=True))


# ─── proposals: questionnaire → agent draft → adopt (ТЗ §5 U05, phase P3d) ───


async def show_questionnaire(target: Message | CallbackQuery, principal: Principal) -> None:
    """The questionnaire card — the only door into a proposal.

    There is no free-text branch: an owner who describes the wish in prose is
    invited back to ``key=value`` answers, because the deterministic parser is
    what makes a chat proposal equal to a dashboard proposal (ТЗ TB-11).
    """
    card = ui.build_questionnaire_card(
        options=proposals.questionnaire_options(),
        source_label=proposals.source_label(),
        default_instrument=plans.default_instrument_key(),
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · ответ одной строкой, "
        f"подставляя свои числа: <code>{ui.esc(proposals.answer_template())}</code></i>",
        proposals_keyboard(can_write=principal.may_mutate),
    )


async def _show_proposal_record(
    target: Message | CallbackQuery, principal: Principal, rec: Any
) -> None:
    """The one render path of a proposal, shared by answer, list and button.

    A proposal that was already adopted is shown *with* its draft, so a second
    tap can never look like a fresh offer (ТЗ TB-12).
    """
    adopted = None
    if getattr(rec, "adopted_plan_id", None):
        try:
            adopted = await plans.get_mine(principal.owner_id, rec.adopted_plan_id)
        except plans.PlanServiceError:
            adopted = None
    card = ui.build_proposal_card(rec, tz_name=_tz(principal), adopted_plan=adopted)
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · просмотр: "
        f"<code>/plans proposal {ui.esc(rec.proposal_id[:8])}</code></i>",
        proposal_keyboard(rec, can_write=principal.may_mutate),
    )


async def run_proposal(target: Message | CallbackQuery, principal: Principal, answer: str) -> None:
    """One questionnaire in, one proposal card out — including a failed one."""
    try:
        rec = await proposals.create(
            principal.owner_id, answer, telegram_user_id=principal.telegram_user_id,
        )
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    await _show_proposal_record(target, principal, rec)


async def list_proposals(target: Message | CallbackQuery, principal: Principal) -> None:
    try:
        rows = await proposals.list_recent(principal.owner_id, proposals.LIST_LIMIT)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_proposal_list_card(rows, tz_name=_tz(principal))
    proposal_rows = [
        _row((f"🔍 {rec.proposal_id[:8]} · {ui.esc(rec.status)}", f"prop:show:{rec.proposal_id}"))
        for rec in rows
    ] or None
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · "
        f"{ui.quality_badge(card.quality)} предложения · {ui.zone_label(_tz(principal))}</i>",
        proposals_keyboard(can_write=principal.may_mutate, proposal_rows=proposal_rows),
    )


async def show_proposal(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    try:
        rec = await proposals.resolve(principal.owner_id, token)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    await _show_proposal_record(target, principal, rec)


async def adopt_proposal(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """A proposal becomes an ordinary draft — and is then handled like one.

    The card that answers is the *plan* preview card, not a proposal card: from
    this moment the object is a draft with its own version, its own hash and the
    usual two-step approval, and the console must not keep showing it as an
    offer (ТЗ Q-10: no start, no automatic approval from a proposal).
    """
    try:
        rec, plan_rec, already = await proposals.adopt(principal.owner_id, token)
        result = await plans.preview(plan_rec)
    except plans.PlanServiceError as exc:
        await _refused(target, exc)
        return
    card = ui.build_plan_preview_card(
        plan_rec, result, tz_name=_tz(principal),
        title_prefix="♻️ Этот черновик уже был создан" if already
        else "📥 Принято из предложения",
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · предложение "
        f"<code>{ui.esc(rec.proposal_id[:8])}</code> → черновик "
        f"<code>{ui.esc(plan_rec.plan_id[:8])}</code></i>",
        plan_keyboard(plan_rec, result, can_write=principal.may_mutate),
    )


# ─── command surface ────────────────────────────────────────────────────────

_USAGE = (
    "📋 <b>Планы</b> — как обращаться:\n"
    "<code>/plans</code> — ваши планы (новые сверху)\n"
    "<code>/plans list draft</code> — только черновики\n"
    "<code>/plans show &lt;id или префикс&gt;</code> — предпросмотр: поля, лимиты, "
    "худший убыток, violations, plan_hash\n"
    "<code>/plans new</code> — черновик с полями, которые проходят контракт\n"
    "<code>/plans set &lt;id&gt; &lt;поле&gt; &lt;значение&gt;</code> — изменить одно поле\n"
    "<code>/plans propose</code> — анкета предложения; "
    "<code>/plans propose &lt;ключ&gt;=&lt;значение&gt; …</code> — ваш ответ\n"
    "<code>/plans proposals</code> · <code>/plans proposal &lt;id&gt;</code> — что предложил агент\n"
    "<code>/plans adopt &lt;id&gt;</code> — принять предложение как обычный черновик\n"
    "Утверждение — кнопкой ✅ с карточки плана (два шага, с повторной проверкой).\n"
    "Запуск сессии — только WebUI."
)


async def show_filtered(message: Message, principal: Principal, status: str) -> None:
    if status not in plans.PLAN_STATUSES:
        await _refused(
            message,
            plans.PlanServiceError(
                f"статуса «{status}» нет; разрешено: {', '.join(plans.PLAN_STATUSES)}",
                reason_code="bad_status_filter",
            ),
        )
        return
    try:
        rows = await plans.list_mine(principal.owner_id, status, LIST_LIMIT)
    except plans.PlanServiceError as exc:
        await _refused(message, exc)
        return
    card = ui.build_plan_list_card(rows, tz_name=_tz(principal))
    plan_rows = [
        _row((f"🔍 {rec.plan_id[:8]} · {ui.esc(rec.status)}", f"plan:show:{rec.plan_id}"))
        for rec in rows
    ] or None
    await message.answer(
        f"<b>{ui.esc(status)}</b>\n{card.body}\n<i>req <code>{card.request_id}</code></i>",
        reply_markup=plans_keyboard(can_write=principal.may_mutate, plan_rows=plan_rows),
        parse_mode="HTML",
    )


@router.message(Command("plans"))
async def cmd_plans(message: Message, principal: Principal, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await show_plans(message, principal)
        return
    parts = args.split(None, 3)
    sub = parts[0].lower()
    if sub in {"help", "?"}:
        await message.answer(_USAGE, parse_mode="HTML")
        return
    if sub == "new":
        if not principal.may_mutate:
            await _refused(message, _denied_write(message, principal, "создавать планы"))
            return
        await new_draft(message, principal)
        return
    if sub == "list":
        await show_filtered(message, principal, parts[1] if len(parts) > 1 else "draft")
        return
    if sub in {"show", "preview", "validate", "view"}:
        await show_plan(message, principal, parts[1] if len(parts) > 1 else "")
        return
    if sub == "set":
        if not principal.may_mutate:
            await _refused(message, _denied_write(message, principal, "менять план"))
            return
        if len(parts) < 4:
            await message.answer(
                "Формат: <code>/plans set &lt;id&gt; &lt;поле&gt; &lt;значение&gt;</code>",
                parse_mode="HTML",
            )
            return
        await set_plan_field(message, principal, parts[1], parts[2], parts[3])
        return
    if sub in {"propose", "agent", "questionnaire"}:
        if not principal.may_mutate:
            await _refused(
                message, _denied_write(message, principal, "запрашивать предложение"),
            )
            return
        answer = args.split(None, 1)[1].strip() if len(parts) > 1 else ""
        if not answer:
            await show_questionnaire(message, principal)
            return
        await run_proposal(message, principal, answer)
        return
    if sub in {"proposals", "props"}:
        await list_proposals(message, principal)
        return
    if sub in {"proposal", "prop"}:
        await show_proposal(message, principal, parts[1] if len(parts) > 1 else "")
        return
    if sub == "adopt":
        if not principal.may_mutate:
            await _refused(message, _denied_write(message, principal, "принимать предложение"))
            return
        if len(parts) < 2:
            await message.answer(
                "Формат: <code>/plans adopt &lt;id или префикс&gt;</code>",
                parse_mode="HTML",
            )
            return
        await adopt_proposal(message, principal, parts[1])
        return
    await message.answer(
        f"🤔 Подкоманда <code>/plans {ui.esc(sub)}</code> не понята.\n\n{_USAGE}",
        parse_mode="HTML",
    )


# ─── callback surface ───────────────────────────────────────────────────────


@router.callback_query(F.data == "plan:list")
async def cb_plans_list(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await show_plans(call, principal)


@router.callback_query(F.data == "plan:new")
async def cb_plan_new(call: CallbackQuery, principal: Principal) -> None:
    if not principal.may_mutate:
        await call.answer("🔒 Только владелец", show_alert=False)
        await _refused(call, _denied_write(call, principal, "создавать планы"))
        return
    if not _write_limiter.allow(principal.telegram_user_id, "plan:new"):
        await call.answer("⏳ Создаём черновик, подождите секунду", show_alert=False)
        return
    await call.answer()
    await new_draft(call, principal)


@router.callback_query(F.data.startswith("plan:show:"))
async def cb_plan_show(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await show_plan(call, principal, parts[2])


@router.callback_query(F.data.startswith("plan:appr:"))
async def cb_plan_approve(call: CallbackQuery, principal: Principal) -> None:
    if not principal.may_mutate:
        await call.answer("🔒 Утверждать может только владелец", show_alert=False)
        return
    parts = (call.data or "").split(":")  # plan:appr:<uuid>:<hash12>
    if len(parts) < 4:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await call.answer()
    await approve_step1(call, principal, parts[2], parts[3])


@router.callback_query(F.data.startswith("plan:confirm:"))
async def cb_plan_confirm(call: CallbackQuery, principal: Principal) -> None:
    if not principal.may_mutate:
        await call.answer("🔒 Утверждать может только владелец", show_alert=False)
        return
    parts = (call.data or "").split(":")
    if len(parts) < 4:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    if not _write_limiter.allow(principal.telegram_user_id, f"plan:confirm:{parts[2]}"):
        await call.answer("⏳ Это утверждение уже обрабатывается", show_alert=False)
        return
    await call.answer()
    await approve_step2(call, principal, parts[2], parts[3])


@router.callback_query(F.data == "prop:help")
async def cb_proposal_help(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await show_questionnaire(call, principal)


@router.callback_query(F.data == "prop:list")
async def cb_proposals_list(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await list_proposals(call, principal)


@router.callback_query(F.data.startswith("prop:show:"))
async def cb_proposal_show(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await show_proposal(call, principal, parts[2])


@router.callback_query(F.data.startswith("prop:adopt:"))
async def cb_proposal_adopt(call: CallbackQuery, principal: Principal) -> None:
    """Adoption is a write: role, rate and the veto are all checked again."""
    if not principal.may_mutate:
        await call.answer("🔒 Принимать может только владелец", show_alert=False)
        return
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    if not _write_limiter.allow(principal.telegram_user_id, f"prop:adopt:{parts[2]}"):
        await call.answer("⏳ Это предложение уже обрабатывается", show_alert=False)
        return
    await call.answer()
    await adopt_proposal(call, principal, parts[2])
