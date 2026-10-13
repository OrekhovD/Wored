"""Simulation plan/session stores -- one implementation for dashboard and bot.

Moved verbatim out of ``webui/simulation_api.py`` in P3b so the Telegram bot can
read and write the same ``simulation_plan_versions`` / ``simulation_sessions``
rows the dashboard shows.  P3a made the *contract* shared (one ``plan_hash``);
this makes the *storage semantics* shared (one state machine, one idempotency
rule, one immutability guard).

Dependency rule: stdlib plus :mod:`trading_math.plan_contract` only.  The asyncpg
pool is **injected** and duck-typed (``Any``) -- this module never imports asyncpg
or FastAPI, so it loads inside the chatbot container (Python 3.9, ``/opt`` on
PYTHONPATH) without dragging the web layer along.  ``PlanImmutable`` stays a
plain exception: the HTTP layer maps it to 409, the bot maps it to a message.

``PostgresSimulationStore`` writes only through the additive simulation_v3
migration tables; ``MemorySimulationStore`` mirrors it for QA and must keep the
same behaviour or the MC-09/10/11 acceptance tests stop meaning anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from trading_math.plan_contract import (
    PLAN_TYPE,
    SCHEMA_VERSION,
    SESSION_TRANSITIONS,
    _iso,
)

# ── stores ──────────────────────────────────────────────────────────────────


class PlanImmutable(Exception):
    """Raised when an edit/approve transition violates immutability."""


@dataclass
class PlanRecord:
    plan_id: str
    owner_id: str
    status: str  # draft|approved|superseded|rejected
    plan: dict[str, Any] | None
    plan_hash: str | None
    violations: list[dict[str, str]]
    version: int
    parent_id: str | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    consumed_by: list[str] | None = None


@dataclass
class SessionRecord:
    session_id: str
    owner_id: str
    plan_id: str
    plan_hash: str
    mode: str
    instrument_key: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    idempotency_key: str
    request_fingerprint: str
    state_log: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


def _plan_to_dict(rec: PlanRecord) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_type": PLAN_TYPE,
        "plan_id": rec.plan_id,
        "owner_id": rec.owner_id,
        "status": rec.status,
        "version": rec.version,
        "parent_id": rec.parent_id,
        "plan": rec.plan,
        "plan_hash": rec.plan_hash,
        "violations": rec.violations,
        "approved_at": _iso(rec.approved_at) if rec.approved_at else None,
        "consumed_by_sessions": rec.consumed_by or [],
        "created_at": _iso(rec.created_at),
        "updated_at": _iso(rec.updated_at),
    }


def _session_to_dict(rec: SessionRecord) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": rec.session_id,
        "owner_id": rec.owner_id,
        "plan_id": rec.plan_id,
        "plan_hash": rec.plan_hash,
        "mode": rec.mode,
        "instrument_key": rec.instrument_key,
        "start_at": _iso(rec.start_at),
        "end_at": _iso(rec.end_at),
        "timezone": rec.timezone,
        "status": rec.status,
        "state_log": rec.state_log,
        "created_at": _iso(rec.created_at),
        "updated_at": _iso(rec.updated_at),
    }


class MemorySimulationStore:
    """QA/dev store — same semantics as the Postgres one, lost on restart."""

    def __init__(self) -> None:
        self.plans: dict[str, PlanRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self._by_owner_key: dict[tuple[str, str], str] = {}  # (owner, idem) -> session_id

    async def create_plan(self, *, owner_id: str, plan: dict[str, Any] | None,
                          plan_hash: str | None, violations: list[dict[str, str]],
                          parent_id: str | None = None) -> PlanRecord:
        now = datetime.now(timezone.utc)
        version = 1
        if parent_id and parent_id in self.plans:
            version = self.plans[parent_id].version + 1
        rec = PlanRecord(str(uuid4()), owner_id, "draft", plan, plan_hash,
                         violations, version, parent_id, now, now)
        self.plans[rec.plan_id] = rec
        return rec

    async def get_plan(self, plan_id: str, owner_id: str) -> PlanRecord | None:
        rec = self.plans.get(plan_id)
        return rec if rec and rec.owner_id == owner_id else None

    async def list_plans(self, owner_id: str, status: str | None, limit: int) -> list[PlanRecord]:
        rows = [p for p in self.plans.values() if p.owner_id == owner_id
                and (status is None or p.status == status)]
        rows.sort(key=lambda p: p.created_at, reverse=True)
        return rows[:limit]

    async def replace_draft(self, *, plan_id: str, owner_id: str, plan: dict[str, Any] | None,
                            plan_hash: str | None, violations: list[dict[str, str]]) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status != "draft":
            raise PlanImmutable(f"plan {plan_id} is {rec.status}; drafts only")
        rec.plan, rec.plan_hash, rec.violations = plan, plan_hash, violations
        rec.updated_at = datetime.now(timezone.utc)
        return rec

    async def approve_plan(self, *, plan_id: str, owner_id: str) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status == "approved":
            raise PlanImmutable("already approved")
        if rec.status != "draft":
            raise PlanImmutable(f"plan is {rec.status}; drafts only")
        rec.status = "approved"
        rec.approved_at = datetime.now(timezone.utc)
        rec.updated_at = rec.approved_at
        return rec

    async def attach_session(self, plan_id: str, session_id: str) -> None:
        rec = self.plans.get(plan_id)
        if rec is not None:
            rec.consumed_by = (rec.consumed_by or []) + [session_id]

    async def create_session(self, rec: SessionRecord) -> SessionRecord:
        key = (rec.owner_id, rec.idempotency_key)
        existing = self._by_owner_key.get(key)
        if existing:
            prior = self.sessions[existing]
            if prior.request_fingerprint != rec.request_fingerprint:
                raise PlanImmutable("idempotency_conflict")  # reused as 409 signal
            return prior
        self.sessions[rec.session_id] = rec
        self._by_owner_key[key] = rec.session_id
        return rec

    async def get_session(self, session_id: str, owner_id: str) -> SessionRecord | None:
        rec = self.sessions.get(session_id)
        return rec if rec and rec.owner_id == owner_id else None

    async def transition_session(self, *, session_id: str, owner_id: str, to: str,
                                 reason: str) -> SessionRecord:
        rec = await self.get_session(session_id, owner_id)
        if rec is None:
            raise KeyError(session_id)
        if to not in SESSION_TRANSITIONS.get(rec.status, set()):
            raise PlanImmutable(f"illegal transition {rec.status} → {to}")
        now = datetime.now(timezone.utc)
        rec.state_log = rec.state_log + [{
            "from": rec.status, "to": to, "reason": reason, "at": _iso(now),
        }]
        rec.status = to
        rec.updated_at = now
        return rec


class PostgresSimulationStore:
    """PG-backed store over the additive simulation_v3 migration.  JSONB keeps
    the normalized plan byte-exact; plan_hash pins what approve froze so old
    versions can never be quietly rewritten (MC-11)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @staticmethod
    def _row_plan(row: Any) -> PlanRecord:
        plan = row["plan"] if isinstance(row["plan"], dict) else json.loads(row["plan"] or "null")
        return PlanRecord(
            plan_id=str(row["id"]), owner_id=row["owner_id"], status=row["status"],
            plan=plan, plan_hash=row["plan_hash"],
            violations=row["violations"] if isinstance(row["violations"], list) else json.loads(row["violations"] or "[]"),
            version=row["version"], parent_id=str(row["parent_id"]) if row["parent_id"] else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            consumed_by=[str(s) for s in (row["consumed_by_sessions"] or [])],
        )

    async def create_plan(self, *, owner_id: str, plan: dict[str, Any] | None,
                          plan_hash: str | None, violations: list[dict[str, str]],
                          parent_id: str | None = None) -> PlanRecord:
        pid = uuid4()
        version = 1
        if parent_id:
            row = await self.pool.fetchrow(
                "SELECT version FROM simulation_plan_versions WHERE id=$1 AND owner_id=$2",
                UUID(parent_id), owner_id)
            if row:
                version = int(row["version"]) + 1
        await self.pool.execute(
            """
            INSERT INTO simulation_plan_versions
              (id, owner_id, status, plan, plan_hash, violations, version, parent_id)
            VALUES ($1, $2, 'draft', $3::jsonb, $4, $5::jsonb, $6, $7)
            """,
            pid, owner_id,
            json.dumps(plan) if plan is not None else None,
            plan_hash, json.dumps(violations), version,
            UUID(parent_id) if parent_id else None,
        )
        rec = await self.get_plan(str(pid), owner_id)
        assert rec is not None
        return rec

    async def get_plan(self, plan_id: str, owner_id: str) -> PlanRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_plan_versions WHERE id=$1 AND owner_id=$2",
            UUID(plan_id), owner_id)
        return self._row_plan(row) if row else None

    async def list_plans(self, owner_id: str, status: str | None, limit: int) -> list[PlanRecord]:
        if status:
            rows = await self.pool.fetch(
                "SELECT * FROM simulation_plan_versions WHERE owner_id=$1 AND status=$2 "
                "ORDER BY created_at DESC LIMIT $3", owner_id, status, limit)
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM simulation_plan_versions WHERE owner_id=$1 "
                "ORDER BY created_at DESC LIMIT $2", owner_id, limit)
        return [self._row_plan(r) for r in rows]

    async def replace_draft(self, *, plan_id: str, owner_id: str, plan: dict[str, Any] | None,
                            plan_hash: str | None, violations: list[dict[str, str]]) -> PlanRecord:
        result = await self.pool.execute(
            "UPDATE simulation_plan_versions SET plan=$3::jsonb, plan_hash=$4, "
            "violations=$5::jsonb, updated_at=now() "
            "WHERE id=$1 AND owner_id=$2 AND status='draft'",
            UUID(plan_id), owner_id,
            json.dumps(plan) if plan is not None else None, plan_hash,
            json.dumps(violations))
        if result.endswith("0"):
            rec = await self.get_plan(plan_id, owner_id)
            if rec is None:
                raise KeyError(plan_id)
            raise PlanImmutable(f"plan {plan_id} is {rec.status}; drafts only")
        rec = await self.get_plan(plan_id, owner_id)
        assert rec is not None
        return rec

    async def approve_plan(self, *, plan_id: str, owner_id: str) -> PlanRecord:
        rec = await self.get_plan(plan_id, owner_id)
        if rec is None:
            raise KeyError(plan_id)
        if rec.status != "draft":
            raise PlanImmutable("already approved" if rec.status == "approved" else f"plan is {rec.status}; drafts only")
        await self.pool.execute(
            "UPDATE simulation_plan_versions SET status='approved', approved_at=now(), "
            "updated_at=now() WHERE id=$1 AND owner_id=$2 AND status='draft'",
            UUID(plan_id), owner_id)
        rec = await self.get_plan(plan_id, owner_id)
        assert rec is not None
        return rec

    async def attach_session(self, plan_id: str, session_id: str) -> None:
        await self.pool.execute(
            "UPDATE simulation_plan_versions SET consumed_by_sessions = "
            "consumed_by_sessions || array[$2::uuid] WHERE id=$1",
            UUID(plan_id), UUID(session_id))

    async def create_session(self, rec: SessionRecord) -> SessionRecord:
        try:
            await self.pool.execute(
                """
                INSERT INTO simulation_sessions
                  (id, owner_id, plan_id, plan_hash, mode, instrument_key,
                   start_at, end_at, timezone, status, idempotency_key,
                   request_fingerprint, state_log)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
                """,
                UUID(rec.session_id), rec.owner_id, UUID(rec.plan_id), rec.plan_hash,
                rec.mode, rec.instrument_key, rec.start_at, rec.end_at, rec.timezone,
                rec.status, rec.idempotency_key, rec.request_fingerprint,
                json.dumps(rec.state_log))
        except Exception as exc:  # unique (owner_id, idempotency_key) collision
            if "uq_simulation_idempotency" not in str(exc):
                raise
            row = await self.pool.fetchrow(
                "SELECT request_fingerprint FROM simulation_sessions WHERE owner_id=$1 AND idempotency_key=$2",
                rec.owner_id, rec.idempotency_key)
            if row and row["request_fingerprint"] == rec.request_fingerprint:
                existing = await self.get_session_by_key(rec.owner_id, rec.idempotency_key)
                assert existing is not None
                return existing
            raise PlanImmutable("idempotency_conflict") from exc
        return rec

    async def get_session_by_key(self, owner_id: str, idem: str) -> SessionRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_sessions WHERE owner_id=$1 AND idempotency_key=$2",
            owner_id, idem)
        return self._row_session(row) if row else None

    @staticmethod
    def _row_session(row: Any) -> SessionRecord:
        log_raw = row["state_log"]
        return SessionRecord(
            session_id=str(row["id"]), owner_id=row["owner_id"],
            plan_id=str(row["plan_id"]), plan_hash=row["plan_hash"],
            mode=row["mode"], instrument_key=row["instrument_key"],
            start_at=row["start_at"], end_at=row["end_at"], timezone=row["timezone"],
            status=row["status"], idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            state_log=log_raw if isinstance(log_raw, list) else json.loads(log_raw or "[]"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    async def get_session(self, session_id: str, owner_id: str) -> SessionRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM simulation_sessions WHERE id=$1 AND owner_id=$2",
            UUID(session_id), owner_id)
        return self._row_session(row) if row else None

    async def transition_session(self, *, session_id: str, owner_id: str, to: str,
                                 reason: str) -> SessionRecord:
        rec = await self.get_session(session_id, owner_id)
        if rec is None:
            raise KeyError(session_id)
        if to not in SESSION_TRANSITIONS.get(rec.status, set()):
            raise PlanImmutable(f"illegal transition {rec.status} → {to}")
        entry = {"from": rec.status, "to": to, "reason": reason,
                 "at": _iso(datetime.now(timezone.utc))}
        await self.pool.execute(
            "UPDATE simulation_sessions SET status=$3, updated_at=now(), "
            "state_log = state_log || $4::jsonb WHERE id=$1 AND owner_id=$2 AND status=$5",
            UUID(session_id), owner_id, to, json.dumps([entry]), rec.status)
        rec = await self.get_session(session_id, owner_id)
        assert rec is not None
        return rec

