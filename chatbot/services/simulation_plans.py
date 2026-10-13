"""SimulationPlanV1 use cases for the Telegram console (ТЗ §5, P3c).

One owner, one plan domain, one validator.  Every rule that decides whether a
plan is approvable lives in :mod:`trading_math` -- the same files the dashboard
loads -- so this module owns only three things:

* **the store handle**: the shared ``PostgresSimulationStore`` over the bot's own
  asyncpg pool.  There is deliberately *no* in-memory fallback here: a bot that
  cannot reach Postgres must say so, because a draft that exists only in this
  process would be invisible to the dashboard and could never be approved;
* **the injected providers**: the market snapshot (Redis) and the candle coverage
  (Postgres) that the shared validation pipeline needs, read through the bot's
  own transports and fed into
  :func:`trading_math.plan_validation.validate_plan`;
* **the write policy**: which edits the bot is willing to persist.

The policy is the security-relevant part and it is narrow on purpose:

* owner only -- an admin or viewer role can read the list, never write;        
* the bot creates and edits **drafts** and can approve one after an explicit,
  freshly re-validated confirmation (curator decision, P3c);
* there is no session start, no pause, no transition and no order anywhere in
  this module: starting a session is a WebUI action (ТЗ Q-10), and the function
  does not exist here rather than being hidden behind a permission check;      
* a draft older than ``BOT_WIZARD_TTL_HOURS`` (default 24) is refused, matching
  the ТЗ rule that a draft is a 24-hour object; the row itself is left untouched
  in ``simulation_plan_versions`` -- expiry is a write policy, not a delete.

Values are never invented, and the write line is drawn where the domain draws it:
a value the shared contract cannot even **parse** (bad enum, out of range, unknown
field) is refused and the previous draft stays byte-identical, while a value that
parses but breaks a **cross-field rule** is stored and then blocks approval — the
same split the dashboard form uses, so chat and web keep one and the same draft
for one and the same input.  Only ``normalize_plan`` decides; nothing here
re-implements the check.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from trading_math.instrument_registry import InstrumentNotFound, load_registry
from trading_math.market_state import MARKET_KEY_PREFIX, build_market_state
from trading_math.plan_contract import (
    PLAN_FIELDS,
    normalize_plan,
    plan_hash,
)
from trading_math.plan_validation import validate_plan
from trading_math.simulation_store import (
    PlanImmutable,
    PlanRecord,
    PostgresSimulationStore,
)

log = logging.getLogger("chatbot.plans")

NA = "N/A"

#: statuses that may be listed by the screen; mirrors the API's own filter check
PLAN_STATUSES = ("draft", "approved", "superseded", "rejected")


class PlanServiceError(Exception):
    """A refusal the console can render verbatim: reason code plus message."""

    reason_code = "plan_service_error"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if reason_code:
            self.reason_code = reason_code


class StoreUnavailable(PlanServiceError):
    reason_code = "simulation_store_unavailable"


class PlanNotFound(PlanServiceError):
    """Also the answer for *someone else's* plan: absence is not disclosed."""

    reason_code = "plan_not_found"


class FieldUnknown(PlanServiceError):
    reason_code = "unknown_field"


class NotDraft(PlanServiceError):
    reason_code = "plan_immutable"


class DraftExpired(PlanServiceError):
    reason_code = "draft_expired"


