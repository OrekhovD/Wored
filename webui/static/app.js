const boot = window.WORED_BOOT ?? { watchlist: ["btcusdt"], defaultSymbol: "btcusdt" };

const state = {
  symbol: boot.defaultSymbol,
  period: "60min",
  size: 240,
  autoRefresh: true,
  overviewTimer: null,
  detailTimer: null,
};

const dom = {
  symbolSelect: document.getElementById("symbolSelect"),
  periodPicker: document.getElementById("periodPicker"),
  autoRefreshButton: document.getElementById("autoRefreshButton"),
  activeSymbolMetric: document.getElementById("activeSymbolMetric"),
  priceStamp: document.getElementById("priceStamp"),
  healthGrid: document.getElementById("healthGrid"),
  watchlistGrid: document.getElementById("watchlistGrid"),
  alertsList: document.getElementById("alertsList"),
  journalList: document.getElementById("journalList"),
  priceChart: document.getElementById("priceChart"),
  volumeChart: document.getElementById("volumeChart"),
  rsiChart: document.getElementById("rsiChart"),
  macdChart: document.getElementById("macdChart"),
};

const formatMoney = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const formatCompact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

let chartApi = null;
let volumeChartApi = null;
let rsiChartApi = null;
let macdChartApi = null;
let priceSeries = null;
let sma20Series = null;
let sma50Series = null;
let volumeSeries = null;
let rsiSeries = null;
let macdLineSeries = null;
let macdSignalSeries = null;
let macdHistogramSeries = null;

function ensureChartLibrary() {
  if (!window.LightweightCharts) {
    throw new Error("Lightweight Charts failed to load");
  }
  return window.LightweightCharts;
}

function chartTheme() {
  return {
    layout: {
      background: { color: "#081219" },
      textColor: "#b5c7d3",
      fontFamily: "IBM Plex Sans, Segoe UI Variable, Trebuchet MS, sans-serif",
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.05)" },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    crosshair: {
      vertLine: { color: "rgba(255,155,84,0.36)", labelBackgroundColor: "#ff9b54" },
      horzLine: { color: "rgba(118,203,255,0.32)", labelBackgroundColor: "#76cbff" },
    },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
    timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
  };
}

