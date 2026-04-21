"""Quota-policy engine — checks thresholds before each request."""
from __future__ import annotations

from dataclasses import dataclass

from core.config import AppConfiguration
from storage.repositories import QuotaStateRepository, UsageRecordRepository


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    remaining_pct: float
    warning_triggered: bool
    critical_triggered: bool
    hard_stop: bool


class QuotaPolicyEngine:
    def __init__(self, quota_repo: QuotaStateRepository, accounting_repo: UsageRecordRepository, config: AppConfiguration) -> None:
        self._quota_repo = quota_repo
        self._accounting_repo = accounting_repo
        self._warn_pct = config.QUOTA_WARNING_THRESHOLD_PCT
        self._critical_pct = config.QUOTA_CRITICAL_THRESHOLD_PCT
        self._hard_stop_pct = config.QUOTA_HARD_STOP_THRESHOLD_PCT

    async def check_quota(self, provider_id: str, model_id: str) -> QuotaCheckResult:
        qs = await self._quota_repo.get_for_provider_model(provider_id, model_id)
        if qs is None:
            return QuotaCheckResult(
                allowed=True,
                remaining_pct=100.0,
                warning_triggered=False,
                critical_triggered=False,
                hard_stop=False,
            )

        remaining = qs.remaining_pct
        return QuotaCheckResult(
            allowed=remaining >= self._hard_stop_pct,
            remaining_pct=remaining,
            warning_triggered=remaining < self._warn_pct,
            critical_triggered=remaining < self._critical_pct,
            hard_stop=remaining < self._hard_stop_pct,
        )

    async def update_quota_state(
        self,
        provider_id: str,
        model_id: str,
        used_tokens: int,
        limit_tokens: int | None,
    ) -> None:
        existing = await self._quota_repo.get_for_provider_model(provider_id, model_id)
        if existing:
            from storage.models import QuotaState as QSModel

            remaining = 100.0
            if limit_tokens and limit_tokens > 0:
                remaining = max(0.0, (1 - used_tokens / limit_tokens) * 100)
            await self._quota_repo.update(
                existing.id,
                used_tokens=used_tokens,
                limit_tokens=limit_tokens,
                remaining_pct=remaining,
            )

    async def should_stop_request(self, provider_id: str, model_id: str) -> bool:
        result = await self.check_quota(provider_id, model_id)
        return not result.allowed

    async def get_warning_status(self, provider_id: str, model_id: str) -> str | None:
        result = await self.check_quota(provider_id, model_id)
        if result.hard_stop:
            return "critical"
        if result.critical_triggered:
            return "critical"
        if result.warning_triggered:
            return "warning"
        return None
