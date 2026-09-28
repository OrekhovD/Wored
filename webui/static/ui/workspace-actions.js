/**
 * workspace-actions.js — typed Command Drawer controller (B3).
 * Handles: context → validation → submit → poll result.
 * Every financial POST uses Idempotency-Key + CSRF.
 */
(function () {
  'use strict';

  // Command registry: action_code → { endpoint, method, confirm, resultLookup }
  var COMMANDS = {
    'settings.update': { endpoint: '/api/trading-day/settings', method: 'POST', confirm: 'none' },
    'day.start':       { endpoint: '/api/trading-day/start', method: 'POST', confirm: 'acknowledge' },
    'order.preview':   { endpoint: null, method: 'POST', confirm: 'none', dynamic: true },
    'order.submit':    { endpoint: null, method: 'POST', confirm: 'typed', dynamic: true },
    'position.close':  { endpoint: null, method: 'POST', confirm: 'acknowledge', dynamic: true },
    'auto.pause':      { endpoint: null, method: 'POST', confirm: 'none', dynamic: true },
    'auto.resume':     { endpoint: null, method: 'POST', confirm: 'none', dynamic: true },
    'auto.close':      { endpoint: null, method: 'POST', confirm: 'acknowledge', dynamic: true },
    'day.finish':      { endpoint: null, method: 'POST', confirm: 'typed', dynamic: true },
  };
  var RESULT_LOOKUP = '/api/paper/commands/';
  var POLL_CMD_INTERVAL = 2000;
  var MAX_POLL_ATTEMPTS = 30;

  // CSRF from meta or hidden field
  function getCsrf() {
    var el = document.querySelector('input[name="csrf_token"]');
    if (el) return el.value;
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  function idempotencyKey() {
    return 'ws-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  // ── Drawer DOM ──
  var overlay = document.getElementById('wsDrawerOverlay');
  if (!overlay) return;
  var titleEl = document.getElementById('wsDrawerTitle');
  var bodyEl = document.getElementById('wsDrawerBody');
  var statusEl = document.getElementById('wsDrawerStatus');
  var submitBtn = document.getElementById('wsDrawerSubmit');
  var cancelBtn = document.getElementById('wsDrawerCancel');
  var closeBtn = document.getElementById('wsDrawerClose');

  var currentAction = null;
  var pollTimer = null;

  function openDrawer(actionCode, opts) {
    currentAction = { code: actionCode, opts: opts || {} };
    overlay.hidden = false;
    statusEl.textContent = '';
    submitBtn.disabled = true;
    titleEl.textContent = actionLabel(actionCode);
    bodyEl.innerHTML = '';

    var def = COMMANDS[actionCode];
    if (!def) { bodyEl.textContent = 'Неизвестное действие: ' + actionCode; return; }

    // Context display
    var ctx = document.createElement('div');
    ctx.className = 'ws-drawer-context';
    ctx.innerHTML = buildContextHtml(actionCode, opts || {});
    bodyEl.appendChild(ctx);

    // B3: order.preview / order.submit render a real form so the payload
    // carries risk / side / instrument / stop_price / take_profit from the
    // user, not from a hardcoded default.
    var isOrderFlow = actionCode === 'order.preview' || actionCode === 'order.submit';
    if (isOrderFlow) {
      renderOrderForm(actionCode, opts || {});
    }

    // Enable submit if no typed confirmation needed
    if (def.confirm === 'none') {
      // Order-form has its own required-field validation; other actions
      // are always submittable at 'none' gate.
      submitBtn.disabled = isOrderFlow;
    } else if (def.confirm === 'acknowledge') {
      var ack = document.createElement('label');
      ack.className = 'ws-drawer-ack';
      ack.innerHTML = '<input type="checkbox" id="wsDrawerAck"> '
        + 'Я подтверждаю действие на ' + esc(opts.account_id || '') + ' счёт';
      bodyEl.appendChild(ack);
      var checkbox = ack.querySelector('input');
      checkbox.addEventListener('change', function () { submitBtn.disabled = !this.checked; });
    } else if (def.confirm === 'typed') {
      var input = document.createElement('div');
      input.className = 'ws-drawer-typed';
      input.innerHTML = '<p>Введите <strong>' + esc(actionCode) + '</strong> для подтверждения:</p>'
        + '<input type="text" id="wsDrawerTypedInput" autocomplete="off">';
      bodyEl.appendChild(input);
      var typed = input.querySelector('input');
      typed.addEventListener('input', function () {
        submitBtn.disabled = this.value !== actionCode;
      });
    }

    submitBtn.onclick = function () { executeCommand(actionCode, opts || {}); };
  }

  function closeDrawer() {
    overlay.hidden = true;
    currentAction = null;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  cancelBtn.addEventListener('click', closeDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) closeDrawer(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !overlay.hidden) closeDrawer(); });

  // ── Execution ──
  async function executeCommand(actionCode, opts) {
    submitBtn.disabled = true;
    statusEl.textContent = 'Отправка…';

    var def = COMMANDS[actionCode];
    var url = def.endpoint;
    if (def.dynamic) {
      url = buildDynamicUrl(actionCode, opts);
    }
    if (!url) { statusEl.textContent = 'Ошибка: URL не определён'; return; }

    var body = buildPayload(actionCode, opts);
    var key = idempotencyKey();

    try {
      var resp = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrf(),
          'Idempotency-Key': key,
        },
        body: JSON.stringify(body),
      });

      if (resp.status === 202) {
        var data = await resp.json();
        var cmdId = data.command_id;
        statusEl.textContent = 'Команда принята (' + cmdId + '). Ожидание…';
        pollCommand(cmdId);
      } else if (resp.ok) {
        // order.preview returns a synchronous 200 with the full quote.
        // Transition the Drawer from "fill form" to "review + submit":
        // render the calculation, swap the primary button to
        // «Открыть позицию», keep the form DOM so `buildPayload` still
        // reads the same field values when the user confirms.
        if (actionCode === 'order.preview') {
          var preview;
          try { preview = await resp.json(); } catch (parseErr) { preview = null; }
          if (preview && preview.allowed) {
            showPreviewResult(preview, opts);
          } else {
            var reasons = (preview && preview.reasons) || [];
            statusEl.textContent = '✗ Preview отклонён: ' + (reasons.join('; ') || 'unknown');
            submitBtn.disabled = false;
          }
        } else {
          statusEl.textContent = 'Выполнено';
          refreshWorkspace();
          setTimeout(closeDrawer, 1500);
        }
      } else {
        var err = await resp.json().catch(function () { return {}; });
        statusEl.textContent = 'Отклонено (' + resp.status + '): ' + (err.detail || err.error || 'unknown');
        submitBtn.disabled = false;
      }
    } catch (e) {
      statusEl.textContent = 'Ошибка сети. Повторный поиск по ключу…';
      // Lost response: do NOT re-POST; search by same key
      statusEl.textContent = 'Нет связи. Команда не отправлена повторно.';
      submitBtn.disabled = false;
    }
  }

  function pollCommand(commandId) {
    var attempts = 0;
    pollTimer = setInterval(async function () {
      attempts++;
      if (attempts > MAX_POLL_ATTEMPTS) {
        clearInterval(pollTimer); pollTimer = null;
        statusEl.textContent = 'Результат не получен. Проверьте вручную.';
        submitBtn.disabled = false;
        return;
      }
      try {
        var resp = await fetch(RESULT_LOOKUP + encodeURIComponent(commandId), { credentials: 'same-origin' });
        if (!resp.ok) return;
        var data = await resp.json();
        if (data.status === 'completed') {
          clearInterval(pollTimer); pollTimer = null;
          statusEl.textContent = '✓ Выполнено';
          refreshWorkspace();
          setTimeout(closeDrawer, 1500);
        } else if (data.status === 'rejected') {
          clearInterval(pollTimer); pollTimer = null;
          statusEl.textContent = '✗ Отклонено: ' + (data.error || 'unknown');
          submitBtn.disabled = false;
        }
        // pending/accepted → keep polling
      } catch (e) { /* retry next interval */ }
    }, POLL_CMD_INTERVAL);
  }

  // ── Helpers ──
  function buildDynamicUrl(actionCode, opts) {
    var dayId = opts.day_id || '';
    var accId = opts.account_id || '';
    var posId = opts.position_id || '';
    switch (actionCode) {
      case 'order.preview': return '/api/paper/accounts/' + accId + '/orders/preview';
      case 'order.submit': return '/api/paper/accounts/' + accId + '/orders';
      case 'position.close': return '/api/paper/positions/' + posId + '/actions';
      case 'auto.pause': return '/api/trading-day/' + dayId + '/automation';
      case 'auto.resume': return '/api/trading-day/' + dayId + '/automation';
      case 'auto.close': return '/api/trading-day/' + dayId + '/automation';
      case 'day.finish': return '/api/trading-day/' + dayId + '/finish';
      default: return null;
    }
  }

  function buildPayload(actionCode, opts) {
    var p = {};
    switch (actionCode) {
      case 'order.preview':
      case 'order.submit':
        // Prefer live form inputs (B3 Drawer order-form); fall back to opts
        // so programmatic callers (fixtures, tests) keep working.
        var form = document.getElementById('wsOrderForm');
        if (form) {
          p = {
            instrument: (form.querySelector('[name="instrument"]') || {}).value || 'BTCUSDT',
            side:       (form.querySelector('[name="side"]:checked') || {}).value || 'buy',
            order_type: (form.querySelector('[name="order_type"]') || {}).value || 'market',
            risk:       (form.querySelector('[name="risk"]') || {}).value || '',
            leverage:   Number((form.querySelector('[name="leverage"]') || {}).value || 10),
            stop_price: (form.querySelector('[name="stop_price"]') || {}).value || null,
            take_profit: (form.querySelector('[name="take_profit"]') || {}).value || null,
          };
          if (p.take_profit === '') p.take_profit = null;
        } else {
          p = { instrument: opts.instrument || 'BTCUSDT', side: opts.side || 'buy',
                order_type: opts.order_type || 'market', risk: opts.risk || '10',
                leverage: opts.leverage || 10, stop_price: opts.stop_price || null,
                take_profit: opts.take_profit || null };
        }
        break;
      case 'position.close':
        p = { action: 'close' };
        break;
      case 'auto.pause': p = { action: 'pause' }; break;
      case 'auto.resume': p = { action: 'resume' }; break;
      case 'auto.close': p = { action: 'close_auto' }; break;
    }
    return p;
  }

  /**
   * B3 order-form: real inputs so the preview/submit contract is satisfied.
   * The form's own validation keeps `submitBtn` disabled until required
   * fields are present and stop_price sits on the correct side of entry.
   */
  function renderOrderForm(actionCode, opts) {
    var ask = Number(opts.market_ask || 0);
    var bid = Number(opts.market_bid || 0);
    // Suggested stop: 2% under ask for buy (worst case we can see).
    var suggestedStop = bid > 0 ? (bid * 0.98).toFixed(2) : '';
    var form = document.createElement('form');
    form.id = 'wsOrderForm';
    form.className = 'ws-drawer-form';
    form.innerHTML =
      '<div class="ws-form-row">'
      + '<label>Инструмент <input type="text" name="instrument" value="BTCUSDT" required></label>'
      + '<label>Тип <select name="order_type"><option value="market">market</option><option value="limit">limit</option></select></label>'
      + '</div>'
      + '<div class="ws-form-row">'
      + '<label class="ws-radio"><input type="radio" name="side" value="buy" checked> Long</label>'
      + '<label class="ws-radio"><input type="radio" name="side" value="sell"> Short</label>'
      + '</div>'
      + '<div class="ws-form-row">'
      + '<label>Риск USDT <input type="number" name="risk" step="0.1" min="0.1" max="100" required></label>'
      + '<label>Плечо <input type="number" name="leverage" min="1" max="20" value="10" required></label>'
      + '</div>'
      + '<div class="ws-form-row">'
      + '<label>Stop <input type="number" name="stop_price" step="0.01" min="0" value="' + suggestedStop + '" required></label>'
      + '<label>Take (опц.) <input type="number" name="take_profit" step="0.01" min="0"></label>'
      + '</div>'
      + '<p class="ws-form-hint">Рынок: bid=' + (bid || '—') + ' / ask=' + (ask || '—')
      + ' (' + esc(opts.market_quality || 'unknown') + ')</p>'
      + '<p class="ws-form-error" id="wsOrderFormError"></p>';
    bodyEl.appendChild(form);

    var errEl = form.querySelector('#wsOrderFormError');
    function revalidate() {
      var data = buildPayload(actionCode, opts);
      var reason = '';
      if (!data.instrument) reason = 'Instrument обязателен';
      else if (!data.risk || Number(data.risk) <= 0) reason = 'Риск должен быть &gt; 0';
      else if (!data.stop_price) reason = 'Stop обязателен';
      else {
        var stop = Number(data.stop_price);
        var entry = data.side === 'buy' ? ask : bid;
        if (entry > 0) {
          if (data.side === 'buy' && stop >= entry) reason = 'Stop должен быть ниже цены Long';
          if (data.side === 'sell' && stop <= entry) reason = 'Stop должен быть выше цены Short';
        }
      }
      errEl.innerHTML = reason;
      submitBtn.disabled = !!reason;
    }
    form.addEventListener('input', revalidate);
    form.addEventListener('change', revalidate);
    revalidate();
  }

  function buildContextHtml(code, opts) {
    var h = '<dl class="ws-drawer-fields">';
    h += '<dt>Действие</dt><dd>' + esc(code) + '</dd>';
    if (opts.day_id) h += '<dt>День</dt><dd>' + esc(opts.day_id) + '</dd>';
    if (opts.account_id) h += '<dt>Счёт</dt><dd>' + esc(opts.account_id) + '</dd>';
    if (opts.position_id) h += '<dt>Позиция</dt><dd>' + esc(opts.position_id) + '</dd>';
    h += '</dl>';
    return h;
  }

  function actionLabel(code) {
    var m = { 'day.start': 'Начать день', 'day.finish': 'Завершить день',
              'order.preview': 'Предпросмотр заявки',
              'order.submit': 'Открыть позицию', 'position.close': 'Закрыть позицию',
              'auto.pause': 'Пауза автомата', 'auto.resume': 'Возобновить автомат',
              'auto.close': 'Отключить автомат', 'settings.update': 'Настройки' };
    return m[code] || code;
  }

  // ── B3 preview→submit UX transition ──
  function showPreviewResult(preview, opts) {
    // Do NOT remove the form; keeping it lets `buildPayload('order.submit', ...)`
    // read the same values verbatim. We only append a summary and rewire Submit.
    var existing = document.querySelector('.ws-preview-result');
    if (existing) existing.remove();
    var box = document.createElement('div');
    box.className = 'ws-preview-result';
    var rows = [
      ['Цена входа', preview.entry_price + ' USDT'],
      ['Сумма (quantity)', preview.quantity],
      ['Notional', preview.notional + ' USDT'],
      ['Reserved margin', preview.reserved_margin + ' USDT'],
      ['Break-even', preview.break_even + ' USDT'],
      ['Ликвидация', preview.liquidation_price + ' USDT (' + preview.liquidation_quality + ')'],
      ['Комиссия вход/выход', preview.entry_fee + ' / ' + preview.estimated_exit_fee + ' USDT'],
      ['Net при стопе', preview.estimated_net_at_sl + ' USDT'],
    ];
    if (preview.estimated_net_at_tp) rows.push(['Net при цели', preview.estimated_net_at_tp + ' USDT']);
    if (preview.funding_rate) rows.push(['Funding rate', preview.funding_rate]);
    var html = '<h4 class="ws-preview-title">Предпросмотр рассчитан</h4><dl class="ws-drawer-fields">';
    for (var i = 0; i < rows.length; i++) {
      html += '<dt>' + esc(rows[i][0]) + '</dt><dd>' + esc(rows[i][1]) + '</dd>';
    }
    html += '</dl><p class="ws-preview-expire">Котка действует до ' + esc(preview.expires_at) + '.</p>';
    box.innerHTML = html;
    bodyEl.appendChild(box);
    // Rewire Submit button
    submitBtn.textContent = 'Открыть позицию';
    submitBtn.classList.add('ws-btn-primary');
    submitBtn.disabled = false;
    statusEl.textContent = 'Preview OK. Подтвердите открытие.';
    submitBtn.onclick = function () {
      // Same opts, action becomes order.submit. buildPayload reads DOM values.
      executeCommand('order.submit', opts);
    };
  }

  function refreshWorkspace() {
    // Trigger the state controller to re-poll
    if (window.__wsPoll) window.__wsPoll();
  }

  function esc(v) { var d = document.createElement('div'); d.textContent = v || ''; return d.innerHTML; }

  // ── Expose drawer API for workspace-state.js ──
  window.WsDrawer = { open: openDrawer, close: closeDrawer };
})();
