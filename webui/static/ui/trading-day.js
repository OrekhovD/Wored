/**
 * WORED Trading Day — TD-02
 * Morning start → day trading → evening report.
 * Two accounts (manual + auto), shared chart, manual ticket, auto controls.
 */
import WORED from './core.js?v=20260910-1';

const TD = (() => {
  'use strict';

  let _dayData = null;
  let _poller = null;
  let _ticketDir = 'long';
  let _marketChart = null;
  let _marketSeries = null;
  let _marketPoints = [];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  // ── Init ────────────────────────────────────────────────────────────────

  function init() {
    const page = document.getElementById('tradingDayPage');
    if (!page) return;

    loadCurrentDay();
    _poller = new WORED.Poller(loadCurrentDay, 15000);
    _poller.start();

    wireStartButton();
    wireSettings();
    wireAutoControls();
    wireTicket();
    wireFinishDay();
    wireReport();
  }

  // ── Load current day ──────────────────────────────────────────────────────

  async function loadCurrentDay() {
    try {
      const resp = await WORED.apiFetch('/api/trading-day/current', {
        resourceKey: 'td-current',
        timeout: 10000,
      });
      if (resp.status === 401) {
        location.href = '/login?next=' + encodeURIComponent(location.pathname);
        return;
      }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      _dayData = await resp.json();
      render();
    } catch (err) {
      const readiness = document.getElementById('tdReadiness');
      if (readiness) readiness.innerHTML = '<span class="ui-text-danger">Нет связи</span>';
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  function render() {
    if (!_dayData) return;
    const d = _dayData;
    const day = d.day;
    renderModeNotice(d);
    if (day?.state === 'reconciled' && d.report) {
      renderReport(d);
    } else if (!day || day.state === 'draft') {
      renderPreStart(d);
    } else if (day.state === 'active' || day.state === 'closing') {
      renderActive(d);
    }
  }

  function renderModeNotice(d) {
    const notice = document.getElementById('tdModeNotice');
    if (!notice) return;
    if (d.market_mode === 'live' && d.market?.quality === 'live') {
      notice.innerHTML = '<strong>Симуляция на live-рынке.</strong> '
        + 'HTX ' + escapeHtml(d.market.contract_code || 'perpetual')
        + ' · bid/ask для исполнения · mark для контроля риска. Реальные заявки не отправляются.';
    } else {
      notice.innerHTML = '<strong>Демо-режим.</strong> Используется явно обозначенная тестовая цена; результаты не являются торговой статистикой.';
    }
  }

  function renderPreStart(d) {
    document.getElementById('tdPreStart').hidden = false;
    document.getElementById('tdActive').hidden = true;
    document.getElementById('tdReport').hidden = true;

    const accounts = d.accounts || [];
    const manual = accounts.find(a => a.kind === 'manual');
    const auto = accounts.find(a => a.kind === 'auto');

    if (manual) {
      document.getElementById('tdManualEquity').textContent = WORED.fmtUSDT(manual.cash);
      document.getElementById('tdManualSub').textContent = 'Симуляция · ' + manual.currency;
    }
    if (auto) {
      document.getElementById('tdAutoEquity').textContent = WORED.fmtUSDT(auto.cash);
      document.getElementById('tdAutoSub').textContent = 'Симуляция · ' + auto.currency;
    }

    if (d.risk_policy) {
      document.getElementById('tdLossLimit').textContent = WORED.fmtUSDT(d.risk_policy.max_daily_loss_usdt);
      document.getElementById('tdMaxLeverage').textContent = d.risk_policy.max_leverage + 'x';
      document.getElementById('tdSetLossLimit').value = d.risk_policy.max_daily_loss_usdt;
      document.getElementById('tdSetRiskPerOrder').value = d.risk_policy.max_risk_per_order_usdt;
      document.getElementById('tdSetMaxLeverage').value = d.risk_policy.max_leverage;
    }

    // Readiness
    const readiness = document.getElementById('tdReadiness');
    if (d.fresh && d.capabilities && d.capabilities.can_start) {
      readiness.innerHTML = '<span class="ui-text-success">Готов к запуску</span>';
      document.getElementById('tdStartBtn').disabled = false;
    } else {
      const reason = d.reason_code || 'Проверка…';
      readiness.innerHTML = '<span class="ui-text-muted">' + reason + '</span>';
      document.getElementById('tdStartBtn').disabled = !d.capabilities?.can_start;
    }

    // End time
    if (d.end_time_local) {
      document.getElementById('tdEndTime').textContent = d.end_time_local + ' ' + (d.timezone || 'Asia/Bangkok');
      document.getElementById('tdSetEndTime').value = d.end_time_local;
    }
    document.getElementById('tdSetTimezone').value = d.timezone || 'Asia/Bangkok';
    if (manual?.opening_equity || manual?.cash) {
      document.getElementById('tdSetCapital').value = manual.opening_equity || manual.cash;
    }
  }

  function renderActive(d) {
    document.getElementById('tdPreStart').hidden = true;
    document.getElementById('tdActive').hidden = false;
    document.getElementById('tdReport').hidden = true;

    const day = d.day;
    if (day) {
      document.getElementById('tdDayDate').textContent = day.local_date || '—';
      // Timer
      if (day.end_at) {
        const endMs = new Date(day.end_at).getTime();
        const nowMs = Date.now();
        const remaining = Math.max(0, endMs - nowMs);
        const hours = Math.floor(remaining / 3600000);
        const mins = Math.floor((remaining % 3600000) / 60000);
        document.getElementById('tdDayTimer').textContent = hours + 'ч ' + mins + 'м до закрытия';
      }
    }

    // Freshness
    const fresh = document.getElementById('tdDayFreshness');
    if (d.market_mode === 'demo') {
      fresh.textContent = 'Тестовая цена';
      fresh.className = 'td-day-freshness ui-text-accent';
    } else if (d.fresh) {
      fresh.textContent = 'Данные актуальны';
      fresh.className = 'td-day-freshness ui-text-success';
    } else {
      fresh.textContent = 'Данные устарели';
      fresh.className = 'td-day-freshness ui-text-accent';
    }

    // Account metrics
    const accounts = d.accounts || [];
    for (const acc of accounts) {
      const prefix = acc.kind === 'manual' ? 'tdManual' : 'tdAuto';
      const eq = document.getElementById(prefix + 'Eq');
      const avail = document.getElementById(prefix + 'Avail');
      const realized = document.getElementById(prefix + 'Realized');
      const unrealized = document.getElementById(prefix + 'Unrealized');
      const costs = document.getElementById(prefix + 'Costs');
      const lossBudget = document.getElementById(prefix + 'LossBudget');
      const posCount = document.getElementById(prefix + 'PosCount');

      if (eq) eq.textContent = WORED.fmtUSDT(acc.equity ?? acc.cash);
      if (avail) avail.textContent = WORED.fmtUSDT(acc.available_margin);
      if (realized) realized.textContent = WORED.fmtPnL(acc.realized_net ?? 0);
      if (unrealized) unrealized.textContent = WORED.fmtPnL(acc.unrealized ?? 0);
      if (costs) costs.textContent = WORED.fmtUSDT(acc.total_costs ?? 0);
      if (lossBudget) lossBudget.textContent = WORED.fmtUSDT(acc.loss_budget ?? 0);
      if (posCount) posCount.textContent = acc.open_positions ?? 0;
    }

    // Auto status
    const autoStatus = document.getElementById('tdAutoStatus');
    if (autoStatus && d.auto_state) {
      autoStatus.textContent = d.auto_state_label || d.auto_state || '—';
    }
    const isPaused = d.auto_state === 'paused';
    document.getElementById('tdAutoPauseBtn').hidden = isPaused;
    document.getElementById('tdAutoResumeBtn').hidden = !isPaused;
    const leverageInput = document.getElementById('tdTicketLeverage');
    if (leverageInput && d.risk_policy?.max_leverage) {
      const maxLeverage = Number(d.risk_policy.max_leverage);
      leverageInput.max = String(maxLeverage);
      if (Number(leverageInput.value) > maxLeverage) {
        leverageInput.value = String(Math.min(10, maxLeverage));
      }
    }

    renderLiveMarket(d.market);

    // Positions
    renderPositions(d.positions || []);
    renderEvents(d.events || []);
  }

  function renderLiveMarket(market) {
    const quote = document.getElementById('tdMarketQuote');
    const container = document.getElementById('tdLiveChart');
    if (!quote || !container) return;
    if (!market || market.quality !== 'live') {
      quote.textContent = 'HTX perpetual недоступен; торговые команды заблокированы';
      return;
    }
    const funding = Number(market.funding_rate || 0) * 100;
    quote.innerHTML = '<span><b>' + escapeHtml(market.contract_code) + '</b> · HTX perpetual</span>'
      + '<span>Bid <b>' + WORED.fmtPrice(market.bid) + '</b></span>'
      + '<span>Mark <b>' + WORED.fmtPrice(market.mark) + '</b></span>'
      + '<span>Ask <b>' + WORED.fmtPrice(market.ask) + '</b></span>'
      + '<span>Index <b>' + WORED.fmtPrice(market.index) + '</b></span>'
      + '<span>Funding <b>' + funding.toFixed(4) + '%</b></span>';

    const time = Math.floor(new Date(market.source_at).getTime() / 1000);
    const value = Number(market.mark);
    if (!Number.isFinite(time) || !Number.isFinite(value)) return;
    const previous = _marketPoints[_marketPoints.length - 1];
    if (!previous || time > previous.time) {
      _marketPoints.push({ time, value });
      _marketPoints = _marketPoints.slice(-240);
    } else if (time === previous.time) {
      previous.value = value;
    }

    if (!_marketChart && window.LightweightCharts) {
      _marketChart = window.LightweightCharts.createChart(container, {
        height: 280,
        layout: { background: { color: '#11151b' }, textColor: '#9ba7b4' },
        grid: { vertLines: { color: '#202731' }, horzLines: { color: '#202731' } },
        rightPriceScale: { borderColor: '#343e4a' },
        timeScale: { borderColor: '#343e4a', timeVisible: true, secondsVisible: true },
      });
      _marketSeries = _marketChart.addSeries(window.LightweightCharts.LineSeries, {
        color: '#4f9cf9', lineWidth: 2, lastValueVisible: true, priceLineVisible: true,
      });
      new ResizeObserver(entries => {
        const width = entries[0]?.contentRect?.width;
        if (width) _marketChart.applyOptions({ width });
      }).observe(container);
    }
    if (_marketSeries) {
      _marketSeries.setData(_marketPoints);
      _marketChart.timeScale().fitContent();
    }
  }

  function renderPositions(positions) {
    const list = document.getElementById('tdPositionsList');
    if (!list) return;
    if (!positions.length) {
      list.innerHTML = '<div class="wored-empty-state">Нет открытых позиций</div>';
      return;
    }
    list.innerHTML = positions.map(p => {
      const dir = p.side === 'long' ? '📈' : '📉';
      const origin = p.origin === 'auto' ? '🤖' : '👤';
      const pnlClass = (p.unrealized_net || 0) >= 0 ? 'ui-text-success' : 'ui-text-danger';
      return '<div class="td-position-item">'
        + '<span class="td-pos-id">#' + p.id.substring(0, 6) + '</span>'
        + '<span class="td-pos-dir">' + dir + ' ' + p.side.toUpperCase() + '</span>'
        + '<span class="td-pos-leverage">' + (p.leverage || '—') + 'x</span>'
        + '<span class="td-pos-origin">' + origin + '</span>'
        + '<span class="td-pos-entry">' + WORED.fmtPrice(p.entry_price) + '</span>'
        + '<span class="td-pos-pnl ' + pnlClass + '">' + WORED.fmtPnL(p.unrealized_net) + '</span>'
        + '<button class="wored-ghost-btn td-pos-close" data-pid="' + p.id + '">Закрыть</button>'
        + '</div>';
    }).join('');

    // Wire close buttons
    list.querySelectorAll('.td-pos-close').forEach(btn => {
      btn.addEventListener('click', () => closePosition(btn.dataset.pid));
    });
  }

  function renderEvents(events) {
    const list = document.getElementById('tdEventsList');
    if (!list) return;
    if (!events.length) {
      list.innerHTML = '<div class="wored-empty-state">Нет событий</div>';
      return;
    }
    list.innerHTML = events.slice(0, 20).map(e => {
      return '<div class="td-event-item">'
        + '<span class="td-event-time">' + WORED.fmtTime(e.timestamp, { title: false }) + '</span>'
        + '<span class="td-event-type">' + (e.type || '') + '</span>'
        + '<span class="td-event-text">' + (e.message || '') + '</span>'
        + '</div>';
    }).join('');
  }

  // ── Start button ──────────────────────────────────────────────────────────

  function wireStartButton() {
    const btn = document.getElementById('tdStartBtn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Запуск…';
      try {
        const csrf = document.querySelector('[data-csrf]')?.value || '';
        const resp = await fetch('/api/trading-day/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          credentials: 'same-origin',
          body: JSON.stringify({
            idempotency_key: crypto.randomUUID(),
            end_time_local: document.getElementById('tdSetEndTime')?.value || '21:00',
            opening_capital: document.getElementById('tdSetCapital')?.value || '1000',
            risk_policy: {
              max_daily_loss_usdt: document.getElementById('tdSetLossLimit')?.value || '50',
              max_risk_per_order_usdt: document.getElementById('tdSetRiskPerOrder')?.value || '10',
              max_leverage: document.getElementById('tdSetMaxLeverage')?.value || 10,
            },
          }),
        });
        if (resp.status === 202) {
          const data = await resp.json();
          WORED.clearPendingForecast();
          loadCurrentDay();
        } else if (resp.status === 401) {
          location.href = '/login?next=' + encodeURIComponent(location.pathname);
        } else {
          const err = await resp.json().catch(() => ({}));
          window.WORED_TOAST?.('Ошибка: ' + (err.detail || resp.status), 'bad');
        }
      } catch (err) {
        window.WORED_TOAST?.('Нет связи', 'bad');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Начать день';
      }
    });
  }

  // ── Settings ───────────────────────────────────────────────────────────────

  function wireSettings() {
    const btn = document.getElementById('tdSettingsBtn');
    const panel = document.getElementById('tdSettings');
    const saveBtn = document.getElementById('tdSaveSettingsBtn');
    if (!btn || !panel || !saveBtn) return;
    btn.addEventListener('click', () => {
      panel.hidden = !panel.hidden;
    });
    saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      const csrf = document.querySelector('[data-csrf]')?.value || '';
      try {
        const response = await fetch('/api/trading-day/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          credentials: 'same-origin',
          body: JSON.stringify({
            end_time_local: document.getElementById('tdSetEndTime').value,
            timezone: document.getElementById('tdSetTimezone').value,
            opening_capital: document.getElementById('tdSetCapital').value,
            max_daily_loss_usdt: document.getElementById('tdSetLossLimit').value,
            max_risk_per_order_usdt: document.getElementById('tdSetRiskPerOrder').value,
            max_leverage: document.getElementById('tdSetMaxLeverage').value,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не удалось сохранить');
        panel.hidden = true;
        window.WORED_TOAST?.('Настройки сохранены', 'ok');
        await loadCurrentDay();
      } catch (error) {
        window.WORED_TOAST?.(error.message || 'Не удалось сохранить настройки', 'bad');
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  // ── Auto controls ──────────────────────────────────────────────────────────

  function wireAutoControls() {
    const pauseBtn = document.getElementById('tdAutoPauseBtn');
    const resumeBtn = document.getElementById('tdAutoResumeBtn');
    const closeBtn = document.getElementById('tdAutoCloseBtn');

    if (pauseBtn) pauseBtn.addEventListener('click', () => sendAutomation('pause'));
    if (resumeBtn) resumeBtn.addEventListener('click', () => sendAutomation('resume'));
    if (closeBtn) closeBtn.addEventListener('click', () => {
      if (confirm('Закрыть все позиции автомата?')) sendAutomation('close_auto');
    });
  }

  async function sendAutomation(action) {
    if (!_dayData?.day?.id) return;
    try {
      const csrf = document.querySelector('[data-csrf]')?.value || '';
      const resp = await fetch('/api/trading-day/' + _dayData.day.id + '/automation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), action }),
      });
      if (resp.ok) {
        window.WORED_TOAST?.('Команда выполнена: ' + action, 'ok');
        loadCurrentDay();
      } else if (resp.status === 401) {
        location.href = '/login?next=' + encodeURIComponent(location.pathname);
      } else {
        const err = await resp.json().catch(() => ({}));
        window.WORED_TOAST?.('Ошибка: ' + (err.detail || resp.status), 'bad');
      }
    } catch (err) {
      window.WORED_TOAST?.('Нет связи', 'bad');
    }
  }

  // ── Ticket ────────────────────────────────────────────────────────────────

  function wireTicket() {
    const longBtn = document.getElementById('tdLongBtn');
    const shortBtn = document.getElementById('tdShortBtn');
    const typeSelect = document.getElementById('tdTicketType');
    const limitField = document.getElementById('tdLimitPriceField');
    const previewBtn = document.getElementById('tdPreviewBtn');
    const submitBtn = document.getElementById('tdSubmitBtn');

    if (longBtn) longBtn.addEventListener('click', () => { _ticketDir = 'long'; updateDirButtons(); });
    if (shortBtn) shortBtn.addEventListener('click', () => { _ticketDir = 'short'; updateDirButtons(); });
    if (typeSelect) typeSelect.addEventListener('change', () => {
      if (limitField) limitField.hidden = typeSelect.value !== 'limit';
    });
    if (previewBtn) previewBtn.addEventListener('click', doPreview);
    if (submitBtn) submitBtn.addEventListener('click', doSubmit);
  }

  function updateDirButtons() {
    const longBtn = document.getElementById('tdLongBtn');
    const shortBtn = document.getElementById('tdShortBtn');
    if (longBtn) longBtn.classList.toggle('is-active', _ticketDir === 'long');
    if (shortBtn) shortBtn.classList.toggle('is-active', _ticketDir === 'short');
  }

  async function doPreview() {
    const previewSection = document.getElementById('tdPreviewSection');
    const submitBtn = document.getElementById('tdSubmitBtn');
    const reasonEl = document.getElementById('tdTicketReason');
    if (submitBtn) submitBtn.disabled = true;
    if (reasonEl) reasonEl.textContent = 'Проверка…';

    try {
      const manual = _dayData?.accounts?.find(a => a.kind === 'manual');
      if (!manual) { if (reasonEl) reasonEl.textContent = 'Ручной счёт не найден'; return; }

      const payload = {
        instrument: document.getElementById('tdTicketSymbol')?.value || 'btcusdt',
        side: _ticketDir === 'long' ? 'buy' : 'sell',
        order_type: document.getElementById('tdTicketType')?.value || 'market',
        risk: document.getElementById('tdTicketRisk')?.value || '10',
        leverage: document.getElementById('tdTicketLeverage')?.value || '1',
        stop_price: document.getElementById('tdTicketStop')?.value || '',
      };
      const limitPrice = document.getElementById('tdTicketPrice')?.value;
      if (limitPrice) payload.price = limitPrice;
      const tp = document.getElementById('tdTicketTP')?.value;
      if (tp) payload.take_profit = tp;

      const csrf = document.querySelector('[data-csrf]')?.value || '';
      const resp = await WORED.apiFetch('/api/paper/accounts/' + manual.id + '/orders/preview', {
        resourceKey: 'td-preview', timeout: 10000, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        credentials: 'same-origin', body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Ошибка ' + resp.status);

      if (previewSection) previewSection.hidden = false;
      document.getElementById('tdPreviewQty').textContent = WORED.fmtQty(data.quantity, 'BTC');
      document.getElementById('tdPreviewNotional').textContent = WORED.fmtUSDT(data.notional);
      document.getElementById('tdPreviewLeverage').textContent = data.leverage + 'x';
      document.getElementById('tdPreviewEntryFee').textContent = WORED.fmtUSDT(data.entry_fee);
      document.getElementById('tdPreviewExitFee').textContent = WORED.fmtUSDT(data.estimated_exit_fee);
      document.getElementById('tdPreviewBE').textContent = WORED.fmtPrice(data.break_even);
      document.getElementById('tdPreviewNetTP').textContent = WORED.fmtPnL(data.estimated_net_at_tp);
      document.getElementById('tdPreviewNetSL').textContent = WORED.fmtPnL(data.estimated_net_at_sl);
      document.getElementById('tdPreviewLiq').textContent = WORED.fmtPrice(data.liquidation_price);
      document.getElementById('tdPreviewSource').textContent = data.market?.mode === 'live'
        ? 'HTX · ' + (data.market.contract_code || 'perpetual') + ' · bid/ask'
        : 'Тестовая цена';
      document.getElementById('tdPreviewAsOf').textContent = WORED.fmtTime(data.price_as_of, { title: false });

      if (data.allowed && submitBtn) {
        submitBtn.disabled = false;
        if (reasonEl) reasonEl.textContent = '';
      } else {
        if (reasonEl) reasonEl.textContent = (data.reasons || []).join(', ') || 'Недоступно';
      }
    } catch (err) {
      if (reasonEl) reasonEl.textContent = err.message || 'Нет связи. Проверьте подключение.';
    }
  }

  async function doSubmit() {
    const submitBtn = document.getElementById('tdSubmitBtn');
    if (!submitBtn || submitBtn.disabled) return;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Отправка…';

    try {
      const manual = _dayData?.accounts?.find(a => a.kind === 'manual');
      if (!manual) return;
      const csrf = document.querySelector('[data-csrf]')?.value || '';

      const body = {
        idempotency_key: crypto.randomUUID(),
        instrument: document.getElementById('tdTicketSymbol')?.value || 'btcusdt',
        side: _ticketDir === 'long' ? 'buy' : 'sell',
        order_type: document.getElementById('tdTicketType')?.value || 'market',
        risk: document.getElementById('tdTicketRisk')?.value || '10',
        leverage: document.getElementById('tdTicketLeverage')?.value || '1',
        stop_price: document.getElementById('tdTicketStop')?.value || null,
        take_profit: document.getElementById('tdTicketTP')?.value || null,
      };
      const limitPrice = document.getElementById('tdTicketPrice')?.value;
      if (limitPrice) body.price = limitPrice;

      const resp = await fetch('/api/paper/accounts/' + manual.id + '/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
        body: JSON.stringify(body),
      });

      if (resp.status === 202) {
        const data = await resp.json();
        window.WORED_TOAST?.('Заявка принята: ' + (data.command_id?.substring(0, 8) || ''), 'ok');
        loadCurrentDay();
      } else if (resp.status === 401) {
        location.href = '/login?next=' + encodeURIComponent(location.pathname);
      } else {
        const err = await resp.json().catch(() => ({}));
        window.WORED_TOAST?.('Отклонено: ' + (err.detail || resp.status), 'bad');
      }
    } catch (err) {
      window.WORED_TOAST?.('Нет связи', 'bad');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Отправить заявку · Ручной счёт';
    }
  }

  async function closePosition(positionId) {
    if (!confirm('Закрыть позицию #' + positionId.substring(0, 6) + '?')) return;
    try {
      const csrf = document.querySelector('[data-csrf]')?.value || '';
      const resp = await fetch('/api/paper/positions/' + positionId + '/actions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), action: 'close' }),
      });
      if (resp.ok) {
        window.WORED_TOAST?.('Позиция закрыта', 'ok');
        loadCurrentDay();
      } else {
        window.WORED_TOAST?.('Ошибка закрытия', 'bad');
      }
    } catch (err) {
      window.WORED_TOAST?.('Нет связи', 'bad');
    }
  }

  // ── Finish day ─────────────────────────────────────────────────────────────

  function wireFinishDay() {
    const btn = document.getElementById('tdFinishDayBtn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!confirm('Завершить день? Будут отменены незаполненные заявки и закрыты позиции на обоих счетах.')) return;
      if (!_dayData?.day?.id) return;
      btn.disabled = true;
      btn.textContent = 'Завершение…';
      try {
        const csrf = document.querySelector('[data-csrf]')?.value || '';
        const resp = await fetch('/api/trading-day/' + _dayData.day.id + '/finish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          credentials: 'same-origin',
          body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
        });
        if (resp.ok) {
          window.WORED_TOAST?.('День завершается…', 'ok');
          loadCurrentDay();
        } else {
          const err = await resp.json().catch(() => ({}));
          window.WORED_TOAST?.('Ошибка: ' + (err.detail || resp.status), 'bad');
        }
      } catch (err) {
        window.WORED_TOAST?.('Нет связи', 'bad');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Завершить день';
      }
    });
  }

  // ── Report ─────────────────────────────────────────────────────────────────

  function wireReport() {
    const nextBtn = document.getElementById('tdNextDayBtn');
    if (nextBtn) nextBtn.addEventListener('click', async () => {
      const csrf = document.querySelector('[data-csrf]')?.value || '';
      const response = await fetch('/api/trading-day/next', {
        method: 'POST', headers: { 'X-CSRF-Token': csrf }, credentials: 'same-origin',
      });
      if (response.ok) await loadCurrentDay();
    });
    const jsonBtn = document.getElementById('tdExportJsonBtn');
    if (jsonBtn) jsonBtn.addEventListener('click', () => downloadReport('json'));
    const htmlBtn = document.getElementById('tdExportHtmlBtn');
    if (htmlBtn) htmlBtn.addEventListener('click', () => downloadReport('html'));
  }

  function downloadReport(format) {
    if (!_dayData?.report) return;
    const isJson = format === 'json';
    const content = isJson
      ? JSON.stringify(_dayData.report, null, 2)
      : '<!doctype html><meta charset="utf-8"><title>WORED — отчёт</title><pre>'
        + JSON.stringify(_dayData.report, null, 2).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))
        + '</pre>';
    const url = URL.createObjectURL(new Blob([content], { type: isJson ? 'application/json' : 'text/html' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'wored-trading-day-report.' + format;
    link.click();
    URL.revokeObjectURL(url);
  }

  function renderReport(d) {
    document.getElementById('tdPreStart').hidden = true;
    document.getElementById('tdActive').hidden = true;
    document.getElementById('tdReport').hidden = false;

    const report = d.report;
    if (!report) return;

    const summary = document.getElementById('tdReportSummary');
    if (summary) {
      const m = report.manual || {};
      const a = report.auto || {};
      summary.innerHTML = '<div class="td-report-cards">'
        + '<div class="td-report-card td-manual"><h3>Ручной</h3>'
        + '<p>Net: ' + WORED.fmtPnL(m.realized_net) + '</p>'
        + '<p>Доходность: ' + WORED.fmtFraction(m.daily_return) + '</p>'
        + '<p>Расходы: ' + WORED.fmtUSDT(m.total_fees) + '</p>'
        + '<p>Сделок: ' + (m.trades_count || 0) + '</p>'
        + '<p>Win rate: ' + (m.win_rate == null ? '—' : WORED.fmtFraction(m.win_rate)) + '</p></div>'
        + '<div class="td-report-card td-auto"><h3>Автомат</h3>'
        + '<p>Net: ' + WORED.fmtPnL(a.realized_net) + '</p>'
        + '<p>Доходность: ' + WORED.fmtFraction(a.daily_return) + '</p>'
        + '<p>Расходы: ' + WORED.fmtUSDT(a.total_fees) + '</p>'
        + '<p>Сделок: ' + (a.trades_count || 0) + '</p>'
        + '<p>Win rate: ' + (a.win_rate == null ? '—' : WORED.fmtFraction(a.win_rate)) + '</p></div>'
        + '</div>';
    }

    const review = report.learning || {};
    const comparison = review.comparison || {};
    const findings = Array.isArray(review.findings) ? review.findings : [];
    const table = document.getElementById('tdReportTable');
    if (table) {
      const comparisonText = comparison.status === 'ready'
        ? 'Лучший net: ' + (comparison.winner === 'manual' ? 'ручной счёт' : comparison.winner === 'auto' ? 'автомат' : 'ничья')
        : (comparison.reason || 'Недостаточно данных');
      const findingHtml = findings.length
        ? findings.map(item => '<article class="td-learning-item">'
          + '<div><strong>' + escapeHtml(item.hypothesis) + '</strong>'
          + '<span class="td-learning-status">' + escapeHtml(item.status) + '</span></div>'
          + '<p>' + escapeHtml(item.evidence) + '</p>'
          + '<p>Кандидат: ' + escapeHtml(item.proposed_change) + '</p>'
          + '</article>').join('')
        : '<p class="ui-text-muted">За этот день проверяемых наблюдений не найдено.</p>';
      table.innerHTML = '<section class="td-learning-review">'
        + '<h3>Разбор и контур обучения</h3>'
        + '<p><strong>Сравнение:</strong> ' + escapeHtml(comparisonText) + '</p>'
        + '<p><strong>Выборка:</strong> ' + escapeHtml(review.sample_size || 0) + ' закрытых сделок</p>'
        + findingHtml
        + '<p class="td-learning-gate"><strong>Допуск правил:</strong> '
        + escapeHtml(review.promotion?.status || 'not_ready') + ' · '
        + escapeHtml(review.promotion?.reason || 'Нет решения') + '</p>'
        + '</section>';
    }
  }

  // ── Auto-init ──────────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return { init, loadCurrentDay };
})();

export default TD;
