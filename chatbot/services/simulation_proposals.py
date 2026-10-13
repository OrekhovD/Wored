"""Plan proposals in the Telegram console (ТЗ §5 U05, phase P3d).

One pipeline, two surfaces.  The questionnaire, the agent call, the deterministic
veto and the stores all live in :mod:`trading_math.simulation_proposal` — the
same file the dashboard loads — so a proposal card in chat carries the *same*
``plan_hash`` candidate and the *same* violation list as the web form, which is
what the P3 exit gate asks for.  What this module owns is only the bot's side of
the boundary:

* **the answer format.**  A questionnaire is parsed deterministically from
  ``key=value`` tokens; no LLM ever reads the owner's words to decide what the
  owner asked for.  Unknown keys, duplicate keys and bare tokens are refused with
  the domain's own violation shape, and nothing is written.
* **the store handle.**  ``PostgresProposalStore`` over the bot's own pool,
  fail-closed like the plan store: a proposal that lives only in this process
  would be invisible to the dashboard and could never be adopted.
* **the source policy.**  The default source is the free, reproducible rule
  engine.  A paid agent is opt-in through ``BOT_PROPOSAL_SOURCE=llm`` and is
  resolved lazily, so an untouched installation never opens a provider client.
* **the write line.**  Adoption turns a *validated* proposal into an ordinary
  draft through :mod:`services.simulation_plans`, and nothing else: no approval,
  no session start (ТЗ Q-10), and a proposal the veto rejected stays a proposal.

Failure is a state, not an exception: a slow, quota-blocked or nonsense agent
lands in ``failed`` with an exact reason code and the card sends the owner to the
manual flow (``/plans new`` + ``/plans set``), which is the ТЗ definition of
"AI failure safe" (TB-11).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from trading_math.instrument_registry import InstrumentNotFound, load_registry
from trading_math.simulation_proposal import (
    GOALS,
    INVOLVEMENT,
    PROPOSAL_TIMEOUT_SECONDS,
    SIDES_PREF,
    AdoptRefused,
    ProposalRecord,
    ProposalSource,
    PostgresProposalStore,
    adopt_plan_body,
    build_proposal_ctx,
    normalize_questionnaire,
    rules_source,
    run_proposal,
)

from services import simulation_plans as plans

log = logging.getLogger("chatbot.proposals")

#: answer token → questionnaire field.  The questionnaire card is rendered from
#: this table, so the help text and the parser cannot drift apart (TB-03).
ANSWER_KEYS: dict[str, str] = {
    "goal": "goal",
    "duration": "duration_hours",
    "side": "side_preference",
    "involvement": "involvement",
    "manual": "manual_budget",
    "auto": "auto_budget",
    "total": "total_budget",
    "maxloss": "max_acceptable_loss",
    "hours": "trading_hours",
    "instrument": "instrument_key",
}

#: how many proposals one card lists
LIST_LIMIT = 5

#: Сколько признанных ключей делают свободную строку «голым ответом анкеты»
#: (RFC Q-20).  Ниже трёх — это вопрос, а не анкета: «side» сам по себе ничего
#: не подтверждает.
MIN_BARE_ANSWER_KEYS = 3


class ProposalRefused(plans.PlanServiceError):
    """A refusal the console renders verbatim, in the domain's own words."""

    reason_code = "proposal_not_adoptable"

    def __init__(self, message: str, *, reason_code: str | None = None,
                 violations: list[dict[str, str]] | None = None) -> None:
        super().__init__(message, reason_code=reason_code)
        self.violations = list(violations or [])


class QuestionnaireInvalid(ProposalRefused):
    reason_code = "questionnaire_invalid"


def proposal_timeout_seconds() -> float:
    """The wall on the agent call; a bad value falls back to the domain default."""
    raw = os.getenv("BOT_PROPOSAL_TIMEOUT", "").strip()
    if not raw:
        return PROPOSAL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return PROPOSAL_TIMEOUT_SECONDS
    return value if value > 0 else PROPOSAL_TIMEOUT_SECONDS


# ── answer parsing (deterministic, never by LLM) ────────────────────────────


