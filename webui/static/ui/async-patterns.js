/**
 * WORED UI Async Patterns — UI-03
 * Freshness tracking, data status states, error rendering, POST lifecycle.
 * Depends on: core.js (WORED.apiFetch, WORED.Poller, WORED.NA, WORED.fmtTime)
 */
import WORED from './core.js?v=20260910-1';

// ── Freshness tracker ───────────────────────────────────────────────────────────

class Freshness {
  /**
   * @param {Object} opts
   * @param {number} opts.staleAfterSeconds - default 60 for ticker
   * @param {Function} opts.onUpdate - callback({state, label, ageSeconds, reasonCode})
   */
  constructor({ staleAfterSeconds = 60, onUpdate = null } = {}) {
    this._staleAfter = staleAfterSeconds;
    this._onUpdate = onUpdate;
    this._asOf = null;        // ISO from backend
    this._fresh = null;       // bool from backend
    this._reasonCode = null;
    this._ageTimer = null;
    this._syncedAt = null;    // when we last synced (for clock drift)
  }

  /** Update from backend snapshot data */
  sync(snapshot) {
    if (!snapshot) {
      this._asOf = null;
      this._fresh = null;
      this._reasonCode = 'no_data';
      this._notify();
      return;
    }
    this._asOf = snapshot.as_of || null;
    this._fresh = snapshot.fresh != null ? snapshot.fresh : null;
    this._reasonCode = snapshot.reason_code || null;
    if (snapshot.stale_after_seconds) this._staleAfter = snapshot.stale_after_seconds;
    this._syncedAt = Date.now();
    this._notify();
    this._startAgeTimer();
  }

  /** Mark as offline / connection error */
  markOffline() {
    this._fresh = false;
    this._reasonCode = 'connection_error';
    this._notify();
  }

  /** Get current state */
  getState() {
    if (!this._asOf) {
      return { state: 'unknown', label: 'Нет данных', ageSeconds: null, reasonCode: this._reasonCode };
    }
    const ageMs = Date.now() - new Date(this._asOf).getTime();
    const ageSec = Math.floor(ageMs / 1000);
    if (this._fresh === false) {
      return { state: 'stale', label: 'Данные устарели', ageSeconds: ageSec, reasonCode: this._reasonCode || 'stale' };
    }
    if (ageSec > this._staleAfter) {
      return { state: 'stale', label: 'Данные устарели', ageSeconds: ageSec, reasonCode: 'age_exceeded' };
    }
    return { state: 'fresh', label: 'Данные доступны', ageSeconds: ageSec, reasonCode: null };
  }

  _notify() {
    if (this._onUpdate) this._onUpdate(this.getState());
  }

  _startAgeTimer() {
    if (this._ageTimer) clearInterval(this._ageTimer);
    // Check every 5s if we crossed the staleness threshold
    this._ageTimer = setInterval(() => {
      const s = this.getState();
      if (s.state === 'stale' || s.state === 'unknown') {
        this._notify();
        if (this._ageTimer) { clearInterval(this._ageTimer); this._ageTimer = null; }
      }
    }, 5000);
  }

  destroy() {
    if (this._ageTimer) { clearInterval(this._ageTimer); this._ageTimer = null; }
  }
}

// ── Data status banner ─────────────────────────────────────────────────────────

const DATA_STATES = {
  fresh:    { label: 'Данные доступны',     cssClass: 'ui-text-success' },
  stale:    { label: 'Данные устарели',     cssClass: 'ui-text-accent' },
  offline:  { label: 'Нет связи',           cssClass: 'ui-text-danger' },
  unknown:  { label: 'Нет данных',          cssClass: 'ui-text-muted' },
  loading:  { label: 'Загрузка…',           cssClass: 'ui-text-info' },
};

function renderDataStatus(el, freshnessState) {
  if (!el) return;
  const s = DATA_STATES[freshnessState] || DATA_STATES.unknown;
  el.textContent = s.label;
  el.className = 'wored-data-status ' + s.cssClass;
}

// ── Error renderer (UI-03 POST error handling) ─────────────────────────────────

const HTTP_ERROR_MAP = {
  400: { label: 'Ошибка в данных', hint: 'Проверьте введённые значения.' },
  401: { label: 'Сессия истекла', hint: 'Войдите заново.', redirect: '/login' },
  403: { label: 'Недостаточно прав', hint: 'Доступ к этому действию не разрешён.' },
  409: { label: 'Конфликт', hint: 'Действие уже выполнено или состояние изменилось.' },
  422: { label: 'Ошибка в данных', hint: 'Проверьте введённые значения.' },
  429: { label: 'Лимит запросов исчерпан', hint: 'Подождите перед повтором.' },
  503: { label: 'Сервис временно недоступен', hint: 'Попробуйте позже.' },
};

function renderHttpError(status, body) {
  const mapped = HTTP_ERROR_MAP[status];
  if (!mapped) {
    return { label: 'Ошибка ' + status, hint: '', redirect: null };
  }
  let hint = mapped.hint;
  // 429 with server retry_at
  if (status === 429 && body && body.retry_at) {
    hint = 'Повтор возможен в ' + WORED.fmtTime(body.retry_at, { title: false });
  }
  // 400/422 with field errors
  if ((status === 400 || status === 422) && body && body.detail) {
    hint = typeof body.detail === 'string' ? body.detail : 'Проверьте введённые значения.';
  }
  return { label: mapped.label, hint, redirect: mapped.redirect || null };
}

