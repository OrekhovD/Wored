"""Real-browser (Playwright/Chromium) evidence for the V3 market workspace.

Run against the V3-aware fixture server (``tests.ui.fixture_app_v3:app``) that
serves the REAL templates, REAL static JS and the REAL ``/api/v3`` read-model
over deterministic stores. These tests render the page in headless Chromium and
assert the acceptance criteria the unit/API/template tests cannot reach:

  * MC-01  instrument list contains only the registered perpetual (no spot);
  * MC-02  live feed -> all quality dots green + entry allowed;
  * MC-03  degraded feed -> entry blocked + stale dot + banner;
  * MC-04  the candlestick chart actually paints a canvas from validated candles.

The server must be reachable at ``UI_QA_HOST``/``UI_QA_PORT`` (default the
conftest picks up). Screenshots land in ``artifacts/v3-browser/``.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from playwright.async_api import TimeoutError as PWTimeout

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[2]
ART_DIR = ROOT / "artifacts" / "v3-browser"

INST_KEY = "htx:linear-swap:BTC-USDT"


async def _login(page, base: str) -> None:
    """fixture_app accepts any non-empty user/pass with the CSRF it renders."""
    await page.goto(f"{base}/login", wait_until="load")
    await page.fill('input[name="password"]', "test-password")
    await page.click('button[type="submit"]')
    await page.wait_for_url(lambda url: not url.endswith("/login"), timeout=8000)


async def _open_workspace(page, base: str) -> None:
    # start each scenario from a known-live market feed
    await page.request.post(f"{base}/__qa__/v3_mode?mode=live")
    await _login(page, base)
    await page.goto(f"{base}/workspace", wait_until="load")
    await page.wait_for_selector("[data-v3-market-root]", timeout=8000)


async def _wait_first_snapshot(page) -> None:
    """The quality strip is rendered only after the first SSE snapshot arrives."""
    await page.wait_for_selector('[data-role="quality"] .v3-market-qdot', timeout=8000)


@pytest.fixture
def base(base_url: str) -> str:
    return base_url


async def test_market_section_mounts_in_chromium(browser, base):
    await _open_workspace(browser, base)
    # instrument select is populated from /api/v3/instruments (options are
    # attached but not "visible" until the dropdown opens — assert attached)
    await browser.wait_for_selector(
        '[data-role="instrument"] option', timeout=8000, state="attached"
    )
    opts = await browser.eval_on_selector_all(
        '[data-role="instrument"] option', "els => els.map(e => ({v: e.value, t: e.textContent}))"
    )
    assert len(opts) >= 1
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "01-market-section.png"), full_page=True)


async def test_instruments_only_perpetual_MC01(browser, base):
    await _open_workspace(browser, base)
    await browser.wait_for_selector(
        '[data-role="instrument"] option', timeout=8000, state="attached"
    )
    opts = await browser.eval_on_selector_all(
        '[data-role="instrument"] option',
        "els => els.map(e => ({v: e.value, t: e.textContent}))",
    )
    values = [o["v"] for o in opts]
    texts = " | ".join(o["t"] for o in opts)
    assert INST_KEY in values
    assert "linear-swap" in texts
    # MC-01: no spot substitution anywhere in the picker
    assert not any("spot" in o["v"].lower() or "spot" in o["t"].lower() for o in opts)
    selected = await browser.eval_on_selector('[data-role="instrument"]', "e => e.value")
    assert selected == INST_KEY


async def test_chart_paints_candles_MC04(browser, base):
    errors: list[str] = []
    browser.on("pageerror", lambda exc: errors.append(str(exc)))
    await _open_workspace(browser, base)
    try:
        await browser.wait_for_selector('[data-role="chart"] canvas', timeout=6000)
    except PWTimeout:
        empty = await browser.query_selector('[data-role="chart"] .v3-market-empty')
        reason = empty and await empty.text_content()
        pytest.fail(
            "MC-04 FAIL: no chart canvas painted. "
            f"fallback='{reason}'. pageerrors={errors[:3]}"
        )
    # status line reports a non-zero candle count from the validated source
    status = await browser.text_content('[data-role="status"]') or ""
    assert "свечей" in status
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "02-chart-candles.png"), full_page=True)


async def test_forming_and_gap_indicators_render_P13(browser, base):
    """P1.3 / MC-02: after candles load, the forming-candle line becomes
    visible with a countdown and the chart container carries a testable
    forming/gap DOM contract.  The gap list is present but hidden when the
    deterministic feed has no holes — never an empty decoration."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    # forming line must become visible (candles loaded → forming computed)
    try:
        await browser.wait_for_selector(
            '[data-role="forming"]:not([hidden])', timeout=8000
        )
    except PWTimeout:
        ds = await browser.evaluate(
            "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
        )
        pytest.fail(f"P1.3 FAIL: forming line never became visible (dataset={ds})")

    text = await browser.text_content('[data-role="forming"]') or ""
    assert "Формируется с" in text and "до закрытия" in text, text

    chart_ds = await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
    )
    # the store seeds a fresh candle every period, so a period is open now
    assert chart_ds.get("formingState") == "forming", chart_ds
    assert chart_ds.get("gapCount") == "0", chart_ds

    node_ds = await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"forming\"]').dataset})"
    )
    assert node_ds.get("formingStartAt"), node_ds
    assert node_ds.get("formingEndsAt"), node_ds

    # gap list exists in the DOM but is hidden when there are no holes
    assert await browser.is_hidden('[data-role="gaps"]')

    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "04-forming-gaps.png"), full_page=True)


