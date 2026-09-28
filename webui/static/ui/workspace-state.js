/**
 * workspace-state.js — polling controller for /workspace B2 (read-only).
 * Fetches /api/workspace/state, updates 4 zones. No commands dispatched.
 */
(function () {
  'use strict';

  var POLL_INTERVAL = 5000;
  var root = document.querySelector('[data-controller="workspace"]');
  if (!root) return;

  var stateUrl = root.getAttribute('data-state-url') || '/api/workspace/state';
  var els = {
    stage: document.getElementById('wsStage'),
    date: document.getElementById('wsDate'),
    market: document.getElementById('wsMarket'),
    accounts: document.getElementById('wsAccounts'),
    pending: document.getElementById('wsPending'),
    stepper: document.getElementById('wsStepper'),
    context: document.getElementById('wsAccountsPanel'),
    inspector: document.getElementById('wsInspector'),
    attentionList: document.getElementById('wsAttentionList'),
  };

  var timer = null;
  var lastState = null;

  async function poll() {
    try {
      var resp = await fetch(stateUrl, { credentials: 'same-origin' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var data = await resp.json();
      lastState = data;
      render(data);
    } catch (e) {
      // Keep last snapshot, mark stale
      if (lastState) {
        els.market.textContent = 'рынок: устарело (' + (lastState.as_of || '?') + ')';
      } else {
        els.stage.textContent = 'Источник недоступен';
      }
    }
  }

  function render(s) {
    // Status bar
    var sb = s.status_bar || {};
    els.stage.textContent = stageLabel(sb.stage);
    els.date.textContent = sb.day_date || '—';
    els.market.textContent = 'рынок: ' + (sb.market_quality || '?');
    els.accounts.textContent = formatAccounts(sb.accounts);
    els.pending.textContent = sb.pending_commands > 0
      ? '⏳ ' + sb.pending_commands + ' команд' : '';
    // F08: operational mode badge
    var mode = s.trader_mode || 'trade';
    els.market.textContent += ' | режим: ' + mode;

    // Stepper
    var steps = els.stepper ? els.stepper.querySelectorAll('.ws-step') : [];
    var reached = false;
    for (var i = 0; i < steps.length; i++) {
      steps[i].classList.remove('is-active', 'is-done');
      if (!reached && steps[i].getAttribute('data-stage') === sb.stage) {
        steps[i].classList.add('is-active');
        reached = true;
      } else if (!reached) {
        steps[i].classList.add('is-done');
      }
    }

    // Attention queue
    renderAttention(s.attention || []);

    // Accounts context
    var dayId = (s.day && (s.day.day_id || s.day.id)) || null;
    renderAccounts(s.accounts || [], s.objects || [], s.capabilities || {}, dayId);
  }

  function stageLabel(stage) {
    var m = { prepare: 'Подготовка', work: 'Работа', finishing: 'Завершение', results: 'Итоги', learning: 'Обучение', blocked: 'Заблокировано' };
    return m[stage] || stage || '—';
  }

  function formatAccounts(accounts) {
    if (!accounts || !accounts.length) return '—';
    return accounts.map(function (a) {
      return a.kind + ': ' + (a.equity || '—') + ' USDT';
    }).join(' | ');
  }

  function renderAttention(items) {
    if (!els.attentionList) return;
    els.attentionList.innerHTML = '';
    if (!items.length) {
      els.attentionList.innerHTML = '<li class="ws-attention-item ws-attention-info">Активных проблем нет</li>';
      return;
    }
    for (var i = 0; i < items.length; i++) {
      var li = document.createElement('li');
      li.className = 'ws-attention-item ws-attention-' + (items[i].severity || 'info');
      li.textContent = items[i].message || items[i].reason_code;
      li.setAttribute('data-reason', items[i].reason_code || '');
      els.attentionList.appendChild(li);
    }
  }

  function renderAccounts(accounts, objects, caps, dayId) {
    if (!els.context) return;
    var html = '';
    var commandsOn = !!caps.commands_enabled;
    for (var i = 0; i < accounts.length; i++) {
      var acc = accounts[i];
      var kind = acc.kind || '?';
      // Domain uses `id`; some fixtures use `account_id`. Accept both so
      // the Drawer click payload always carries a real UUID.
      var accId = esc(acc.account_id || acc.id || '');
      html += '<div class="ws-account-card" data-kind="' + esc(kind) + '" data-account-id="' + accId + '">';
      html += '<h4 class="ws-account-title">' + esc(kind === 'manual' ? 'Ручной счёт' : 'Автоматический счёт') + '</h4>';
      html += '<dl class="ws-account-stats">';
      html += '<div><dt>Equity</dt><dd>' + esc(str(acc.equity || acc.cash)) + ' USDT</dd></div>';
      html += '<div><dt>Позиции</dt><dd>' + (acc.open_positions || 0) + '</dd></div>';
      html += '<div><dt>Реализовано</dt><dd>' + esc(str(acc.realized_net)) + '</dd></div>';
      html += '</dl>';
      // Objects for this account
      var accObjects = objects.filter(function (o) { return o.object_ref && o.object_ref.account_id === kind; });
      if (accObjects.length) {
        html += '<ul class="ws-object-list">';
        for (var j = 0; j < accObjects.length; j++) {
          var obj = accObjects[j];
          html += '<li class="ws-object-item" data-obj-id="' + esc(obj.object_ref.id) + '" data-obj-kind="' + esc(obj.object_ref.kind) + '">';
          html += esc(obj.summary || obj.type);
          if (commandsOn && obj.object_ref.kind === 'position') {
            html += ' <button type="button" class="ws-btn-mini" data-action="position.close" '
                 +  'data-pos-id="' + esc(obj.object_ref.id) + '" data-account-id="' + accId + '">Закрыть</button>';
          }
          html += '</li>';
        }
        html += '</ul>';
      }
      // Action buttons wired to Command Drawer (B3+)
      if (commandsOn) {
        html += '<div class="ws-account-actions">';
        if (kind === 'manual' && caps.can_enter) {
          html += '<button type="button" class="ws-btn" data-action="order.preview" '
               +  'data-account-id="' + accId + '">Новая заявка</button>';
        }
        if (kind === 'auto') {
          if (caps.can_pause_auto) {
            html += '<button type="button" class="ws-btn" data-action="auto.pause" '
                 +  'data-account-id="' + accId + '" data-day-id="' + esc(dayId || '') + '">Пауза</button>';
          }
          if (caps.can_resume_auto) {
            html += '<button type="button" class="ws-btn" data-action="auto.resume" '
                 +  'data-account-id="' + accId + '" data-day-id="' + esc(dayId || '') + '">Возобновить</button>';
          }
          if (caps.can_close_auto) {
            html += '<button type="button" class="ws-btn ws-btn-danger" data-action="auto.close" '
                 +  'data-account-id="' + accId + '" data-day-id="' + esc(dayId || '') + '">Стоп авто</button>';
          }
        }
        html += '</div>';
      } else {
        html += '<p class="ws-action-disabled">Команды будут доступны при подключении к домену.</p>';
      }
      html += '</div>';
    }
    if (!html) {
      html = '<div class="wored-empty-state">';
      if (commandsOn && caps.can_start) {
        html += 'Нет активного дня. '
             + '<button type="button" class="ws-btn ws-btn-primary" data-action="day.start">Начать день</button>';
      } else if (commandsOn && caps.can_finish && dayId) {
        html += 'День активен, но данных нет. '
             + '<button type="button" class="ws-btn ws-btn-danger" data-action="day.finish" '
             +  'data-day-id="' + esc(dayId) + '">Завершить день</button>';
      } else if (caps.can_start) {
        html += 'Нет активного дня. Ожидание домена для запуска.';
      } else {
        html += 'Ожидание данных…';
      }
      html += '</div>';
    }
    els.context.innerHTML = html;
    wireDrawerButtons(els.context);
  }

  function wireDrawerButtons(scope) {
    if (!window.WsDrawer) return;
    var btns = scope.querySelectorAll('[data-action]');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].__wsWired) continue;
      btns[i].__wsWired = true;
      btns[i].addEventListener('click', function (ev) {
        var el = ev.currentTarget;
        // Pass the current market snapshot so Drawer can compute a
        // suggested stop_price for order.* actions. `lastState.market` is
        // the same object the read model returned to `render()`.
        var mkt = (lastState && lastState.market) || {};
        window.WsDrawer.open(el.getAttribute('data-action'), {
          account_id: el.getAttribute('data-account-id') || '',
          position_id: el.getAttribute('data-pos-id') || '',
          day_id: el.getAttribute('data-day-id') || '',
          market_ask: mkt.ask || '',
          market_bid: mkt.bid || '',
          market_mark: mkt.mark || '',
          market_quality: mkt.quality || 'unknown'
        });
      });
    }
  }

  function str(v) { return v != null ? String(v) : '—'; }
  function esc(v) { var d = document.createElement('div'); d.textContent = v || ''; return d.innerHTML; }

  // Start polling
  poll();
  timer = setInterval(poll, POLL_INTERVAL);

  // Cleanup on page navigation (SPA-like if needed)
  window.addEventListener('beforeunload', function () {
    if (timer) clearInterval(timer);
  });
})();
