/**
 * WORED V3 Market Workspace controller — P1.2b
 *
 * Hydrates the ``.v3-market`` section on /workspace with:
 *   1. instrument selector (from /api/v3/instruments)
 *   2. period selector (per-instrument ``supported_periods``)
 *   3. quality strip: per-component dots (ticker / mark / index / funding)
 *      driven by the SSE stream — no steady-state polling; /state is polled
 *      ONLY as a degraded-stream fallback (see G3 below)
 *   4. candlestick chart via mountMarketChart()
 *   5. degraded banner (can_enter=false OR stream error) that disables the
 *      entry affordance so a stale feed cannot be traded against
 *   6. G4 (§39): instrument/period/horizon/positions-status/account are mirrored
 *      into the URL (replaceState) and restored on load, so a refresh keeps the
 *      same workspace context
 *   7. G3 (§53): SSE snapshots carry ``id: <sequence>`` for Last-Event-ID
 *      resync; if no snapshot arrives within STREAM_STALE_MS the controller
 *      falls back to /state polling and resumes SSE once the socket is healthy
 *
 * Only runs on pages that contain ``[data-v3-market-root]``.  Uses safe DOM
 * APIs (createElement + textContent) throughout — no innerHTML with data
 * coming from the network.
 *
 * Data contracts (see webui/market_workspace.py):
 *   GET /api/v3/instruments            → { instruments: [{instrument_key, …}] }
 *   GET /api/v3/market/{key}/candles   → { candles: [...], gaps, quality, … }
 *   SSE /api/v3/market/{key}/stream    → events snapshot | keepalive | error | bye
 */

import { mountMarketChart } from './market-chart.js?v=20260929-2';

const DEFAULT_PERIOD = '1m';
const DEFAULT_HORIZON = '1h';
const DEFAULT_CANDLE_LIMIT = 200;
// G3 (§53): if no snapshot (SSE or poll) lands within this window, treat the
// stream as degraded and fall back to /state polling until it recovers.  6 s is
// just past the 5 s ticker freshness budget so a stalled feed trips naturally.
const STREAM_STALE_MS = 6000;
const POLL_INTERVAL_MS = 2000;
const WATCHDOG_INTERVAL_MS = 2000;
const PERIODS_UI = [
  { v: '1m', label: '1m' },
  { v: '5m', label: '5m' },
  { v: '15m', label: '15m' },
  { v: '1h', label: '1h' },
  { v: '4h', label: '4h' },
  { v: '1d', label: '1d' },
];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function fmt(decStr) {
  // Decimal-strings arrive verbatim; UI shows them without rounding.
  if (decStr == null) return '—';
  return String(decStr);
}

function renderQualityStrip(container, quality) {
  // Quality is a dict: {ticker, mark, index, funding, worst}.  Missing = no
  // data yet, stale = present but too old, live = within threshold.
  container.replaceChildren();
  const names = ['ticker', 'mark', 'index', 'funding'];
  for (const name of names) {
    const dot = el('span', 'v3-market-qdot');
    const q = (quality && quality[name]) || 'missing';
    dot.dataset.quality = q;
    dot.setAttribute('aria-label', `${name}: ${q}`);
    const label = el('span', 'v3-market-qlabel', name);
    const wrap = el('span', 'v3-market-qitem');
    wrap.appendChild(dot);
    wrap.appendChild(label);
    container.appendChild(wrap);
  }
  const worst = el('span', 'v3-market-qworst', quality && quality.worst ? quality.worst : 'missing');
  worst.dataset.quality = (quality && quality.worst) || 'missing';
  container.appendChild(worst);
}

function setEntryBadge(badgeEl, state) {
  badgeEl.replaceChildren();
  if (!state) {
    badgeEl.className = 'v3-market-entrybadge is-unknown';
    badgeEl.textContent = '—';
    return;
  }
  const caps = state.capabilities || {};
  if (caps.can_enter) {
    badgeEl.className = 'v3-market-entrybadge is-live';
    badgeEl.textContent = 'Можно входить';
  } else {
    badgeEl.className = 'v3-market-entrybadge is-blocked';
    const reason = caps.reason_code || 'stale';
    badgeEl.textContent = `Вход закрыт: ${reason}`;
  }
}