# ── G4 (ТЗ §39): URL-state persistence ─────────────────────────────────────────


async def test_url_selections_restored_and_synced_G4(browser, base):
    """G4: ``?period=&horizon=`` restore the controls on load, and every later
    change is mirrored back into the URL with ``replaceState`` (no navigation).
    The root records the exact query as ``data-url-synced-to`` so the contract is
    assertable without scraping ``location``."""
    await browser.request.post(f"{base}/__qa__/v3_mode?mode=live")
    await _login(browser, base)
    await browser.goto(f"{base}/workspace?period=5m&horizon=1h", wait_until="load")
    await browser.wait_for_selector("[data-v3-market-root]", timeout=8000)
    await _wait_first_snapshot(browser)

    # restored from the query string, not the template defaults
    assert await browser.input_value('[data-role="period"]') == "5m"
    assert await browser.input_value('[data-role="horizon"]') == "1h"

    # syncUrl() writes instrument/period/horizon back after the first load
    await browser.wait_for_function(
        """() => {
            const q = document.querySelector('[data-v3-market-root]').dataset.urlSyncedTo || '';
            return q.includes('period=5m') && q.includes('horizon=1h');
        }""",
        timeout=8000,
    )
    synced = await browser.eval_on_selector("[data-v3-market-root]", "e => e.dataset.urlSyncedTo")
    # URLSearchParams percent-encodes the key's colons — compare decoded values
    params = parse_qs(synced)
    assert params.get("instrument") == [INST_KEY], synced  # instrument mirrored too
    assert params.get("period") == ["5m"], synced
    assert params.get("horizon") == ["1h"], synced

    # changing a control updates the URL in place (replaceState — same document)
    await browser.select_option('[data-role="period"]', "15m")
    await browser.wait_for_function(
        """() => (document.querySelector('[data-v3-market-root]').dataset.urlSyncedTo || '')
                  .includes('period=15m')""",
        timeout=8000,
    )
    assert "period=15m" in browser.url, browser.url
    assert "horizon=1h" in browser.url, browser.url

    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "05-url-state-g4.png"), full_page=True)


async def test_unknown_url_period_falls_back_G4(browser, base):
    """G4 guard: a value that is not a supported period is never honoured — the
    picker falls back to a legitimate timeframe instead of fabricating one."""
    await browser.request.post(f"{base}/__qa__/v3_mode?mode=live")
    await _login(browser, base)
    await browser.goto(f"{base}/workspace?period=99m", wait_until="load")
    await browser.wait_for_selector("[data-v3-market-root]", timeout=8000)
    await _wait_first_snapshot(browser)

    chosen = await browser.input_value('[data-role="period"]')
    assert chosen != "99m", chosen
    assert chosen in ("1m", "5m", "15m", "1h", "4h", "1d"), chosen
    synced = await browser.eval_on_selector("[data-v3-market-root]", "e => e.dataset.urlSyncedTo")
    assert f"period={chosen}" in synced, synced


# ── G3 (ТЗ §53): stream degradation → /state polling → SSE resume ───────────────


