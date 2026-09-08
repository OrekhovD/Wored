"""Usage Ledger — atomic reservation/settlement for LLM accounting.

R04: One transport attempt = one ledger row, including retry, fallback, timeout,
cancellation, and invalid_response. Reservation is atomic; settlement adjusts
reserved → used. Crash after send is conservative: full reserve charged as
unknown_charge.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from ai.contracts import (
    AttemptState,
    CostClass,
    RequestState,
    UsageSource,
)

log = logging.getLogger(__name__)


class UsageLedger:
    """Usage ledger with atomic reservation and settlement.

    In production, this uses PostgreSQL with row-level locking.
    In tests / without DB, it falls back to in-memory tracking.
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.getenv("WORED_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
        self._pool = None
        # In-memory fallback for testing without DB
        self._mem_requests: dict[str, dict] = {}
        self._mem_attempts: dict[str, dict] = {}
        self._mem_buckets: dict[str, dict] = {}
        self._mem_decisions: list[dict] = []
        self._mem_gates: dict[str, dict] = {}

    async def _get_pool(self):
        """Lazily connect to PostgreSQL."""
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            return None
        try:
            import asyncpg
            dsn = self._dsn
            if "postgresql+asyncpg://" in dsn:
                dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
            self._pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
            return self._pool
        except Exception as exc:
            log.warning("UsageLedger: PostgreSQL unavailable, using in-memory: %s", exc)
            return None

    # ── Request lifecycle ────────────────────────────────────────────────

    async def create_request(
        self,
        request_id: str,
        source: str,
        principal: str,
        task_type: str,
        snapshot_id: str | None = None,
    ) -> None:
        """Create an llm_requests row."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO llm_requests (id, source, principal, task_type, snapshot_id, final_state)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    request_id, source, principal, task_type, snapshot_id, RequestState.QUEUED.value,
                )
        else:
            self._mem_requests[request_id] = {
                "id": request_id, "source": source, "principal": principal,
                "task_type": task_type, "snapshot_id": snapshot_id,
                "final_state": RequestState.QUEUED.value, "next_sequence": 0,
            }

    async def update_request_state(self, request_id: str, state: RequestState) -> None:
        """Update llm_requests.final_state."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE llm_requests SET final_state = $1 WHERE id = $2",
                    state.value, request_id,
                )
        else:
            if request_id in self._mem_requests:
                self._mem_requests[request_id]["final_state"] = state.value

    # ── Atomic reservation ───────────────────────────────────────────────

    async def reserve(
        self,
        request_id: str,
        sequence: int,
        provider: str,
        model: str,
        principal: str,
        source: str,
        context_tokens: int,
        cost_class: CostClass,
        pricing: dict[str, Any],
    ) -> dict | None:
        """Atomically reserve budget for an attempt.

        Returns attempt dict with attempt_id on success, None if budget exhausted.

        For each attempt, forms bucket keys for runtime/global, provider, and model
        simultaneously: day, week, month in UTC. Checks used+reserved+requested
        <= limit for each dimension. Increases reserved and creates attempt/reservation.
        Only after COMMIT is a network call allowed.
        """
        attempt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        bucket_keys = self._compute_bucket_keys(principal, source, provider, model, now)

        # Compute token reservation: conservative — full context_tokens if no exact tokenizer
        reserved_tokens = context_tokens
        reserved_requests = 1

        # Compute cost reservation for metered models
        reserved_cost = 0.0
        if cost_class == CostClass.METERED:
            input_rate = pricing.get("input_per_million", 0) or 0
            output_rate = pricing.get("output_per_million", 0) or 0
            max_rate = max(input_rate, output_rate)
            if max_rate > 0 and context_tokens > 0:
                # Upper bound: context_tokens * max_rate / 1_000_000
                reserved_cost = context_tokens * max_rate / 1_000_000
            elif max_rate > 0 and context_tokens == 0:
                # Unknown context for metered → policy_denied
                return None
        # free/included: monetary reserve/charge = 0, but requests/tokens still tracked

        pool = await self._get_pool()
        if pool:
            return await self._reserve_db(
                pool, attempt_id, request_id, sequence, provider, model,
                principal, source, reserved_tokens, reserved_requests,
                reserved_cost, cost_class, bucket_keys, now,
            )
        else:
            return await self._reserve_mem(
                attempt_id, request_id, sequence, provider, model,
                principal, source, reserved_tokens, reserved_requests,
                reserved_cost, cost_class, bucket_keys, now,
            )

    def _compute_bucket_keys(
        self, principal: str, source: str, provider: str, model: str,
        now: datetime,
    ) -> list[dict]:
        """Compute bucket keys: runtime/global, provider, model for day/week/month in UTC."""
        # Week starts Monday 00:00, month starts 1st 00:00
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Monday of this week
        days_since_monday = now.weekday()
        week_start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                      - __import__("datetime").timedelta(days=days_since_monday))
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        periods = [
            ("day", day_start),
            ("week", week_start),
            ("month", month_start),
        ]
        scopes = [
            "global",
            f"provider:{provider}",
            f"model:{provider}/{model}",
        ]
        keys = []
        for scope in scopes:
            for period_kind, period_start in periods:
                keys.append({
                    "scope_key": scope,
                    "period_kind": period_kind,
                    "period_start": period_start,
                })
        return keys

    async def _reserve_db(self, pool, attempt_id, request_id, sequence,
                           provider, model, principal, source,
                           reserved_tokens, reserved_requests,
                           reserved_cost, cost_class, bucket_keys, now) -> dict | None:
        """Reserve via PostgreSQL with row-level locking."""
        from ai.budget_policy import BudgetPolicy
        policy = BudgetPolicy()

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Atomically increment sequence
                    row = await conn.fetchrow(
                        "UPDATE llm_requests SET next_sequence = next_sequence + 1 "
                        "WHERE id = $1 RETURNING next_sequence",
                        request_id,
                    )
                    if not row:
                        return None
                    actual_sequence = row["next_sequence"] - 1  # we just incremented

                    # Check each bucket
                    for bk in bucket_keys:
                        limits = policy.get_limits(bk["scope_key"], bk["period_kind"], cost_class)
                        # Ensure bucket exists
                        await conn.execute(
                            """INSERT INTO llm_budget_buckets
                               (scope_key, period_kind, period_start, request_limit, token_limit, cost_limit)
                               VALUES ($1, $2, $3, $4, $5, $6)
                               ON CONFLICT (scope_key, period_kind, period_start) DO NOTHING""",
                            bk["scope_key"], bk["period_kind"], bk["period_start"],
                            limits["request_limit"], limits["token_limit"], limits["cost_limit"],
                        )
                        # Lock and check
                        bucket = await conn.fetchrow(
                            "SELECT * FROM llm_budget_buckets "
                            "WHERE scope_key = $1 AND period_kind = $2 AND period_start = $3 "
                            "FOR UPDATE",
                            bk["scope_key"], bk["period_kind"], bk["period_start"],
                        )
                        if not bucket:
                            continue

                        # Check limits
                        used_plus_reserved = (bucket["used_requests"] or 0) + (bucket["reserved_requests"] or 0)
                        if limits["request_limit"] is not None and used_plus_reserved + reserved_requests > limits["request_limit"]:
                            return None

                        used_plus_reserved_tokens = (bucket["used_tokens"] or 0) + (bucket["reserved_tokens"] or 0)
                        if limits["token_limit"] is not None and used_plus_reserved_tokens + reserved_tokens > limits["token_limit"]:
                            return None

                        if cost_class == CostClass.METERED:
                            used_plus_reserved_cost = float(bucket["used_cost"] or 0) + float(bucket["reserved_cost"] or 0)
                            if limits["cost_limit"] is not None and used_plus_reserved_cost + reserved_cost > float(limits["cost_limit"]):
                                return None

                        # Increase reserved
                        await conn.execute(
                            """UPDATE llm_budget_buckets
                               SET reserved_requests = reserved_requests + $1,
                                   reserved_tokens = reserved_tokens + $2,
                                   reserved_cost = reserved_cost + $3
                               WHERE scope_key = $4 AND period_kind = $5 AND period_start = $6""",
                            reserved_requests, reserved_tokens, reserved_cost,
                            bk["scope_key"], bk["period_kind"], bk["period_start"],
                        )

                    # Create attempt row
                    deadline = now + __import__("datetime").timedelta(minutes=5)
                    await conn.execute(
                        """INSERT INTO llm_attempts
                           (id, request_id, sequence, provider, model,
                            started_at, deadline_at, state, reserved_tokens, reserved_cost)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                        attempt_id, request_id, sequence, provider, model,
                        now, deadline, AttemptState.RESERVED.value,
                        reserved_tokens, reserved_cost,
                    )

                    # Create reservation rows
                    for bk in bucket_keys:
                        await conn.execute(
                            """INSERT INTO llm_reservations
                               (attempt_id, scope_key, period_kind, period_start,
                                requests, tokens, cost)
                               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                            attempt_id, bk["scope_key"], bk["period_kind"], bk["period_start"],
                            reserved_requests, reserved_tokens, reserved_cost,
                        )

            return {"attempt_id": attempt_id, "request_id": request_id, "sequence": sequence}
        except Exception as exc:
            log.error("UsageLedger reserve DB error: %s", exc)
            return None

    async def _reserve_mem(self, attempt_id, request_id, sequence,
                            provider, model, principal, source,
                            reserved_tokens, reserved_requests,
                            reserved_cost, cost_class, bucket_keys, now) -> dict | None:
        """Reserve via in-memory buckets (for testing without DB)."""
        from ai.budget_policy import BudgetPolicy
        policy = BudgetPolicy()

        for bk in bucket_keys:
            key = f"{bk['scope_key']}:{bk['period_kind']}:{bk['period_start'].isoformat()}"
            limits = policy.get_limits(bk["scope_key"], bk["period_kind"], cost_class)
            bucket = self._mem_buckets.setdefault(key, {
                "scope_key": bk["scope_key"],
                "period_kind": bk["period_kind"],
                "period_start": bk["period_start"],
                "request_limit": limits["request_limit"],
                "token_limit": limits["token_limit"],
                "cost_limit": limits["cost_limit"],
                "used_requests": 0, "reserved_requests": 0,
                "used_tokens": 0, "reserved_tokens": 0,
                "used_cost": 0.0, "reserved_cost": 0.0,
            })

            used_plus_reserved = bucket["used_requests"] + bucket["reserved_requests"]
            if limits["request_limit"] is not None and used_plus_reserved + reserved_requests > limits["request_limit"]:
                return None

            used_plus_reserved_tokens = bucket["used_tokens"] + bucket["reserved_tokens"]
            if limits["token_limit"] is not None and used_plus_reserved_tokens + reserved_tokens > limits["token_limit"]:
                return None

            if cost_class == CostClass.METERED and limits["cost_limit"] is not None:
                used_plus_reserved_cost = bucket["used_cost"] + bucket["reserved_cost"]
                if used_plus_reserved_cost + reserved_cost > limits["cost_limit"]:
                    return None

        # All checks passed — reserve
        for bk in bucket_keys:
            key = f"{bk['scope_key']}:{bk['period_kind']}:{bk['period_start'].isoformat()}"
            bucket = self._mem_buckets[key]
            bucket["reserved_requests"] += reserved_requests
            bucket["reserved_tokens"] += reserved_tokens
            bucket["reserved_cost"] += reserved_cost

        self._mem_attempts[attempt_id] = {
            "id": attempt_id, "request_id": request_id, "sequence": sequence,
            "provider": provider, "model": model,
            "state": AttemptState.RESERVED.value,
            "reserved_tokens": reserved_tokens, "reserved_cost": reserved_cost,
            "started_at": now.isoformat(),
        }

        return {"attempt_id": attempt_id, "request_id": request_id, "sequence": sequence}

    # ── Attempt state updates ─────────────────────────────────────────────

    async def update_attempt(self, attempt_id: str, state: AttemptState) -> None:
        """Update attempt state (reserved → running, etc.)."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE llm_attempts SET state = $1 WHERE id = $2",
                    state.value, attempt_id,
                )
        else:
            if attempt_id in self._mem_attempts:
                self._mem_attempts[attempt_id]["state"] = state.value

    # ── Settlement ───────────────────────────────────────────────────────

    async def settle(
        self,
        attempt_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        tool_tokens: int | None = None,
        usage_source: UsageSource = UsageSource.ACTUAL,
    ) -> None:
        """Settle a successful attempt: move reserved → used with actual token counts.

        If settled_at already filled → idempotent, do nothing.
        Cached/reasoning details not double-counted if included in input/output totals.
        If provider didn't report usage or crash, charge full reserve as unknown.
        """
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
        if total_tokens == 0:
            # No usage data — charge full reserve
            await self.settle_unknown(attempt_id, usage_source=usage_source.value)
            return

        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                # Check idempotency
                row = await conn.fetchrow(
                    "SELECT settled_at FROM llm_attempts WHERE id = $1", attempt_id,
                )
                if row and row["settled_at"] is not None:
                    return  # Already settled — idempotent

                await conn.execute(
                    """UPDATE llm_attempts SET
                       state = $1, finished_at = NOW(),
                       input_tokens = $2, output_tokens = $3,
                       cached_tokens = $4, reasoning_tokens = $5,
                       tool_tokens = $6, charged_tokens = $7,
                       usage_source = $8, settled_at = NOW()
                       WHERE id = $9""",
                    AttemptState.SUCCEEDED.value,
                    input_tokens, output_tokens, cached_tokens,
                    reasoning_tokens, tool_tokens, total_tokens,
                    usage_source.value, attempt_id,
                )
                # Move reserved → used in buckets
                await self._settle_buckets(conn, attempt_id, requests=1, tokens=total_tokens, cost=0)
        else:
            if attempt_id in self._mem_attempts:
                att = self._mem_attempts[attempt_id]
                att["state"] = AttemptState.SUCCEEDED.value
                att["input_tokens"] = input_tokens
                att["output_tokens"] = output_tokens
                att["settled_at"] = datetime.now(timezone.utc).isoformat()

    async def settle_unknown(self, attempt_id: str, error_code: str = "unknown_charge") -> None:
        """Settle an attempt as unknown_charge: charge the full reserved amount."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT settled_at, reserved_tokens, reserved_cost FROM llm_attempts WHERE id = $1",
                    attempt_id,
                )
                if row and row["settled_at"] is not None:
                    return  # Already settled

                reserved_tokens = row["reserved_tokens"] if row else 0
                reserved_cost = float(row["reserved_cost"] or 0) if row else 0
                await conn.execute(
                    """UPDATE llm_attempts SET
                       state = $1, finished_at = NOW(),
                       error_code = $2, charged_tokens = $3,
                       charged_cost = $4, usage_source = $5, settled_at = NOW()
                       WHERE id = $6""",
                    AttemptState.UNKNOWN_CHARGE.value, error_code,
                    reserved_tokens, reserved_cost,
                    UsageSource.UNKNOWN.value, attempt_id,
                )
                await self._settle_buckets(conn, attempt_id, requests=1,
                                           tokens=reserved_tokens, cost=reserved_cost)
        else:
            if attempt_id in self._mem_attempts:
                self._mem_attempts[attempt_id]["state"] = AttemptState.UNKNOWN_CHARGE.value
                self._mem_attempts[attempt_id]["settled_at"] = datetime.now(timezone.utc).isoformat()

    async def _settle_buckets(self, conn, attempt_id: str, requests: int,
                               tokens: int, cost: float) -> None:
        """Move reserved → used for all buckets of this attempt's reservations."""
        from ai.budget_policy import BudgetPolicy
        policy = BudgetPolicy()

        reservations = await conn.fetch(
            "SELECT scope_key, period_kind, period_start, requests, tokens, cost "
            "FROM llm_reservations WHERE attempt_id = $1",
            attempt_id,
        )
        for res in reservations:
            await conn.execute(
                """UPDATE llm_budget_buckets
                   SET reserved_requests = GREATEST(0, reserved_requests - $1),
                       reserved_tokens = GREATEST(0, reserved_tokens - $2),
                       reserved_cost = GREATEST(0, reserved_cost - $3),
                       used_requests = used_requests + $1,
                       used_tokens = used_tokens + $2,
                       used_cost = used_cost + $3
                   WHERE scope_key = $4 AND period_kind = $5 AND period_start = $6""",
                res["requests"], res["tokens"], float(res["cost"] or 0),
                res["scope_key"], res["period_kind"], res["period_start"],
            )

    # ── Routing decisions ────────────────────────────────────────────────

    async def log_routing_decision(self, request_id: str, sequence: int,
                                     candidate: str, decision: str,
                                     reason_code: str | None = None) -> None:
        """Log a routing decision."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO llm_routing_decisions
                       (request_id, sequence, candidate_provider_model, decision, reason_code, created_at)
                       VALUES ($1, $2, $3, $4, $5, NOW())""",
                    request_id, sequence, candidate, decision, reason_code,
                )
        else:
            self._mem_decisions.append({
                "request_id": request_id, "sequence": sequence,
                "candidate": candidate, "decision": decision,
                "reason_code": reason_code,
            })

    # ── Validation gates ──────────────────────────────────────────────────

    async def check_gate(self, provider: str, model: str) -> bool:
        """Check if a validation gate is valid for this model."""
        pool = await self._get_pool()
        if pool:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT passed, expires_at FROM llm_validation_gates
                       WHERE provider = $1 AND model = $2 AND passed = TRUE
                       ORDER BY expires_at DESC LIMIT 1""",
                    provider, model,
                )
                if not row:
                    return False
                from datetime import datetime as dt, timezone as tz
                return row["expires_at"].replace(tzinfo=tz.utc) > dt.now(tz.utc)
        else:
            gate = self._mem_gates.get(f"{provider}/{model}")
            if not gate:
                return False  # No gate data → gate false
            return gate.get("passed", False)

    # ── Reaper ────────────────────────────────────────────────────────────

    async def reap_stale_attempts(self, stale_seconds: int = 60) -> int:
        """Close attempts running past their deadline+60s as unknown_charge.

        Idempotent: settled_at check prevents double settlement.
        Returns count of reaped attempts.
        """
        pool = await self._get_pool()
        if not pool:
            return 0
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id FROM llm_attempts
                   WHERE state = 'running'
                     AND deadline_at IS NOT NULL
                     AND deadline_at + INTERVAL '60 seconds' < NOW()
                     AND settled_at IS NULL""",
            )
            for row in rows:
                await self.settle_unknown(row["id"], error_code="reaper_timeout")
            return len(rows)

    # ── Test helpers ──────────────────────────────────────────────────────

    def get_mem_buckets(self) -> dict:
        """Get in-memory buckets for testing."""
        return dict(self._mem_buckets)

    def get_mem_attempts(self) -> dict:
        """Get in-memory attempts for testing."""
        return dict(self._mem_attempts)

    def get_mem_decisions(self) -> list:
        """Get in-memory routing decisions for testing."""
        return list(self._mem_decisions)

    def reset(self) -> None:
        """Reset all in-memory state for testing."""
        self._mem_requests.clear()
        self._mem_attempts.clear()
        self._mem_buckets.clear()
        self._mem_decisions.clear()
        self._mem_gates.clear()