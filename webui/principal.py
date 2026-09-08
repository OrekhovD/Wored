"""Principal: the authoritative identity for auth decisions.

A Principal is never derived from user-supplied body fields like user_id or
requested_by — only from cryptographically verified sources (cookie session,
Telegram initData signature, or internal service token).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from access_control import verify_telegram


@dataclass(frozen=True)
class Principal:
    kind: str          # "password_admin" | "telegram_admin" | "internal_service"
    subject: str       # Human-readable identifier (username, tg:@user, "internal")
    telegram_user_id: int | None  # None for non-Telegram principals
    is_admin: bool     # True for all current principal types


# Legacy user_id for password_admin — must not be relied on for ownership.
PASSWORD_ADMIN_USER_ID = 0


def create_from_cookie(session: dict) -> Principal | None:
    """Reconstruct a Principal from a verified cookie session.

    Validates that the session's telegram_user_id is still in the allowed set,
    so a revoked admin immediately loses access on next request.
    """
    if not session.get("authenticated"):
        return None

    auth_type = session.get("auth_type")

    if auth_type == "password":
        username = session.get("username", "")
        if not username:
            return None
        return Principal(
            kind="password_admin",
            subject=username,
            telegram_user_id=None,
            is_admin=True,
        )

    if auth_type == "telegram":
        user = session.get("telegram_user")
        if not isinstance(user, dict):
            return None
        user_id = user.get("user_id")
        if type(user_id) is not int:
            return None
        # Re-check allowed IDs so a revoked admin is blocked immediately.
        import os
        raw_ids = os.getenv("TELEGRAM_ADMIN_IDS", os.getenv("TELEGRAM_ADMIN_ID", ""))
        try:
            allowed_ids = {int(v.strip()) for v in raw_ids.split(",") if v.strip()}
        except ValueError:
            return None
        if user_id not in allowed_ids:
            return None
        return Principal(
            kind="telegram_admin",
            subject=f"tg:{user_id}",
            telegram_user_id=user_id,
            is_admin=True,
        )

    return None


def create_from_telegram(
    init_data: str,
    bot_tokens: list[str],
    allowed_ids: set[int],
    now: float | None = None,
) -> Principal | None:
    """Verify Telegram initData against a list of bot tokens.

    Tries each token in order; exactly one must verify.  If zero or more than
    one verify, the authentication fails (ambiguity = rejection).
    """
    if not init_data or not bot_tokens or not allowed_ids:
        return None

    verified_user: dict | None = None
    for token in bot_tokens:
        result = verify_telegram(init_data, token, allowed_ids, now=now)
        if result is not None:
            if verified_user is not None:
                # More than one token verified — ambiguous, reject.
                return None
            verified_user = result

    if verified_user is None:
        return None

    return Principal(
        kind="telegram_admin",
        subject=f"tg:{verified_user['user_id']}",
        telegram_user_id=verified_user["user_id"],
        is_admin=True,
    )


def create_from_internal_token(
    token: str | None,
    configured_token: str,
) -> Principal | None:
    """Authenticate an internal service call via bearer token.

    Returns None if token is absent, empty, or doesn't match.
    """
    if not configured_token or not token:
        return None
    if not hmac.compare_digest(configured_token, token):
        return None
    return Principal(
        kind="internal_service",
        subject="internal",
        telegram_user_id=None,
        is_admin=False,
    )