/**
 * WORED Forecast Chart — UI-05
 * Shared chart for Command Deck and Prediction detail.
 * Uses Lightweight Charts 5.2.0 (loaded via CDN in base.html).
 * History: CandlestickSeries OHLC; Forecast: LineSeries predicted_price + dashed low/high.
 */
import WORED from './core.js';

// ── Chart factory ────────────────────────────────────────────────────────────────

/**
 * Create a forecast chart in a container element.
 * @param {HTMLElement} container - DOM element for the chart
 * @param {Object} opts
 * @param {number} opts.height - chart height in px (default 360)
 * @returns {Object} chart controller with update(data) and destroy()
 */
function createForecastChart(container, { height = 360 } = {}) {
  if (!container || typeof LightweightCharts === 'undefined') {
    if (container) {
      container.innerHTML = '<div class="wored-empty-state">Графики недоступны. Библиотека Lightweight Charts не загрузилась.</div>';
    }
    return null;
  }

  // Determine mobile height
  const isMobile = window.innerWidth < 768;
  const isLandscape = window.innerWidth > window.innerHeight && isMobile;
  const chartHeight = isLandscape ? 200 : (isMobile ? 280 : height);

  const chart = LightweightCharts.createChart(container, {
    height: chartHeight,
    width: container.clientWidth,
    layout: {
      background: { color: 'transparent' },
      textColor: 'var(--ui-muted, #a3a3a3)',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: 'rgba(64,64,64,0.15)' },
      horzLines: { color: 'rgba(64,64,64,0.15)' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: 'rgba(64,64,64,0.3)',
    },
    timeScale: {
      borderColor: 'rgba(64,64,64,0.3)',
      timeVisible: true,
      secondsVisible: false,
    },
  });

  // Candlestick series for historical data
  const candleSeries = chart.addCandlestickSeries({
    upColor: 'var(--ui-success, #22c55e)',
    downColor: 'var(--ui-danger, #ef4444)',
    borderUpColor: 'var(--ui-success, #22c55e)',
    borderDownColor: 'var(--ui-danger, #ef4444)',
    wickUpColor: 'var(--ui-success, #22c55e)',
    wickDownColor: 'var(--ui-danger, #ef4444)',
  });

  // Line series for forecast predicted price
  const forecastSeries = chart.addLineSeries({
    color: 'var(--ui-info, #3b82f6)',
    lineWidth: 2,
    title: 'Прогноз',
  });

  // Dashed line for low range
  const lowSeries = chart.addLineSeries({
    color: 'rgba(59,130,246,0.4)',
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    title: 'Нижняя граница',
  });

  // Dashed line for high range
  const highSeries = chart.addLineSeries({
    color: 'rgba(59,130,246,0.4)',
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    title: 'Верхняя граница',
  });

  // ResizeObserver
  let resizeObserver = null;
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      if (container.clientWidth > 0) {
        chart.applyOptions({ width: container.clientWidth });
      }
    });
    resizeObserver.observe(container);
  }

  // ── Update method ──────────────────────────────────────────────────────────

  function update({ candles = [], forecast = [], role = 'arbiter' } = {}) {
    // Historical candles
    if (candles.length > 0) {
      const ohlcData = candles.map(c => ({
        time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
        open: c.open, high: c.high, low: c.low, close: c.close,
      })).sort((a, b) => a.time - b.time);
      candleSeries.setData(ohlcData);
    } else {
      candleSeries.setData([]);
    }

    // Forecast points
    if (forecast.length > 0) {
      // Sort by target_time
      const sorted = [...forecast].sort((a, b) => {
        const ta = new Date(a.target_time).getTime();
        const tb = new Date(b.target_time).getTime();
        return ta - tb;
      });

      // Filter: skip non-finite, duplicate target_time, low>high
      const valid = sorted.filter(p => {
        if (!p.target_time) return false;
        const pp = Number(p.predicted_price);
        if (!Number.isFinite(pp)) return false;
        if (p.low != null && p.high != null) {
          if (!Number.isFinite(p.low) || !Number.isFinite(p.high)) return false;
          if (p.low > p.high) return false;
        }
        return true;
      });

      const timeSet = new Set();
      const predictedData = [];
      const lowData = [];
      const highData = [];

      for (const p of valid) {
        const t = Math.floor(new Date(p.target_time).getTime() / 1000);
        if (timeSet.has(t)) continue; // skip duplicates
        timeSet.add(t);

        predictedData.push({ time: t, value: Number(p.predicted_price) });
        if (p.low != null && Number.isFinite(p.low)) {
          lowData.push({ time: t, value: Number(p.low) });
        }
        if (p.high != null && Number.isFinite(p.high)) {
          highData.push({ time: t, value: Number(p.high) });
        }
      }

      forecastSeries.setData(predictedData);
      lowSeries.setData(lowData);
      highSeries.setData(highData);

      // Set series title to role
      forecastSeries.applyOptions({
        title: role === 'bull' ? 'Bull' : role === 'bear' ? 'Bear' : 'Арбитр',
      });
    } else {
      forecastSeries.setData([]);
      lowSeries.setData([]);
      highSeries.setData([]);
    }

    // Fit content
    chart.timeScale().fitContent();
  }

  // ── "Now" marker ─────────────────────────────────────────────────────────────

  function setNowMarker(timeUnix) {
    // Vertical line at current time
    candleSeries.setMarkers([{
      time: timeUnix,
      position: 'aboveBar',
      color: 'var(--ui-accent, #f97316)',
      shape: 'arrowDown',
      text: 'Сейчас',
    }]);
  }

  // ── Destroy ─────────────────────────────────────────────────────────────────

  function destroy() {
    if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
    chart.remove();
  }

  return { chart, update, setNowMarker, destroy };
}

export { createForecastChart };