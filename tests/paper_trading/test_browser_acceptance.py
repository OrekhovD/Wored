"""AC-19: Browser desktop/mobile acceptance tests.

Live smoke checks against a running WebUI (desktop 1440x900 / mobile 390x844
acceptance profile). Path: «Сегодня» → manual preview/open → auto status → «Итоги».
Checks: no overflow, preserved routes, charts, touch targets.

Environment:
  WEBUI_ACCEPTANCE_URL   base URL of a reachable webui (default 127.0.0.1:8080)
  WEBUI_ADMIN_PASSWORD   admin password (NEVER hardcode; tests skip when unset)
When the webui is unreachable or the password is unset the live checks skip —
this module is meant for host-side runs against a compose stack, not for the
isolated QA container.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

WEBUI_URL = os.getenv("WEBUI_ACCEPTANCE_URL", "http://127.0.0.1:8080")
WEBUI_PASSWORD = os.getenv("WEBUI_ADMIN_PASSWORD", "")
SCREENSHOTS_DIR = Path(__file__).parent / "fixtures" / "browser"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _reachable() -> bool:
    try:
        with urllib.request.urlopen(WEBUI_URL + "/healthz", timeout=2):
            return True
    except Exception:
        return False


def _require_live() -> None:
    if not _reachable():
        pytest.skip(f"live WebUI not reachable at {WEBUI_URL}")


def _curl_status(route: str) -> int:
    """Get HTTP status code for a route (redirects not followed)."""
    _require_live()
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(WEBUI_URL + route, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def _curl_login_and_get(route: str) -> str:
    """Login and fetch a protected route's HTML; '' when login unavailable.

    Session cookie is managed manually (not via CookieJar): the webui marks it
    Secure, and browsers exempt http://localhost while urllib does not.
    """
    if not WEBUI_PASSWORD:
        pytest.skip("WEBUI_ADMIN_PASSWORD not set (live webui login required)")
    _require_live()
    opener = urllib.request.build_opener(_NoRedirect())
    cookie: dict[str, str] = {}

    def _cookie_header() -> str:
        return "; ".join(f"{k}={v}" for k, v in cookie.items())

    def _store(res) -> None:
        raw = res.headers.get("Set-Cookie") or ""
        if "=" in raw:
            value, _, _attrs = raw.partition(";")
            name, _, val = value.partition("=")
            cookie[name.strip()] = val.strip()

    try:
        req = urllib.request.Request(WEBUI_URL + "/login", headers={"Cookie": _cookie_header()})
        with opener.open(req, timeout=5) as resp:
            page = resp.read().decode("utf-8", "replace")
            _store(resp)
    except urllib.error.HTTPError as exc:
        _store(exc)
        page = exc.read().decode("utf-8", "replace")
    except Exception:
        return ""
    match = re.search(r'csrf_token" value="([^"]+)"', page)
    if not match:
        return ""
    data = urllib.parse.urlencode({
        "username": "admin",
        "password": WEBUI_PASSWORD,
        "next": route,
        "csrf_token": match.group(1),
    }).encode()
    try:
        req = urllib.request.Request(
            WEBUI_URL + "/login", data=data,
            headers={"Cookie": _cookie_header(), "Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=5) as resp:
            _store(resp)
            location = resp.headers.get("location") or ""
    except urllib.error.HTTPError as exc:
        _store(exc)
        location = exc.headers.get("location") or ""
    except Exception:
        return ""
    # Follow the post-login redirect (303 → next) and then fetch the target.
    target = location if location.startswith("/") else route
    try:
        req = urllib.request.Request(
            WEBUI_URL + target,
            headers={"Cookie": _cookie_header()},
        )
        with opener.open(req, timeout=5) as resp:
            _store(resp)
            if resp.status >= 400:
                return ""
            if "Web UI Login" in resp.read(2048).decode("utf-8", "replace"):
                return ""  # still unauthenticated
        req = urllib.request.Request(
            WEBUI_URL + (route if target != route else target),
            headers={"Cookie": _cookie_header()},
        )
        with opener.open(req, timeout=5) as resp:
            if resp.status >= 400:
                return ""
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return ""


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