function setStatusLine(el2, meta) {
  const bits = [];
  if (meta.instrument) bits.push(meta.instrument);
  if (meta.period) bits.push(meta.period);
  if (meta.candles != null) bits.push(`${meta.candles} свечей`);
  if (meta.derivedFrom) bits.push(`derived from ${meta.derivedFrom}`);
  if (meta.gaps && meta.gaps > 0) bits.push(`gaps: ${meta.gaps}`);
  if (meta.sourceStatus && meta.sourceStatus !== 'ok') bits.push(`source: ${meta.sourceStatus}`);
  el2.textContent = bits.join(' · ') || '—';
}

function fmtCoverage(cov) {
  if (cov == null || !Number.isFinite(Number(cov))) return '—';
  return Math.round(Number(cov) * 100) + '%';
}

function fmtClock(iso) {
  // UTC HH:MM:SS of an ISO timestamp — the chart timeline is UTC too.
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return '—';
  return d.toISOString().slice(11, 19);
}

function fmtMmSs(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '—';
  const s = Math.max(Math.floor(Number(seconds)), 0);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

// renderForming draws the P1.3 forming-candle state.  It is deliberately a
// TIME fact only: the store persists closed candles, so we never draw or
// invent an open body — the line says when the period started/closes and that
// the body is not in the source.  Stale market adds an explicit caveat.
function renderForming(node, forming, quality) {
  if (!node) return;
  if (!forming || !forming.start_at) {
    node.hidden = true;
    node.textContent = '—';
    node.dataset.formingState = 'unknown';
    return;
  }
  const remaining = Math.max(
    Math.floor((Date.parse(forming.ends_at) - Date.now()) / 1000), 0);
  const bits = [
    'Формируется с ' + fmtClock(forming.start_at) + ' UTC',
    'до закрытия ~' + fmtMmSs(remaining),
  ];
  if (forming.candle_body_available === false) bits.push('тело — после закрытия');
  if (quality && quality.worst === 'stale') bits.push('рынок устарел');
  node.hidden = false;
  node.textContent = bits.join(' · ');
  node.dataset.formingState = remaining === 0 ? 'due' : 'forming';
  node.dataset.formingStartAt = forming.start_at;
  node.dataset.formingEndsAt = forming.ends_at;
}

// renderGaps makes MC-02 gap detection visible: every missing span is listed
// by its exact expected/found boundaries.  No gaps → the block is hidden,
// not shown as an empty decoration.
function renderGaps(node, gaps) {
  if (!node) return;
  const list = gaps || [];
  node.replaceChildren();
  node.hidden = list.length === 0;
  for (const g of list) {
    const li = el('li', 'v3-market-gap',
      'пропуск: ' + g.expected_start_at + ' → ' + g.found_start_at);
    node.appendChild(li);
  }
}

// renderForecastStatus writes a plain text line from the forecast payload (or
// the fail-closed reason).  It never builds HTML, only textContent.
function renderForecastStatus(el2, pred, opts) {
  if (!el2) return;
  if (opts && opts.error) {
    el2.textContent = 'Прогноз: ' + opts.error;
    return;
  }
  if (!pred || pred.error_code || !(pred.intervals && pred.intervals.length)) {
    const msg = (pred && (pred.message || pred.error_code)) || 'нет данных';
    el2.textContent = 'Прогноз: ' + msg;
    return;
  }
  const bits = [
    `горизонт ${pred.horizon}`,
    `период ${pred.period}`,
    `покрытие ${fmtCoverage(pred.coverage)}`,
    `${pred.intervals.length}/${pred.expected_candles} баров`,
  ];
  if (pred.role) bits.push(`роли: ${pred.role}`);
  if (pred.confidence_kind) bits.push(`уверенность: ${pred.confidence_kind}`);
  if (opts && opts.inWindow != null) bits.push(opts.inWindow ? 'есть факт для сверки' : 'факт ещё не наступил');
  el2.textContent = 'Прогноз: ' + bits.join(' · ');
}

function fmtPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return Number(v).toFixed(2) + '%';
}

