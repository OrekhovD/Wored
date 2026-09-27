"""Block E/F05 — management pages (Итоги / Обучение).

Proves the IA pages exist, render 200, show real data or honest empty states.
Nav IA (V2 4-group) must expose the entry points.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestResultsPage:
    async def test_results_list_200(self, auth_client):
        resp = await auth_client.get("/results")
        assert resp.status_code == 200

    async def test_results_shows_days(self, auth_client):
        """F05: fixture provides 2 closed days."""
        resp = await auth_client.get("/results")
        text = resp.text
        assert "wored-list-item" in text
        assert "2026-09-20" in text

    async def test_results_detail_200(self, auth_client):
        """F05: detail page shows report data."""
        resp = await auth_client.get("/results/day-aaa-001")
        assert resp.status_code == 200
        assert "manual" in resp.text

    async def test_api_results_list(self, auth_client):
        """F05: JSON API returns day cards."""
        resp = await auth_client.get("/api/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["days"]) == 2

    async def test_api_result_detail(self, auth_client):
        """F05: JSON API returns full report."""
        resp = await auth_client.get("/api/results/day-aaa-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["report"]["day_id"] == "day-aaa-001"


@pytest.mark.asyncio
class TestLearningPage:
    async def test_learning_list_200(self, auth_client):
        resp = await auth_client.get("/learning")
        assert resp.status_code == 200

    async def test_learning_empty_state_not_fabricated(self, auth_client):
        resp = await auth_client.get("/learning")
        text = resp.text
        assert "wored-empty-state" in text
        assert "wored-list-item" not in text

    async def test_learning_detail_200(self, auth_client):
        resp = await auth_client.get("/learning/cand-1")
        assert resp.status_code == 200
        assert "wored-empty-state" in resp.text


@pytest.mark.asyncio
class TestManagementNavIA:
    async def test_nav_exposes_results_and_learning(self, auth_client):
        resp = await auth_client.get("/trading-day")
        text = resp.text
        assert 'href="/results"' in text
        assert 'href="/learning"' in text

    async def test_nav_no_retired_trader_link(self, auth_client):
        resp = await auth_client.get("/trading-day")
        assert 'href="/trader"' not in resp.text
