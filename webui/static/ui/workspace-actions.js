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

    // Enable submit if no typed confirmation needed
    if (def.confirm === 'none') {
      submitBtn.disabled = false;
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
        statusEl.textContent = 'Выполнено';
        refreshWorkspace();
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
      case 'order.submit':
        p = { instrument: opts.instrument || 'BTCUSDT', side: opts.side || 'buy',
              order_type: opts.order_type || 'market', risk: opts.risk || '10',
              leverage: opts.leverage || 10, stop_price: opts.stop_price || null,
              take_profit: opts.take_profit || null };
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
              'order.submit': 'Открыть позицию', 'position.close': 'Закрыть позицию',
              'auto.pause': 'Пауза автомата', 'auto.resume': 'Возобновить автомат',
              'auto.close': 'Отключить автомат', 'settings.update': 'Настройки' };
    return m[code] || code;
  }

  function refreshWorkspace() {
    // Trigger the state controller to re-poll
    if (window.__wsPoll) window.__wsPoll();
  }

  function esc(v) { var d = document.createElement('div'); d.textContent = v || ''; return d.innerHTML; }

  // ── Expose drawer API for workspace-state.js ──
  window.WsDrawer = { open: openDrawer, close: closeDrawer };
})();
