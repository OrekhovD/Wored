"""UI tests for the V3 market panel on /workspace (P1.2b).

These assert the template renders the expected containers, the isolated CSS
is linked, and the module script is wired.  The chart's actual rendering is
proven by scripts/run_ui_acceptance.py with a real browser; here we only
check the HTML contract the browser needs to hydrate against.

Fixture note: the tests/ui/conftest ``app`` fixture builds a *fixture* app
(tests/ui/fixture_app.py) that renders the same Jinja templates as prod but
does not mount the /api/v3 router.  We therefore assert only the template
markers.  Behavioural V3 coverage lives in tests/test_market_workspace.py
and tests/test_market_stream_unit.py.
"""
from __future__ import annotations

import re

import pytest


# Any `.innerHTML = ...` in a served JS file is a network-to-DOM injection
# surface.  Comments may still mention the word; we only fail on assignments.
_INNERHTML_ASSIGN = re.compile(r"\.innerHTML\s*=")


@pytest.mark.asyncio
class TestV3MarketTemplate:
    async def test_workspace_contains_v3_market_section(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200
        # Section root + ARIA label
        assert 'data-v3-market-root' in resp.text
        assert 'aria-label="Рынок (perpetual)"' in resp.text

    async def test_workspace_contains_v3_role_hooks(self, auth_client):
        resp = await auth_client.get("/workspace")
        # Each data-role is a mount point that market-workspace.js looks up.
        for role in ("instrument", "period", "chart", "quality", "quote", "status", "entry-badge", "banner"):
            assert f'data-role="{role}"' in resp.text, f"missing data-role={role}"

    async def test_workspace_contains_p13_forming_and_gap_hooks(self, auth_client):
        """P1.3: the forming-candle line and the gap list have dedicated,
        initially-hidden mount points hydrated by market-workspace.js.  They
        are aria-live/labelled so a screen reader announces freshness facts."""
        resp = await auth_client.get("/workspace")
        assert 'data-role="forming"' in resp.text
        assert 'data-role="gaps"' in resp.text
        # present but hidden until real data arrives (no fabricated placeholder)
        assert 'data-role="forming" hidden' in resp.text
        assert 'aria-live="polite"' in resp.text
        assert 'aria-label="Пропуски в истории свечей"' in resp.text

    async def test_workspace_links_market_workspace_css(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "/static/ui/market-workspace.css" in resp.text

    async def test_workspace_loads_market_workspace_module(self, auth_client):
        resp = await auth_client.get("/workspace")
        # module type + filename (cache-buster query is allowed)
        assert 'type="module"' in resp.text
        assert "/static/ui/market-workspace.js" in resp.text

    async def test_static_files_are_served(self, client):
        # static files are mounted by fixture_app; verify our three new assets
        # are readable from the container's static tree.  No auth needed.
        for path in (
            "/static/ui/market-chart.js",
            "/static/ui/market-workspace.js",
            "/static/ui/market-workspace.css",
        ):
            r = await client.get(path)
            assert r.status_code == 200, f"{path} → {r.status_code}"
            assert len(r.text) > 100, f"{path} appears empty"

    async def test_existing_terminal_untouched(self, auth_client):
        """AGENTS guardrail: workspace.html must remain incremental; existing
        ws-* zones (status, stepper, context, attention, quicklinks, drawer)
        stay in place."""
        resp = await auth_client.get("/workspace")
        for marker in ("wsStatus", "wsStepper", "wsContext", "wsAttention",
                       "ws-quicklinks", "wsDrawerOverlay",
                       "workspace-state.js", "workspace-actions.js"):
            assert marker in resp.text, f"workspace.html regressed: {marker} missing"


@pytest.mark.asyncio
class TestV3MarketStaticJSHasNoDangerousPatterns:
    """Static scan companion: our new JS files must not use innerHTML with
    data coming from the network (AGENTS security rule + L1 static scan)."""

    async def test_market_chart_js_avoids_innerhtml_for_data(self, client):
        r = await client.get("/static/ui/market-chart.js")
        assert r.status_code == 200
        # The LWC-not-loaded fallback originally used innerHTML with a static
        # string; the scanner flagged the pattern.  Fix replaced it with
        # createElement + textContent, so no innerHTML assignments remain.
        assert not _INNERHTML_ASSIGN.search(r.text)

    async def test_market_workspace_js_avoids_innerhtml(self, client):
        r = await client.get("/static/ui/market-workspace.js")
        assert r.status_code == 200
        assert not _INNERHTML_ASSIGN.search(r.text)


@pytest.mark.asyncio
class TestV3MarketG3G4SourceContract:
    """G4 (§39) URL-state and G3 (§53) stream-resync are wired in the
    controller.  These source guards keep the behaviour from being silently
    dropped by a future edit (the executable proof lives in the browser suite)."""

    async def test_controller_persists_selections_to_url(self, client):
        js = (await client.get("/static/ui/market-workspace.js")).text
        # G4: reads the query string and writes selections back via replaceState
        assert "URLSearchParams" in js
        assert "replaceState" in js
        assert "function applyUrlSelections" in js
        assert "function syncUrl" in js
        assert "'instrument'" in js and "'period'" in js and "'horizon'" in js

    async def test_controller_has_stream_fallback_and_resync(self, client):
        js = (await client.get("/static/ui/market-workspace.js")).text
        # G3: degraded-stream fallback to /state polling + shared snapshot apply,
        # with a testable streamMode contract (sse | poll).
        assert "function startPollFallback" in js
        assert "function stopPollFallback" in js
        assert "function applySnapshot" in js
        assert "streamMode" in js
        assert "STREAM_STALE_MS" in js


@pytest.mark.asyncio
class TestV3MarketChartVersionTolerance:
    """base.html pins Lightweight Charts 5.2.0, which removed the v4
    ``addCandlestickSeries`` family.  A real-browser run caught the chart
    blanking because market-chart.js still used the v4 API; this guards the fix
    so a future edit cannot silently reintroduce the version-only call."""

    async def test_uses_series_factory_not_v4_only_callers(self, client):
        r = await client.get("/static/ui/market-chart.js")
        assert r.status_code == 200
        # v5 path must be present…
        assert "_addSeries" in r.text
        assert "addSeries(" in r.text
        assert "LightweightCharts.CandlestickSeries" in r.text
        # …and the bare v4-only calls that threw "not a function" must not.
        for legacy in ("chart.addCandlestickSeries(", "chart.addHistogramSeries(", "chart.addLineSeries("):
            assert legacy not in r.text, f"regressed to v4-only API: {legacy}"


@pytest.mark.asyncio
class TestV3MarketMobileTapTargets:
    """MC-04 (mobile): the browser run proved selectors were ~30px tall on a
    390px phone; a mobile media-query rule raises them to ≥44px and bounds their
    width.  This source guard keeps the rule from being silently dropped."""

    async def test_mobile_media_query_enforces_44px_tap_targets(self, client):
        r = await client.get("/static/ui/market-workspace.css")
        assert r.status_code == 200
        css = r.text
        # isolate the small-screen block so we don't match a stray desktop rule
        idx = css.find("@media (max-width: 640px)")
        assert idx != -1, "no mobile @media (max-width: 640px) block found"
        body = css[idx:]
        assert "min-height: 44px" in body, "mobile selectors lost their 44px tap height"
        assert "min-width: 44px" in body, "mobile selectors lost their 44px tap width"
        assert "max-width: 100%" in body, "mobile selectors lost the overflow bound"
        assert "box-sizing: border-box" in body, "mobile box-sizing guard missing"


@pytest.mark.asyncio
class TestV3ForecastTemplate:
    """P2.2 added a forecast sub-block inside the market section.  The browser
    controller hydrates against these exact data-role hooks."""

    async def test_workspace_contains_forecast_hooks(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200
        assert 'data-role="forecast"' in resp.text
        for role in ("horizon", "forecast-toggle", "forecast-legend", "forecast-status", "forecast-accuracy"):
            assert f'data-role="{role}"' in resp.text, f"missing data-role={role}"
        # four honest horizons from the ТЗ §5 matrix
        for h in ("15m", "1h", "4h", "24h"):
            assert f'value="{h}"' in resp.text, f"horizon option {h} missing"

    async def test_forecast_market_block_is_inside_market_section(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        start = text.index('data-v3-market-root')
        end = text.index("</section>", start)
        block = text[start:end]
        assert 'data-role="forecast-status"' in block, "forecast block escaped the market section"


@pytest.mark.asyncio
class TestV3ForecastOverlayContract:
    """MC-06 source guard: the forecast is drawn as a *line + band*, never fed
    into the candlestick series.  A real-browser run proves the pixel output;
    this keeps a future edit from reintroducing a fabricated forecast candle."""

    async def test_chart_draws_forecast_as_line_not_candle(self, client):
        r = await client.get("/static/ui/market-chart.js")
        assert r.status_code == 200
        js = r.text
        assert "setForecast" in js
        assert "_addSeries(chart, 'Line'" in js, "forecast overlay must use line series"
        # the candlestick series is written to exactly once (real candles only);
        # the forecast path must never call it.
        assert js.count("candleSeries.setData(") == 1, "forecast leaked into the candle series"

    async def test_controller_fetches_forecasts_and_publishes_dom_hook(self, client):
        r = await client.get("/static/ui/market-workspace.js")
        assert r.status_code == 200
        js = r.text
        assert "/api/v3/forecasts" in js
        assert "dataset.forecastBars" in js or "forecastBars" in js
        assert "forecastRender" in js
        assert "'line-band'" in js

    async def test_controller_fetches_holdout_accuracy(self, client):
        r = await client.get("/static/ui/market-workspace.js")
        assert r.status_code == 200
        js = r.text
        assert "/api/v3/forecasts/accuracy" in js
        assert "renderAccuracy" in js
        # an N/A holdout must be reachable, not only the numeric branch
        assert "insufficient_sample" not in js or "N/A" in js


@pytest.mark.asyncio
class TestV3SimulationTemplate:
    """P3.2 session constructor: the template must expose the exact data-role
    hooks simulation-workspace.js hydrates against, and the existing terminal
    zones must survive (AGENTS incremental guardrail)."""

    async def test_workspace_contains_simulation_root(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200
        assert 'data-v3-simulation-root' in resp.text
        assert 'aria-label="Конструктор сессии"' in resp.text

    async def test_simulation_hooks_present(self, auth_client):
        resp = await auth_client.get("/workspace")
        for role in ("sim-form", "sim-mode", "sim-start", "sim-end", "sim-tz",
                     "sim-seed", "sim-manual-budget", "sim-auto-budget",
                     "sim-strategy", "sim-style", "sim-side-long", "sim-side-short",
                     "sim-leverage", "sim-risk", "sim-daily-loss", "sim-session-loss",
                     "sim-exposure", "sim-positions", "sim-cooldown", "sim-hours",
                     "sim-tp", "sim-validate", "sim-save", "sim-approve",
                     "sim-start-btn", "sim-status", "sim-violations", "sim-hash"):
            assert f'data-role="{role}"' in resp.text, f"missing data-role={role}"

    async def test_simulation_links_assets(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "/static/ui/simulation-workspace.css" in resp.text
        assert "/static/ui/simulation-workspace.js" in resp.text

    async def test_static_serves_simulation_controller(self, client):
        for path in ("/static/ui/simulation-workspace.js",
                     "/static/ui/simulation-workspace.css"):
            r = await client.get(path)
            assert r.status_code == 200, f"{path} → {r.status_code}"
            assert len(r.text) > 100, f"{path} appears empty"

    async def test_terminal_still_untouched_after_p3(self, auth_client):
        """P3 must not have disturbed the market section or ws-* terminal zones."""
        resp = await auth_client.get("/workspace")
        text = resp.text
        assert 'data-v3-market-root' in text
        assert 'data-role="forecast-accuracy"' in text
        for marker in ("wsContext", "wsAttention", "ws-quicklinks", "wsDrawerOverlay"):
            assert marker in text, f"workspace.html regressed: {marker} missing"
        # section order: market → simulation → context
        assert (text.index('data-v3-market-root')
                < text.index('data-v3-simulation-root')
                < text.index('id="wsContext"'))


@pytest.mark.asyncio
class TestV3SimulationContract:
    """Source guards for the P3 flow: v3 API base, CSRF header, a real
    Idempotency-Key on session start, catalog values pinned to the server,
    no innerHTML injection, and the mobile 44px tap guard."""

    async def test_controller_uses_v3_api_with_csrf_and_idempotency(self, client):
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        assert "/api/v3/simulation" in js
        assert "X-CSRF-Token" in js
        assert "Idempotency-Key" in js
        assert "plans/validate" in js and "/approve" in js and "/sessions" in js

    async def test_catalog_versions_match_server(self, client):
        from simulation_api import (
            FEE_SCHEDULE_VERSIONS,
            FUNDING_MODEL_VERSIONS,
            MARKET_DATA_POLICIES,
            SLIPPAGE_MODEL_VERSIONS,
        )
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        for pinned in (FEE_SCHEDULE_VERSIONS[0], SLIPPAGE_MODEL_VERSIONS[0],
                       FUNDING_MODEL_VERSIONS[0], MARKET_DATA_POLICIES[0]):
            assert pinned in js, f"controller drifted from server catalog: {pinned}"

    async def test_controller_has_no_innerhtml_injection(self, client):
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        assert not _INNERHTML_ASSIGN.search(js), "network data must not reach innerHTML"

    async def test_mobile_tap_targets_guard(self, client):
        css = (await client.get("/static/ui/simulation-workspace.css")).text
        idx = css.find("@media (max-width: 640px)")
        assert idx >= 0, "simulation CSS lost its mobile guardrail block"
        body = css[idx:]
        assert "min-height: 44px" in body, "mobile inputs/buttons lost the 44px tap height"
        assert "box-sizing: border-box" in body, "mobile box-sizing guard missing"


@pytest.mark.asyncio
class TestV4ProposalTemplate:
    """P4 questionnaire block: the controller hydrates against these hooks and
    the accept button must start disabled (draft is never auto-applied)."""

    async def test_workspace_contains_questionnaire_hooks(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200
        for role in ("simq-goal", "simq-duration", "simq-side", "simq-involvement",
                     "simq-manual", "simq-auto", "simq-maxloss", "simq-hours",
                     "sim-propose", "sim-accept", "sim-proposal-status",
                     "sim-proposal-compare"):
            assert f'data-role="{role}"' in resp.text, f"missing data-role={role}"

    async def test_accept_button_starts_disabled(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        idx = text.index('data-role="sim-accept"')
        # capture the full <button …> tag (the role attr is not the last one)
        tag_start = text.rindex("<button", 0, idx)
        tag = text[tag_start:text.index(">", tag_start) + 1]
        assert "disabled" in tag, "«Принять как черновик» must start disabled (MC-12)"

    async def test_proposal_block_is_inside_simulation_section(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        start = text.index('data-v3-simulation-root')
        end = text.index("</section>", start)
        block = text[start:end]
        assert 'data-role="simq-goal"' in block
        assert 'data-role="sim-proposal-compare"' in block


@pytest.mark.asyncio
class TestV4ProposalContract:
    """Source guards for the P4 flow: proposal endpoint + polling, accept only
    fills the form (never approves/starts), no innerHTML injection, styles for
    the comparison table, and the cache-buster actually bumped."""

    async def test_controller_calls_proposals_and_polls(self, client):
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        assert "plan-proposals" in js
        assert "pollProposal" in js

    async def test_accept_never_approves_or_starts(self, client):
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        idx = js.index("function acceptProposal")
        fn = js[idx:js.index("\n  }", idx)]
        # scan the executable code only — the contract comment names the
        # forbidden verbs precisely to say they are absent
        code = "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("//"))
        assert "fillForm" in code, "accept must only fill the manual form"
        for forbidden in ("approve", "sessions", "saveDraft"):
            assert forbidden not in code, f"acceptProposal must not {forbidden} (MC-12)"

    async def test_controller_still_has_no_innerhtml(self, client):
        js = (await client.get("/static/ui/simulation-workspace.js")).text
        assert not _INNERHTML_ASSIGN.search(js)

    async def test_proposal_styles_present(self, client):
        css = (await client.get("/static/ui/simulation-workspace.css")).text
        assert ".v3-sim-prop-table" in css
        assert ".v3-sim-proposal-status" in css

    async def test_cache_buster_bumped_for_p4(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "simulation-workspace.js?v=20260929-4" in resp.text
        assert "simulation-workspace.css?v=20260929-4" in resp.text


@pytest.mark.asyncio
class TestV3PositionsTemplate:
    """P5.3b positions dashboard (MC-17): the market controller hydrates the
    four buckets against these exact data-role hooks, and the block must stay
    *inside* the market section (AGENTS incremental guardrail)."""

    async def test_workspace_contains_positions_hooks(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200
        assert 'data-role="positions"' in resp.text
        for role in ("positions-status", "positions-account", "positions-updated",
                     "positions-open", "positions-pending", "positions-closed",
                     "positions-liquidated"):
            assert f'data-role="{role}"' in resp.text, f"missing data-role={role}"

    async def test_positions_filter_options(self, auth_client):
        """The status filter exposes exactly the ТЗ §79 buckets."""
        resp = await auth_client.get("/workspace")
        text = resp.text
        start = text.index('data-role="positions-status"')
        sel = text[start:text.index("</select>", start)]
        for value in ("all", "open", "pending", "closed", "liquidated"):
            assert f'value="{value}"' in sel, f"positions status option {value} missing"

    async def test_positions_block_is_inside_market_section(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        start = text.index('data-v3-market-root')
        end = text.index("</section>", start)
        block = text[start:end]
        assert 'data-role="positions-liquidated"' in block, "positions block escaped the market section"

    async def test_terminal_still_untouched_after_p5(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        assert 'data-role="forecast-accuracy"' in text
        for marker in ("wsContext", "wsAttention", "ws-quicklinks", "wsDrawerOverlay"):
            assert marker in text, f"workspace.html regressed: {marker} missing"


@pytest.mark.asyncio
class TestV3PositionsContract:
    """Source guards for the P5.3b controller/chart: it reads /api/v3/positions,
    draws entry/stop/target/liquidation via price lines, publishes the dataset
    DOM contract the browser asserts, and never routes network data to innerHTML."""

    async def test_controller_fetches_positions_and_publishes_dom_hook(self, client):
        js = (await client.get("/static/ui/market-workspace.js")).text
        assert "/api/v3/positions" in js
        assert "setPositionOverlays" in js
        assert "dataset.positionSourceStatus" in js
        assert "dataset.positionLiquidationLines" in js
        # a source outage must be reachable, not only the ok branch
        assert "'error'" in js

    async def test_chart_draws_position_levels_as_price_lines(self, client):
        js = (await client.get("/static/ui/market-chart.js")).text
        assert "setPositionOverlays" in js
        assert "createPriceLine" in js
        # liquidation level uses the shared-formula value, distinct red
        assert "liquidation" in js and "#b91c1c" in js

    async def test_controller_has_no_innerhtml_injection(self, client):
        js = (await client.get("/static/ui/market-workspace.js")).text
        assert not _INNERHTML_ASSIGN.search(js), "positions data must not reach innerHTML"

    async def test_positions_styles_present(self, client):
        css = (await client.get("/static/ui/market-workspace.css")).text
        assert ".v3-positions" in css
        assert ".v3-positions-table" in css
        assert ".v3-positions-empty" in css

    async def test_cache_buster_bumped_for_p5(self, auth_client):
        # The cache-busters were re-bumped in P1.3 (forming/gap indicators) and
        # again for G3/G4 (stream fallback + URL-state).  This guards that both
        # market assets are served versioned and current.
        resp = await auth_client.get("/workspace")
        assert "market-workspace.css?v=20260930-1" in resp.text
        assert "market-workspace.js?v=20260930-2" in resp.text

    async def test_p13_forming_and_gap_styles_present(self, client):
        """P1.3: the fresh indicators have dedicated CSS — an accent-bordered
        forming line, an amber 'due' variant, and a mono red gap list."""
        css = (await client.get("/static/ui/market-workspace.css")).text
        assert ".v3-market-forming" in css
        assert 'data-forming-state="due"' in css
        assert ".v3-market-gaps" in css

