"""Shared enumerations for the Hytergram gateway."""

from __future__ import annotations

from enum import StrEnum


class RoutingMode(StrEnum):
    FREE_ONLY = "free_only"
    BALANCED = "balanced"
    PREMIUM = "premium"


class ProviderId(StrEnum):
    DASHSCOPE = "dashscope"
    NVAPI = "nvapi"
    ZHIPU = "zhipu"
    AI_STUDIO = "ai_studio"


class ModelStatus(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    INACTIVE = "inactive"


class RequestStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    POLICY_REJECTION = "policy_rejection"


class ContextCompressionMethod(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    TRUNCATION = "truncation"


class HandoffVersion(StrEnum):
    V1 = "v1"


class HTXDataType(StrEnum):
    TICKER = "ticker"
    CANDLE = "candle"
    ORDER_BOOK = "order_book"
    RECENT_TRADE = "recent_trade"
    SYMBOLS = "symbols"
