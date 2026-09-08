import importlib.util
import os
import re
import sys
import unittest
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT / "chatbot")]
AVAILABLE = all(importlib.util.find_spec(name) for name in ("asyncpg", "redis", "itsdangerous", "fastapi"))

# On Windows with uv-managed Python 3.11, SSL context creation fails at
# module level in aiohttp when the system has no default CA bundle at
# openssl_cafile (C:\Program Files\Common Files\SSL\cert.pem). The uv Python
# bundles its own OpenSSL which looks for CA at that path, but it doesn't exist.
# Setting SSL_CERT_FILE to certifi's CA bundle before aiohttp import fixes this.
if os.name == "nt":
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        if os.environ.get("SSL_CERT_FILE", None) == "":
            del os.environ["SSL_CERT_FILE"]

# Pre-import aiohttp/openai/app at module level so their module-level SSL context
# is created BEFORE any test's patch.dict(clear=True) wipes SSL_CERT_FILE from env.
# On Windows/uv, aiohttp creates _SSL_CONTEXT_VERIFIED at import time via
# ssl.create_default_context(), which fails if SSL_CERT_FILE is empty/missing.
if AVAILABLE:
    try:
        import aiohttp  # noqa: F401 — force module-level SSL init before tests
    except Exception:
        pass  # Will be caught by test failures if SSL is truly broken


@unittest.skipUnless(AVAILABLE, "WebUI integration dependencies not available")
class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        env_dict = {"WEBUI_AUTH_ENABLED": "true", "WEBUI_ADMIN_PASSWORD": "qa-password",
                     "WEBUI_SESSION_SECRET": "qa-session-secret-32-characters-long", "DATABASE_URL": "",
                     "REDIS_URL": "redis://127.0.0.1:1/0", "WEBUI_INTERNAL_TOKEN": "qa-internal"}
        # Preserve SSL_CERT_FILE on Windows (uv Python 3.11 requires it for SSL)
        _ssl_cert = os.environ.get("SSL_CERT_FILE", "")
        if _ssl_cert:
            env_dict["SSL_CERT_FILE"] = _ssl_cert
        self.env = patch.dict(os.environ, env_dict, clear=True)
        self.env.start()
        from app import app
        self.client = TestClient(app, follow_redirects=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def test_all_admin_entry_points_require_login(self):
        for path in ("/api/command-deck", "/api/trade/preview", "/api/briefing", "/api/forecast/1/status"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/command-deck").status_code, 303)

    def test_missing_internal_token_is_denied(self):
        self.assertEqual(self.client.post("/api/internal/predictions", json={}).status_code, 403)

    def test_unavailable_dependencies_fail_readiness(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/readyz").status_code, 503)

    def test_signed_session_does_not_bypass_origin_check(self):
        page = self.client.get("/login")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        response = self.client.post("/login", data={"username": "admin", "password": "qa-password", "csrf_token": token})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/command-deck").status_code, 200)
        response = self.client.post("/api/positions/open", json={}, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)

    def telegram_exchange(self, date=None):
        from test_contracts import signed_telegram
        page = self.client.get("/login")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        return self.client.post("/api/auth/telegram", json={"csrf_token": token,
            "init_data": signed_telegram(date=int(time.time()) if date is None else date)})

    def test_telegram_login_creates_session_but_keeps_origin_boundary(self):
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "test-token", "TELEGRAM_ADMIN_IDS": "42"}):
            self.assertEqual(self.telegram_exchange().status_code, 200)
            self.assertEqual(self.client.get("/command-deck").status_code, 200)
            self.assertEqual(self.client.post("/api/positions/open", json={},
                headers={"Origin": "https://untrusted.example"}).status_code, 403)

    def test_revoked_telegram_admin_loses_cookie_access(self):
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "test-token", "TELEGRAM_ADMIN_IDS": "42"}):
            self.assertEqual(self.telegram_exchange().status_code, 200)
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "43"}):
            self.assertEqual(self.client.get("/api/command-deck").status_code, 401)

    def test_expired_telegram_data_cannot_start_session(self):
        with patch.dict(os.environ, {"TELEGRAM_TOKEN": "test-token", "TELEGRAM_ADMIN_IDS": "42"}):
            self.assertEqual(self.telegram_exchange(date=1000).status_code, 401)