// renderAccuracy publishes the MC-08 holdout block.  Insufficient sample must
// read N/A, never a fabricated number.  Also writes a data-* contract so a
// browser test can prove the verdict without parsing prose.
function renderAccuracy(el2, h) {
  if (!el2) return;
  if (!h || h.verdict == null) {
    el2.textContent = 'Точность: —';
    el2.dataset.accuracyVerdict = 'unknown';
    el2.dataset.accuracySample = '0';
    return;
  }
  el2.dataset.accuracyVerdict = String(h.verdict);
  el2.dataset.accuracySample = String(h.sample_n || 0);
  if (h.verdict === 'N/A') {
    const need = h.min_sample != null ? ` (нужно ≥${h.min_sample})` : '';
    el2.textContent = `Точность (holdout): N/A — недостаточная выборка, N=${h.sample_n || 0}${need}`;
    return;
  }
  const skill = h.skill_vs_baseline == null ? '—'
    : (Number(h.skill_vs_baseline) >= 0 ? '+' : '') + Number(h.skill_vs_baseline).toFixed(3);
  el2.textContent =
    `Точность (holdout): MAPE ${fmtPct(h.mape)} · baseline ${fmtPct(h.baseline_mape)} · ` +
    `покрытие ${fmtPct(h.coverage != null ? h.coverage * 100 : null)} · ` +
    `skill ${skill} (${h.verdict}) · N=${h.sample_n}`;
}

