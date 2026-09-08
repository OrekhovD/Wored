"""Administrative access primitives. Never accept unsigned development identities."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlsplit


def verify_telegram(init_data: str, bot_token: str, allowed_ids: set[int],
                    now: float | None = None, max_age: int = 300) -> dict | None:
    if not init_data or not bot_token or not allowed_ids:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            return None
        signature = data.pop("hash", "")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        message = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
        expected = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        age = (time.time() if now is None else now) - int(data["auth_date"])
        if not 0 <= age <= max_age:
            return None
        user = json.loads(data["user"])
        user_id = user.get("id")
        if type(user_id) is not int or user_id not in allowed_ids:
            return None
        return {"user_id": user_id, "username": user.get("username", "")}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def verify_telegram_multi(
    init_data: str,
    bot_tokens: list[str],
    allowed_ids: set[int],
    now: float | None = None,
    max_age: int = 300,
) -> dict | None:
    """Verify initData against multiple bot tokens, accepting exactly one match.

    Falls back to single-token mode if bot_tokens is empty: uses
    TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN env var for compatibility.
    Returns the user dict on success, None on failure.
    """
    if not init_data or not allowed_ids:
        return None

    # If no explicit multi-tokens provided, fall back to single-token env vars.
    if not bot_tokens:
        single_token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
        if not single_token:
            return None
        return verify_telegram(init_data, single_token, allowed_ids, now=now, max_age=max_age)

    # Try each token; exactly one must succeed.
    verified: dict | None = None
    for token in bot_tokens:
        result = verify_telegram(init_data, token, allowed_ids, now=now, max_age=max_age)
        if result is not None:
            if verified is not None:
                return None  # ambiguous — more than one token verified
            verified = result
    return verified


def allowed_origin(origin: str, request_url: str, public_base_url: str = "") -> bool:
    def authority(value):
        parsed = urlsplit(value)
        return (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    try:
        if origin == "null" or urlsplit(origin).scheme not in {"https", "http"}:
            return False
        return authority(origin) in {authority(request_url), authority(public_base_url)}
    except ValueError:
        return False


def safe_next_url(value: str) -> str:
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(char) < 32 for char in value)):
        return "/"
    return value
