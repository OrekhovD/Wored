"""Custom exception hierarchy for the Hytergram gateway."""

from __future__ import annotations


class HypergramError(Exception):
    """Base exception for all application errors."""


class ConfigError(HypergramError):
    """Invalid or missing configuration."""


class QuotaExceededError(HypergramError):
    """Token quota has been exceeded."""


class ProviderError(HypergramError):
    """Generic provider error."""


class ProviderUnavailableError(ProviderError):
    """Provider is unavailable."""


class RateLimitError(ProviderError):
    """Provider returned rate limit / quota error."""


class InvalidResponseError(HypergramError):
    """Provider returned invalid response shape."""


class ContextError(HypergramError):
    """Context management error."""


class HandoffError(ContextError):
    """Context handoff failure."""


class HTXError(HypergramError):
    """HTX API error."""


class HTXRateLimitError(HTXError):
    """HTX rate limit reached."""


class RoutingError(HypergramError):
    """Routing decision failure."""


class NoCandidateModelError(RoutingError):
    """No candidate model available for routing."""


class BotAccessError(HypergramError):
    """Unauthorized bot access attempt."""
