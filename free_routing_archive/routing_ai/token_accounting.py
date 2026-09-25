"""
Token Accounting - logs every AI request for audit and quota tracking.
Records: request_id, user_id, task_type, routing_mode, models, tokens, latency, status.
"""
from __future__ import annotations

import logging
import time
import uuid

log = logging.getLogger(__name__)


def generate_request_id() -> str:
    return uuid.uuid4().hex[:16]


async def log_ai_request(
    user_id: int,
    task_type: str,
    routing_mode: str,
    requested_model: str,
    final_model: str,
    response=None,
    latency_ms: int = 0,
    status: str = "ok",
    error_type: str = None,
    error_msg: str = None,
) -> str:
    """Log an AI request to usage_log table. Returns request_id."""
    from storage.postgres_client import record_usage

    request_id = generate_request_id()

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    if response and hasattr(response, "usage") and response.usage:
        prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
        total_tokens = getattr(response.usage, "total_tokens", 0) or 0

    try:
        await record_usage(
            request_id=request_id,
            user_id=user_id,
            task_type=task_type,
            routing_mode=routing_mode,
            requested_model=requested_model,
            final_model=final_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            status=status,
            error_type=error_type,
            error_msg=error_msg,
        )
    except Exception as exc:
        log.error("Token accounting failed: %s", exc)

    return request_id


class RequestTimer:
    """Context manager for measuring AI request latency."""

    def __init__(self):
        self.start = 0
        self.latency_ms = 0

    def __enter__(self):
        self.start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.latency_ms = int((time.monotonic() - self.start) * 1000)
