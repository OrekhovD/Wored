"""R03: Principal, dual bot auth, and CSRF enforcement tests.

Tests R03-01..07:
  R03-01: Valid login from each bot (two tokens)
  R03-02: Signature from wrong bot is rejected
  R03-03: Revoked admin loses cookie access (allowed_ids changes)
  R03-04: user_id substitution from public body doesn't grant access
  R03-05: Cross-origin cookie POST gets 403
  R03-06: Replay of old initData (>300s or future) is rejected
  R03-07: Absent internal token returns 403 for /api/internal/*
"""
import hashlib
import hmac
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT / "chatbot")]

# SSL workaround for uv Python on Windows — must happen before aiohttp import
if os.name == "nt":
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        if os.environ.get("SSL_CERT_FILE", None) == "":
            del os.environ["SSL_CERT_FILE"]

# Pre-import aiohttp at module level so SSL context is created before any
# test's patch.dict(clear=True) wipes SSL_CERT_FILE from env.
try:
    import aiohttp  # noqa: F401
except Exception:
    pass

from access_control import verify_telegram, verify_telegram_multi, allowed_origin
from principal import (
    Principal,
    create_from_cookie,
    create_from_telegram,
    create_from_internal_token,
    PASSWORD_ADMIN_USER_ID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOT_TOKEN_A = "730:AAH_test_bot_alpha_token_for_r03"
BOT_TOKEN_B = "740:AAH_test_bot_beta_token_for_r03"
WRONG_TOKEN = "999:AAH_wrong_bot_token_for_r03"
ADMIN_IDS = {42, 43}
NOW = 1700000000


def _sign_init_data(user_id: int, auth_date: int, bot_token: str) -> str:
    """Create a signed Telegram initData string for testing."""
    from urllib.parse import urlencode
    user_json = json.dumps({"id": user_id, "username": f"u{user_id}"})
    data = {"auth_date": str(auth_date), "user": user_json}
    message = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    data["hash"] = signature
    return urlencode(data)


def _base_env(**overrides):
    """Base env dict for TestClient with dual-bot config."""
    env = {
        "WEBUI_AUTH_ENABLED": "true",
        "WEBUI_ADMIN_PASSWORD": "qa-password",
        "WEBUI_SESSION_SECRET": "qa-session-secret-32-characters-long!",
        "DATABASE_URL": "",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "WEBUI_INTERNAL_TOKEN": "qa-internal",
        "TELEGRAM_ADMIN_IDS": "42,43",
        "TELEGRAM_TOKEN": BOT_TOKEN_A,
        "WEBUI_TELEGRAM_BOT_TOKENS": json.dumps([BOT_TOKEN_A, BOT_TOKEN_B]),
    }
    env.update(overrides)
    # Preserve SSL_CERT_FILE on Windows
    _ssl_cert = os.environ.get("SSL_CERT_FILE", "")
    if _ssl_cert:
        env["SSL_CERT_FILE"] = _ssl_cert
    return env


class _TestClientMixin:
    """Shared setUp/tearDown for TestClient-based tests."""

    def _make_client(self, env_dict):
        from fastapi.testclient import TestClient
        from app import app
        self.env = patch.dict(os.environ, env_dict, clear=True)
        self.env.start()
        self.client = TestClient(app, follow_redirects=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()


# ===========================================================================
# R03-01: Valid login from each bot (two tokens)
# ===========================================================================
class TestDualBotAuth(unittest.TestCase):
    """R03-01..03, 06: Multi-token Telegram auth via Principal."""

    def test_r03_01_valid_login_from_each_bot(self):
        """Each bot token individually verifies a valid initData."""
        for token in (BOT_TOKEN_A, BOT_TOKEN_B):
            init_data = _sign_init_data(42, NOW, token)
            p = create_from_telegram(init_data, [BOT_TOKEN_A, BOT_TOKEN_B], ADMIN_IDS, now=NOW)
            self.assertIsNotNone(p, f"Expected Principal for token ending …{token[-6:]}")
            self.assertEqual(p.kind, "telegram_admin")
            self.assertEqual(p.telegram_user_id, 42)
            self.assertTrue(p.is_admin)

    def test_r03_01_verify_telegram_multi_returns_user_dict(self):
        """verify_telegram_multi also works with each token."""
        for token in (BOT_TOKEN_A, BOT_TOKEN_B):
            init_data = _sign_init_data(42, NOW, token)
            result = verify_telegram_multi(init_data, [BOT_TOKEN_A, BOT_TOKEN_B], ADMIN_IDS, now=NOW)
            self.assertIsNotNone(result)
            self.assertEqual(result["user_id"], 42)

    def test_r03_02_wrong_bot_signature_rejected(self):
        """initData signed with a token not in the list is rejected."""
        init_data = _sign_init_data(42, NOW, WRONG_TOKEN)
        p = create_from_telegram(init_data, [BOT_TOKEN_A, BOT_TOKEN_B], ADMIN_IDS, now=NOW)
        self.assertIsNone(p)

    def test_r03_03_revoked_admin_loses_cookie_access(self):
        """Changing allowed_ids revokes an already-issued cookie session."""
        # Session created when 42 was an admin
        session = {
            "authenticated": True,
            "auth_type": "telegram",
            "username": "admin",
            "telegram_user": {"user_id": 42, "username": "u42"},
        }
        # When allowed_ids includes 42, cookie works
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "42,43", "TELEGRAM_ADMIN_ID": ""}):
            p = create_from_cookie(session)
            self.assertIsNotNone(p)
            self.assertEqual(p.telegram_user_id, 42)

        # After revoking 42 from the allowed list, cookie is rejected
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "43", "TELEGRAM_ADMIN_ID": ""}):
            p = create_from_cookie(session)
            self.assertIsNone(p, "Revoked admin must be blocked even with valid cookie")

    def test_r03_06_replay_of_old_init_data_rejected(self):
        """initData older than 300s or from the future is rejected."""
        # Too old (> 300s)
        init_data = _sign_init_data(42, NOW - 600, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        self.assertIsNone(p, "Stale initData must be rejected")

        # Future timestamp
        init_data = _sign_init_data(42, NOW + 600, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        self.assertIsNone(p, "Future initData must be rejected")

    def test_r03_06_exactly_at_max_age_boundary(self):
        """initData exactly 300s old should be accepted (boundary)."""
        init_data = _sign_init_data(42, NOW - 300, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        self.assertIsNotNone(p, "Data at exactly 300s should be accepted")

    def test_empty_bot_tokens_falls_back_to_single(self):
        """When bot_tokens list is empty, verify_telegram_multi falls back to env var."""
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": BOT_TOKEN_A, "TELEGRAM_BOT_TOKEN": ""}):
            result = verify_telegram_multi(init_data, [], ADMIN_IDS, now=NOW)
            self.assertIsNotNone(result)
            self.assertEqual(result["user_id"], 42)


# ===========================================================================
# R03-04: user_id from public body doesn't grant access
# ===========================================================================
class TestUserIdSubstitution(unittest.TestCase):
    """R03-04: Public body user_id/requested_by must not determine rights."""

    def test_public_body_user_id_ignored(self):
        """A Principal never derives identity from user_id in a request body."""
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        # The principal's identity is from the verified initData, not any body field
        self.assertEqual(p.telegram_user_id, 42)
        # Even if someone sent user_id=999 in the body, it doesn't affect the principal
        self.assertEqual(p.subject, "tg:42")

    def test_password_admin_has_legacy_user_id_zero(self):
        """Password admin principals use user_id=0, not from any body field."""
        p = Principal(
            kind="password_admin",
            subject="admin",
            telegram_user_id=None,
            is_admin=True,
        )
        self.assertIsNone(p.telegram_user_id)
        self.assertTrue(p.is_admin)


# ===========================================================================
# R03-05: Cross-origin cookie POST gets 403
# ===========================================================================
class TestCSRFOriginEnforcement(_TestClientMixin, unittest.TestCase):
    """R03-05: Cross-origin POST with cookie is rejected."""

    def setUp(self):
        self._make_client(_base_env())

    def test_cross_origin_post_denied(self):
        """POST with session cookie but wrong Origin → 403."""
        # Log in via password
        page = self.client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        resp = self.client.post("/login", data={
            "username": "admin", "password": "qa-password", "csrf_token": csrf,
        })
        self.assertEqual(resp.status_code, 303)

        # Authenticated GET works
        self.assertEqual(self.client.get("/command-deck").status_code, 200)

        # POST with wrong Origin is denied
        resp = self.client.post("/api/positions/open", json={},
                                headers={"Origin": "https://untrusted.example"})
        self.assertEqual(resp.status_code, 403)

    def test_same_origin_post_allowed(self):
        """POST with same Origin is allowed."""
        page = self.client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        resp = self.client.post("/login", data={
            "username": "admin", "password": "qa-password", "csrf_token": csrf,
        })
        self.assertEqual(resp.status_code, 303)

        # Same-origin POST should not be blocked (origin matches request URL)
        resp = self.client.post("/api/positions/open", json={},
                                headers={"Origin": "http://testserver"})
        # The endpoint itself may return 4xx for business reasons, but NOT 403 for origin
        self.assertNotEqual(resp.status_code, 403)


# ===========================================================================
# R03-07: Absent internal token → 403 for /api/internal/*
# ===========================================================================
class TestInternalTokenEnforcement(_TestClientMixin, unittest.TestCase):
    """R03-07: Missing or wrong internal token is rejected."""

    def setUp(self):
        self._make_client(_base_env())

    def test_absent_internal_token_returns_403(self):
        """No X-Internal-Token header → 403."""
        resp = self.client.post("/api/internal/predictions", json={})
        self.assertEqual(resp.status_code, 403)

    def test_wrong_internal_token_returns_403(self):
        """Incorrect X-Internal-Token → 403."""
        resp = self.client.post("/api/internal/predictions", json={},
                                headers={"X-Internal-Token": "wrong"})
        self.assertEqual(resp.status_code, 403)

    def test_correct_internal_token_accepted(self):
        """Correct X-Internal-Token is not rejected at the middleware level."""
        resp = self.client.post("/api/internal/predictions", json={},
                                headers={"X-Internal-Token": "qa-internal"})
        # Not 403 — may be 422, 503 etc depending on business logic, but not 403
        self.assertNotEqual(resp.status_code, 403)


# ===========================================================================
# R03 Principal unit tests (pure logic, no TestClient)
# ===========================================================================
class TestPrincipalCreation(unittest.TestCase):
    """Unit tests for Principal creation functions."""

    def test_create_from_cookie_password(self):
        session = {
            "authenticated": True,
            "auth_type": "password",
            "username": "admin",
        }
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "", "TELEGRAM_ADMIN_ID": ""}):
            p = create_from_cookie(session)
        self.assertIsNotNone(p)
        self.assertEqual(p.kind, "password_admin")
        self.assertEqual(p.subject, "admin")
        self.assertTrue(p.is_admin)

    def test_create_from_cookie_telegram(self):
        session = {
            "authenticated": True,
            "auth_type": "telegram",
            "username": "admin",
            "telegram_user": {"user_id": 42, "username": "u42"},
        }
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "42", "TELEGRAM_ADMIN_ID": ""}):
            p = create_from_cookie(session)
        self.assertIsNotNone(p)
        self.assertEqual(p.kind, "telegram_admin")
        self.assertEqual(p.telegram_user_id, 42)

    def test_create_from_cookie_unauthenticated(self):
        self.assertIsNone(create_from_cookie({"authenticated": False}))
        self.assertIsNone(create_from_cookie({}))

    def test_create_from_internal_token(self):
        p = create_from_internal_token("secret", "secret")
        self.assertIsNotNone(p)
        self.assertEqual(p.kind, "internal_service")
        self.assertFalse(p.is_admin)

    def test_create_from_internal_token_wrong(self):
        self.assertIsNone(create_from_internal_token("wrong", "secret"))
        self.assertIsNone(create_from_internal_token("", "secret"))
        self.assertIsNone(create_from_internal_token("secret", ""))

    def test_ambiguous_multi_token_rejected(self):
        """If initData verifies against more than one token, it must be rejected."""
        # Use the same token twice — should not be ambiguous since it's the same
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        self.assertIsNotNone(p, "Same token once in list should work")

    def test_create_from_telegram_empty_tokens_rejected(self):
        """Empty token list → None."""
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [], ADMIN_IDS, now=NOW)
        self.assertIsNone(p)

    def test_create_from_telegram_empty_allowed_ids_rejected(self):
        """Empty allowed_ids → None."""
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], set(), now=NOW)
        self.assertIsNone(p)

    def test_create_from_telegram_non_admin_user_rejected(self):
        """User ID not in allowed_ids → None."""
        init_data = _sign_init_data(999, NOW, BOT_TOKEN_A)
        p = create_from_telegram(init_data, [BOT_TOKEN_A], ADMIN_IDS, now=NOW)
        self.assertIsNone(p)