// ── POST lifecycle helper ───────────────────────────────────────────────────────

/**
 * Perform a POST with UI-03 lifecycle:
 * - Disable button synchronously before first await
 * - Set aria-busy on form/container
 * - Map HTTP errors via renderHttpError
 * - Re-enable on completion
 * - Does NOT auto-retry
 * @returns {Promise<{ok: boolean, data: any, error: string|null}>}
 */
async function postAction(url, { button = null, container = null, body = null, csrfToken = null, idempotencyKey = null, signal = null } = {}) {
  const btn = button;
  const wasDisabled = btn ? btn.disabled : false;
  if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
  if (container) container.setAttribute('aria-busy', 'true');

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

    const resp = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });

    let data = null;
    try { data = await resp.json(); } catch { /* non-JSON response */ }

    if (!resp.ok) {
      const err = renderHttpError(resp.status, data);
      if (err.redirect && resp.status === 401) {
        const next = encodeURIComponent(location.pathname + location.search);
        location.href = err.redirect + '?next=' + next;
      }
      return { ok: false, data, error: err.label + (err.hint ? '. ' + err.hint : '') };
    }

    return { ok: true, data, error: null };
  } catch (err) {
    if (err.name === 'AbortError') {
      return { ok: false, data: null, error: 'Запрос отменён' };
    }
    // Timeout / network error
    return { ok: false, data: null, error: 'Ответ не получен. Проверяем, было ли действие выполнено.' };
  } finally {
    if (btn) { btn.disabled = wasDisabled; btn.removeAttribute('aria-busy'); }
    if (container) container.removeAttribute('aria-busy');
  }
}

// ── Error banner helper ────────────────────────────────────────────────────────

function showBlockError(container, message, { keepPrevious = true } = {}) {
  if (!container) return;
  // Find or create error element
  let errEl = container.querySelector('.wored-block-error');
  if (!errEl) {
    errEl = document.createElement('div');
    errEl.className = 'wored-block-error ui-text-danger';
    errEl.setAttribute('role', 'alert');
    container.prepend(errEl);
  }
  errEl.textContent = message;
  errEl.style.display = message ? '' : 'none';
}

function clearBlockError(container) {
  if (!container) return;
  const errEl = container.querySelector('.wored-block-error');
  if (errEl) errEl.style.display = 'none';
}

// ── Execution state polling (UI-03 job lifecycle) ───────────────────────────────

const POLL_INTERVALS = {
  job: 3000,       // forecast job status
  deck: 15000,     // command deck
  session: 15000, // daily session
  system: 30000,   // system page
};

const BACKOFF_SCHEDULE = [3000, 6000, 12000, 30000];

/**
 * Poll a job status endpoint with proper execution_state handling.
 * @param {string} url - status endpoint URL
 * @param {Object} opts
 * @param {Function} opts.onUpdate - called with parsed status data
 * @param {Function} opts.onTerminal - called when job reaches terminal state
 * @param {number} opts.deadlineAt - epoch ms, stop fast polling after this
 * @param {AbortSignal} opts.signal - external abort
 */
async function pollJobStatus(url, { onUpdate = null, onTerminal = null, deadlineAt = null, signal = null } = {}) {
  let errorCount = 0;
  let generation = 0;
  const myGen = ++generation;

  while (true) {
    if (signal && signal.aborted) return;
    if (myGen !== generation) return; // superseded

    let data = null;
    let ok = false;
    try {
      const resp = await WORED.apiFetch(url, { resourceKey: 'job:' + url, timeout: 10000, signal });
      ok = resp.ok;
      if (resp.status === 401) {
        const next = encodeURIComponent(location.pathname + location.search);
        location.href = '/login?next=' + next;
        return;
      }
      data = await resp.json();
      errorCount = 0;
    } catch (err) {
      if (err.name === 'AbortError') return;
      errorCount++;
      data = null;
    }

    if (data) {
      const es = data.execution_state || null;
      const legacy = data.status || null;
      const isTerminal = ['completed', 'failed', 'expired'].includes(es) ||
                         (es === null && ['active', 'completed', 'failed'].includes(legacy));
      if (onUpdate) onUpdate(data);

      if (isTerminal) {
        if (onTerminal) onTerminal(data);
        return;
      }
    }

    // Check deadline
    const now = Date.now();
    if (deadlineAt && now >= deadlineAt) {
      if (onUpdate) onUpdate({ _deadline: true, label: 'Время ожидания вышло; состояние уточняется' });
      // One final GET
      try {
        const r = await WORED.apiFetch(url, { resourceKey: 'job:' + url, timeout: 10000 });
        if (r.ok) {
          const finalData = await r.json();
          if (onUpdate) onUpdate(finalData);
        }
      } catch { /* ignore */ }
      return;
    }

    // Calculate delay
    const delay = errorCount > 0
      ? (BACKOFF_SCHEDULE[Math.min(errorCount - 1, BACKOFF_SCHEDULE.length - 1)] || 30000)
      : POLL_INTERVALS.job;

    await new Promise(resolve => setTimeout(resolve, delay));
  }
}

// ── Export ──────────────────────────────────────────────────────────────────────

export {
  Freshness,
  DATA_STATES,
  renderDataStatus,
  renderHttpError,
  postAction,
  showBlockError,
  clearBlockError,
  pollJobStatus,
  POLL_INTERVALS,
  BACKOFF_SCHEDULE,
};
