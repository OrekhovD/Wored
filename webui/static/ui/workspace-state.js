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
    renderAccounts(s.accounts || [], s.objects || [], s.capabilities || {});
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

  function renderAccounts(accounts, objects, caps) {
    if (!els.context) return;
    var html = '';
    for (var i = 0; i < accounts.length; i++) {
      var acc = accounts[i];
      html += '<div class="ws-account-card" data-kind="' + esc(acc.kind || '?') + '">';
      html += '<h4 class="ws-account-title">' + esc(acc.kind === 'manual' ? 'Ручной счёт' : 'Автоматический счёт') + '</h4>';
      html += '<dl class="ws-account-stats">';
      html += '<div><dt>Equity</dt><dd>' + esc(str(acc.equity || acc.cash)) + ' USDT</dd></div>';
      html += '<div><dt>Позиции</dt><dd>' + (acc.open_positions || 0) + '</dd></div>';
      html += '<div><dt>Реализовано</dt><dd>' + esc(str(acc.realized_net)) + '</dd></div>';
      html += '</dl>';
      // Objects for this account
      var accObjects = objects.filter(function (o) { return o.object_ref && o.object_ref.account_id === acc.kind; });
      if (accObjects.length) {
        html += '<ul class="ws-object-list">';
        for (var j = 0; j < accObjects.length; j++) {
          var obj = accObjects[j];
          html += '<li class="ws-object-item" data-obj-id="' + esc(obj.object_ref.id) + '" data-obj-kind="' + esc(obj.object_ref.kind) + '">';
          html += esc(obj.summary || obj.type);
          html += '</li>';
        }
        html += '</ul>';
      }
      // Disabled action buttons (B2)
      if (!caps.commands_enabled) {
        html += '<p class="ws-action-disabled">Действия будут доступны в V2 B3</p>';
      }
      html += '</div>';
    }
    if (!html) {
      html = '<div class="wored-empty-state">Нет активного дня. '
        + (caps.can_start ? 'Готов к запуску.' : 'Ожидание данных…')
        + '</div>';
    }
    els.context.innerHTML = html;
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
