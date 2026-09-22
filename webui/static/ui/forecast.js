/**
 * WORED Forecast Submit/Poll/Restore — UI-06
 * Handles forecast form submission, idempotency, polling, and restore after reload.
 * Depends on: core.js (WORED), async-patterns.js (pollJobStatus)
 */
import WORED from './core.js?v=20260910-1';
import { pollJobStatus, showBlockError, clearBlockError } from './async-patterns.js?v=20260922-1';

const STORAGE_KEY = 'wored.ui.forecast.v1';

// ── Init ─────────────────────────────────────────────────────────────────────

function initForecastForm() {
  const form = document.getElementById('forecastForm');
  if (!form) return;

  const resultEl = document.getElementById('forecastResult');
  const submitBtn = document.getElementById('forecastSubmit');
  const pendingEl = document.getElementById('forecastPending');

  // Horizon hint: "N × timeframe = duration"
  const horizonInput = document.getElementById('forecastHorizon');
  const timeframeSelect = document.getElementById('forecastTimeframe');
  const hintEl = document.getElementById('forecastHorizonHint');

  const TIMEFRAME_MINUTES = {
    '1min': 1, '5min': 5, '15min': 15, '30min': 30,
    '60min': 60, '4hour': 240, '1day': 1440,
  };

  function updateHorizonHint() {
    const h = parseInt(horizonInput.value) || 1;
    const tf = timeframeSelect.value;
    const mins = (TIMEFRAME_MINUTES[tf] || 60) * h;
    const tfLabel = timeframeSelect.options[timeframeSelect.selectedIndex].text;
    if (mins < 60) {
      hintEl.textContent = h + ' × ' + tfLabel + ' = ' + mins + ' мин';
    } else if (mins < 1440) {
      hintEl.textContent = h + ' × ' + tfLabel + ' = ' + (mins / 60).toFixed(1) + ' ч';
    } else {
      hintEl.textContent = h + ' × ' + tfLabel + ' = ' + (mins / 1440).toFixed(1) + ' д';
    }
  }

  horizonInput.addEventListener('input', updateHorizonHint);
  timeframeSelect.addEventListener('change', updateHorizonHint);
  updateHorizonHint();

  // Shortcut buttons
  document.querySelectorAll('.forecast-shortcuts button[data-h]').forEach(btn => {
    btn.addEventListener('click', () => {
      horizonInput.value = btn.dataset.h;
      updateHorizonHint();
    });
  });

  // ── Restore pending forecast ──────────────────────────────────────────
  const pending = WORED.loadPendingForecast();
  if (pending && pending.requestId) {
    // Check if URL already has the request ID
    const urlMatch = location.pathname.match(/^\/predictions\/(\d+)/);
    if (!urlMatch || urlMatch[1] !== String(pending.requestId)) {
      // Show pending state and poll
      if (resultEl) {
        resultEl.innerHTML = '<div class="wored-loading-state">Проверяем прогноз #' + pending.requestId + '…</div>';
      }
      pollPendingForecast(pending.requestId, pending.deadlineAt);
    }
  }

  // ── Submit handler ───────────────────────────────────────────────────────
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submitBtn) { submitBtn.disabled = true; submitBtn.setAttribute('aria-busy', 'true'); }
    if (pendingEl) pendingEl.hidden = false;
    clearBlockError(form);

    const formData = new FormData(form);
    const symbol = formData.get('symbol');
    const timeframe = formData.get('timeframe');
    const horizonSteps = parseInt(formData.get('horizon_steps'));
    const depth = parseInt(formData.get('depth'));
    const csrfToken = formData.get('csrf_token');

    // Canonical payload
    const payload = { symbol, timeframe, horizon_steps: horizonSteps, depth };

    // Generate idempotency key BEFORE POST
    const key = crypto.randomUUID();

    // Save to sessionStorage BEFORE POST (for restore)
    const submittedAt = Date.now();
    const deadlineAt = submittedAt + 10 * 60 * 1000; // 10 min
    WORED.savePendingForecast({
      key, payload, requestId: null, submittedAt, deadlineAt,
    });

    try {
      const resp = await fetch('/api/predictions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
          'Idempotency-Key': key,
        },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });

      if (resp.status === 202) {
        const data = await resp.json();
        const requestId = data.request_id || data.id;
        // Update sessionStorage with request ID
        WORED.savePendingForecast({
          key, payload, requestId, submittedAt, deadlineAt,
        });
        // Navigate to detail
        const newUrl = '/predictions/' + requestId;
        history.pushState({ requestId }, '', newUrl);
        // Start polling
        pollPendingForecast(requestId, deadlineAt);
      } else if (resp.status === 401) {
        const next = encodeURIComponent(location.pathname + location.search);
        location.href = '/login?next=' + next;
      } else if (resp.status === 429) {
        let retryAt = null;
        try { const d = await resp.json(); retryAt = d.retry_at; } catch {}
        const msg = 'Лимит запросов исчерпан' + (retryAt ? '. Повтор возможен в ' + WORED.fmtTime(retryAt, { title: false }) : '');
        showBlockError(form, msg);
      } else {
        let detail = 'Ошибка ' + resp.status;
        try { const d = await resp.json(); detail = d.detail || detail; } catch {}
        showBlockError(form, typeof detail === 'string' ? detail : 'Проверьте введённые значения.');
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        showBlockError(form, 'Ответ не получен. Проверяем, было ли действие выполнено.');
      }
    } finally {
      if (submitBtn) { submitBtn.disabled = false; submitBtn.removeAttribute('aria-busy'); }
      if (pendingEl) pendingEl.hidden = true;
    }
  });
}

