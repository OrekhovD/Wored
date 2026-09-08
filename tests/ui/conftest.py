"""UI-11 pytest fixtures: app, client, browser, base_url, viewport_sizes."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# Ensure project root on path
ROOT = Path(__file__).resolve().parents[2]
WEBUI_DIR = ROOT / "webui"
for p in (str(ROOT), str(WEBUI_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def app():
    """FastAPI fixture app instance."""
    from tests.ui.fixture_app import create_app
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    """httpx AsyncClient wired to the fixture app."""
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def base_url() -> str:
    """Base URL for the fixture server (used by browser tests)."""
    host = os.environ.get("UI_QA_HOST", "127.0.0.1")
    port = os.environ.get("UI_QA_PORT", "18080")
    return f"http://{host}:{port}"


@pytest.fixture
def viewport_sizes() -> dict[str, dict[str, int]]:
    """Standard viewport sizes for responsive testing."""
    return {
        "mobile":  {"width": 390, "height": 844},
        "tablet":  {"width": 768, "height": 1024},
        "desktop": {"width": 1280, "height": 800},
    }


@pytest_asyncio.fixture
async def browser(base_url: str, viewport_sizes: dict):
    """Playwright browser page. Requires playwright installed.

    Yields a Page with desktop viewport, navigated to base_url.
    Skips if playwright is not available.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport=viewport_sizes["desktop"],
            base_url=base_url,
        )
        page = await ctx.new_page()
        yield page
        await ctx.close()
        await browser.close()


@pytest_asyncio.fixture
async def auth_client(app):
    """httpx AsyncClient pre-authenticated as test admin."""
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as ac:
        # GET login page to establish session + CSRF
        resp = await ac.get("/login")
        csrf = _extract_csrf(resp.text)
        # POST login
        await ac.post("/login", data={
            "username": "fixture-admin",
            "password": "test-password",
            "next": "/",
            "csrf_token": csrf,
        }, follow_redirects=False)
        yield ac


def _extract_csrf(html: str) -> str:
    """Extract csrf_token value from login form HTML."""
    import re
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1) if m else ""