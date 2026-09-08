from __future__ import annotations

import hashlib
import os
import re
import uuid
from typing import Any

import httpx

from forecast_input import validate_idempotency_key


def get_webui_internal_base_url() -> str:
    return os.getenv("WEBUI_INTERNAL_URL", "http://webui:8000").rstrip("/")


def get_webui_public_base_url() -> str:
    """Public WebUI base URL for Telegram inline buttons.

    Priority: WEBUI_PUBLIC_BASE_URL > TG_MINIAPP_URL > WEBUI_URL.
    Strips any path suffix (e.g. /daily-session) to get the bare base URL.
    Telegram requires a public HTTPS URL — localhost will be rejected.
    """
    raw = (
        os.getenv("WEBUI_PUBLIC_BASE_URL")
        or os.getenv("TG_MINIAPP_URL")
        or os.getenv("WEBUI_URL")
        or "http://localhost:8080"
    )
    # Strip path suffix — TG_MINIAPP_URL may include /daily-session
    from urllib.parse import urlparse
    parsed = urlparse(raw.rstrip("/"))
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base


def get_session_secret() -> str:
    explicit = os.getenv("WEBUI_SESSION_SECRET", "").strip()
    if explicit:
        return explicit

    admin_username = os.getenv("WEBUI_ADMIN_USERNAME", "admin").strip()
    admin_password = os.getenv("WEBUI_ADMIN_PASSWORD", "").strip()
    material = f"{admin_username}::{admin_password}::{os.getenv('TELEGRAM_ADMIN_ID', 'local')}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"wored-webui-{digest}"


def get_internal_api_token() -> str:
    token = os.getenv("WEBUI_INTERNAL_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WEBUI_INTERNAL_TOKEN must match the WebUI configuration")
    return token


async def create_prediction_request(
    symbol: str,
    horizon_hours: int,
    requested_by: str,
    idempotency_key: str | None = None,
    base_timeframe: str = "60min",
    depth: int = 3,
    horizon_steps: int | None = None,
) -> dict[str, Any]:
    """Create a forecast request via the internal API.

    Supports both legacy horizon_hours and the new horizon_steps parameter.
    The idempotency key is validated; if None, a random one is generated.
    """
    # Validate idempotency key if provided
    if idempotency_key:
        try:
            idempotency_key = validate_idempotency_key(idempotency_key)
        except ValueError:
            pass  # Let the server validate — but try our best
    effective_key = idempotency_key or uuid.uuid4().hex

    payload: dict[str, Any] = {
        "symbol": symbol,
        "requested_by": requested_by,
        "source": "telegram",
        "base_timeframe": base_timeframe,
        "depth": depth,
    }
    # Prefer horizon_steps when given; fall back to horizon_hours conversion
    if horizon_steps is not None:
        payload["horizon_steps"] = horizon_steps
    else:
        payload["horizon_hours"] = horizon_hours

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        response = await client.post(
            f"{get_webui_internal_base_url()}/api/internal/predictions",
            json=payload,
            headers={"X-Internal-Token": get_internal_api_token(), "Idempotency-Key": effective_key},
        )
        response.raise_for_status()
        return response.json()


async def get_forecast_status(request_id: int) -> dict[str, Any]:
    """Query the normalized forecast status via the internal API.

    Returns execution_state, evaluation_state, failure_code, deadline_at, as_of, valid_until.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(
            f"{get_webui_internal_base_url()}/api/forecast/{request_id}/status",
            headers={"X-Internal-Token": get_internal_api_token()},
        )
        response.raise_for_status()
        return response.json()