async def test_stream_mode_sse_when_healthy_G3(browser, base):
    """G3 baseline: on a healthy feed the controller declares ``sse`` mode on the
    chart container — the testable half of the resync contract."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    mode = await browser.eval_on_selector('[data-role="chart"]', "e => e.dataset.streamMode")
    assert mode == "sse", mode


async def test_stream_falls_back_to_poll_and_resumes_G3(browser, base):
    """G3: when the SSE socket cannot be established the controller must keep the
    UI moving by polling ``/state`` (``streamMode='poll'``), and return to SSE
    once the route is healthy again — proving the §53 degraded-stream path.

    The route is aborted for the whole window while we assert the fallback, then
    un-ruled and the page is reloaded so a *fresh* EventSource connects cleanly
    (the browser's own reconnect backoff can sit at 3–5 s and would race the
    reload-based resume check)."""
    # regex, not glob: the encoded instrument key contains no "/" but the URL
    # scheme does, so a leading "*" would never match.
    stream_pattern = re.compile(r"/api/v3/market/[^/]+/stream")

    async def _kill_stream(route):
        await route.abort()

    await browser.route(stream_pattern, _kill_stream)
    try:
        await _open_workspace(browser, base)
        # quality strip is rendered by applySnapshot(), which now arrives from
        # the /state poll — so dots appearing at all proves the fallback works
        await browser.wait_for_selector(
            '[data-role="chart"][data-stream-mode="poll"]', timeout=12000
        )
        await _wait_first_snapshot(browser)
        badge_cls = await browser.get_attribute('[data-role="entry-badge"]', "class") or ""
        assert "is-live" in badge_cls, badge_cls
    finally:
        await browser.unroute(stream_pattern)

    # socket is reachable again → the first snapshot stops the poll fallback
    await browser.reload(wait_until="load")
    await browser.wait_for_selector("[data-v3-market-root]", timeout=8000)
    await _wait_first_snapshot(browser)
    await browser.wait_for_selector(
        '[data-role="chart"][data-stream-mode="sse"]', timeout=15000
    )
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "06-stream-fallback-g3.png"), full_page=True)


async def test_live_feed_allows_entry_MC02(browser, base):
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    qualities = await browser.eval_on_selector_all(
        '[data-role="quality"] .v3-market-qdot', "els => els.map(e => e.dataset.quality)"
    )
    worst = await browser.eval_on_selector(
        '[data-role="quality"] .v3-market-qworst', "e => e.dataset.quality"
    )
    assert qualities == ["live", "live", "live", "live"], qualities
    assert worst == "live"
    badge_cls = await browser.get_attribute('[data-role="entry-badge"]', "class") or ""
    assert "is-live" in badge_cls
    badge_text = await browser.text_content('[data-role="entry-badge"]') or ""
    assert "Можно входить" in badge_text
    quote = await browser.text_content('[data-role="quote"]') or ""
    assert "mark" in quote and "65000.3" in quote


async def test_degraded_feed_blocks_entry_MC03(browser, base):
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    # flip the market source to stale; the open SSE stream re-reads within ~1s
    await browser.request.post(f"{base}/__qa__/v3_mode?mode=stale")
    try:
        await browser.wait_for_selector(
            '[data-role="entry-badge"].is-blocked', timeout=8000
        )
    except PWTimeout:
        cls = await browser.get_attribute('[data-role="entry-badge"]', "class") or ""
        pytest.fail(f"MC-03 FAIL: entry badge never became blocked (class={cls!r})")
    qualities = await browser.eval_on_selector_all(
        '[data-role="quality"] .v3-market-qdot', "els => els.map(e => e.dataset.quality)"
    )
    assert "stale" in qualities, qualities
    assert await browser.is_visible('[data-role="banner"]')
    banner = await browser.text_content('[data-role="banner"]') or ""
    assert "устарел" in banner or "запрещ" in banner
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "03-degraded-blocked.png"), full_page=True)
    # leave the server live for any subsequent test
    await browser.request.post(f"{base}/__qa__/v3_mode?mode=live")


async def test_mobile_chart_fits_no_horizontal_overflow_MC04(
    browser, base, viewport_sizes
):
    """MC-04 (mobile 390×844): the market chart/axes/legend stay readable and
    the market section never forces horizontal overflow."""
    mob = viewport_sizes["mobile"]  # 390×844
    assert mob["width"] == 390 and mob["height"] == 844
    await browser.set_viewport_size({"width": mob["width"], "height": mob["height"]})
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    try:
        await browser.wait_for_selector('[data-role="chart"] canvas', timeout=8000)
    except PWTimeout:
        pytest.fail("MC-04 mobile FAIL: chart canvas never painted at 390px")

    # The market section box itself must sit inside the viewport and not scroll
    # horizontally (scope limited to the V3 section so legacy chrome can't mask it).
    fits = await browser.evaluate(
        """() => {
          const sec = document.querySelector('.v3-market');
          const r = sec.getBoundingClientRect();
          return {
            vw: document.documentElement.clientWidth,
            left: r.left, right: r.right,
            scrollW: sec.scrollWidth, clientW: sec.clientWidth,
          };
        }"""
    )
    assert fits["left"] >= -1 and fits["right"] <= fits["vw"] + 1, fits
    assert fits["scrollW"] <= fits["clientW"] + 1, fits

    # No descendant of the section may extend past the viewport width.
    over = await browser.evaluate(
        """() => {
          const vw = document.documentElement.clientWidth;
          let worst = 0, tag = '';
          document.querySelectorAll('.v3-market *').forEach((e) => {
            const b = e.getBoundingClientRect();
            if (b.width > 0 && b.right > vw) {
              const o = b.right - vw;
              if (o > worst) { worst = o; tag = e.className || e.tagName; }
            }
          });
          return { worst, tag };
        }"""
    )
    assert over["worst"] <= 1, f"MC-04 mobile FAIL: element overflows viewport: {over}"

    # Readability: legend (entry badge), quote and status all carry text.
    badge = (await browser.text_content('[data-role="entry-badge"]') or "").strip()
    quote = (await browser.text_content('[data-role="quote"]') or "").strip()
    status = (await browser.text_content('[data-role="status"]') or "").strip()
    assert badge and quote and status, (badge, quote, status)

    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(
        path=str(ART_DIR / "04-mobile-390-market.png"), full_page=True
    )


async def test_mobile_tap_targets_at_least_44px_MC04(browser, base, viewport_sizes):
    """MC-04: interactive controls (instrument / period selectors) are at least
    44×44px on a phone."""
    mob = viewport_sizes["mobile"]
    await browser.set_viewport_size({"width": mob["width"], "height": mob["height"]})
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    await browser.wait_for_selector(
        '[data-role="instrument"] option', timeout=8000, state="attached"
    )
    rects = await browser.eval_on_selector_all(
        '[data-role="instrument"], [data-role="period"]',
        """els => els.map(e => {
             const b = e.getBoundingClientRect();
             return { role: e.dataset.role, w: Math.round(b.width), h: Math.round(b.height) };
           })""",
    )
    assert rects, "no selectors found"
    for r in rects:
        assert r["h"] >= 44, f"MC-04 mobile FAIL: {r['role']} tap height {r['h']}px < 44px: {rects}"
        assert r["w"] >= 44, f"MC-04 mobile FAIL: {r['role']} tap width {r['w']}px < 44px: {rects}"


# ── Forecast overlay (P2.2): MC-05 / MC-06 / MC-07 ─────────────────────────────
#
# The controller publishes a testable DOM contract on the chart container so a
# browser run can prove the overlay drew *what* it drew without scraping canvas
# pixels:  data-forecast-bars / -render / -valid-ohlc / -in-window.


async def _wait_forecast(browser, predicate, *, timeout=8000):
    await browser.wait_for_function(
        """(pred) => {
            const box = document.querySelector('[data-role="chart"]');
            if (!box) return false;
            const bars = Number(box.dataset.forecastBars || '-1');
            const render = box.dataset.forecastRender || '';
            const valid = box.dataset.forecastValidOhlc || '';
            const win = box.dataset.forecastInWindow || '';
            if (pred === 'ready') return bars > 0 && render === 'line-band';
            if (pred === 'empty') return bars === 0;
            if (pred === 'in-window') return bars > 0 && win === 'true';
            return false;
        }""",
        arg=predicate,
        timeout=timeout,
    )


async def test_forecast_overlay_draws_line_band_MC05_06(browser, base):
    """MC-05: the selected horizon renders a forecast overlay; MC-06: it is a
    line + band, never a candle (has_valid_ohlc false)."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    try:
        await _wait_forecast(browser, "ready")
    except PWTimeout:
        ds = await browser.evaluate(
            "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
        )
        pytest.fail(f"MC-05 FAIL: forecast overlay never drew (dataset={ds})")

    ds = await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
    )
    # default period 1m + horizon 15m → 15 predicted bars
    assert ds.get("forecastBars") == "15", ds
    assert ds.get("forecastRender") == "line-band", ds
    assert ds.get("forecastValidOhlc") == "false", ds  # MC-06: never a candle

    status = await browser.text_content('[data-role="forecast-status"]') or ""
    assert "горизонт 15m" in status and "покрытие" in status, status
    # legend explaining line+band is visible
    assert await browser.is_visible('[data-role="forecast-legend"]')

    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "05-forecast-overlay.png"), full_page=True)