def parse_proposal_answer(text: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """``goal=risk_control duration=24 …`` → the questionnaire the domain expects.

    Only the *shape* is checked here (a token is ``key=value``, the key is known,
    a key appears once).  Every value judgement belongs to
    :func:`normalize_questionnaire`, which is the same function the dashboard's
    form runs through — so a chat answer and a web body cannot disagree about
    what a valid questionnaire is.
    """
    raw: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for token in (text or "").split():
        key, sep, value = token.partition("=")
        field = ANSWER_KEYS.get(key.strip().lower())
        if not sep or not field:
            errors.append({
                "field": key or token,
                "code": "bad_token",
                "message": (
                    "формат ответа: key=value; разрешённые ключи: "
                    + ", ".join(sorted(ANSWER_KEYS))
                ),
            })
            continue
        if field in raw:
            errors.append({
                "field": field,
                "code": "duplicate_key",
                "message": f"ключ «{key}» указан дважды — последний ответ важнее первого, "
                           "поэтому бот ничего не выбирает сам",
            })
            continue
        raw[field] = value.strip()
    return raw, errors


def answer_template() -> str:
    """Точная форма команды-ответа — единственное место, где она записана.

    Из неё складывается и подсказка на карточке анкеты, и отказ Q-20: два
    текста об одной команде сошлись бы в разные утверждения, если бы шаблон
    жили в каждом экране отдельно (ТЗ TB-03).
    """
    return (
        "/plans propose goal=risk_control duration=24 side=both "
        "involvement=supervised manual=<USDT> auto=<USDT> maxloss=<USDT> hours=any"
    )


def recognised_answer_keys(text: str) -> int:
    """Сколько полей анкеты в строке — через тот же парсер, что и приём анкеты.

    Разбор делает :func:`parse_proposal_answer`, поэтому «что такое известный
    ключ» не может означать в отказе одно, а в приёме другое.  Ошибки формы здесь
    не важны: нас интересует только число признанных полей.
    """
    raw, _errors = parse_proposal_answer(text)
    return len(raw) if len(raw) >= MIN_BARE_ANSWER_KEYS else 0


# ── store and source handles ────────────────────────────────────────────────


async def get_store() -> PostgresProposalStore:
    """The Postgres proposal store over the bot's pool, or a refusal."""
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if pool is None:
        raise plans.StoreUnavailable(
            "хранилище предложений недоступно: Postgres не подключён — предложение "
            "не будет видно дашборду и его нельзя ни показать, ни принять"
        )
    return PostgresProposalStore(pool)


def questionnaire_options() -> dict[str, tuple[str, ...]]:
    """The allowed values, taken straight from the domain that enforces them.

    The questionnaire card renders from this call, so the console cannot offer an
    option the validator would refuse (and the handler never re-declares the
    domain's enumerations — ТЗ TB-03).
    """
    return {"goal": GOALS, "side_preference": SIDES_PREF, "involvement": INVOLVEMENT}


def source_label() -> str:
    """What is about to produce the draft, in the owner's terms — including cost."""
    kind = os.getenv("BOT_PROPOSAL_SOURCE", "").strip().lower()
    if kind == "llm":
        return f"платный агент, tier «{os.getenv('BOT_PROPOSAL_TIER', '').strip() or 'analyst'}»"
    return "детерминированные правила (бесплатно, воспроизводимо)"


def resolve_source() -> ProposalSource:
    """The agent behind the proposal.  Free by default, paid only on request.

    ``BOT_PROPOSAL_SOURCE=llm`` is the only way to reach a provider, and the
    import is inside the branch: with the default a container that has no AI key
    at all still produces proposals, and no proposal path can silently become a
    billable call.
    """
    kind = os.getenv("BOT_PROPOSAL_SOURCE", "").strip().lower()
    if kind == "llm":
        from ai.proposal_source import llm_source

        return llm_source
    return rules_source


# ── use cases ───────────────────────────────────────────────────────────────


async def create(owner_id: str, answer: str, *,
                 telegram_user_id: int | None = None) -> ProposalRecord:
    """Run one questionnaire and return its final record.

    The proposal is computed **inline** under the shared timeout wall rather than
    as a background task: in chat a card that never updates would hide the
    answer, whereas here a slow agent simply arrives as ``failed / llm_timeout``
    with the manual route next to it — the same verdict the dashboard's poller
    would have shown, one round-trip earlier.
    """
    raw, errors = parse_proposal_answer(answer)
    if errors:
        raise QuestionnaireInvalid("анкета не разобрана: исправьте формат ответа",
                                   violations=errors)
    q, violations = normalize_questionnaire(raw)
    if violations:
        raise QuestionnaireInvalid("анкета не принята: домен отклонил поля",
                                   violations=violations)

    instrument = str(raw.get("instrument_key") or "") or plans.default_instrument_key()
    try:
        spec = load_registry().get(instrument)
    except InstrumentNotFound:
        raise ProposalRefused(
            f"инструмента «{instrument}» нет в реестре — бот не берёт ближайший "
            "похожий вместо него",
            reason_code="unknown_instrument",
        )
    q["instrument_key"] = spec.instrument_key

    store = await get_store()
    now = datetime.now(timezone.utc)
    rec = ProposalRecord(str(uuid4()), owner_id, "pending", q, now, now)
    await store.create(rec)

    ctx = await build_proposal_ctx(plans.market_state, spec.instrument_key, now=now)
    if telegram_user_id is not None:
        # only the paid agent reads it, to meter the owner's daily quota before
        # anything is spent; the free rule engine ignores the key entirely
        ctx["telegram_user_id"] = int(telegram_user_id)
    await run_proposal(store, rec, resolve_source(), ctx, proposal_timeout_seconds())
    log.info(
        "PROPOSAL_RAN actor=%s prop=%s status=%s source=%s model=%s hash=%s",
        owner_id[:8], rec.proposal_id[:8], rec.status,
        os.getenv("BOT_PROPOSAL_SOURCE", "rules").strip().lower() or "rules",
        rec.model_version, (rec.plan_hash or "")[:19],
    )
    return rec


async def list_recent(owner_id: str, limit: int = LIST_LIMIT) -> list[ProposalRecord]:
    store = await get_store()
    return await store.list_recent(owner_id, max(1, min(int(limit), 20)))


async def get(owner_id: str, proposal_id: str) -> ProposalRecord:
    """Owner-scoped load; a foreign or malformed id is simply not found."""
    try:
        UUID(str(proposal_id))
    except (ValueError, TypeError, AttributeError):
        raise plans.PlanNotFound("предложения с таким идентификатором нет")
    store = await get_store()
    rec = await store.get(str(proposal_id), owner_id)
    if rec is None:
        raise plans.PlanNotFound(
            "предложения с таким идентификатором нет (или оно принадлежит другому владельцу)"
        )
    return rec


async def resolve(owner_id: str, token: str) -> ProposalRecord:
    """Full uuid or a unique prefix of this owner's own proposals.

    Same rule as plans, reached through the same helper, so the console has one
    answer to "is this identifier resolvable" (TB-03).
    """

    async def _own_ids() -> list[str]:
        return [rec.proposal_id for rec in await list_recent(owner_id, 20)]

    resolved = await plans.resolve_prefixed(
        token, _own_ids,
        entity_label="предложения", entity_dative="предложениям",
        not_found="такого идентификатора среди ваших предложений нет",
        reason_code="ambiguous_proposal_id",
    )
    return await get(owner_id, resolved)


async def adopt(owner_id: str, proposal_id: str) -> tuple[ProposalRecord, Any, bool]:
    """Make a proposal an ordinary own draft, or explain why it cannot.

    Returns ``(proposal, plan_record, already_adopted)``.  The veto is the
    domain's: :func:`adopt_plan_body` refuses a draft the shared validator
    rejected, and the numbers in the new draft are the proposal's numbers —
    re-normalized by the plan contract, never re-invented here.  A second tap
    answers with the draft that already exists instead of writing a twin, which
    is what makes the button safe to press twice.
    """
    rec = await get(owner_id, proposal_id)
    if rec.adopted_plan_id:
        try:
            existing = await plans.get_mine(owner_id, rec.adopted_plan_id)
        except plans.PlanNotFound:
            # the binding points at a row that is gone; refusing is honest, and
            # inventing a fresh draft behind the same proposal would not be
            raise ProposalRefused(
                "привязанный черновик не найден — примите предложение заново",
                reason_code="adopted_plan_missing",
            )
        return rec, existing, True

    try:
        body = adopt_plan_body(rec)
    except AdoptRefused as exc:  # the domain's veto, passed through verbatim
        raise ProposalRefused(
            exc.message,
            reason_code=exc.reason_code,
            violations=list(exc.violations),
        ) from None

    plan_rec = await plans.create_draft(owner_id, body)
    store = await get_store()
    bound = await store.mark_adopted(rec.proposal_id, plan_rec.plan_id, owner_id)
    if not bound:
        # the draft is real and visible; only the binding failed, so say exactly
        # that rather than pretending the tap did nothing
        log.warning("PROPOSAL_ADOPT_UNBOUND prop=%s plan=%s",
                    rec.proposal_id[:8], plan_rec.plan_id[:8])
    log.info(
        "PROPOSAL_ADOPTED actor=%s prop=%s plan=%s hash=%s bound=%s",
        owner_id[:8], rec.proposal_id[:8], plan_rec.plan_id[:8],
        (plan_rec.plan_hash or "")[:19], bound,
    )
    return rec, plan_rec, False
