/**
 * trader-chart.js — WORED Trader Deck chart initialization
 *
 * Uses lightweight-charts v4.2.0 API:
 *   - CandlestickSeries for price (pane 0)
 *   - HistogramSeries for volume (pane 1)
 *   - LineSeries for MACD dif/dea + histogram (pane 2)
 *   - LineSeries for KDJ K/D/J (pane 3)
 *   - Forecast candles as semi-transparent overlay
 *   - q90/q10 quantile bands as dashed lines
 *   - MA/EMA/BOLL/SAR overlay toggles
 *   - Position markers (arrows for entries, circles/squares for exits)
 *   - Entry/TP/SL/LIQ price lines for open positions
 *   - Crosshair tooltip with OHLCV data
 *   - Responsive resize
 */
(function () {
  'use strict';

  var LWC = window.LightweightCharts;
  if (!LWC) {
    console.error('[trader-chart] lightweight-charts not loaded');
    return;
  }

  var UP = '#22c55e';
  var DOWN = '#ef4444';
  var ACCENT = '#f97316';
  var INFO = '#3b82f6';
  var WARN = '#eab308';

  var chart, candles, fcSeries, q90Series, q10Series;
  var volSeries, macdHist, macdDif, macdDea, kSeries, dSeries, jSeries;
  var overlayState = {};
  var chartData = { bars: [], forecast: [], positions: [] };

  // ── indicator math ──────────────────────────────────────────────
  function sma(a, n) {
    var o = new Array(a.length).fill(null);
    var s = 0;
    for (var i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; if (i >= n - 1) o[i] = s / n; }
    return o;
  }
  function ema(a, n) {
    var o = new Array(a.length).fill(null);
    var k = 2 / (n + 1);
    var e = null;
    for (var i = 0; i < a.length; i++) {
      if (i === n - 1) { e = a.slice(0, n).reduce(function (x, y) { return x + y; }, 0) / n; o[i] = e; }
      else if (i >= n) { e = a[i] * k + e * (1 - k); o[i] = e; }
    }
    return o;
  }
  function boll(a, n, m) {
    n = n || 20; m = m || 2;
    var mid = sma(a, n), up = [], dn = [];
    for (var i = 0; i < a.length; i++) {
      if (mid[i] == null) { up.push(null); dn.push(null); continue; }
      var v = 0;
      for (var j = i - n + 1; j <= i; j++) v += Math.pow(a[j] - mid[i], 2);
      var sd = Math.sqrt(v / n);
      up.push(mid[i] + m * sd); dn.push(mid[i] - m * sd);
    }
    return { mid: mid, up: up, dn: dn };
  }
  function sar(b, step, max) {
    step = step || 0.02; max = max || 0.2;
    var o = new Array(b.length).fill(null);
    if (b.length < 2) return o;
    var up = b[1].close >= b[0].close, af = step, ep = up ? b[0].high : b[0].low, s = up ? b[0].low : b[0].high;
    for (var i = 1; i < b.length; i++) {
      s = s + af * (ep - s);
      if (up) {
        s = Math.min(s, b[i - 1].low, i > 1 ? b[i - 2].low : b[i - 1].low);
        if (b[i].low < s) { up = false; s = ep; ep = b[i].low; af = step; }
        else if (b[i].high > ep) { ep = b[i].high; af = Math.min(af + step, max); }
      } else {
        s = Math.max(s, b[i - 1].high, i > 1 ? b[i - 2].high : b[i - 1].high);
        if (b[i].high > s) { up = true; s = ep; ep = b[i].high; af = step; }
        else if (b[i].low < ep) { ep = b[i].low; af = Math.min(af + step, max); }
      }
      o[i] = s;
    }
    return o;
  }
  function macd(a) {
    var e12 = ema(a, 12), e26 = ema(a, 26);
    var dif = a.map(function (_, i) { return e12[i] != null && e26[i] != null ? e12[i] - e26[i] : null; });
    var start = dif.findIndex(function (v) { return v != null; });
    var dea = new Array(a.length).fill(null);
    if (start >= 0) { var tail = ema(dif.slice(start), 9); tail.forEach(function (v, i) { dea[start + i] = v; }); }
    var hist = dif.map(function (v, i) { return v != null && dea[i] != null ? v - dea[i] : null; });
    return { dif: dif, dea: dea, hist: hist };
  }
  function kdj(b, n) {
    n = n || 9;
    var K = [], D = [], J = [];
    var k = 50, d = 50;
    for (var i = 0; i < b.length; i++) {
      if (i < n - 1) { K.push(null); D.push(null); J.push(null); continue; }
      var hh = -Infinity, ll = Infinity;
      for (var j = i - n + 1; j <= i; j++) { hh = Math.max(hh, b[j].high); ll = Math.min(ll, b[j].low); }
      var rsv = hh > ll ? (b[i].close - ll) / (hh - ll) * 100 : 50;
      k = 2 / 3 * k + rsv / 3; d = 2 / 3 * d + k / 3;
      K.push(k); D.push(d); J.push(3 * k - 2 * d);
    }
    return { K: K, D: D, J: J };
  }
  function computeAll(b) {
    var c = b.map(function (x) { return x.close; });
    return {
      ma7: sma(c, 7), ma25: sma(c, 25), ma99: sma(c, 99),
      ema7: ema(c, 7), ema25: ema(c, 25), ema99: ema(c, 99),
      boll: boll(c), sar: sar(b),
      macd: macd(c), kdj: kdj(b),
      vol5: sma(b.map(function (x) { return x.volume; }), 5)
    };
  }

  // ── formatters ──────────────────────────────────────────────────
  function fmt(v, d) { d = d == null ? 1 : d; return v == null || !isFinite(v) ? '—' : v.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function fmtUsd(v, d) { d = d == null ? 2 : d; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d); }
  function pct(v, d) { d = d == null ? 2 : d; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v * 100).toFixed(d) + '%'; }
  function hhmm(t) { var d = new Date(t * 1000 + 7 * 3600 * 1000); return d.toISOString().slice(11, 16); }
  function ddhh(t) { var s = new Date(t * 1000 + 7 * 3600 * 1000).toISOString(); return s.slice(8, 10) + '.' + s.slice(5, 7) + ' ' + s.slice(11, 16); }

  // ── chart initialization ────────────────────────────────────────
  function initChart() {
    var wrap = document.getElementById('trChartWrap');
    var container = document.getElementById('trChart');
    if (!container) return;

    chart = LWC.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: '#0e0e0e' },
        textColor: '#a3a3a3',
        fontFamily: "'Cascadia Code','JetBrains Mono',Consolas,monospace",
        fontSize: 11
      },
      grid: { vertLines: { color: 'rgba(255,255,255,0.035)' }, horzLines: { color: 'rgba(255,255,255,0.035)' } },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false, rightOffset: 3, barSpacing: 9 },
      crosshair: { mode: LWC.CrosshairMode.Normal },
      localization: { priceFormatter: function (p) { return p.toLocaleString('ru-RU', { maximumFractionDigits: 1 }); } }
    });

    // Pane 0: Candlesticks (LWC v5 API)
    candles = chart.addSeries(LWC.CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderVisible: false,
      wickUpColor: UP, wickDownColor: DOWN, priceLineColor: INFO
    });

    // Pane 1: Volume histogram
    volSeries = chart.addSeries(LWC.HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1);

    // Pane 2: MACD
    macdHist = chart.addSeries(LWC.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 2);
    macdDif = chart.addSeries(LWC.LineSeries, { color: '#e5e5e5', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 2);
    macdDea = chart.addSeries(LWC.LineSeries, { color: ACCENT, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 2);

    // Pane 3: KDJ
    kSeries = chart.addSeries(LWC.LineSeries, { color: '#e5e5e5', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 3);
    dSeries = chart.addSeries(LWC.LineSeries, { color: WARN, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 3);
    jSeries = chart.addSeries(LWC.LineSeries, { color: '#c084fc', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 3);

    // Forecast overlay (semi-transparent candles)
    fcSeries = chart.addSeries(LWC.CandlestickSeries, {
      upColor: 'rgba(34,197,94,0.22)', downColor: 'rgba(239,68,68,0.22)',
      borderVisible: true, borderUpColor: 'rgba(34,197,94,0.9)', borderDownColor: 'rgba(239,68,68,0.9)',
      wickUpColor: 'rgba(34,197,94,0.75)', wickDownColor: 'rgba(239,68,68,0.75)',
      priceLineVisible: false, lastValueVisible: false
    });

    // q90/q10 quantile bands (dashed lines)
    var bandOpt = { color: 'rgba(249,115,22,0.75)', lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    q90Series = chart.addSeries(LWC.LineSeries, bandOpt);
    q10Series = chart.addSeries(LWC.LineSeries, bandOpt);

    // Crosshair tooltip
    chart.subscribeCrosshairMove(function (p) {
      var L = document.getElementById('trLegend');
      if (!L) return;
      if (!p || !p.time) { renderLegend(); return; }
      var f = chartData.forecast.find(function (x) { return x.time === p.time; });
      if (f) {
        L.innerHTML = '<span style="color:var(--ui-accent)">ПРОГНОЗ t+' + f.step + ' · ' + hhmm(f.time) + ' BKK</span>' +
          '<span>O ' + fmt(f.open) + '</span><span>C q10/q50/q90 ' + fmt(f.c10) + ' / ' + fmt(f.close) + ' / ' + fmt(f.c90) + '</span>' +
          '<span>H q90 ' + fmt(f.h90) + '</span><span>L q10 ' + fmt(f.l10) + '</span><span>P(up) ' + (f.p_up * 100).toFixed(0) + '%</span>';
        return;
      }
      var b = chartData.bars.find(function (x) { return x.time === p.time; });
      if (!b) return;
      L.innerHTML = '<span>' + ddhh(b.time) + '</span><span>O ' + fmt(b.open) + '</span><span>H ' + fmt(b.high) + '</span>' +
        '<span>L ' + fmt(b.low) + '</span><span class="' + (b.close >= b.open ? 'tr-up' : 'tr-down') + '">C ' + fmt(b.close) + ' (' + pct((b.close - b.open) / b.open) + ')</span>' +
        '<span class="tr-muted">V ' + fmt(b.volume, 1) + ' BTC</span>';
    });

    // Responsive resize
    if (wrap) {
      new ResizeObserver(function () { if (chart) chart.applyOptions({ width: wrap.clientWidth }); }).observe(wrap);
    }
  }

  function renderLegend() {
    var L = document.getElementById('trLegend');
    if (!L || !chartData.bars.length) return;
    var IND = computeAll(chartData.bars);
    var i = chartData.bars.length - 1;
    var parts = [];
    if (overlayState.MA && overlayState.MA.on) parts.push('<span style="color:#facc15">MA7 ' + fmt(IND.ma7[i]) + '</span><span style="color:#c084fc">MA25 ' + fmt(IND.ma25[i]) + '</span><span style="color:#38bdf8">MA99 ' + fmt(IND.ma99[i]) + '</span>');
    if (overlayState.EMA && overlayState.EMA.on) parts.push('<span style="color:#fb7185">EMA7 ' + fmt(IND.ema7[i]) + '</span><span style="color:#a3e635">EMA25 ' + fmt(IND.ema25[i]) + '</span><span style="color:#67e8f9">EMA99 ' + fmt(IND.ema99[i]) + '</span>');
    if (overlayState.BOLL && overlayState.BOLL.on) parts.push('<span style="color:#94a3b8">BOLL ' + fmt(IND.boll.up[i]) + ' · ' + fmt(IND.boll.mid[i]) + ' · ' + fmt(IND.boll.dn[i]) + '</span>');
    if (overlayState.SAR && overlayState.SAR.on) parts.push('<span style="color:#e879f9">SAR ' + fmt(IND.sar[i]) + '</span>');
    L.innerHTML = parts.join('') || '<span class="tr-muted">индикаторы скрыты</span>';
    L.classList.add('tr-num');
  }

  function addOverlayToggle(key, label, color, defaultOn) {
    var box = document.getElementById('trOverlayToggles');
    if (!box) return;
    var b = document.createElement('button');
    b.className = 'tr-tg';
    b.setAttribute('aria-pressed', String(defaultOn));
    b.innerHTML = '<i style="color:' + color + '"></i>' + key;
    b.onclick = function () {
      overlayState[key].on = !overlayState[key].on;
      b.setAttribute('aria-pressed', String(overlayState[key].on));
      overlayState[key].series.forEach(function (s) { s.applyOptions({ visible: overlayState[key].on }); });
      renderLegend();
    };
    overlayState[key] = { on: defaultOn, series: [], label: label, color: color, btn: b };
    box.appendChild(b);
  }

  function line(arr, color, opts) {
    opts = opts || {};
    var s = chart.addSeries(LWC.LineSeries, Object.assign({ color: color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, opts));
    s.setData(arr.map(function (v, i) {
      return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v };
    }));
    return s;
  }

  function cut(a) { var N = chartData.bars.length; return a.map(function (v, i) { return i < N ? v : null; }); }

  function setupOverlays() {
    if (!chartData.bars.length) return;
    var c = chartData.bars.map(function (x) { return x.close; });
    var IND = computeAll(chartData.bars);
    var INDF = computeAll(chartData.extBars);

    // Remove old overlay series
    Object.keys(overlayState).forEach(function (key) {
      var o = overlayState[key];
      if (o.series) o.series.forEach(function (s) { try { chart.removeSeries(s); } catch (e) {} });
    });
    overlayState = {};
    var box = document.getElementById('trOverlayToggles');
    if (box) box.innerHTML = '';

    addOverlayToggle('MA', 'MA 7 25 99', '#facc15', true);
    overlayState.MA.series = [
      line(cut(INDF.ma7), '#facc15'),
      line(cut(INDF.ma25), '#c084fc'),
      line(cut(INDF.ma99), '#38bdf8')
    ];

    addOverlayToggle('EMA', 'EMA 7 25 99', '#fb7185', false);
    overlayState.EMA.series = [
      line(cut(INDF.ema7), '#fb7185', { lineStyle: 2 }),
      line(cut(INDF.ema25), '#a3e635', { lineStyle: 2 }),
      line(cut(INDF.ema99), '#67e8f9', { lineStyle: 2 })
    ];

    addOverlayToggle('BOLL', 'BOLL 20 2', '#94a3b8', true);
    overlayState.BOLL.series = [
      line(cut(INDF.boll.up), 'rgba(148,163,184,.7)'),
      line(cut(INDF.boll.mid), 'rgba(148,163,184,.45)'),
      line(cut(INDF.boll.dn), 'rgba(148,163,184,.7)')
    ];

    addOverlayToggle('SAR', 'SAR 0.02 0.2', '#e879f9', false);
    overlayState.SAR.series = [
      line(cut(INDF.sar), '#e879f9', { lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.6 })
    ];

    // Apply initial visibility
    Object.keys(overlayState).forEach(function (key) {
      var o = overlayState[key];
      o.series.forEach(function (s) { s.applyOptions({ visible: o.on }); });
    });

    renderLegend();
  }

  function setupPositionMarkers() {
    if (!candles || !chartData.positions.length) return;
    var markers = [];
    chartData.positions.forEach(function (tr) {
      var dir = tr.side === 'long' ? 1 : -1;
      markers.push({
        time: tr.open_time,
        position: dir === 1 ? 'belowBar' : 'aboveBar',
        color: dir === 1 ? UP : DOWN,
        shape: dir === 1 ? 'arrowUp' : 'arrowDown',
        text: (dir === 1 ? 'L ' : 'S ') + tr.profile
      });
      if (tr.exit_time) {
        var et = Math.floor(tr.exit_time / 3600) * 3600;
        markers.push({
          time: et,
          position: 'inBar',
          color: tr.status === 'liq' ? DOWN : (tr.net > 0 ? UP : '#a3a3a3'),
          shape: tr.status === 'liq' ? 'square' : 'circle',
          text: tr.status === 'liq' ? 'LIQ' : ''
        });
      }
    });
    markers.sort(function (a, b) { return a.time - b.time; });
    try {
      candles.setMarkers(markers);
    } catch (e) {
      // v5 uses createSeriesMarkers — fallback
      try { LWC.createSeriesMarkers(candles, markers); } catch (e2) {}
    }

    // Price lines for open positions
    var openTr = chartData.positions.find(function (t) { return t.status === 'open'; });
    if (openTr) {
      candles.createPriceLine({ price: openTr.entry, color: INFO, lineWidth: 1, lineStyle: LWC.LineStyle.Solid, axisLabelVisible: true, title: 'ENTRY' });
      candles.createPriceLine({ price: openTr.tp, color: UP, lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: 'TP' });
      candles.createPriceLine({ price: openTr.sl, color: WARN, lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: 'SL' });
      candles.createPriceLine({ price: openTr.liq, color: DOWN, lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: true, title: 'LIQ' });
    }
  }

  function updateChart() {
    if (!chart || !chartData.bars.length) return;
    var N = chartData.bars.length;
    var last = chartData.bars[N - 1];

    // Candles
    candles.setData(chartData.bars.map(function (b) {
      return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
    }));

    // Forecast candles
    if (chartData.forecast.length) {
      fcSeries.setData(chartData.forecast.map(function (f) {
        return { time: f.time, open: f.open, high: f.h90 || f.high, low: f.l10 || f.low, close: f.close };
      }));

      // q90/q10 bands
      q90Series.setData([{ time: last.time, value: last.close }].concat(chartData.forecast.map(function (f) { return { time: f.time, value: f.c90 }; })));
      q10Series.setData([{ time: last.time, value: last.close }].concat(chartData.forecast.map(function (f) { return { time: f.time, value: f.c10 }; })));
    }

    // Volume
    var volData = chartData.bars.map(function (b) {
      return { time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(34,197,94,.55)' : 'rgba(239,68,68,.55)' };
    });
    if (chartData.forecast.length) {
      chartData.forecast.forEach(function (f) {
        volData.push({ time: f.time, value: f.vol || 0, color: 'rgba(249,115,22,.35)' });
      });
    }
    volSeries.setData(volData);

    // MACD
    var INDF = computeAll(chartData.extBars);
    macdHist.setData(INDF.macd.hist.map(function (v, i) {
      return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v, color: i >= N ? 'rgba(249,115,22,.5)' : (v >= 0 ? 'rgba(34,197,94,.6)' : 'rgba(239,68,68,.6)') };
    }));
    macdDif.setData(INDF.macd.dif.map(function (v, i) { return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v }; }));
    macdDea.setData(INDF.macd.dea.map(function (v, i) { return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v }; }));

    // KDJ
    kSeries.setData(INDF.kdj.K.map(function (v, i) { return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v }; }));
    dSeries.setData(INDF.kdj.D.map(function (v, i) { return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v }; }));
    jSeries.setData(INDF.kdj.J.map(function (v, i) { return v == null ? { time: chartData.extBars[i].time } : { time: chartData.extBars[i].time, value: v }; }));

    // Overlays
    setupOverlays();

    // Position markers
    setupPositionMarkers();

    // Set visible range
    var span = function () { return Math.max(28, Math.min(84, Math.round((document.getElementById('trChartWrap') || {}).clientWidth / 12))); };
    chart.timeScale().setVisibleLogicalRange({ from: N - span(), to: N + 5 });

    // "Now" button
    var tgNow = document.getElementById('trTgNow');
    if (tgNow) tgNow.onclick = function () { chart.timeScale().setVisibleLogicalRange({ from: N - span(), to: N + 5 }); };

    // Place overlay elements
    placeOverlay();
    chart.timeScale().subscribeVisibleLogicalRangeChange(function () { requestAnimationFrame(placeOverlay); });
  }

  function placeOverlay() {
    if (!chartData.bars.length) return;
    var wrap = document.getElementById('trChartWrap');
    if (!wrap) return;
    var ts = chart.timeScale();
    var last = chartData.bars[chartData.bars.length - 1];
    var x = ts.timeToCoordinate(last.time);
    var fcEnd = chartData.forecast.length ? chartData.forecast[chartData.forecast.length - 1].time : last.time;
    var xEnd = ts.timeToCoordinate(fcEnd);
    var mainH = wrap.clientHeight * 0.58;
    try { mainH = chart.panes()[0].getHeight(); } catch (e) {}
    var now = document.getElementById('trNowLine');
    var zone = document.getElementById('trFcZone');
    var psw = 60;
    try { psw = chart.priceScale('right').width(); } catch (e) {}
    var w = wrap.clientWidth - psw;
    if (x == null) { if (now) now.hidden = true; if (zone) zone.hidden = true; return; }
    var bs = ts.options().barSpacing || 9;
    var xs = x + bs / 2 + 1;
    if (now) { now.hidden = false; now.style.left = xs + 'px'; now.style.height = mainH + 'px'; }
    if (zone) { zone.hidden = false; zone.style.left = xs + 'px'; zone.style.width = Math.max(0, w - xs) + 'px'; zone.style.height = mainH + 'px'; }
    var zlN = document.getElementById('trZlNow');
    if (zlN) zlN.style.left = Math.max(0, xs - 58) + 'px';
    var zlF = document.getElementById('trZlFc');
    if (zlF) { zlF.style.left = (xs + 8) + 'px'; zlF.hidden = (w - xs) < 60; }
  }

  // ── data loading ────────────────────────────────────────────────
  async function loadData() {
    try {
      var resp = await fetch('/api/trader/candles?symbol=btcusdt&period=60min&size=200', { credentials: 'same-origin' });
      if (resp.ok) {
        var data = await resp.json();
        chartData.bars = (data.candles || []).map(function (c) {
          return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume };
        });
      }
    } catch (e) { console.error('[trader-chart] candles fetch failed', e); }

    try {
      var resp2 = await fetch('/api/trader/forecast?symbol=btcusdt', { credentials: 'same-origin' });
      if (resp2.ok) {
        var fcData = await resp2.json();
        chartData.forecast = (fcData.steps || []).map(function (f) {
          return {
            time: f.time, step: f.step, open: f.open, close: f.close,
            high: f.high, low: f.low, c10: f.c10, c90: f.c90, h90: f.h90, l10: f.l10,
            vol: f.vol, p_up: f.p_up, sigma: f.sigma
          };
        });
      }
    } catch (e) { console.error('[trader-chart] forecast fetch failed', e); }

    try {
      var resp3 = await fetch('/api/trader/positions', { credentials: 'same-origin' });
      if (resp3.ok) {
        var posData = await resp3.json();
        chartData.positions = posData.positions || [];
      }
    } catch (e) { console.error('[trader-chart] positions fetch failed', e); }

    // Build extended bars for indicator computation
    chartData.extBars = chartData.bars.concat(chartData.forecast.map(function (f) {
      return { time: f.time, open: f.open, high: f.h90 || f.high, low: f.l10 || f.low, close: f.close, volume: f.vol || 0 };
    }));

    updateChart();
    updateTopBar();
    updateSideCards();
  }

  function updateTopBar() {
    if (!chartData.bars.length) return;
    var last = chartData.bars[chartData.bars.length - 1];
    var prev = chartData.bars.length > 24 ? chartData.bars[chartData.bars.length - 25] : chartData.bars[0];
    var tLast = document.getElementById('trLast');
    if (tLast) tLast.textContent = fmt(last.close);
    var ch24 = (last.close - prev.close) / prev.close;
    var t24 = document.getElementById('tr24');
    if (t24) { t24.textContent = pct(ch24); t24.className = 'tr-v tr-num ' + (ch24 >= 0 ? 'tr-up' : 'tr-down'); }
    var high24 = Math.max.apply(null, chartData.bars.slice(-24).map(function (b) { return b.high; }));
    var low24 = Math.min.apply(null, chartData.bars.slice(-24).map(function (b) { return b.low; }));
    var tHL = document.getElementById('trHL');
    if (tHL) tHL.textContent = fmt(high24) + ' / ' + fmt(low24);
    var tBook = document.getElementById('trBook');
    if (tBook) tBook.textContent = fmt(last.close - 0.1) + ' / ' + fmt(last.close + 0.1);
    // Funding countdown (8h interval)
    var now = Math.floor(Date.now() / 1000);
    var next = Math.ceil(now / 28800) * 28800;
    var s = next - now;
    var tFund = document.getElementById('trFund');
    if (tFund) tFund.textContent = '0.0100% (демо) · ' + String(Math.floor(s / 3600)).padStart(2, '0') + ':' + String(Math.floor(s % 3600 / 60)).padStart(2, '0');
  }

  function updateSideCards() {
    // Forecast card
    if (chartData.forecast.length) {
      var f1 = chartData.forecast[0];
      var pUp = document.getElementById('trPUp');
      if (pUp) { pUp.textContent = (f1.p_up * 100).toFixed(0) + '%'; pUp.className = 'tr-big tr-num ' + (f1.p_up >= 0.5 ? 'tr-up' : 'tr-down'); }
      var pUpMark = document.getElementById('trPUpMark');
      if (pUpMark) pUpMark.style.left = (f1.p_up * 100) + '%';
      var fcKv = document.getElementById('trFcKv');
      if (fcKv) fcKv.innerHTML = [
        ['Close q50', fmt(f1.close)],
        ['Диапазон q10–q90', fmt(f1.c10, 0) + ' – ' + fmt(f1.c90, 0)],
        ['Фитили H q90 / L q10', fmt(f1.h90, 0) + ' / ' + fmt(f1.l10, 0)],
        ['σ часа (EWMA)', (f1.sigma * 100).toFixed(3) + '%']
      ].map(function (r) { return '<dt>' + r[0] + '</dt><dd class="tr-num">' + r[1] + '</dd>'; }).join('');
    }

    // Positions summary
    var openCount = chartData.positions.filter(function (t) { return t.status === 'open'; }).length;
    var total = chartData.positions.length;
    var posSummary = document.getElementById('trPosSummary');
    if (posSummary) posSummary.textContent = openCount + ' / ' + total;
  }

  // ── init ────────────────────────────────────────────────────────
  function init() {
    initChart();
    loadData();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose for other modules
  window.TraderChart = { loadData: loadData, chartData: chartData };
})();