async def test_forecast_overlay_horizon_switch_MC05(browser, base):
    """MC-05: switching the horizon re-queries and re-draws a different bar count."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    await _wait_forecast(browser, "ready")
    # 15m horizon at 1m period → 15 bars; switch period to 15m so 15m horizon = 1 bar
    await browser.select_option('[data-role="period"]', "15m")
    await browser.wait_for_function(
        """() => {
            const box = document.querySelector('[data-role=\"chart\"]');
            return box && box.dataset.forecastBars === '1';
        }""",
        timeout=8000,
    )
    ds = await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
    )
    assert ds.get("forecastBars") == "1", ds
    assert ds.get("forecastRender") == "line-band", ds


async def test_forecast_vs_fact_in_window_MC07(browser, base):
    """MC-07: the overlay sits on the same chart as the fact candles and at least
    one predicted bar already has realised fact behind it."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    await browser.wait_for_selector('[data-role="chart"] canvas', timeout=8000)
    await _wait_forecast(browser, "in-window")
    ds = await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
    )
    assert ds.get("forecastInWindow") == "true", ds
    assert int(ds.get("forecastBars", "0")) > 0, ds
    status = await browser.text_content('[data-role="forecast-status"]') or ""
    assert "факт" in status, status
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "07-forecast-vs-fact.png"), full_page=True)


async def test_forecast_fail_closed_when_absent_MC05(browser, base):
    """The overlay fails closed: with no completed forecast the read-model emits
    ``no_verified_forecast`` and the controller draws nothing (never a made-up
    path)."""
    await browser.request.post(f"{base}/__qa__/v3_forecast?present=false")
    try:
        await _open_workspace(browser, base)
        await _wait_first_snapshot(browser)
        await _wait_forecast(browser, "empty")
        status = await browser.text_content('[data-role="forecast-status"]') or ""
        assert "прогноз" in status.lower()
        assert ("нет проверенного" in status.lower()) or ("нет данных" in status.lower()), status
        ds = await browser.evaluate(
            "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
        )
        assert ds.get("forecastBars") == "0", ds
    finally:
        await browser.request.post(f"{base}/__qa__/v3_forecast?present=true")


async def _wait_accuracy(browser, verdict, *, timeout=8000):
    await browser.wait_for_function(
        """(v) => {
            const el = document.querySelector('[data-role="forecast-accuracy"]');
            return !!el && el.dataset.accuracyVerdict === v;
        }""",
        arg=verdict,
        timeout=timeout,
    )


