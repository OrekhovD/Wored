/**
 * WORED V3 Market Chart — P1.2b
 *
 * Thin wrapper around Lightweight Charts 5.2.0 (already loaded via CDN in
 * base.html).  Owns the candlestick + volume + optional mark-price line for
 * the /workspace market section.  Deliberately does NOT invent a forming
 * candle: only rows the backend has validated as closed are drawn.
 *
 * Public API:
 *   mountMarketChart(container, { height }) -> controller
 *   controller.setData(candles, meta)       — replace the full series
 *   controller.applyState(state)            — update mark line + status text
 *   controller.setForecast(prediction)      — draw the forecast as line + band
 *   controller.destroy()                    — release LWC instance
 *
 * Data contract (see webui/market_workspace.py):
 *   candle = { start_at, end_at, open, high, low, close, volume }
 *   state  = MarketStateV1 with .quote.mark and .capabilities.can_enter
 *
 * Forecast contract (see webui/forecast_workspace.py, ForecastIntervalPredictionV1):
 *   prediction = { intervals: [{ target_end_at, predicted_close, band_low,
 *                                band_high, has_valid_ohlc: false, … }], … }
 *   The ensemble path has no proven intra-candle extreme (has_valid_ohlc is
 *   always false), so MC-06 forbids drawing it as a candlestick: we render the
 *   median as one line and the q10..q90 interval as two band-edge lines.
 */

/**
 * Create a series across Lightweight Charts major versions.
 *   * v5 (what base.html pins at 5.2.0): chart.addSeries(SeriesDefinition, opts)
 *   * v4:                                 the per-type "add …Series" helpers.
 * A CDN version bump must never silently blank the chart, so try v5 first and
 * fall back to the v4 method name.
 */
function _addSeries(chart, name, options) {
  if (typeof chart.addSeries === 'function') {
    const def =
      name === 'Candlestick' ? LightweightCharts.CandlestickSeries
        : name === 'Histogram' ? LightweightCharts.HistogramSeries
          : LightweightCharts.LineSeries;
    if (def) return chart.addSeries(def, options);
  }
  return chart['add' + name + 'Series'](options);
}