export function mountV3Market(root) {
  const instSel = root.querySelector('[data-role="instrument"]');
  const perSel = root.querySelector('[data-role="period"]');
  const chartBox = root.querySelector('[data-role="chart"]');
  const qualityStrip = root.querySelector('[data-role="quality"]');
  const quoteLine = root.querySelector('[data-role="quote"]');
  const statusLine = root.querySelector('[data-role="status"]');
  const entryBadge = root.querySelector('[data-role="entry-badge"]');
  const banner = root.querySelector('[data-role="banner"]');
  const horizonSel = root.querySelector('[data-role="horizon"]');
  const forecastStatus = root.querySelector('[data-role="forecast-status"]');
  const forecastAccuracy = root.querySelector('[data-role="forecast-accuracy"]');
  const forecastLegend = root.querySelector('[data-role="forecast-legend"]');
  const forecastToggle = root.querySelector('[data-role="forecast-toggle"]');
  const posStatusSel = root.querySelector('[data-role="positions-status"]');
  const posAccountSel = root.querySelector('[data-role="positions-account"]');
  const posUpdated = root.querySelector('[data-role="positions-updated"]');
  const formingLine = root.querySelector('[data-role="forming"]');
  const gapsBox = root.querySelector('[data-role="gaps"]');
  const posTables = {
    open: root.querySelector('[data-role="positions-open"]'),
    pending: root.querySelector('[data-role="positions-pending"]'),
    closed: root.querySelector('[data-role="positions-closed"]'),
    liquidated: root.querySelector('[data-role="positions-liquidated"]'),
  };

  if (!instSel || !chartBox || !qualityStrip) return null;

  const chart = mountMarketChart(chartBox, { height: 320 });
  let currentInstrument = null;
  let currentPeriod = DEFAULT_PERIOD;
  let currentHorizon = DEFAULT_HORIZON;
  let forecastOn = true;
  let candleTimeRange = null; // {min,max} unix seconds of loaded fact candles
  let eventSource = null;
  let lastState = null;
  let positionsStatusFilter = 'all';
  let positionsAccountFilter = 'all';
  let formingMeta = null;        // P1.3: candles.forming from the last load
  let formingTimer = null;       // 1 Hz countdown while a period is open
  // G3: stream liveness bookkeeping — the last time *any* source produced a
  // snapshot, plus the polling-fallback timer and the watchdog that trips it.
  let lastSnapshotAt = 0;
  let pollTimer = null;
  let watchdogTimer = null;
  // G4 (§39): selections (instrument/period/horizon/status/account) are mirrored
  // into the URL so a refresh restores the exact same workspace context.
  const urlState = new URLSearchParams(location.search);

  // syncUrl writes the current selections into the query string via
  // replaceState (never a navigation/reload) and records the result on the root
  // as a testable DOM contract for the browser evidence.
  function syncUrl() {
    const p = new URLSearchParams(location.search);
    if (currentInstrument) p.set('instrument', currentInstrument);
    if (currentPeriod) p.set('period', currentPeriod);
    if (currentHorizon) p.set('horizon', currentHorizon);
    if (positionsStatusFilter && positionsStatusFilter !== 'all') p.set('status', positionsStatusFilter);
    else p.delete('status');
    if (positionsAccountFilter && positionsAccountFilter !== 'all') p.set('account', positionsAccountFilter);
    else p.delete('account');
    const qs = p.toString();
    const next = location.pathname + (qs ? '?' + qs : '');
    try { history.replaceState({}, '', next); } catch (_) { /* ignore sandboxed history */ }
    root.dataset.urlSyncedTo = qs;
  }

  // startPollFallback begins /state polling when the SSE stream is degraded;
  // it is idempotent.  Each poll reuses the same snapshot renderer, so the UI
  // recovers identically whether data arrives via SSE or the fallback.
  function startPollFallback() {
    if (pollTimer || !currentInstrument) return;
    if (chartBox) chartBox.dataset.streamMode = 'poll';
    pollTimer = setInterval(async () => {
      if (!currentInstrument) return;
      try {
        const r = await fetch(
          `/api/v3/market/${encodeURIComponent(currentInstrument)}/state`,
          { credentials: 'same-origin' },
        );
        if (!r.ok) return;
        const state = await r.json();
        lastSnapshotAt = Date.now();
        applySnapshot(state);
      } catch (_) { /* keep the last value; the watchdog will hold fallback */ }
    }, POLL_INTERVAL_MS);
  }

  function stopPollFallback() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (chartBox) chartBox.dataset.streamMode = 'sse';
  }

  // applySnapshot renders one MarketStateV1 regardless of transport.  Shared by
  // the SSE listener and the /state poll fallback so both paths behave the same.
  function applySnapshot(state) {
    lastState = state;
    renderQualityStrip(qualityStrip, state.quality);
    setEntryBadge(entryBadge, state);
    if (quoteLine && state.quote) {
      quoteLine.textContent =
        `mark ${fmt(state.quote.mark)} · index ${fmt(state.quote.index)} · ` +
        `bid ${fmt(state.quote.bid)} / ask ${fmt(state.quote.ask)} · ` +
        `spread ${fmt(state.spread)} · funding ${fmt(state.quote.funding_rate)}`;
    } else if (quoteLine) {
      quoteLine.textContent = 'данные отсутствуют';
    }
    if (chart) chart.applyState(state);
    // forming line reflects feed staleness without waiting for a reload
    if (formingMeta) renderForming(formingLine, formingMeta, state.quality);
    // Only auto-hide the banner when we're back to live
    if (state.quality && state.quality.worst === 'live') showBanner('', null);
    else if (state.quality && state.quality.worst === 'stale') showBanner('Рыночные данные устарели — вход запрещён.', 'warn');
  }

  function showBanner(text, kind) {
    if (!banner) return;
    banner.className = 'v3-market-banner is-' + (kind || 'warn');
    banner.textContent = text || '';
    banner.hidden = !text;
  }

  async function loadInstruments() {
    const r = await fetch('/api/v3/instruments', { credentials: 'same-origin' });
    if (!r.ok) throw new Error('instruments HTTP ' + r.status);
    const data = await r.json();
    const items = (data.instruments || []).filter((i) => i.status === 'active');
    instSel.replaceChildren();
    if (items.length === 0) {
      // MC-01: registry empty means no legitimate instrument to trade.  We do
      // not fall back to spot or any hardcoded symbol.
      showBanner('Реестр инструментов пуст — торговля недоступна.', 'danger');
      return null;
    }
    for (const i of items) {
      const opt = el('option', null, `${i.contract_code} (${i.venue} ${i.market_type})`);
      opt.value = i.instrument_key;
      instSel.appendChild(opt);
    }
    // G4 (§39): restore the URL-selected instrument when it is a valid, active
    // registry entry; otherwise fall back to the first active one (never a
    // hardcoded symbol, never a spot key).
    const want = urlState.get('instrument');
    const chosen = (want && items.find((i) => i.instrument_key === want)) || items[0];
    instSel.value = chosen.instrument_key;
    return chosen;
  }

  function populatePeriods(spec) {
    if (!perSel) return;
    const allowed = spec.supported_periods || [];
    const set = new Set(allowed);
    perSel.replaceChildren();
    for (const p of PERIODS_UI) {
      if (!set.has(p.v)) continue;
      const opt = el('option', null, p.label);
      opt.value = p.v;
      perSel.appendChild(opt);
    }
    // G4 (§39): honour ?period= only if it is a supported period for THIS
    // instrument; an incompatible value falls back to the default, never an
    // unsupported timeframe.
    const wantP = urlState.get('period');
    if (wantP && set.has(wantP)) {
      currentPeriod = wantP;
    } else {
      currentPeriod = allowed.includes(DEFAULT_PERIOD) ? DEFAULT_PERIOD : (allowed[0] || DEFAULT_PERIOD);
    }
    perSel.value = currentPeriod;
  }

  // applyUrlSelections restores horizon and the positions filters before the
  // first load.  Only values that exist as options are accepted; anything else
  // keeps the template default.  instrument/period are handled in their loaders
  // because they depend on the fetched registry.
  function applyUrlSelections() {
    if (horizonSel) {
      const wantH = urlState.get('horizon');
      const opts = Array.from(horizonSel.options).map((o) => o.value);
      if (wantH && opts.includes(wantH)) {
        currentHorizon = wantH;
        horizonSel.value = wantH;
      }
    }
    if (posStatusSel) {
      const wantS = urlState.get('status');
      const opts = Array.from(posStatusSel.options).map((o) => o.value);
      if (wantS && opts.includes(wantS)) {
        positionsStatusFilter = wantS;
        posStatusSel.value = wantS;
      }
    }
    if (posAccountSel) {
      const wantA = urlState.get('account');
      const opts = Array.from(posAccountSel.options).map((o) => o.value);
      if (wantA && opts.includes(wantA)) {
        positionsAccountFilter = wantA;
        posAccountSel.value = wantA;
      }
    }
  }

  async function loadCandles() {
    if (!currentInstrument) return;
    const url = `/api/v3/market/${encodeURIComponent(currentInstrument)}/candles?period=${encodeURIComponent(currentPeriod)}&limit=${DEFAULT_CANDLE_LIMIT}`;
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('candles HTTP ' + r.status);
      const data = await r.json();
      const candles = data.candles || [];
      if (candles.length) {
        const t0 = Math.floor(Date.parse(candles[0].start_at) / 1000);
        const t1 = Math.floor(Date.parse(candles[candles.length - 1].end_at || candles[candles.length - 1].start_at) / 1000);
        candleTimeRange = { min: Math.min(t0, t1), max: Math.max(t0, t1) };
      } else {
        candleTimeRange = null;
      }
      if (chart) {
        const last = candles.length ? candles[candles.length - 1] : null;
        chart.setData(candles, { last_candle_time: last ? Math.floor(Date.parse(last.start_at) / 1000) : null });
        if (lastState) chart.applyState(lastState);
      }
      setStatusLine(statusLine, {
        instrument: currentInstrument,
        period: currentPeriod,
        candles: candles.length,
        derivedFrom: data.derived_from,
        gaps: (data.gaps || []).length,
        sourceStatus: data.source_status,
      });
      // P1.3: forming-period state + explicit gap list (facts from the API,
      // never a synthesized candle).
      formingMeta = data.forming || null;
      renderForming(formingLine, formingMeta, lastState && lastState.quality);
      renderGaps(gapsBox, data.gaps);
      if (chartBox) {
        chartBox.dataset.formingState = formingMeta ? formingMeta.state : 'unknown';
        chartBox.dataset.gapCount = String((data.gaps || []).length);
      }
      if (formingTimer) { clearInterval(formingTimer); formingTimer = null; }
      if (formingMeta && formingMeta.start_at) {
        formingTimer = setInterval(() => {
          renderForming(formingLine, formingMeta, lastState && lastState.quality);
        }, 1000);
      }
      if (candles.length === 0) {
        showBanner('История свечей пуста — график не рисуется (источник: ' + (data.source_status || 'unknown') + ').', 'warn');
      }
    } catch (e) {
      formingMeta = null;
      if (formingTimer) { clearInterval(formingTimer); formingTimer = null; }
      renderForming(formingLine, null, null);
      renderGaps(gapsBox, []);
      setStatusLine(statusLine, { instrument: currentInstrument, period: currentPeriod });
      showBanner('Не удалось загрузить свечи: ' + e.message, 'danger');
    }
  }

  function _setForecastDomHook(bars, validOhlc, inWindow) {
    // Testable DOM contract for the browser evidence (MC-05/06/07): the chart
    // container carries what the overlay drew without needing to read the canvas.
    chartBox.dataset.forecastBars = String(bars);
    chartBox.dataset.forecastRender = 'line-band';
    chartBox.dataset.forecastValidOhlc = String(validOhlc);
    if (inWindow != null) chartBox.dataset.forecastInWindow = String(inWindow);
  }

  async function loadForecast() {
    if (!currentInstrument) return;
    if (!forecastOn) {
      if (chart) chart.setForecast(null);
      _setForecastDomHook(0, 'false', null);
      renderForecastStatus(forecastStatus, null, { error: 'оверлей выключен' });
      if (forecastLegend) forecastLegend.hidden = true;
      return;
    }
    const url = `/api/v3/forecasts?instrument_key=${encodeURIComponent(currentInstrument)}&horizon=${encodeURIComponent(currentHorizon)}&period=${encodeURIComponent(currentPeriod)}&limit=1`;
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (r.status === 400) {
        let detail = {};
        try { detail = (await r.json()).detail || {}; } catch (_) { /* ignore */ }
        if (chart) chart.setForecast(null);
        _setForecastDomHook(0, 'false', null);
        const sug = (detail.suggested_periods || []).join('/') || '—';
        renderForecastStatus(forecastStatus, null, { error: `горизонт ${currentHorizon} несовместим с периодом ${currentPeriod} (попробуйте ${sug})` });
        if (forecastLegend) forecastLegend.hidden = true;
        return;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const pred = (data.forecasts && data.forecasts[0]) || null;
      const intervals = (pred && pred.intervals) || [];
      if (!pred || pred.error_code || intervals.length === 0) {
        if (chart) chart.setForecast(null);
        _setForecastDomHook(0, 'false', null);
        renderForecastStatus(forecastStatus, pred, {});
        if (forecastLegend) forecastLegend.hidden = true;
        return;
      }
      const drawn = chart ? chart.setForecast(pred) : 0;
      // forecast-vs-fact (MC-07): is any predicted bar's window already covered
      // by the loaded fact candles?  Uses the same validated timestamps.
      let inWindow = false;
      if (candleTimeRange) {
        for (const iv of intervals) {
          const t = Math.floor(Date.parse(iv.target_end_at) / 1000);
          if (Number.isFinite(t) && t >= candleTimeRange.min && t <= candleTimeRange.max) { inWindow = true; break; }
        }
      }
      // Every interval from the read-model is line+band, never a candle (MC-06).
      _setForecastDomHook(drawn, false, inWindow);
      renderForecastStatus(forecastStatus, pred, { inWindow });
      if (forecastLegend) forecastLegend.hidden = false;
    } catch (e) {
      if (chart) chart.setForecast(null);
      _setForecastDomHook(0, 'false', null);
      renderForecastStatus(forecastStatus, null, { error: 'не удалось загрузить: ' + e.message });
      if (forecastLegend) forecastLegend.hidden = true;
    }
  }

  async function loadAccuracy() {
    if (!currentInstrument) return;
    const url = `/api/v3/forecasts/accuracy?instrument_key=${encodeURIComponent(currentInstrument)}&horizon=${encodeURIComponent(currentHorizon)}&period=${encodeURIComponent(currentPeriod)}&limit=20`;
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (r.status === 400) {
        renderAccuracy(forecastAccuracy, { verdict: 'N/A', sample_n: 0, min_sample: 5, reason: 'incompatible_horizon_period' });
        return;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      renderAccuracy(forecastAccuracy, data.holdout);
    } catch (e) {
      if (forecastAccuracy) {
        forecastAccuracy.textContent = 'Точность: недоступно (' + e.message + ')';
        forecastAccuracy.dataset.accuracyVerdict = 'error';
      }
    }
  }

  // ── P5.3b positions dashboard (MC-17) ──────────────────────────────────────
  // Renders the four buckets the ТЗ §34 names (open / pending / closed /
  // liquidated) and hands the cards to the chart so the table and the overlay
  // show the SAME ids and levels.  Only safe DOM is used — a card value is
  // never injected as HTML.
  function _row(cells) {
    const tr = el('tr');
    for (const c of cells) tr.appendChild(el('td', null, c));
    return tr;
  }

  function _renderBucket(container, headers, rows) {
    if (!container) return;
    container.replaceChildren();
    const table = el('table', 'v3-positions-table');
    const thead = el('thead');
    const hrow = el('tr');
    for (const h of headers) hrow.appendChild(el('th', null, h));
    thead.appendChild(hrow);
    table.appendChild(thead);
    const tbody = el('tbody');
    if (!rows.length) {
      const tr = el('tr', 'v3-positions-emptyrow');
      const td = el('td', 'v3-positions-empty');
      td.colSpan = headers.length;
      td.textContent = '—';
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      for (const r of rows) tbody.appendChild(_row(r));
    }
    table.appendChild(tbody);
    container.appendChild(table);
  }

  const _sign = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + v);

  function _renderPositions(data) {
    const cards = data.positions || [];
    const open = cards.filter((p) => p.status === 'open');
    const closed = cards.filter((p) => p.status === 'closed');
    const liq = cards.filter((p) => p.status === 'liquidated');
    const pending = data.pending || [];

    _renderBucket(posTables.open, ['id', 'acct', 'side', 'qty', 'entry', 'mark', 'uPnL net', 'stop', 'target', 'liq'],
      open.map((p) => [String(p.position_id).slice(0, 6), p.account_kind, p.side, fmt(p.qty),
        fmt(p.entry_price), fmt(p.mark), fmt(_sign(p.unrealized_net)), fmt(p.stop_loss),
        fmt(p.take_profit), fmt(p.liquidation_price)]));
    _renderBucket(posTables.pending, ['order', 'acct', 'side', 'qty', 'price', 'state'],
      pending.map((p) => [String(p.order_id).slice(0, 6), p.account_kind, p.side, fmt(p.qty),
        fmt(p.entry_price), p.order_state]));
    _renderBucket(posTables.closed, ['id', 'acct', 'side', 'qty', 'entry', 'exit', 'realized net', 'reason'],
      closed.map((p) => [String(p.position_id).slice(0, 6), p.account_kind, p.side, fmt(p.qty),
        fmt(p.entry_price), fmt(p.close_price), fmt(_sign(p.realized_net_pnl)), fmt(p.exit_reason) || 'manual']));
    _renderBucket(posTables.liquidated, ['id', 'acct', 'side', 'qty', 'entry', 'exit', 'realized net', 'reason'],
      liq.map((p) => [String(p.position_id).slice(0, 6), p.account_kind, p.side, fmt(p.qty),
        fmt(p.entry_price), fmt(p.close_price), fmt(_sign(p.realized_net_pnl)), fmt(p.exit_reason) || 'liquidation']));

    // Chart overlays use every position card, so ids/levels match the tables.
    let drawn = { lines: 0, markers: 0, liquidation: 0 };
    if (chart && typeof chart.setPositionOverlays === 'function') {
      drawn = chart.setPositionOverlays(cards) || drawn;
    }

    // Testable DOM contract for browser evidence (MC-17) — no canvas parsing.
    chartBox.dataset.positionCount = String(cards.length + pending.length);
    chartBox.dataset.positionOpen = String(open.length);
    chartBox.dataset.positionLiquidation = String(liq.length);
    chartBox.dataset.positionPending = String(pending.length);
    chartBox.dataset.positionPriceLines = String(drawn.lines);
    chartBox.dataset.positionMarkers = String(drawn.markers);
    chartBox.dataset.positionLiquidationLines = String(drawn.liquidation);
    chartBox.dataset.positionSourceStatus = data.source_status || 'ok';

    if (posUpdated) {
      const bits = [`${cards.length} позиций`, `${pending.length} в ожидании`];
      if (data.mark != null) bits.push(`mark ${fmt(data.mark)}`);
      if (data.mark_status && data.mark_status !== 'live') bits.push(`mark: ${data.mark_status}`);
      bits.push('обновлено ' + (data.as_of || ''));
      posUpdated.textContent = bits.join(' · ');
    }
  }

  async function loadPositions() {
    if (!currentInstrument) return;
    if (!posTables.open && !posTables.pending && !posTables.closed && !posTables.liquidated) return;
    const url = `/api/v3/positions?status=${encodeURIComponent(positionsStatusFilter)}`
      + `&account=${encodeURIComponent(positionsAccountFilter)}&limit=50`;
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (r.status === 503) {
        chartBox.dataset.positionSourceStatus = 'error';
        if (posUpdated) posUpdated.textContent = 'Позиции: источник недоступен (503)';
        return;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      // The API is owner-scoped server-side; filter the visible instrument only.
      data.positions = (data.positions || []).filter((p) => p.instrument === (currentInstrument.split(':').pop()));
      data.pending = (data.pending || []).filter((p) => p.instrument === (currentInstrument.split(':').pop()));
      _renderPositions(data);
    } catch (e) {
      chartBox.dataset.positionSourceStatus = 'error';
      if (posUpdated) posUpdated.textContent = 'Позиции: не удалось загрузить (' + e.message + ')';
    }
  }

  function openStream() {
    if (eventSource) {
      try { eventSource.close(); } catch (_) { /* ignore */ }
      eventSource = null;
    }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
    if (!currentInstrument) return;
    lastSnapshotAt = 0;
    if (chartBox) chartBox.dataset.streamMode = 'sse';
    const url = `/api/v3/market/${encodeURIComponent(currentInstrument)}/stream`;
    eventSource = new EventSource(url, { withCredentials: true });
    eventSource.addEventListener('snapshot', (ev) => {
      try {
        const state = JSON.parse(ev.data);
        lastSnapshotAt = Date.now();
        applySnapshot(state);
        // The live stream is healthy again — stop any polling fallback.
        if (eventSource && eventSource.readyState === EventSource.OPEN) stopPollFallback();
      } catch (e) {
        showBanner('Повреждённый кадр в потоке: ' + e.message, 'danger');
      }
    });
    eventSource.addEventListener('error', (ev) => {
      // This is a server-emitted error frame, distinct from EventSource's own
      // transport error.  Reason code is in the JSON payload.
      try {
        const data = JSON.parse(ev.data);
        showBanner('Источник данных: ' + (data.reason_code || 'error'), 'danger');
      } catch (_) {
        showBanner('Источник данных недоступен.', 'danger');
      }
    });
    eventSource.addEventListener('bye', () => {
      // Session cap reached.  EventSource auto-reconnects; nothing to do.
    });
    // Transport-level error (network drop) — EventSource keeps reconnecting and
    // resends Last-Event-ID (server tags snapshots with id: <sequence>).  Start
    // polling /state right away so the UI keeps moving until the socket returns.
    eventSource.onerror = () => {
      showBanner('Поток прерван, переподключение…', 'warn');
      startPollFallback();
    };
    // Watchdog: even without a hard onerror, a silently stalled feed (no
    // snapshot for STREAM_STALE_MS) trips the polling fallback, and a recovered
    // open socket stops it.  This is the §53 backoff/resync behaviour.
    watchdogTimer = setInterval(() => {
      const stalled = Date.now() - lastSnapshotAt > STREAM_STALE_MS;
      const open = eventSource && eventSource.readyState === EventSource.OPEN;
      if (stalled) startPollFallback();
      else if (open && lastSnapshotAt > 0) stopPollFallback();
    }, WATCHDOG_INTERVAL_MS);
  }

  async function selectInstrument(spec) {
    if (!spec) return;
    currentInstrument = spec.instrument_key;
    populatePeriods(spec);
    await loadCandles();
    await loadForecast();
    await loadAccuracy();
    await loadPositions();
    openStream();
    syncUrl();
  }

  instSel.addEventListener('change', async () => {
    const key = instSel.value;
    try {
      const r = await fetch('/api/v3/instruments', { credentials: 'same-origin' });
      const data = await r.json();
      const spec = (data.instruments || []).find((i) => i.instrument_key === key);
      await selectInstrument(spec);
    } catch (e) {
      showBanner('Не переключить инструмент: ' + e.message, 'danger');
    }
  });
  if (perSel) {
    perSel.addEventListener('change', async () => {
      currentPeriod = perSel.value || DEFAULT_PERIOD;
      await loadCandles();
      await loadForecast();
      await loadAccuracy();
      syncUrl();
    });
  }
  if (horizonSel) {
    currentHorizon = horizonSel.value || DEFAULT_HORIZON;
    horizonSel.addEventListener('change', async () => {
      currentHorizon = horizonSel.value || DEFAULT_HORIZON;
      await loadForecast();
      await loadAccuracy();
      syncUrl();
    });
  }
  if (forecastToggle) {
    forecastOn = forecastToggle.checked;
    forecastToggle.addEventListener('change', async () => {
      forecastOn = forecastToggle.checked;
      await loadForecast();
    });
  }
  if (posStatusSel) {
    positionsStatusFilter = posStatusSel.value || 'all';
    posStatusSel.addEventListener('change', async () => {
      positionsStatusFilter = posStatusSel.value || 'all';
      await loadPositions();
      syncUrl();
    });
  }
  if (posAccountSel) {
    positionsAccountFilter = posAccountSel.value || 'all';
    posAccountSel.addEventListener('change', async () => {
      positionsAccountFilter = posAccountSel.value || 'all';
      await loadPositions();
      syncUrl();
    });
  }

  // Bootstrap
  (async function boot() {
    try {
      applyUrlSelections();
      const first = await loadInstruments();
      await selectInstrument(first);
    } catch (e) {
      showBanner('V3 market недоступен: ' + e.message, 'danger');
    }
  })();

  return {
    destroy() {
      if (eventSource) { try { eventSource.close(); } catch (_) { /* ignore */ } }
      if (formingTimer) { clearInterval(formingTimer); formingTimer = null; }
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
      if (chart) chart.destroy();
    },
  };
}

if (typeof document !== 'undefined') {
  const start = () => {
    document.querySelectorAll('[data-v3-market-root]').forEach((root) => {
      if (root.dataset.v3Mounted === '1') return;
      root.dataset.v3Mounted = '1';
      mountV3Market(root);
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
}
