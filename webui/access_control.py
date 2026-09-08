"""Administrative access primitives. Never accept unsigned development identities."""
from __future__ import annotations

import hashlib
import hmac
import json
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
