"""
Quota management - checking limits before sending AI request.
Limits: day/week/month per tier (worker/analyst/premium).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_QUOTAS = {
    "worker":  int(os.getenv("QUOTA_WORKER_DAY", "1000")),
    "analyst": int(os.getenv("QUOTA_ANALYST_DAY", "100")),
    "premium": int(os.getenv("QUOTA_PREMIUM_DAY", "20")),
}

WARN_THRESHOLD = 0.80
BLOCK_THRESHOLD = 0.95


async def check_quota(user_id: int, tier: str) -> dict:
    from storage.postgres_client import get_usage_summary
    daily_limit = DEFAULT_QUOTAS.get(tier, 100)
    summary = await get_usage_summary(user_id, period="day")
    used = summary.get("requests", 0)
    remaining = max(0, daily_limit - used)
    usage_pct = (used / daily_limit * 100) if daily_limit > 0 else 0
    warning = None
    allowed = True
    if usage_pct >= BLOCK_THRESHOLD * 100:
        if tier in ("analyst", "premium"):
            allowed = False
            warning = f"Quota {tier} at {usage_pct:.0f}%. Heavy ops blocked."
    elif usage_pct >= WARN_THRESHOLD * 100:
        warning = f"Warning: {usage_pct:.0f}% of daily {tier} quota used."
    return {"allowed": allowed, "usage_pct": round(usage_pct, 1), "remaining": remaining, "daily_limit": daily_limit, "warning": warning}


async def get_quota_status(user_id: int) -> dict:
    result = {}
    for tier in ("worker", "analyst", "premium"):
        result[tier] = await check_quota(user_id, tier)
    return result


def get_tier_for_intent(intent: str) -> str:
    mapping = {
        "price": "worker", "simple": "worker", "chat": "worker", "trade_sim": "worker",
        "trade_plan": "analyst", "analysis": "analyst", "comparison": "analyst",
        "deep_analysis": "premium",
    }
    return mapping.get(intent, "worker")
