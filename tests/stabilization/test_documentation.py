"""R09: Documentation and configuration verification.

Tests R09-01..04:
  R09-01: ENV registry documents all required keys
  R09-02: .env.example has all new stabilization keys
  R09-03: Provider registry JSON is valid and has required fields
  R09-04: Documentation files exist and are non-empty
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestENVRegistry(unittest.TestCase):
    """R09-01: ENV registry documents all required keys."""

    def test_env_registry_exists(self):
        registry = ROOT / "docs" / "ENV-REGISTRY.md"
        self.assertTrue(registry.exists(), f"Missing {registry}")

    def test_env_registry_covers_required_keys(self):
        registry = ROOT / "docs" / "ENV-REGISTRY.md"
        if not registry.exists():
            self.skipTest("ENV-REGISTRY.md not found")
        content = registry.read_text()
        required_keys = [
            "DATABASE_URL",
            "REDIS_URL",
            "WEBUI_AUTH_ENABLED",
            "WEBUI_ADMIN_PASSWORD",
            "WEBUI_SESSION_SECRET",
            "WEBUI_INTERNAL_TOKEN",
            "WEBUI_TELEGRAM_BOT_TOKENS",
            "TELEGRAM_ADMIN_IDS",
            "WEBUI_PUBLIC_BASE_URL",
            "WEBUI_COOKIE_SECURE",
            "LLM_ROUTING_MODE",
            "LLM_PAID_ENABLED",
            "LLM_REGISTRY_PATH",
            "SIM_ALLOWED_SYMBOLS",
        ]
        for key in required_keys:
            self.assertIn(key, content, f"ENV-REGISTRY.md missing key: {key}")

    def test_env_registry_has_secrecy_column(self):
        registry = ROOT / "docs" / "ENV-REGISTRY.md"
        if not registry.exists():
            self.skipTest("ENV-REGISTRY.md not found")
        content = registry.read_text()
        self.assertIn("Secret", content, "ENV-REGISTRY.md missing Secret column")


class TestEnvExample(unittest.TestCase):
    """R09-02: .env.example has all stabilization keys."""

    def test_env_example_exists(self):
        env_example = ROOT / ".env.example"
        self.assertTrue(env_example.exists(), f"Missing {env_example}")

    def test_env_example_has_stabilization_keys(self):
        env_example = ROOT / ".env.example"
        if not env_example.exists():
            self.skipTest(".env.example not found")
        content = env_example.read_text()
        required_keys = [
            "WEBUI_AUTH_ENABLED",
            "WEBUI_ADMIN_PASSWORD",
            "WEBUI_SESSION_SECRET",
            "WEBUI_INTERNAL_TOKEN",
            "WEBUI_TELEGRAM_BOT_TOKENS",
            "WEBUI_PUBLIC_BASE_URL",
            "WEBUI_COOKIE_SECURE",
            "LLM_ROUTING_MODE",
            "LLM_PAID_ENABLED",
            "LLM_REGISTRY_PATH",
            "SIM_ALLOWED_SYMBOLS",
        ]
        for key in required_keys:
            self.assertIn(key, content, f".env.example missing key: {key}")

    def test_env_wored_example_exists(self):
        env_wored = ROOT / ".env.wored.example"
        self.assertTrue(env_wored.exists(), f"Missing {env_wored}")

    def test_env_wored_example_has_shared_token(self):
        env_wored = ROOT / ".env.wored.example"
        if not env_wored.exists():
            self.skipTest(".env.wored.example not found")
        content = env_wored.read_text()
        self.assertIn("WEBUI_INTERNAL_TOKEN", content,
                       ".env.wored.example must reference WEBUI_INTERNAL_TOKEN")


# R09-03 provider_registry.json assertions removed: the free-model provider
# registry was archived with the free-model routing subsystem to
# free_routing_archive/config/provider_registry.json. The active chatbot uses
# chatbot/ai/models.py (Ollama Cloud + local Bonsai), not the gateway registry.
# Restore this class together with the archive if the gateway stack is revived.


class TestDocumentationFiles(unittest.TestCase):
    """R09-04: Documentation files exist and are non-empty."""

    REQUIRED_DOCS = [
        "docs/ENV-REGISTRY.md",
        "docs/KNOWN_LIMITS.md",
        "docs/OPERATIONS-RUNBOOK.md",
        "docs/STABILIZATION-IMPLEMENTATION-2026-09-08.md",
        "docs/PROVIDER-REGISTRY.md",
        "docs/CHANGELOG_WEEKLY.md",
        ".env.example",
        ".env.wored.example",
    ]

    def test_required_docs_exist(self):
        for doc_path in self.REQUIRED_DOCS:
            path = ROOT / doc_path
            self.assertTrue(path.exists(), f"Missing documentation: {doc_path}")

    def test_required_docs_non_empty(self):
        for doc_path in self.REQUIRED_DOCS:
            path = ROOT / doc_path
            if not path.exists():
                self.skipTest(f"{doc_path} not found")
            content = path.read_text()
            self.assertGreater(len(content.strip()), 100,
                               f"{doc_path} is too short ({len(content)} chars)")


if __name__ == "__main__":
    unittest.main()