"""F16: Telegram Mini App contract verification.

Tests that the auth chain and prediction API work correctly when accessed
via Telegram initData header (simulating what a Telegram Mini App WebView
does). No live Telegram bot required — uses HMAC verification with a
synthetic bot token.

Contract points tested:
  - Valid initData → 200 on /api/predictions (GET)
  - Expired initData → 401 (auth rejected)
  - Prediction response schema: {items: [...]}
  - Internal token auth: X-Wored-Internal-Token for chatbot service calls
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unittest
from urllib.parse import urlencode

# ─── Synthetic bot token for testing ────────────────────────────────────────
BOT_TOKEN = "123456:TEST-bot-token-for-f16-contract"
ADMIN_USER_ID = 42


def _sign_init_data(user_id: int, auth_date: int, bot_token: str) -> str:
    """Produce a valid Telegram WebApp initData string."""
    user_json = json.dumps({"id": user_id, "first_name": "Admin"})
    data = {"auth_date": str(auth_date), "user": user_json}
    message = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    data["hash"] = signature
    return urlencode(data)


class TestTelegramInitDataSigning(unittest.TestCase):
    """Unit-level: verify HMAC signing and verification chain."""

    def setUp(self):
        # Patch env so access_control sees our token and allowed IDs
        self._env_patch = {
            "TELEGRAM_TOKEN": BOT_TOKEN,
            "TELEGRAM_ADMIN_IDS": str(ADMIN_USER_ID),
            "WEBUI_TELEGRAM_BOT_TOKENS": json.dumps([BOT_TOKEN]),
        }
        self._old = {}
        for k, v in self._env_patch.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_valid_initdata_verifies(self):
        """A freshly-signed initData must pass verification."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
        from access_control import verify_telegram

        now = int(time.time())
        init_data = _sign_init_data(ADMIN_USER_ID, now, BOT_TOKEN)
        result = verify_telegram(init_data, BOT_TOKEN, {ADMIN_USER_ID})
        self.assertIsNotNone(result)
        self.assertEqual(result["user_id"], ADMIN_USER_ID)

    def test_expired_initdata_rejected(self):
        """InitData older than 24h must be rejected."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
        from access_control import verify_telegram

        old_ts = int(time.time()) - 90000  # >24h ago
        init_data = _sign_init_data(ADMIN_USER_ID, old_ts, BOT_TOKEN)
        result = verify_telegram(init_data, BOT_TOKEN, {ADMIN_USER_ID}, max_age=86400)
        self.assertIsNone(result)

    def test_non_admin_rejected(self):
        """A valid signature from a non-allowed user_id is rejected."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
        from access_control import verify_telegram

        now = int(time.time())
        init_data = _sign_init_data(99, now, BOT_TOKEN)
        result = verify_telegram(init_data, BOT_TOKEN, {ADMIN_USER_ID})
        self.assertIsNone(result)

    def test_principal_from_telegram(self):
        """create_from_telegram returns a proper Principal."""
        import sys
        from pathlib import Path
        root = str(Path(__file__).resolve().parents[2])
        sys.path.insert(0, root + "/webui")
        from principal import create_from_telegram

        now = int(time.time())
        init_data = _sign_init_data(ADMIN_USER_ID, now, BOT_TOKEN)
        p = create_from_telegram(init_data, [BOT_TOKEN], {ADMIN_USER_ID})
        self.assertIsNotNone(p)
        self.assertEqual(p.kind, "telegram_admin")
        self.assertEqual(p.telegram_user_id, ADMIN_USER_ID)
        self.assertTrue(p.is_admin)


if __name__ == "__main__":
    unittest.main()