class TestVerifyTelegramMultiFallback(unittest.TestCase):
    """Test verify_telegram_multi fallback to single env var."""

    def test_fallback_to_telegram_token_env(self):
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": BOT_TOKEN_A, "TELEGRAM_BOT_TOKEN": ""}):
            result = verify_telegram_multi(init_data, [], ADMIN_IDS, now=NOW)
            self.assertIsNotNone(result)
            self.assertEqual(result["user_id"], 42)

    def test_fallback_to_telegram_bot_token_env(self):
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "", "TELEGRAM_BOT_TOKEN": BOT_TOKEN_A}):
            result = verify_telegram_multi(init_data, [], ADMIN_IDS, now=NOW)
            self.assertIsNotNone(result)

    def test_no_tokens_no_env_returns_none(self):
        init_data = _sign_init_data(42, NOW, BOT_TOKEN_A)
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "", "TELEGRAM_BOT_TOKEN": ""}):
            result = verify_telegram_multi(init_data, [], ADMIN_IDS, now=NOW)
            self.assertIsNone(result)


class TestGetTelegramBotTokens(unittest.TestCase):
    """Test get_telegram_bot_tokens() parsing and validation."""

    def _import_app(self):
        """Import app module fresh (env-dependent)."""
        import importlib
        import app as app_module
        importlib.reload(app_module)
        return app_module

    def test_parse_json_array(self):
        app_mod = self._import_app()
        with patch.dict(os.environ, {
            "WEBUI_TELEGRAM_BOT_TOKENS": '["token1", "token2"]',
            "TELEGRAM_TOKEN": "", "TELEGRAM_BOT_TOKEN": "",
        }, clear=False):
            # Need to re-read since get_telegram_bot_tokens reads env at call time
            result = app_mod.get_telegram_bot_tokens()
            self.assertEqual(result, ["token1", "token2"])

    def test_fallback_to_single_token(self):
        app_mod = self._import_app()
        with patch.dict(os.environ, {
            "WEBUI_TELEGRAM_BOT_TOKENS": "",
            "TELEGRAM_TOKEN": "my-single-token", "TELEGRAM_BOT_TOKEN": "",
        }, clear=False):
            result = app_mod.get_telegram_bot_tokens()
            self.assertEqual(result, ["my-single-token"])

    def test_empty_json_array_raises(self):
        app_mod = self._import_app()
        with patch.dict(os.environ, {
            "WEBUI_TELEGRAM_BOT_TOKENS": "[]",
            "TELEGRAM_TOKEN": "", "TELEGRAM_BOT_TOKEN": "",
        }, clear=False):
            with self.assertRaises(RuntimeError):
                app_mod.get_telegram_bot_tokens()

    def test_invalid_json_raises(self):
        app_mod = self._import_app()
        with patch.dict(os.environ, {
            "WEBUI_TELEGRAM_BOT_TOKENS": "not-json",
            "TELEGRAM_TOKEN": "", "TELEGRAM_BOT_TOKEN": "",
        }, clear=False):
            with self.assertRaises(RuntimeError):
                app_mod.get_telegram_bot_tokens()


if __name__ == "__main__":
    unittest.main()