async def test_forecast_accuracy_na_when_unsettled_MC08(browser, base):
    """MC-08: with no realised facts the holdout must read N/A (insufficient
    sample), never a fabricated accuracy number."""
    await browser.request.post(f"{base}/__qa__/v3_forecast?present=true&settled=false")
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    await _wait_accuracy(browser, "N/A")
    el = await browser.text_content('[data-role="forecast-accuracy"]') or ""
    assert "N/A" in el and "недостаточная выборка" in el, el
    assert "Точность (holdout)" in el, el


async def test_forecast_accuracy_beats_baseline_when_settled_MC08(browser, base):
    """MC-08: once the holdout has realised facts the panel shows MAPE, the
    persistence baseline and a skill verdict computed deterministically."""
    await browser.request.post(f"{base}/__qa__/v3_forecast?present=true&settled=true")
    try:
        await _open_workspace(browser, base)
        await _wait_first_snapshot(browser)
        await _wait_accuracy(browser, "beats_baseline")
        el = await browser.text_content('[data-role="forecast-accuracy"]') or ""
        assert "MAPE" in el and "baseline" in el and "skill" in el, el
        assert "beats_baseline" in el, el
        ART_DIR.mkdir(parents=True, exist_ok=True)
        await browser.screenshot(
            path=str(ART_DIR / "08-forecast-accuracy-holdout.png"), full_page=True
        )
    finally:
        await browser.request.post(f"{base}/__qa__/v3_forecast?present=true&settled=false")


# ── P3: session constructor (MC-09 / MC-10 / MC-11) ─────────────────────────


def _dt_local(dt) -> str:
    """datetime-local value from an aware UTC datetime (label pins UTC)."""
    return dt.strftime("%Y-%m-%dT%H:%M")


async def _sim_reset(browser, base) -> None:
    await browser.request.post(f"{base}/__qa__/v3_sim_reset")


async def _open_sim(browser, base) -> None:
    await _open_workspace(browser, base)
    await browser.wait_for_selector("[data-v3-simulation-root]", timeout=8000)


async def _wait_sim(browser, verdict: str, *, timeout: int = 8000) -> None:
    await browser.wait_for_function(
        """(v) => {
            const e = document.querySelector('[data-role="sim-status"]');
            return !!e && e.dataset.simVerdict === v;
        }""",
        arg=verdict,
        timeout=timeout,
    )


async def _sim_dataset(browser) -> dict:
    return await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"sim-status\"]').dataset})"
    )


async def _fill_valid_window(browser) -> None:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    await browser.fill('[data-role="sim-start"]', _dt_local(now + timedelta(minutes=10)))
    await browser.fill('[data-role="sim-end"]', _dt_local(now + timedelta(hours=6)))


async def test_simulation_persist_and_restore_MC09(browser, base):
    """MC-09: every SimulationPlanV1 field the user set is preserved exactly
    and rehydrates into the form after a refresh."""
    await _sim_reset(browser, base)
    await _open_sim(browser, base)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    await browser.fill('[data-role="sim-start"]', _dt_local(now + timedelta(minutes=15)))
    await browser.fill('[data-role="sim-end"]', _dt_local(now + timedelta(hours=8)))
    await browser.fill('[data-role="sim-tz"]', "Europe/Moscow")
    await browser.fill('[data-role="sim-manual-budget"]', "1234.50")
    await browser.fill('[data-role="sim-auto-budget"]', "777")
    await browser.fill('[data-role="sim-risk"]', "7")
    await browser.fill('[data-role="sim-daily-loss"]', "21")
    await browser.fill('[data-role="sim-session-loss"]', "42")
    await browser.fill('[data-role="sim-exposure"]', "100")
    await browser.fill('[data-role="sim-positions"]', "3")
    await browser.fill('[data-role="sim-cooldown"]', "9")
    await browser.uncheck('[data-role="sim-side-short"]')
    await browser.click('[data-role="sim-validate"]')
    await _wait_sim(browser, "ok")
    ds = await _sim_dataset(browser)
    assert ds.get("simHash", "").startswith("sha256:"), ds
    await browser.click('[data-role="sim-save"]')
    await _wait_sim(browser, "saved")
    plan_id = (await _sim_dataset(browser)).get("simPlanId")
    assert plan_id, "plan_id not exposed after save"

    # refresh → the controller restores the latest draft with exact values
    await browser.reload(wait_until="load")
    await browser.wait_for_selector("[data-v3-simulation-root]", timeout=8000)
    await _wait_sim(browser, "saved")
    vals = await browser.evaluate(
        """() => { const g = (r) => document.querySelector(`[data-role="${r}"]`).value;
            return {tz: g('sim-tz'), mb: g('sim-manual-budget'), ab: g('sim-auto-budget'),
                    risk: g('sim-risk'), daily: g('sim-daily-loss'), sess: g('sim-session-loss'),
                    exp: g('sim-exposure'), pos: g('sim-positions'), cd: g('sim-cooldown'),
                    start: g('sim-start'), end: g('sim-end'),
                    long: document.querySelector('[data-role=\"sim-side-long\"]').checked,
                    short: document.querySelector('[data-role=\"sim-side-short\"]').checked}; }"""
    )
    assert vals["tz"] == "Europe/Moscow"
    assert float(vals["mb"]) == 1234.5   # numerically exact after the round trip
    assert float(vals["ab"]) == 777
    assert float(vals["risk"]) == 7
    assert float(vals["daily"]) == 21
    assert float(vals["sess"]) == 42
    assert float(vals["exp"]) == 100
    assert int(vals["pos"]) == 3
    assert int(vals["cd"]) == 9
    assert vals["start"] == _dt_local(now + timedelta(minutes=15))
    assert vals["end"] == _dt_local(now + timedelta(hours=8))
    assert vals["long"] is True and vals["short"] is False
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "09-session-constructor.png"), full_page=True)


