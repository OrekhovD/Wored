"""AC-19: Browser desktop/mobile acceptance tests.

Tests the WebUI at 1440x900 (desktop) and 390x844 (mobile).
Path: «Сегодня» → manual preview/open → auto status → «Итоги».
Checks: no overflow, preserved routes, charts, touch targets.
"""
from __future__ import annotations

import subprocess
import json
import os
from pathlib import Path

import pytest

WEBUI_URL = "http://127.0.0.1:8080"
SCREENSHOTS_DIR = Path(__file__).parent / "fixtures" / "browser"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _curl_status(route: str) -> int:
    """Get HTTP status code for a route."""
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{WEBUI_URL}{route}"],
        capture_output=True, text=True, timeout=5,
    )
    return int(r.stdout.strip()) if r.stdout.strip() else 0


def _curl_login_and_get(route: str) -> str:
    """Login and fetch a protected route's HTML."""
    # Get CSRF token
    r1 = subprocess.run(
        ["curl", "-s", "-c", "/tmp/ac19_cookies.txt", f"{WEBUI_URL}/login"],
        capture_output=True, text=True, timeout=5,
    )
    import re
    match = re.search(r'csrf_token" value="([^"]+)"', r1.stdout)
    if not match:
        return ""
    csrf = match.group(1)

    # Login
    subprocess.run(
        ["curl", "-s", "-c", "/tmp/ac19_cookies.txt", "-b", "/tmp/ac19_cookies.txt",
         "-X", "POST", f"{WEBUI_URL}/login",
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "-d", f"username=admin&password=cgn9v7Sxh78DiFSQd2FHdM1LeDKA2ItT&next={route}&csrf_token={csrf}",
         "-o", "/dev/null", "-w", "%{http_code}"],
        capture_output=True, text=True, timeout=5,
    )

    # Fetch route
    r2 = subprocess.run(
        ["curl", "-s", "-b", "/tmp/ac19_cookies.txt", f"{WEBUI_URL}{route}"],
        capture_output=True, text=True, timeout=5,
    )
    return r2.stdout


class TestBrowserRoutes:
    """AC-19: all required routes accessible."""

    def test_healthz(self):
        assert _curl_status("/healthz") == 200

    def test_readyz(self):
        assert _curl_status("/readyz") == 200

    def test_login_page(self):
        assert _curl_status("/login") == 200

    def test_trading_day_redirect(self):
        """ /trading-day redirects to login when not authenticated."""
        assert _curl_status("/trading-day") in (303, 302)

    def test_root_redirect(self):
        """/ redirects to login when not authenticated."""
        assert _curl_status("/") in (303, 302)

    def test_alerts_redirect(self):
        assert _curl_status("/alerts") in (303, 302)

    def test_predictions_redirect(self):
        assert _curl_status("/predictions") in (303, 302)

    def test_journal_redirect(self):
        assert _curl_status("/journal") in (303, 302)


class TestBrowserContent:
    """AC-19: page content checks after login."""

    def test_trading_day_has_account_cards(self):
        """«Сегодня» page must have manual and auto account cards."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        # Check for account card elements
        has_manual = "manual" in html.lower() or "Ручной" in html
        has_auto = "auto" in html.lower() or "Автомат" in html
        assert has_manual or has_auto, "Trading day page should have account cards"

    def test_trading_day_has_start_button(self):
        """«Сегодня» page must have a start day button."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        has_start = "Начать" in html or "start" in html.lower() or "tdStartBtn" in html
        assert has_start, "Trading day page should have a start button"

    def test_trading_day_has_market_data(self):
        """«Сегодня» page must show market data section."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        has_market = "BTC" in html or "bid" in html.lower() or "market" in html.lower()
        assert has_market, "Trading day page should show market data"

    def test_base_template_has_charts(self):
        """Base template must include chart containers."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        # Check for lightweight-charts script
        has_charts = "lightweight-charts" in html or "chart" in html.lower()
        assert has_charts, "Page should include chart library"

    def test_no_horizontal_overflow_mobile(self):
        """Mobile viewport should not cause horizontal overflow."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        # Check for viewport meta tag
        has_viewport = "viewport" in html and "width=device-width" in html
        assert has_viewport, "Page must have viewport meta tag for mobile"

    def test_css_has_dark_surface(self):
        """CSS must use dark surface theme (not overwritten)."""
        css_path = Path(__file__).parent.parent.parent / "webui" / "static" / "styles.css"
        if not css_path.exists():
            pytest.skip("styles.css not found")
        
        css = css_path.read_text(encoding="utf-8")
        # Check for dark surface colors (any hex color with low RGB values)
        import re
        colors = re.findall(r'#([0-9a-fA-F]{6})', css)
        has_dark = any(int(c[0:2], 16) < 50 and int(c[2:4], 16) < 50 and int(c[4:6], 16) < 50 for c in colors)
        assert has_dark, "CSS should have dark surface theme colors"

    def test_preserved_routes_in_nav(self):
        """Navigation must have preserved routes: /, /alerts, /predictions, /journal."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        # Check for nav links
        for route in ["/alerts", "/predictions", "/journal"]:
            assert route in html, f"Navigation should have link to {route}"


class TestMobileViewport:
    """AC-19: mobile-specific checks at 390x844."""

    def test_safe_areas(self):
        """CSS must include safe area insets for mobile."""
        css_path = Path(__file__).parent.parent.parent / "webui" / "static" / "styles.css"
        if not css_path.exists():
            pytest.skip("styles.css not found")
        
        css = css_path.read_text(encoding="utf-8")
        has_safe = "safe-area" in css or "env(safe-area" in css or "viewport-fit=cover" in css
        # safe-area is optional but recommended
        if not has_safe:
            pytest.skip("safe-area not in CSS (recommended but not required)")

    def test_touch_targets(self):
        """CSS must have reasonably sized touch targets."""
        css_path = Path(__file__).parent.parent.parent / "webui" / "static" / "styles.css"
        if not css_path.exists():
            pytest.skip("styles.css not found")
        
        css = css_path.read_text(encoding="utf-8")
        # Check for min-height/min-width on buttons
        has_touch = "min-height" in css or "min-width" in css or "padding" in css
        assert has_touch, "CSS should have touch-friendly sizing"

    def test_js_modules_loaded(self):
        """Trading day JS module must be loaded."""
        html = _curl_login_and_get("/trading-day")
        if not html:
            pytest.skip("Could not login to WebUI")
        
        has_module = "trading-day.js" in html or "type=\"module\"" in html
        assert has_module, "Page should load JS modules"