export function mountMarketChart(container, { height = 320 } = {}) {
  if (!container) return null;
  if (typeof LightweightCharts === 'undefined') {
    container.replaceChildren();
    const div = document.createElement('div');
    div.className = 'v3-market-empty';
    div.textContent = 'Библиотека Lightweight Charts не загрузилась.';
    container.appendChild(div);
    return null;
  }

  const isMobile = window.innerWidth < 768;
  const chartHeight = isMobile ? Math.min(height, 260) : height;

  const chart = LightweightCharts.createChart(container, {
    height: chartHeight,
    width: container.clientWidth || 640,
    layout: {
      background: { color: 'transparent' },
      textColor: '#a3a3a3',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: 'rgba(64,64,64,0.15)' },
      horzLines: { color: 'rgba(64,64,64,0.15)' },
    },
    rightPriceScale: {
      borderColor: 'rgba(64,64,64,0.3)',
      // Give the candle body vertical room so the price-axis tags (Mark, entry,
      // forecast) don't pile up against the top/bottom edges.
      scaleMargins: { top: 0.1, bottom: 0.15 },
    },
    timeScale: {
      borderColor: 'rgba(64,64,64,0.3)',
      timeVisible: true,
      secondsVisible: false,
      // Default rightOffset (60 bars) left a wide dead margin right of the last
      // candle; 15 keeps just enough future room for the forecast band.
      rightOffset: 15,
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const candleSeries = _addSeries(chart, 'Candlestick', {
    upColor: '#22c55e',
    downColor: '#ef4444',
    borderUpColor: '#22c55e',
    borderDownColor: '#ef4444',
    wickUpColor: '#22c55e',
    wickDownColor: '#ef4444',
    priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
  });

  const volumeSeries = _addSeries(chart, 'Histogram', {
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    color: 'rgba(59,130,246,0.35)',
    // Volume is a background histogram; its own price-axis tag only adds noise.
    priceLineVisible: false,
    lastValueVisible: false,
  });
  chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  // Horizontal line for mark_price — set via a single-point line series so it
  // shows as an overlay, not a fabricated candle.  We clear it on empty state.
  const markSeries = _addSeries(chart, 'Line', {
    color: '#f97316',
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: true,
    lastValueVisible: true,
    title: 'Mark',
  });

  // Forecast overlay is created lazily on the first non-empty prediction so an
  // idle chart stays lean.  It is ALWAYS a line (+ band edges), never a candle:
  // MC-06. Blue accent for the median path, translucent blue for the interval.
  let forecastMid = null;
  let forecastBandHi = null;
  let forecastBandLo = null;
  function _ensureForecastSeries() {
    if (forecastMid) return;
    forecastMid = _addSeries(chart, 'Line', {
      color: '#3b82f6',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'Прогноз',
    });
    // Band edges stay as dotted guide lines but carry NO price-axis tag: with
    // q90/q10/Mark/entry/forecast all landing in a narrow price band their
    // labels overlapped into an unreadable stack. The band edges are already
    // explained by the HTML legend ("полоса q10–q90").
    const bandOpts = () => ({
      color: 'rgba(59,130,246,0.45)',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    forecastBandHi = _addSeries(chart, 'Line', bandOpts());
    forecastBandLo = _addSeries(chart, 'Line', bandOpts());
  }
  function _clearForecast() {
    if (forecastMid) { forecastMid.setData([]); forecastBandHi.setData([]); forecastBandLo.setData([]); }
  }

  let currentMeta = null;

  function toUnixSec(iso) {
    const ms = Date.parse(iso);
    if (Number.isNaN(ms)) return null;
    return Math.floor(ms / 1000);
  }

  function setData(candles, meta = null) {
    currentMeta = meta;
    const cs = [];
    const vs = [];
    for (const c of candles) {
      const t = toUnixSec(c.start_at);
      if (t === null) continue; // skip rows the backend marked invalid
      const o = Number(c.open), h = Number(c.high), l = Number(c.low), cl = Number(c.close);
      if (!Number.isFinite(o) || !Number.isFinite(h) || !Number.isFinite(l) || !Number.isFinite(cl)) continue;
      cs.push({ time: t, open: o, high: h, low: l, close: cl });
      const v = Number(c.volume);
      if (Number.isFinite(v) && v >= 0) {
        vs.push({ time: t, value: v, color: cl >= o ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)' });
      }
    }
    candleSeries.setData(cs);
    volumeSeries.setData(vs);
    chart.timeScale().fitContent();
  }

  function applyState(state) {
    if (!state) return;
    // Anchor mark line on the last candle time we have; if no candles, use
    // the state.as_of timestamp truncated to seconds.  Do not fabricate two
    // points — one point is fine, LWC will render it as a horizontal line.
    const mark = state.quote && state.quote.mark != null ? Number(state.quote.mark) : null;
    const anchorTime =
      (currentMeta && currentMeta.last_candle_time) ||
      toUnixSec(state.as_of) ||
      Math.floor(Date.now() / 1000);
    if (mark !== null && Number.isFinite(mark)) {
      markSeries.setData([{ time: anchorTime, value: mark }]);
    } else {
      markSeries.setData([]);
    }
  }

  function setForecast(prediction) {
    // MC-06: a forecast is drawn as a median line + q10/q90 band edges.  We
    // never build a candlestick from it, and we drop any interval whose
    // timestamp or value the backend did not validate.
    const intervals = prediction && Array.isArray(prediction.intervals) ? prediction.intervals : [];
    if (intervals.length === 0) {
      _clearForecast();
      try { chart.timeScale().fitContent(); } catch (_) { /* ignore */ }
      return 0;
    }
    _ensureForecastSeries();
    const mid = [];
    const hi = [];
    const lo = [];
    for (const iv of intervals) {
      const t = toUnixSec(iv.target_end_at);
      if (t === null) continue;
      const m = Number(iv.predicted_close);
      const bh = Number(iv.band_high);
      const bl = Number(iv.band_low);
      if (Number.isFinite(m)) mid.push({ time: t, value: m });
      if (Number.isFinite(bh)) hi.push({ time: t, value: bh });
      if (Number.isFinite(bl)) lo.push({ time: t, value: bl });
    }
    forecastMid.setData(mid);
    forecastBandHi.setData(hi);
    forecastBandLo.setData(lo);
    // Re-fit so the future forecast band sits snugly in view for any horizon,
    // instead of relying on a fixed right margin (which left dead space on
    // short horizons and clipped the band on long ones).
    try { chart.timeScale().fitContent(); } catch (_) { /* ignore */ }
    return mid.length;
  }

  function destroy() {
    try { chart.remove(); } catch (_) { /* ignore */ }
  }

  // ── P5.3b position overlays (MC-17) ────────────────────────────────────────
  // Levels (entry / stop / target / liquidation) are horizontal price lines on
  // the candle series; events (open / close / liquidation) are time markers.
  // Every value is read straight from the position card — nothing here derives
  // a number the backend did not already record.  Version-tolerant: LWC v5 uses
  // createSeriesMarkers, v4 uses setMarkers; a missing API must not blank the
  // chart, so all overlay calls are guarded.
  let priceLines = [];
  let markerHandle = null;

  function _clearPositionOverlays() {
    for (const pl of priceLines) {
      try { candleSeries.removePriceLine(pl); } catch (_) { /* ignore */ }
    }
    priceLines = [];
    try {
      if (typeof candleSeries.setMarkers === 'function') candleSeries.setMarkers([]);
      else if (markerHandle && typeof markerHandle.setMarkers === 'function') markerHandle.setMarkers([]);
    } catch (_) { /* ignore */ }
    markerHandle = null;
  }

  function _addPriceLine(price, color, title, style) {
    if (price == null) return false;
    const n = Number(price);
    if (!Number.isFinite(n)) return false;
    try {
      priceLines.push(candleSeries.createPriceLine({
        price: n, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title,
      }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function setPositionOverlays(positions) {
    _clearPositionOverlays();
    const list = Array.isArray(positions) ? positions : [];
    const markers = [];
    let liq = 0;
    for (const p of list) {
      const short = (id) => (id ? String(id).slice(0, 6) : '—');
      if (p.kind === 'position' && p.status === 'open') {
        _addPriceLine(p.entry_price, '#f97316', 'entry', LightweightCharts.LineStyle.Solid);
        _addPriceLine(p.stop_loss, '#ef4444', 'stop', LightweightCharts.LineStyle.Dashed);
        _addPriceLine(p.take_profit, '#22c55e', 'target', LightweightCharts.LineStyle.Dashed);
        if (_addPriceLine(p.liquidation_price, '#b91c1c', 'liquidation', LightweightCharts.LineStyle.Dotted)) {
          liq += 1;
        }
        const t = toUnixSec(p.opened_at);
        if (t !== null) {
          markers.push({
            time: t, position: 'belowBar', color: '#f97316', shape: 'arrowUp',
            text: (p.side === 'long' ? 'L' : 'S') + ' #' + short(p.position_id),
          });
        }
      } else if (p.kind === 'position' && (p.status === 'closed' || p.status === 'liquidated')) {
        const t = toUnixSec(p.closed_at);
        if (t !== null) {
          const isLiq = p.status === 'liquidated';
          markers.push({
            time: t, position: 'aboveBar', color: isLiq ? '#b91c1c' : '#a3a3a3',
            shape: 'arrowDown', text: (isLiq ? 'LIQ' : 'X') + ' #' + short(p.position_id),
          });
        }
      }
    }
    try {
      if (typeof candleSeries.setMarkers === 'function') candleSeries.setMarkers(markers);
      else if (typeof candleSeries.createSeriesMarkers === 'function') markerHandle = candleSeries.createSeriesMarkers(markers);
    } catch (_) { /* ignore */ }
    return { lines: priceLines.length, markers: markers.length, liquidation: liq };
  }

  // Auto-resize on window change (small screen flip, side panel collapse).
  const ro = new ResizeObserver(() => {
    try { chart.applyOptions({ width: container.clientWidth }); } catch (_) { /* ignore */ }
  });
  ro.observe(container);

  return { setData, applyState, setForecast, setPositionOverlays, destroy };
}