async def test_simulation_stale_quote_blocks_validation_MC10(browser, base):
    """MC-10: a degraded feed blocks with the exact reason code; approve stays
    unreachable — the user cannot plan against a quote the system won't trade."""
    await _sim_reset(browser, base)
    await _open_sim(browser, base)
    await _fill_valid_window(browser)
    await browser.request.post(f"{base}/__qa__/v3_mode?mode=stale")
    try:
        await browser.click('[data-role="sim-validate"]')
        await _wait_sim(browser, "violations")
        codes = await browser.eval_on_selector_all(
            '[data-role="sim-violations"] li', "els => els.map(e => e.dataset.code)"
        )
        assert "stale_quote" in codes, codes
        assert await browser.is_disabled('[data-role="sim-approve"]')
    finally:
        await browser.request.post(f"{base}/__qa__/v3_mode?mode=live")


async def test_simulation_missing_intervals_blocks_replay_MC10(browser, base):
    """MC-10: a replay window without full 1m history is refused with
    missing_intervals, not started on partial data."""
    await _sim_reset(browser, base)
    await browser.request.post(f"{base}/__qa__/v3_coverage?complete=false")
    try:
        await _open_sim(browser, base)
        await browser.select_option('[data-role="sim-mode"]', "historical_replay")
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        await browser.fill('[data-role="sim-start"]', _dt_local(now - timedelta(hours=4)))
        await browser.fill('[data-role="sim-end"]', _dt_local(now - timedelta(hours=2)))
        await browser.click('[data-role="sim-validate"]')
        await _wait_sim(browser, "violations")
        codes = await browser.eval_on_selector_all(
            '[data-role="sim-violations"] li', "els => els.map(e => e.dataset.code)"
        )
        assert "missing_intervals" in codes, codes
    finally:
        await browser.request.post(f"{base}/__qa__/v3_coverage?complete=true")


async def test_simulation_approve_start_immutable_MC11(browser, base):
    """MC-11: approve pins the plan_hash, the approved plan can no longer be
    edited (even via direct API), and the started session carries the same
    frozen hash with a stamped first transition."""
    import json as _json
    await _sim_reset(browser, base)
    await _open_sim(browser, base)
    await _fill_valid_window(browser)
    await browser.click('[data-role="sim-validate"]')
    await _wait_sim(browser, "ok")
    pinned_hash = (await _sim_dataset(browser))["simHash"]
    await browser.click('[data-role="sim-approve"]')
    await _wait_sim(browser, "approved")
    ds = await _sim_dataset(browser)
    assert ds["simHash"] == pinned_hash, "hash moved across approval"
    plan_id = ds["simPlanId"]

    # direct API edit of the approved version must be refused
    csrf = await browser.get_attribute('meta[name="csrf-token"]', "content")
    resp = await browser.request.post(
        f"{base}/api/v3/simulation/plans/{plan_id}",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data=_json.dumps({"plan": {
            "instrument_key": INST_KEY, "mode": "live_paper",
            "start_at": ds.get("simStart") or "x", "end_at": "x",
        }}),
    )
    assert resp.status == 409, resp.status
    body = await resp.json()
    assert body["detail"]["reason_code"] == "plan_immutable", body

    await browser.click('[data-role="sim-start-btn"]')
    await _wait_sim(browser, "started")
    ds2 = await _sim_dataset(browser)
    assert ds2["simHash"] == pinned_hash, "session started on a different hash"
    session_id = ds2.get("simSessionId")
    assert session_id, ds2
    sresp = await browser.request.get(f"{base}/api/v3/simulation/sessions/{session_id}")
    assert sresp.status == 200
    sess = (await sresp.json())["session"]
    assert sess["plan_hash"] == pinned_hash
    assert sess["status"] == "starting"
    assert sess["state_log"][0]["to"] == "starting"
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "11-session-started.png"), full_page=True)


# ── P4: AI proposal (MC-12 / MC-13) ─────────────────────────────────────────


async def _set_proposal_source(browser, base, kind) -> None:
    r = await browser.request.post(f"{base}/__qa__/v3_proposal_source?kind={kind}")
    assert r.status == 200
    body = await r.json()
    assert body["kind"] == kind, f"QA source toggle rejected {kind}: {body}"


async def _wait_proposal(browser, verdict: str, *, timeout: int = 8000) -> None:
    await browser.wait_for_function(
        """(v) => {
            const e = document.querySelector('[data-role="sim-proposal-status"]');
            return !!e && e.dataset.propVerdict === v;
        }""",
        arg=verdict,
        timeout=timeout,
    )


async def _proposal_dataset(browser) -> dict:
    return await browser.evaluate(
        "() => ({...document.querySelector('[data-role=\"sim-proposal-status\"]').dataset})"
    )