class ApprovalRefused(PlanServiceError):
    """Carries the violation list the shared pipeline produced."""

    reason_code = "plan_not_approvable"

    def __init__(self, message: str, violations: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.violations = violations


class ContractRefused(PlanServiceError):
    """The shared contract rejected the values, so nothing was written.

    The violation list is the domain's own, verbatim: the card shows exactly
    what ``normalize_plan`` answered, which is the same list the dashboard form
    would show for the same body.
    """

    reason_code = "plan_invalid"

    def __init__(self, message: str, violations: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.violations = violations


class VersionChanged(PlanServiceError):
    reason_code = "plan_version_changed"


def wizard_ttl_hours() -> int:
    """Draft lifetime in hours; a non-numeric value falls back to the ТЗ default."""
    try:
        value = int(os.getenv("BOT_WIZARD_TTL_HOURS", "24") or "24")
    except ValueError:
        return 24
    return value if value > 0 else 24


def draft_age_hours(rec: PlanRecord, *, now: datetime | None = None) -> float:
    moment = now or datetime.now(timezone.utc)
    created = rec.created_at
    if created.tzinfo is None:  # a naive row is UTC by storage convention
        created = created.replace(tzinfo=timezone.utc)
    return (moment - created).total_seconds() / 3600.0


def is_expired(rec: PlanRecord) -> bool:
    return draft_age_hours(rec) > wizard_ttl_hours()


def _require_draft_window(rec: PlanRecord) -> None:
    if rec.status == "draft" and is_expired(rec):
        raise DraftExpired(
            f"черновику {draft_age_hours(rec):.0f} ч — лимит {wizard_ttl_hours()} ч. "
            "Создайте новый (/plans new): старый план остаётся в истории и не удаляется."
        )


async def get_store() -> PostgresSimulationStore:
    """The Postgres store over the bot's pool, or a refusal.  Never memory."""
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if pool is None:
        raise StoreUnavailable(
            "хранилище планов недоступно: Postgres не подключён — черновики "
            "не будут видны дашборду, поэтому бот ничего не создаёт"
        )
    return PostgresSimulationStore(pool)


# ── providers for the shared validation pipeline ────────────────────────────


async def market_state(instrument_key: str) -> dict[str, Any] | None:
    """Same Redis key and same verdict function as the dashboard.

    ``None`` means the source is unreachable, which the pipeline turns into a
    blocking violation — an unchecked quote is never a valid quote.

    Public because :mod:`services.simulation_proposals` feeds the *same* reader
    into the shared proposal context: one market read per process, not one per
    vertical (TB-03).
    """
    try:
        spec = load_registry().get(instrument_key)
    except InstrumentNotFound:
        return None
    from storage.redis_client import get_redis

    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(f"{MARKET_KEY_PREFIX}:{spec.contract_code}")
    except Exception as exc:  # transport failure is an error, not an absence
        log.warning("plan market read failed for %s: %s", spec.contract_code, exc)
        return None
    payload: Any = None
    if raw is not None:
        try:
            parsed = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
            payload = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
    return build_market_state(spec, payload, now=datetime.now(timezone.utc))


_COVERAGE_SQL = """
        SELECT count(*) AS n FROM trader_v1_perp_candles
        WHERE venue = $1 AND contract_code = $2 AND timeframe = '1min'
          AND open_time >= $3 AND close_time <= $4
"""


async def _coverage(
    instrument_key: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """1-minute closed-candle coverage of a replay window (fail-closed)."""
    try:
        spec = load_registry().get(instrument_key)
    except InstrumentNotFound:
        return {"required_intervals": 0, "available_intervals": None,
                "reason_code": "unknown_instrument"}
    required = int((end - start).total_seconds() // 60)
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if pool is None:
        return {"required_intervals": required, "available_intervals": None,
                "reason_code": "history_source_unavailable"}
    rows = await pool.fetch(
        _COVERAGE_SQL, spec.venue, spec.contract_code, start, end,
    )
    available = int(rows[0]["n"]) if rows else 0
    out: dict[str, Any] = {"required_intervals": required, "available_intervals": available}
    if available < required:
        out["reason_code"] = "missing_intervals"
    return out


# ── reads ───────────────────────────────────────────────────────────────────


async def list_mine(
    owner_id: str, status: str | None = None, limit: int = 10
) -> list[PlanRecord]:
    store = await get_store()
    return await store.list_plans(owner_id, status, max(1, min(int(limit), 50)))


async def get_mine(owner_id: str, plan_id: str) -> PlanRecord:
    """Owner-scoped load.  A foreign or malformed id answers as *not found*."""
    try:
        UUID(str(plan_id))
    except (ValueError, TypeError, AttributeError):
        raise PlanNotFound("план с таким идентификатором нет")
    store = await get_store()
    rec = await store.get_plan(str(plan_id), owner_id)
    if rec is None:
        raise PlanNotFound("план с таким идентификатором нет (или он принадлежит другому владельцу)")
    return rec


async def preview(rec: PlanRecord) -> dict[str, Any]:
    """Run the shared pipeline over a stored draft.

    The dashboard's ``/plans/validate`` produces this exact payload shape, so a
    Telegram card and the web form report the same violations and the same
    ``plan_hash`` (ТЗ P3 exit gate).
    """
    if rec.plan is None:
        return {
            "normalized": None,
            "violations": list(rec.violations) or [
                {"field": "_plan", "code": "plan_unparseable",
                 "message": "черновик не прошёл нормализацию"}],
            "worst_case_risk": None,
            "market": None,
            "plan_hash": None,
            "can_approve": False,
        }
    return await validate_plan(
        rec.plan, market=market_state, coverage=_coverage,
        now=datetime.now(timezone.utc),
    )


# ── id resolution (one rule for every owner-scoped entity) ─────────────────


async def resolve_prefixed(token: str, list_candidates: Callable[[], Awaitable[list[str]]],
                           *, entity_label: str,
                           entity_dative: str, not_found: str,
                           reason_code: str = "ambiguous_plan_id") -> str:
    """Accept a full id or a *unique* prefix of the caller's own ids.

    Only ids the caller already owns are considered, so an ambiguous or foreign
    prefix cannot be used to probe somebody else's data.  Shared with proposals
    so that "what counts as a resolvable id" has one answer in the console; the
    candidates come from a callable because a full uuid must not pay for a
    listing, and the two case forms are passed in because Russian nouns decline
    and the wording of each refusal is part of the contract the plan screens
    already test.
    """
    text = (token or "").strip()
    if not text:
        raise PlanNotFound(f"укажите идентификатор {entity_label}")
    try:
        UUID(text)
        return text
    except ValueError:
        pass
    matches = [c for c in await list_candidates() if c.startswith(text)]
    if not matches:
        raise PlanNotFound(not_found)
    if len(matches) > 1:
        raise PlanServiceError(
            f"префикс «{text}» подходит к {len(matches)} {entity_dative} — "
            "уточните идентификатор",
            reason_code=reason_code,
        )
    return matches[0]


# ── writes (drafts only) ────────────────────────────────────────────────────


def default_instrument_key() -> str:
    """The first registered perpetual — the registry is the only source."""
    perpetuals = [s for s in load_registry().list() if s.is_perpetual]
    if not perpetuals:
        raise PlanServiceError("в реестре нет ни одного бессрочного инструмента")
    return perpetuals[0].instrument_key


def skeleton_body(*, now: datetime | None = None, instrument_key: str | None = None) -> dict[str, Any]:
    """A new draft that already satisfies the contract, with explicit numbers.

    Deliberately *valid*: a draft whose window is in the past or whose risk
    exceeds its budget would be born broken, and the ТЗ forbids silently
    presenting an unusable default.  Times are whole minutes in UTC; the window
    is 8 hours, inside the live_paper 1-168 h limit.
    """
    moment = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    start = moment + timedelta(minutes=15)
    end = start + timedelta(hours=8)
    from security import principal as principal_mod

    return {
        "instrument_key": instrument_key or default_instrument_key(),
        "mode": "live_paper",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": principal_mod.default_timezone(),
        "manual_budget": "1000.00",
        "auto_budget": "1000.00",
        "strategy_id": "baseline_v1",
        "strategy_version": "baseline_v1",
        "style": "default",
        "allowed_sides": ["long", "short"],
        "max_leverage": 3,
        "max_risk_per_order": "10.00",
        "max_daily_loss": "50.00",
        "max_session_loss": "100.00",
        "max_total_exposure": "500.00",
        "max_open_positions": 2,
    }


def _normalize_for_write(body: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Contract check before persisting.  Refusal carries the violations."""
    plan, violations = normalize_plan(body)
    codes = [v.as_dict() for v in violations]
    if plan is None:
        return {}, codes
    return plan, []


async def create_draft(owner_id: str, body: dict[str, Any] | None = None) -> PlanRecord:
    """Persist a new draft owned by this principal.

    Normalization is the shared contract's, and the hash stored here is the same
    ``plan_hash`` the dashboard would store for the same body.
    """
    payload = dict(body if body is not None else skeleton_body())
    plan, codes = _normalize_for_write(payload)
    if not plan:
        raise ContractRefused("черновик не создан: поля не проходят контракт", codes)
    store = await get_store()
    rec = await store.create_plan(
        owner_id=owner_id, plan=plan, plan_hash=plan_hash(plan), violations=codes,
    )
    log.info(
        "PLAN_DRAFT_CREATED actor=%s plan=%s hash=%s",
        owner_id[:8], rec.plan_id[:8], (rec.plan_hash or "")[:19],
    )
    return rec


async def set_field(owner_id: str, plan_id: str, field: str, value: Any) -> PlanRecord:
    """Change one field of an own draft, or explain why not.

    The merge base is the stored *normalized* plan, so an edit never smuggles an
    unknown key into the canonical JSON and never changes the hash implicitly:
    the plan is re-normalized and re-hashed by the shared contract.  Parsing is
    the boundary — a value the contract cannot parse is not written at all, a
    value that parses but violates a cross-field rule is written and then
    refuses itself at approval, exactly as the dashboard saves a broken form.
    """
    if field not in PLAN_FIELDS:
        raise FieldUnknown(
            f"поля «{field}» нет в SimulationPlanV1; "
            f"разрешённые: {', '.join(sorted(PLAN_FIELDS))}"
        )
    rec = await get_mine(owner_id, plan_id)
    _require_draft_window(rec)
    if rec.status != "draft":
        raise NotDraft(
            f"план в состоянии «{rec.status}» неизменяем. Изменение версии — "
            "новый черновик; утверждённый план правится только как новая версия в WebUI"
        )
    base = dict(rec.plan or {})
    base[field] = value
    plan, codes = _normalize_for_write(base)
    if not plan:
        raise ContractRefused(
            "значение не записано: контракт его отклонил; черновик остался прежним",
            codes,
        )
    store = await get_store()
    rec = await store.replace_draft(
        plan_id=rec.plan_id, owner_id=owner_id, plan=plan,
        plan_hash=plan_hash(plan), violations=codes,
    )
    log.info("PLAN_DRAFT_EDITED actor=%s plan=%s field=%s",
             owner_id[:8], rec.plan_id[:8], field)
    return rec


async def approve(owner_id: str, plan_id: str, expected_hash: str) -> PlanRecord:
    """Approve an own draft after the confirmation token was checked.

    Three separate guards, in this order:

    1. the token must carry the hash the user was *shown*; if the draft changed
       in the meantime the approval is refused rather than pinning a version
       nobody confirmed (MC-11);
    2. the full shared pipeline re-runs *now* — the quote, the clock and the
       coverage may have moved since the preview card;
    3. the store enforces immutability, so a double press cannot approve twice.
    """
    rec = await get_mine(owner_id, plan_id)
    _require_draft_window(rec)
    if rec.status != "draft":
        raise NotDraft(f"план уже в состоянии «{rec.status}»")
    result = await preview(rec)
    actual = result.get("plan_hash")
    if not actual or actual != expected_hash:
        raise VersionChanged(
            "версия плана изменилась с момента карточки — откройте предпросмотр заново"
        )
    if not result.get("can_approve"):
        raise ApprovalRefused(
            "домен не утверждает этот план", list(result.get("violations") or [])
        )
    store = await get_store()
    try:
        rec = await store.approve_plan(plan_id=rec.plan_id, owner_id=owner_id)
    except PlanImmutable as exc:
        raise NotDraft(str(exc))
    log.info(
        "PLAN_APPROVED actor=%s plan=%s hash=%s",
        owner_id[:8], rec.plan_id[:8], (rec.plan_hash or "")[:19],
    )
    return rec
