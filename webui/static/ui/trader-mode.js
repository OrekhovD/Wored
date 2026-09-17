/**
 * trader-mode.js — WORED Trader Deck mode buttons (trade/reduce_only/pause)
 *
 * Features:
 *   - POST to /api/trader/mode with idempotency key
 *   - Visual state management (pill + button aria-pressed)
 *   - Confirmation for pause
 */
(function () {
  'use strict';

  var currentMode = 'trade';
  var MODE_MAP = {
    trade: ['tr-live', 'TRADE'],
    reduce_only: ['tr-demo', 'ТОЛЬКО ЗАКРЫТИЕ'],
    pause: ['tr-risk', 'PAUSED']
  };

  function setModeVisual(mode) {
    currentMode = mode;
    var pill = document.getElementById('trModePill');
    if (pill) {
      var m = MODE_MAP[mode] || MODE_MAP.trade;
      pill.className = 'tr-pill ' + m[0];
      pill.innerHTML = '<span class="tr-dot"></span>' + m[1];
    }
    var btnReduce = document.getElementById('trBtnReduce');
    if (btnReduce) btnReduce.setAttribute('aria-pressed', String(mode === 'reduce_only'));
    var btnPause = document.getElementById('trBtnPause');
    if (btnPause) btnPause.setAttribute('aria-pressed', String(mode === 'pause'));
  }

  function showToast(message, level) {
    if (window.WORED_TOAST) {
      window.WORED_TOAST(message, level);
    } else {
      console.log('[trader-mode]', message);
    }
  }

  async function postMode(mode, reason) {
    var idempotencyKey = 'mode-' + mode + '-' + Date.now();
    try {
      var resp = await fetch('/api/trader/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ mode: mode, idempotency_key: idempotencyKey, reason: reason || '' })
      });
      if (resp.ok) {
        var data = await resp.json();
        if (data.applied) {
          setModeVisual(data.mode);
          showToast('Режим → ' + (MODE_MAP[data.mode] || ['', data.mode])[1], 'ok');
        } else {
          showToast('Режим уже установлен: ' + data.mode, 'ok');
        }
        return data;
      } else if (resp.status === 401) {
        showToast('Требуется авторизация', 'bad');
      } else if (resp.status === 422) {
        showToast('Недопустимый режим', 'bad');
      } else {
        showToast('Ошибка смены режима (' + resp.status + ')', 'bad');
      }
    } catch (e) {
      showToast('Сеть недоступна', 'bad');
      console.error('[trader-mode] POST failed', e);
    }
    return null;
  }

  function handleReduce() {
    if (currentMode === 'reduce_only') {
      postMode('trade', 'resume from reduce_only');
    } else {
      postMode('reduce_only', 'switched to reduce_only');
    }
  }

  function handlePause() {
    if (currentMode === 'pause') {
      postMode('trade', 'resume from pause');
    } else {
      // Confirmation for pause
      var confirmed = window.confirm(
        'Пауза остановит новые входы автомата.\n' +
        'Открытые позиции останутся под защитой.\n\n' +
        'Продолжить?'
      );
      if (confirmed) {
        postMode('pause', 'manual pause confirmed');
      }
    }
  }

  async function loadCurrentMode() {
    try {
      var resp = await fetch('/api/trader/state', { credentials: 'same-origin' });
      if (resp.ok) {
        var data = await resp.json();
        if (data.mode) setModeVisual(data.mode);
      }
    } catch (e) {
      console.error('[trader-mode] state fetch failed', e);
    }
  }

  function init() {
    var btnReduce = document.getElementById('trBtnReduce');
    if (btnReduce) btnReduce.addEventListener('click', handleReduce);
    var btnPause = document.getElementById('trBtnPause');
    if (btnPause) btnPause.addEventListener('click', handlePause);
    loadCurrentMode();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.TraderMode = { postMode: postMode, setModeVisual: setModeVisual };
})();