async def _fill_questionnaire(browser) -> None:
    await browser.fill('[data-role="simq-manual"]', "100")
    await browser.fill('[data-role="simq-auto"]', "50")
    await browser.fill('[data-role="simq-maxloss"]', "30")
    await browser.fill('[data-role="simq-duration"]', "24")


async def test_ai_proposal_accept_as_draft_MC12(browser, base):
    """MC-12: the questionnaire returns a draft with rationale; accepting it
    ONLY fills the manual form — approve/start stay separate and disabled."""
    await _sim_reset(browser, base)
    await _set_proposal_source(browser, base, "rules")
    await _open_sim(browser, base)
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    await browser.fill('[data-role="sim-start"]', _dt_local(now + timedelta(minutes=10)))
    await browser.fill('[data-role="sim-end"]', _dt_local(now + timedelta(hours=6)))
    await _fill_questionnaire(browser)
    await browser.click('[data-role="sim-propose"]')
    await _wait_proposal(browser, "completed")
    ds = await _proposal_dataset(browser)
    assert ds.get("propAdoptable") == "true", ds
    assert ds.get("propId"), ds
    rows = await browser.evaluate(
        """() => ({...document.querySelector('[data-role="sim-proposal-compare"]').dataset,
                   hasHash: document.querySelector('[data-role="sim-proposal-compare"]').textContent.includes('plan_hash'),
                   rationale: !!document.querySelector('.v3-sim-prop-rationale')})"""
    )
    assert int(rows["propRows"]) >= 8, rows
    assert rows["hasHash"], "model/plan_hash meta missing"
    assert rows["rationale"], "per-parameter rationale missing"

    # MC-12 core: accept is NOT approve — buttons stay gated behind manual steps
    approve_state = await browser.get_attribute('[data-role="sim-approve"]', "disabled")
    start_state = await browser.get_attribute('[data-role="sim-start-btn"]', "disabled")
    assert approve_state is not None and start_state is not None

    await browser.click('[data-role="sim-accept"]')
    vals = await browser.evaluate(
        """() => { const g = (r) => document.querySelector(`[data-role="${r}"]`).value;
            return {mb: g('sim-manual-budget'), ab: g('sim-auto-budget'),
                    sess: g('sim-session-loss'), cd: g('sim-cooldown')}; }"""
    )
    assert float(vals["mb"]) == 100 and float(vals["ab"]) == 50, vals
    assert float(vals["sess"]) == 30, vals  # max_acceptable_loss lands as session loss
    assert int(vals["cd"]) == 5, vals       # supervised → 5-min cooldown
    pds = await _proposal_dataset(browser)
    assert pds.get("propAccepted") == "true", pds
    approve_after = await browser.get_attribute('[data-role="sim-approve"]', "disabled")
    assert approve_after is not None, "approve became enabled without validation (MC-12)"
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "12-ai-proposal.png"), full_page=True)


async def test_ai_proposal_vetoed_by_validator_MC12(browser, base):
    """MC-12 veto: an off-catalog draft is surfaced with the exact violation,
    NOT silently corrected; accept stays unreachable."""
    await _sim_reset(browser, base)
    try:
        await _set_proposal_source(browser, base, "bad_draft")
        await _open_sim(browser, base)
        await _fill_questionnaire(browser)
        await browser.click('[data-role="sim-propose"]')
        await _wait_proposal(browser, "vetoed")
        ds = await _proposal_dataset(browser)
        assert ds.get("propAdoptable") == "false", ds
        accept_disabled = await browser.get_attribute('[data-role="sim-accept"]', "disabled")
        assert accept_disabled is not None, "accept enabled for a vetoed draft"
        viol = await browser.evaluate(
            """() => [...document.querySelectorAll('[data-role="sim-proposal-compare"] li')]
                       .map(li => li.dataset.field + ':' + li.dataset.code)"""
        )
        assert any("strategy_version" in x and "field_enum" in x for x in viol), viol
    finally:
        await _set_proposal_source(browser, base, "rules")


async def test_ai_proposal_failure_keeps_manual_path_MC13(browser, base):
    """MC-13: a failed agent (quota stub here) shows the exact reason code,
    exposes no draft, and the manual validate button stays usable — the failed
    flow never produced a paid fallback or an auto-start."""
    await _sim_reset(browser, base)
    try:
        await _set_proposal_source(browser, base, "quota")
        await _open_sim(browser, base)
        await _fill_questionnaire(browser)
        await browser.click('[data-role="sim-propose"]')
        await _wait_proposal(browser, "failed")
        ds = await _proposal_dataset(browser)
        assert ds.get("propFailCode") == "llm_quota", ds
        text = await browser.text_content('[data-role="sim-proposal-status"]') or ""
        assert "платный fallback" in text, text
        accept_disabled = await browser.get_attribute('[data-role="sim-accept"]', "disabled")
        assert accept_disabled is not None
        # manual path alive: validate returns a real verdict, not an error state
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        await browser.fill('[data-role="sim-start"]', _dt_local(now + timedelta(minutes=10)))
        await browser.fill('[data-role="sim-end"]', _dt_local(now + timedelta(hours=6)))
        await browser.click('[data-role="sim-validate"]')
        await _wait_sim(browser, "ok")
        ART_DIR.mkdir(parents=True, exist_ok=True)
        await browser.screenshot(path=str(ART_DIR / "13-proposal-failed.png"), full_page=True)
    finally:
        await _set_proposal_source(browser, base, "rules")


