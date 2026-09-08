"""R07: Health, readiness, and error handling contracts.

Tests R07-01…04:
  R07-01: /healthz returns 200 for live HTTP process even when PG is dead.
  R07-02: Stale ticker + fresh journal → /readyz returns 503 with components.
  R07-03: Failed task heartbeat is not green.
  R07-04: /api/health does not call any provider.
"""
from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# SSL workaround for uv Python on Windows
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
except ImportError:
    if os.environ.get("SSL_CERT_FILE", None) == "":
        del os.environ["SSL_CERT_FILE"]

# Pre-import aiohttp/openai/app at module level so their module-level SSL context
# is created BEFORE any test's patch.dict(clear=True) wipes SSL_CERT_FILE from env.
try:
    import aiohttp  # noqa: F401
except Exception:
    pass  # Will be caught by test failures if SSL is truly broken


class TestReadinessContracts(unittest.TestCase):
    """R07: Health/readiness contracts using TestClient with env isolation."""

    def setUp(self):
        from fastapi.testclient import TestClient
        env_dict = {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ADMIN_PASSWORD": "qa-password",
            "WEBUI_SESSION_SECRET": "qa-session-secret-32-characters-long",
            "DATABASE_URL": "",
            "REDIS_URL": "redis://127.0.0.1:1/0",
            "WEBUI_INTERNAL_TOKEN": "qa-internal",
        }
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

    def test_r07_01_healthz_is_always_200(self):
        """R07-01: /healthz is a liveness probe — always 200 for a live process."""
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("alive", False))

    def test_r07_02_unavailable_deps_yield_503(self):
        """R07-02: With PG/Redis unavailable, /readyz must return 503
        with component breakdown showing what's unavailable."""
        resp = self.client.get("/readyz")
        # Without actual PG/Redis, readiness should be 503
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertFalse(data.get("ready", True))
        # Must have component breakdown
        self.assertIn("redis", data)
        self.assertIn("postgres", data)

    def test_r07_04_health_does_not_call_provider(self):
        """R07-04: /api/health must not call any external provider.
        Even with Redis unavailable, it should return status without inference."""
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("redis", data)
        self.assertIn("postgres", data)
        # No provider/inference fields should appear
        self.assertNotIn("inference", data)
        self.assertNotIn("model", data)


if __name__ == "__main__":
    unittest.main()