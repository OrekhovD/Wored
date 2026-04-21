"""Request ID generation utilities."""

from __future__ import annotations

import uuid


def generate_request_id() -> str:
    return uuid.uuid4().hex


def generate_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:16]}"