function createCharts() {
  const LW = ensureChartLibrary();
  const sharedOptions = chartTheme();

  chartApi = LW.createChart(dom.priceChart, {
    ...sharedOptions,
    height: dom.priceChart.clientHeight,
    autoSize: true,
  });
  priceSeries = chartApi.addSeries(LW.CandlestickSeries, {
    upColor: "#1fd6a3",
    downColor: "#ff5d73",
    borderVisible: false,
    wickUpColor: "#1fd6a3",
    wickDownColor: "#ff5d73",
  });
  sma20Series = chartApi.addSeries(LW.LineSeries, {
    color: "#ffb86a",
    lineWidth: 2,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  sma50Series = chartApi.addSeries(LW.LineSeries, {
    color: "#76cbff",
    lineWidth: 2,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  volumeChartApi = LW.createChart(dom.volumeChart, {
    ...sharedOptions,
    height: dom.volumeChart.clientHeight,
    autoSize: true,
  });
  volumeSeries = volumeChartApi.addSeries(LW.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceLineVisible: false,
    lastValueVisible: false,
  });

  rsiChartApi = LW.createChart(dom.rsiChart, {
    ...sharedOptions,
    height: dom.rsiChart.clientHeight,
    autoSize: true,
  });
  rsiSeries = rsiChartApi.addSeries(LW.LineSeries, {
    color: "#f7d07a",
    lineWidth: 2,
    priceLineVisible: false,
  });
  rsiChartApi.priceScale("right").applyOptions({
    autoScale: true,
    scaleMargins: { top: 0.16, bottom: 0.16 },
  });

  macdChartApi = LW.createChart(dom.macdChart, {
    ...sharedOptions,
    height: dom.macdChart.clientHeight,
    autoSize: true,
  });
  macdHistogramSeries = macdChartApi.addSeries(LW.HistogramSeries, {
    priceLineVisible: false,
    lastValueVisible: false,
  });
  macdLineSeries = macdChartApi.addSeries(LW.LineSeries, {
    color: "#76cbff",
    lineWidth: 2,
    priceLineVisible: false,
  });
  macdSignalSeries = macdChartApi.addSeries(LW.LineSeries, {
    color: "#ff9b54",
    lineWidth: 2,
    priceLineVisible: false,
  });

  const syncCharts = [chartApi, volumeChartApi, rsiChartApi, macdChartApi];
  syncCharts.forEach((chart) => chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (!range) {
      return;
    }
    syncCharts.forEach((target) => {
      if (target !== chart) {
        target.timeScale().setVisibleLogicalRange(range);
      }
    });
  }));
}

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function setPeriodButton(period) {
  for (const button of dom.periodPicker.querySelectorAll("button")) {
    button.classList.toggle("is-active", button.dataset.period === period);
  }
}

function renderHealth(health) {
  const collectorState = health.collector_feed ? "status-ok" : "status-warn";
  dom.healthGrid.innerHTML = `
    <div class="status-pill"><span>Redis</span><strong class="${health.redis ? "status-ok" : "status-bad"}">${health.redis ? "online" : "offline"}</strong></div>
    <div class="status-pill"><span>Postgres</span><strong class="${health.postgres ? "status-ok" : "status-bad"}">${health.postgres ? "online" : "offline"}</strong></div>
    <div class="status-pill"><span>Collector feed</span><strong class="${collectorState}">${health.collector_feed ? "fresh" : "stale"}</strong></div>
    <div class="status-pill"><span>Last journal age</span><strong>${health.last_journal_age_minutes ?? "n/a"} min</strong></div>
  `;
}

function renderWatchlist(items) {
  dom.watchlistGrid.innerHTML = items.map((item) => {
    if (item.price === null) {
      return `
        <div class="watch-card">
          <div class="watch-card-header">
            <span class="watch-symbol">${item.symbol.toUpperCase()}</span>
            <span class="status-bad">no data</span>
          </div>
          <div class="watch-metrics"><span>Redis cache empty</span></div>
        </div>
      `;
    }

    const deltaClass = item.change_pct >= 0 ? "delta-up" : "delta-down";
    return `
      <button class="watch-card" data-symbol="${item.symbol}">
        <div class="watch-card-header">
          <span class="watch-symbol">${item.symbol.toUpperCase()}</span>
          <span class="${deltaClass}">${item.change_pct.toFixed(2)}%</span>
        </div>
        <div class="watch-price">${formatMoney.format(item.price)}</div>
        <div class="watch-metrics">
          <span>Vol ${formatCompact.format(item.volume)}</span>
          <span>${item.source}</span>
        </div>
      </button>
    `;
  }).join("");

  for (const card of dom.watchlistGrid.querySelectorAll("[data-symbol]")) {
    card.addEventListener("click", () => {
      state.symbol = card.dataset.symbol;
      dom.symbolSelect.value = state.symbol;
      dom.activeSymbolMetric.textContent = state.symbol.toUpperCase();
      refreshAllDetails();
    });
  }
}

function renderAlerts(items) {
  if (!items.length) {
    dom.alertsList.innerHTML = `<div class="placeholder">Пока нет событий для выбранного окна.</div>`;
    return;
  }

  dom.alertsList.innerHTML = items.map((item) => {
    const directionUp = item.threshold >= 0;
    return `
      <article class="event-card">
        <div class="event-label ${directionUp ? "event-up" : "event-down"}">${directionUp ? "Spike" : "Drop"}</div>
        <div class="watch-card-header">
          <span class="watch-symbol">${item.symbol.toUpperCase()}</span>
          <span class="${directionUp ? "delta-up" : "delta-down"}">${item.threshold.toFixed(2)}%</span>
        </div>
        <div class="event-meta">
          <span>${new Date(item.timestamp).toLocaleString()}</span>
        </div>
      </article>
    `;
  }).join("");
}

function renderJournal(items) {
  if (!items.length) {
    dom.journalList.innerHTML = `<div class="placeholder">AI journal пока пуст для ${state.symbol.toUpperCase()}.</div>`;
    return;
  }

  dom.journalList.innerHTML = items.map((item) => {
    const symbolEntry = item.symbols[0];
    const indicators = symbolEntry?.indicators ?? {};
    return `
      <article class="journal-card">
        <div class="journal-topline">
          <span class="journal-symbol">${symbolEntry.symbol.toUpperCase()}</span>
          <span class="subtle">${new Date(item.timestamp).toLocaleString()}</span>
        </div>
        <div class="journal-metrics">
          <span>Price ${formatMoney.format(symbolEntry.price)}</span>
          <span class="${symbolEntry.change_pct >= 0 ? "delta-up" : "delta-down"}">${symbolEntry.change_pct.toFixed(2)}%</span>
          <span>RSI ${Number(indicators.rsi_14 ?? 0).toFixed(1)}</span>
          <span>MACD ${Number(indicators.macd_hist ?? 0).toFixed(3)}</span>
        </div>
        <div class="journal-context">${item.market_context || "Scheduled snapshot without extra note."}</div>
      </article>
    `;
  }).join("");
}

function fitAllCharts() {
  chartApi.timeScale().fitContent();
  volumeChartApi.timeScale().fitContent();
  rsiChartApi.timeScale().fitContent();
  macdChartApi.timeScale().fitContent();
}

function renderCandles(payload) {
  priceSeries.setData(payload.candles);
  sma20Series.setData(payload.sma20);
  sma50Series.setData(payload.sma50);
  volumeSeries.setData(payload.volume);
  rsiSeries.setData(payload.rsi14);
  macdHistogramSeries.setData(payload.macd.histogram);
  macdLineSeries.setData(payload.macd.macd);
  macdSignalSeries.setData(payload.macd.signal);
  fitAllCharts();
  dom.priceStamp.textContent = `HTX ${payload.period} • ${new Date(payload.fetched_at).toLocaleTimeString()}`;
}

async function refreshOverview() {
  const overview = await fetchJson("/api/overview");
  renderHealth(overview.health);
  renderWatchlist(overview.watchlist);
}

async function refreshAlerts() {
  const payload = await fetchJson(`/api/alerts?symbol=${encodeURIComponent(state.symbol)}&limit=10`);
  renderAlerts(payload.items);
}

async function refreshJournal() {
  const payload = await fetchJson(`/api/journal?symbol=${encodeURIComponent(state.symbol)}&limit=8`);
  renderJournal(payload.items);
}

async function refreshCandles() {
  const payload = await fetchJson(`/api/candles?symbol=${encodeURIComponent(state.symbol)}&period=${encodeURIComponent(state.period)}&size=${state.size}`);
  renderCandles(payload);
}

async function refreshAllDetails() {
  dom.activeSymbolMetric.textContent = state.symbol.toUpperCase();
  await Promise.all([refreshCandles(), refreshAlerts(), refreshJournal()]);
}

function mountInteractions() {
  dom.symbolSelect.addEventListener("change", async (event) => {
    state.symbol = event.target.value;
    await refreshAllDetails();
  });

  dom.periodPicker.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-period]");
    if (!button) {
      return;
    }
    state.period = button.dataset.period;
    setPeriodButton(state.period);
    await refreshCandles();
  });

  dom.autoRefreshButton.addEventListener("click", () => {
    state.autoRefresh = !state.autoRefresh;
    dom.autoRefreshButton.classList.toggle("is-active", state.autoRefresh);
  });
}

function startPolling() {
  state.overviewTimer = window.setInterval(() => {
    refreshOverview().catch(console.error);
  }, 15000);

  state.detailTimer = window.setInterval(() => {
    if (!state.autoRefresh) {
      return;
    }
    refreshAllDetails().catch(console.error);
  }, 30000);
}

async function bootstrap() {
  createCharts();
  mountInteractions();
  setPeriodButton(state.period);
  await refreshOverview();
  await refreshAllDetails();
  startPolling();
}

bootstrap().catch((error) => {
  console.error(error);
  dom.healthGrid.innerHTML = `<div class="placeholder">Web UI bootstrap failed: ${error.message}</div>`;
});