# ── P5.3b positions dashboard (MC-17) ─────────────────────────────────────────


async def _positions_dataset(page) -> dict:
    return await page.evaluate(
        "() => ({...document.querySelector('[data-role=\"chart\"]').dataset})"
    )


async def _wait_positions(browser, status: str, *, timeout=8000) -> None:
    await browser.wait_for_function(
        """(v) => {
            const box = document.querySelector('[data-role="chart"]');
            return !!box && box.dataset.positionSourceStatus === v;
        }""",
        arg=status,
        timeout=timeout,
    )


async def test_positions_dashboard_renders_buckets_MC17(browser, base):
    """MC-17: order→fill→position→close/liquidation for manual+auto on one
    instrument.  The four buckets render, and the chart draws the SAME ids and
    levels the table shows (entry/stop/target/liquidation price lines + markers).
    Amounts are recorded facts; the liquidation level is the shared-formula value
    the enforcement acts on — never a stored guess."""
    errors: list[str] = []
    browser.on("pageerror", lambda exc: errors.append(str(exc)))
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    try:
        await _wait_positions(browser, "ok")
    except PWTimeout:
        ds = await _positions_dataset(browser)
        pytest.fail(f"MC-17 FAIL: positions never reached 'ok' (dataset={ds}) errors={errors[:3]}")

    ds = await _positions_dataset(browser)
    # seeded scenario: 3 positions (open/closed/liquidated) + 1 pending intent
    assert ds.get("positionCount") == "4", ds
    assert ds.get("positionOpen") == "1", ds
    assert ds.get("positionLiquidation") == "1", ds
    assert ds.get("positionPending") == "1", ds
    # the open long contributes 4 price lines (entry/stop/target/liquidation)
    assert ds.get("positionPriceLines") == "4", ds
    assert ds.get("positionLiquidationLines") == "1", ds
    # open + closed + liquidated each contribute one time marker
    assert ds.get("positionMarkers") == "3", ds
    assert errors == [], errors

    # each bucket table is present in the DOM (data-role containers hydrated)
    for role in ("positions-open", "positions-pending", "positions-closed", "positions-liquidated"):
        assert await browser.query_selector(f'[data-role="{role}"] .v3-positions-table'), role
    # the liquidated bucket shows the exit_reason 'liquidation'
    liq_text = await browser.text_content('[data-role="positions-liquidated"]') or ""
    assert "liquidation" in liq_text, liq_text
    ART_DIR.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(path=str(ART_DIR / "17-positions-dashboard.png"), full_page=True)


async def test_positions_status_filter_MC17(browser, base):
    """MC-17 filter: narrowing to open shows only the open bucket and hides the
    terminal ones, and the chart overlays follow the filtered cards."""
    await _open_workspace(browser, base)
    await _wait_first_snapshot(browser)
    await _wait_positions(browser, "ok")
    await browser.select_option('[data-role="positions-status"]', "open")
    await browser.wait_for_function(
        """() => {
            const box = document.querySelector('[data-role="chart"]');
            return box && box.dataset.positionOpen === '1' && box.dataset.positionLiquidation === '0';
        }""",
        timeout=8000,
    )
    ds = await _positions_dataset(browser)
    assert ds.get("positionOpen") == "1", ds
    # status=open excludes liquidated + closed from the cards
    assert ds.get("positionLiquidation") == "0", ds
    assert ds.get("positionPriceLines") == "4", ds  # open long still draws 4 levels
    await browser.select_option('[data-role="positions-status"]', "all")


async def test_positions_source_states_MC17(browser, base):
    """MC-17 fail-visible: distinct empty and error source states.  An empty
    source renders the empty-state rows (not a fabricated table); a source outage
    surfaces ``positionSourceStatus='error'`` instead of silently showing a demo
    list."""
    try:
        # empty
        await browser.request.post(f"{base}/__qa__/v3_positions?mode=empty")
        await _open_workspace(browser, base)
        await _wait_first_snapshot(browser)
        await _wait_positions(browser, "empty")
        ds = await _positions_dataset(browser)
        assert ds.get("positionCount") == "0", ds
        assert await browser.query_selector('[data-role="positions-open"] .v3-positions-empty')
        # no chart overlays when there is nothing recorded
        assert ds.get("positionPriceLines") == "0", ds

        # error (source outage → endpoint 503): already authenticated, so reload
        # the workspace in the same session rather than logging in a second time.
        await browser.request.post(f"{base}/__qa__/v3_positions?mode=error")
        await browser.goto(f"{base}/workspace", wait_until="load")
        await browser.wait_for_selector("[data-v3-market-root]", timeout=8000)
        await _wait_positions(browser, "error")
        updated = await browser.text_content('[data-role="positions-updated"]') or ""
        assert "недоступен" in updated or "не удалось" in updated, updated
    finally:
        await browser.request.post(f"{base}/__qa__/v3_positions?mode=ok")