// ── Poll pending forecast ─────────────────────────────────────────────────────

function pollPendingForecast(requestId, deadlineAt) {
  const resultEl = document.getElementById('forecastResult');
  const url = '/api/forecast/' + requestId + '/status';
  const deadlineMs = deadlineAt ? new Date(deadlineAt).getTime() : Date.now() + 10 * 60 * 1000;

  pollJobStatus(url, {
    onUpdate(data) {
      if (!resultEl) return;
      if (data._deadline) {
        resultEl.innerHTML = '<div class="wored-empty-state">Время ожидания вышло; состояние уточняется. <button class="wored-ghost-btn" onclick="location.reload()">Проверить состояние</button></div>';
        return;
      }
      const es = data.execution_state;
      const sl = WORED.stateLabel(es, data.status);
      let html = '<div class="forecast-progress ' + sl.cssClass + '">';
      html += '<span>' + sl.label + '</span>';
      if (data.as_of) html += '<span class="forecast-progress-time">Создан ' + WORED.fmtTime(data.as_of, { title: false }) + '</span>';
      html += '</div>';
      resultEl.innerHTML = html;
    },
    onTerminal(data) {
      if (!resultEl) return;
      const es = data.execution_state;
      if (es === 'completed' || (!es && data.status === 'active')) {
        WORED.clearPendingForecast();
        resultEl.innerHTML = '<div class="ui-text-success">Прогноз #' + requestId + ' готов. <a href="/predictions/' + requestId + '">Открыть</a></div>';
      } else if (es === 'partial') {
        // A role of the bundle failed: the forecast exists but the band rests on
        // fewer voices than the deck implies. Saying "готов" here would repeat the
        // lie the status column used to carry (review M7).
        WORED.clearPendingForecast();
        resultEl.innerHTML = '<div class="ui-text-accent">Прогноз #' + requestId + ' готов частично: ' + describeRoleGaps(data.roles) + '. <a href="/predictions/' + requestId + '">Открыть</a></div>';
      } else if (es === 'failed') {
        WORED.clearPendingForecast();
        resultEl.innerHTML = '<div class="ui-text-danger">Прогноз #' + requestId + ' завершился ошибкой: ' + (data.failure_code || 'нет результата') + '</div>'
          + '<div class="wored-empty-state"><button class="wored-btn" id="forecastRetryBtn">Создать новый расчёт</button></div>';
        // A25: Retry creates a SEPARATE new request with a new key
        const retryBtn = document.getElementById('forecastRetryBtn');
        if (retryBtn) retryBtn.addEventListener('click', () => {
          // Clear old pending and reset form for new submission
          WORED.clearPendingForecast();
          resultEl.innerHTML = '';
          // Focus the submit button for new request
          const submitBtn = document.getElementById('forecastSubmit');
          if (submitBtn) submitBtn.focus();
        });
      } else if (es === 'expired') {
        WORED.clearPendingForecast();
        resultEl.innerHTML = '<div class="ui-text-accent">Прогноз #' + requestId + ' истёк</div>'
          + '<div class="wored-empty-state"><button class="wored-btn" id="forecastRetryBtn">Создать новый расчёт</button></div>';
        const retryBtn = document.getElementById('forecastRetryBtn');
        if (retryBtn) retryBtn.addEventListener('click', () => {
          WORED.clearPendingForecast();
          resultEl.innerHTML = '';
          const submitBtn = document.getElementById('forecastSubmit');
          if (submitBtn) submitBtn.focus();
        });
      }
    },
    deadlineAt: deadlineMs,
  });
}

// ── Role coverage wording ───────────────────────────────────────────────────

const ROLE_VOCABULARY = { bull: 'Bull', bear: 'Bear', arbiter: 'арбитр' };

/** Human-readable summary of which bundle voices actually answered. */
function describeRoleGaps(roles) {
  if (!Array.isArray(roles) || !roles.length) return 'часть голосов бандла не ответила';
  const byRole = {};
  roles.forEach(function (r) { if (r && r.role) byRole[r.role] = r; });
  const names = Object.keys(ROLE_VOCABULARY);
  // A role with no run row at all never answered; a run that completed without
  // points is not an opinion either - both are gaps.
  const missing = names.filter(function (r) {
    return !byRole[r] || byRole[r].state !== 'completed';
  });
  if (!missing.length) return 'ответили все три голоса';
  const answered = names.length - missing.length;
  const missingLabels = missing.map(function (r) { return ROLE_VOCABULARY[r]; }).join(', ');
  return 'ответили ' + answered + ' голоса из ' + names.length + ', нет: ' + missingLabels;
}

// ── Auto-init on DOM ready ───────────────────────────────────────────────────

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initForecastForm);
} else {
  initForecastForm();
}

export { initForecastForm, pollPendingForecast };
