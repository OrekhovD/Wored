"""B4 parity tests: ensure every F-ID route remains reachable from V2 workspace.
Proves that the navigation restructuring (V2 4-group IA) and workspace quick-links
do NOT break any existing page (TZ V2-10: URL compat).
"""
import pytest


# All F-ID pages that must still return 200 (F06–F14)
PARITY_ROUTES = [
    ("/", "F06 — market"),
    ("/alerts", "F07 — alerts"),
    ("/predictions", "F09 — forecasts"),
    ("/journal", "F10 — journal"),
    ("/results", "F11 — history list"),
    ("/learning", "F12 — learning"),
    ("/strategy", "F13 — strategy"),
    ("/system", "F14a — system"),
    ("/model-management", "F14b — model-management"),
    ("/daily-session", "F04 — daily-session"),
    ("/futures-lab", "F04b — futures-lab"),
    ("/trading-day", "V1 trading-day (legacy)"),
    ("/command-deck", "V1 command-deck (legacy)"),
    ("/workspace", "V2 workspace"),
]


@pytest.mark.asyncio
class TestRouteParity:
    """V2-10: all existing routes return 200 after B4 nav restructuring."""

    @pytest.mark.parametrize("path,label", PARITY_ROUTES)
    async def test_route_200(self, auth_client, path, label):
        resp = await auth_client.get(path)
        assert resp.status_code == 200, f"{label} ({path}) returned {resp.status_code}"


@pytest.mark.asyncio
class TestWorkspaceQuickLinks:
    """B4: workspace context has quick-links to research and history tools."""

    async def test_quicklinks_section_present(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "ws-quicklinks" in resp.text

    @pytest.mark.parametrize("href", [
        "/", "/alerts", "/predictions", "/journal",
        "/results", "/learning", "/strategy",
    ])
    async def test_quicklink_present(self, auth_client, href):
        resp = await auth_client.get("/workspace")
        assert f'href="{href}"' in resp.text


@pytest.mark.asyncio
class TestNavGroupsV2:
    """B4: navigation uses V2 4-group layout."""

    async def test_four_groups_in_html(self, auth_client):
        resp = await auth_client.get("/workspace")
        # Check group labels
        assert "Рабочая&nbsp;область" in resp.text
        assert "Исследование" in resp.text
        assert "История" in resp.text
        assert "Система" in resp.text

    async def test_alert_badge_preserved(self, auth_client):
        """F07: nav badge element must remain."""
        resp = await auth_client.get("/workspace")
        assert "navAlertBadge" in resp.text


@pytest.mark.asyncio
class TestLearningReadOnly:
    """B4 V2-08: learning page shows honest empty/no-data state."""

    async def test_learning_200(self, auth_client):
        resp = await auth_client.get("/learning")
        assert resp.status_code == 200

    async def test_learning_no_fake_recommendations(self, auth_client):
        resp = await auth_client.get("/learning")
        # Must not contain fabricated "auto-apply" buttons or false evidence
        text_lower = resp.text.lower()
        assert "авто-применение" not in text_lower
        assert "applied automatically" not in text_lower
