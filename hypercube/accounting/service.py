"""Token accounting service — record and aggregate usage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage.repositories import UsageRecordRepository


class TokenAccountingService:
    def __init__(self, repo: UsageRecordRepository) -> None:
        self._repo = repo

    async def record_usage(self, record) -> None:  # core.schemas.UsageRecord
        """Persist a usage record."""
        from storage.models import UsageRecord as UsageRecordModel

        obj = UsageRecordModel(
            request_id=record.request_id,
            conversation_id=record.conversation_id,
            telegram_user_id=record.telegram_user_id,
            provider_id=record.provider_id,
            model_id=record.model_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            reasoning_tokens=record.reasoning_tokens,
            cached_tokens=record.cached_tokens,
            latency_ms=record.latency_ms,
            status=record.status,
            error_code=record.error_code,
            cost_estimate=record.cost_estimate,
            cost_currency="USD",
            quota_scope=record.quota_scope,
            warning_triggered=record.warning_triggered,
            fallback_triggered=record.fallback_triggered,
            context_handoff_triggered=record.context_handoff_triggered,
            uncertain_usage=record.uncertain_usage,
        )
        await self._repo.create(obj)

    async def get_user_usage(self, user_id: int, window: str = "day") -> dict:
        start, end = self._window(window)
        return await self._repo.aggregate_by_user(user_id, start, end)

    async def get_model_usage(self, model_id: str, window: str = "day") -> dict:
        start, end = self._window(window)
        records = await self._repo.by_model(model_id, start, end)
        total_tokens = sum(r.total_tokens or 0 for r in records)
        total_cost = sum(r.cost_estimate for r in records)
        return {"requests": len(records), "total_tokens": total_tokens, "cost": total_cost}

    async def get_provider_usage(self, provider_id: str, window: str = "day") -> dict:
        start, end = self._window(window)
        records = await self._repo.by_provider(provider_id, start, end)
        total_tokens = sum(r.total_tokens or 0 for r in records)
        total_cost = sum(r.cost_estimate for r in records)
        return {"requests": len(records), "total_tokens": total_tokens, "cost": total_cost}

    async def get_usage_history(self, user_id: int, limit: int = 50, offset: int = 0) -> list:
        return await self._repo.list(limit=limit, offset=offset)

    async def get_error_rate(self, user_id: int, window: str = "day") -> float:
        start, end = self._window(window)
        return await self._repo.error_rate(user_id, start, end)

    async def get_total_cost(self, user_id: int, window: str = "day") -> float:
        usage = await self.get_user_usage(user_id, window)
        return usage.get("cost", 0.0)

    @staticmethod
    def _window(window: str) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        if window == "day":
            return now - timedelta(days=1), now
        if window == "week":
            return now - timedelta(weeks=1), now
        if window == "month":
            return now - timedelta(days=30), now
        return now - timedelta(days=1), now
