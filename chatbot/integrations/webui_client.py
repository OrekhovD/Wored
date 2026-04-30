from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx


def get_webui_internal_base_url() -> str:
    return os.getenv("WEBUI_INTERNAL_URL", "http://webui:8000").rstrip("/")


def get_webui_public_base_url() -> str:
    return os.getenv("WEBUI_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")


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
    explicit = os.getenv("WEBUI_INTERNAL_TOKEN", "").strip()
    if explicit:
        return explicit

    material = f"wored-internal::{get_session_secret()}::{os.getenv('TELEGRAM_TOKEN', 'local')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def create_prediction_request(symbol: str, horizon_hours: int, requested_by: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
        response = await client.post(
            f"{get_webui_internal_base_url()}/api/internal/predictions",
            json={
                "symbol": symbol,
                "horizon_hours": horizon_hours,
                "requested_by": requested_by,
                "source": "telegram",
            },
            headers={"X-Internal-Token": get_internal_api_token()},
        )
        response.raise_for_status()
        return response.json()
