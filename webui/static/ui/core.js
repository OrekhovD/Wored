/**
 * WORED UI Core — UI-01
 * Format helpers, fetch wrapper, status labels, dialog lifecycle, number formatting.
 * Loaded as ES module: <script type="module" src="/static/ui/core.js">
 */
const WORED = (() => {
  'use strict';

  // ── Number formatting (UI-01 spec) ──────────────────────────────────────────

  const ruFmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const ruFmt8 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 8 });
  const pctFmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const NA = '\u2014'; // —

  function finiteNumber(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string' || value.trim() === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  /**
   * Format USDT value. ≥1 → 2-8 decimals by source precision;
   * <1 → up to 8; rounds to zero → show '<0,00000001 USDT'.
   * null/undefined/NaN/Infinity → '—'.
   */
  function fmtUSDT(value, { minDecimals = 2, maxDecimals = 8 } = {}) {
    value = finiteNumber(value);
    if (value == null) return NA;
    if (value === 0) return '0,00 USDT';
    const abs = Math.abs(value);
    if (abs > 0 && abs < 0.01) {
      const formatted = ruFmt8.format(value);
      if (formatted === '0,00' || formatted === '-0,00' || formatted === '0,00000000') {
        return (value < 0 ? '-' : '') + '<0,00000001 USDT';
      }
      return formatted + ' USDT';
    }
    if (abs >= 1) {
      // Use ruFmt with maxDecimals for prices ≥ 1
      const fmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: minDecimals, maximumFractionDigits: maxDecimals });
      return fmt.format(value) + ' USDT';
    }
    return ruFmt8.format(value) + ' USDT';
  }

  /** Format a price — same as USDT but without suffix for internal use */
  function fmtPrice(value) {
    value = finiteNumber(value);
    if (value == null) return NA;
    if (value === 0) return '0';
    const abs = Math.abs(value);
    if (abs >= 1) {
      const fmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 8 });
      return fmt.format(value);
    }
    return ruFmt8.format(value);
  }

  /** Format percentage points (input already in %). E.g. 0.5 → '0,50 %' */
  function fmtPct(value) {
    value = finiteNumber(value);
    if (value == null) return NA;
    return pctFmt.format(value) + ' %';
  }

  /** Format a fraction as percentage. E.g. 0.005 → '0,50 %' */
  function fmtFraction(value) {
    value = finiteNumber(value);
    if (value == null) return NA;
    return pctFmt.format(value * 100) + ' %';
  }

  /** Format PnL with sign for positive. E.g. +1,25 USDT / -1,25 USDT / 0,00 USDT */
  function fmtPnL(value) {
    value = finiteNumber(value);
    if (value == null) return NA;
    if (value === 0) return '0,00 USDT';
    const formatted = ruFmt.format(value) + ' USDT';
    return value > 0 ? '+' + formatted : formatted;
  }

  /** Format quantity with asset suffix. E.g. 0,000155642023 BTC */
  function fmtQty(value, asset = 'BTC') {
    value = finiteNumber(value);
    if (value == null) return NA;
    return ruFmt8.format(value) + ' ' + asset;
  }

  /** Format ISO UTC timestamp to visible 'DD.MM HH:MM UTC'.
   *  Title attribute gets full ISO for tooltip. */
  function fmtTime(iso, { title = true } = {}) {
    if (!iso) return NA;
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return NA;
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
      const hh = String(d.getUTCHours()).padStart(2, '0');
      const mi = String(d.getUTCMinutes()).padStart(2, '0');
      const visible = `${dd}.${mm} ${hh}:${mi} UTC`;
      if (title) {
        const el = document.createElement('span');
        el.textContent = visible;
        el.title = iso;
        return el;
      }
      return visible;
    } catch { return NA; }
  }

  /** Format time ago from ISO timestamp */
  function fmtTimeAgo(iso) {
    if (!iso) return NA;
    try {
      const d = new Date(iso);
      const diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return 'только что';
      if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
      if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
      return `${Math.floor(diff / 86400)} д назад`;
    } catch { return NA; }
  }

  // ── Status labels (UI-03 spec) ──────────────────────────────────────────────

  const EXECUTION_STATES = {
    queued:    { label: 'В очереди',        cssClass: 'ui-status-queued' },
    running:   { label: 'Рассчитывается',    cssClass: 'ui-status-running' },
    partial:   { label: 'Частичный результат', cssClass: 'ui-status-partial' },
    completed: { label: 'Расчёт завершён',   cssClass: 'ui-status-completed' },
    failed:    { label: 'Ошибка расчёта',    cssClass: 'ui-status-failed' },
    expired:   { label: 'Истёк',            cssClass: 'ui-status-expired' },
  };

  // Legacy fallback mapping
  const LEGACY_STATE_MAP = {
    pending: 'queued',
    active: null, // ambiguous — need real result
  };

  function stateLabel(executionState, legacyStatus) {
    const key = executionState || LEGACY_STATE_MAP[legacyStatus];
    if (key && EXECUTION_STATES[key]) return EXECUTION_STATES[key];
    return { label: 'Состояние уточняется', cssClass: 'ui-status-queued' };
  }

  function stateLabelHtml(executionState, legacyStatus) {
    const s = stateLabel(executionState, legacyStatus);
    const span = document.createElement('span');
    span.className = s.cssClass;
    span.textContent = s.label;
    return span;
  }

  // ── Fetch wrapper with abort and error handling (UI-03 spec) ────────────────

  const _activeRequests = new Map(); // resource key → AbortController

  function abortPrevious(resourceKey) {
    const ctrl = _activeRequests.get(resourceKey);
    if (ctrl) { ctrl.abort(); _activeRequests.delete(resourceKey); }
  }

  async function apiFetch(url, { resourceKey = null, timeout = 10000, ...opts } = {}) {
    if (resourceKey) abortPrevious(resourceKey);
    const ctrl = new AbortController();
    if (resourceKey) _activeRequests.set(resourceKey, ctrl);
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const resp = await fetch(url, { ...opts, signal: ctrl.signal, credentials: 'same-origin' });
      clearTimeout(timer);
      if (resourceKey && _activeRequests.get(resourceKey) === ctrl) _activeRequests.delete(resourceKey);
      return resp;
    } catch (err) {
      clearTimeout(timer);
      if (resourceKey && _activeRequests.get(resourceKey) === ctrl) _activeRequests.delete(resourceKey);
      if (err.name === 'AbortError') throw err;
      throw err;
    }
  }

  // ── Dialog lifecycle (UI-07 spec) ────────────────────────────────────────────

  let _activeDialog = null;

  function openDialog(dialogEl, triggerEl) {
    if (_activeDialog) closeDialog();
    _activeDialog = dialogEl;
    _activeDialog._trigger = triggerEl || null;
    dialogEl.setAttribute('aria-modal', 'true');
    dialogEl.setAttribute('role', 'dialog');
    dialogEl.hidden = false;
    // Focus heading
    const heading = dialogEl.querySelector('[aria-labelledby]') || dialogEl.querySelector('h2, h3, h4');
    if (heading) { heading.tabIndex = -1; heading.focus(); }
    document.addEventListener('keydown', _dialogKeyHandler);
    document.body.style.overflow = 'hidden';
  }

  function closeDialog() {
    if (!_activeDialog) return;
    _activeDialog.hidden = true;
    _activeDialog.removeAttribute('aria-modal');
    document.removeEventListener('keydown', _dialogKeyHandler);
    document.body.style.overflow = '';
    const trigger = _activeDialog._trigger;
    _activeDialog = null;
    if (trigger && typeof trigger.focus === 'function') trigger.focus();
  }

  function _dialogKeyHandler(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeDialog(); }
  }

  // ── Polling lifecycle (UI-03 spec) ──────────────────────────────────────────

  class Poller {
    constructor(fn, intervalMs, { backoffMs = [3000, 6000, 12000, 30000], pauseOnHidden = true } = {}) {
      this._fn = fn;
      this._interval = intervalMs;
      this._backoff = backoffMs;
      this._pauseOnHidden = pauseOnHidden;
      this._timer = null;
      this._errorCount = 0;
      this._generation = 0;
      this._running = false;
      this._onHidden = () => { if (this._running) this.pause(); };
      this._onVisible = () => { if (this._running) { this._generation++; this.start(); } };
    }

    start() {
      this.stop();
      this._running = true;
      if (this._pauseOnHidden) {
        document.addEventListener('visibilitychange', this._onVisible);
        document.addEventListener('visibilitychange', this._onHidden);
      }
      this._tick();
      return this;
    }

    stop() {
      this._running = false;
      clearTimeout(this._timer);
      if (this._pauseOnHidden) {
        document.removeEventListener('visibilitychange', this._onVisible);
        document.removeEventListener('visibilitychange', this._onHidden);
      }
      return this;
    }

    pause() { clearTimeout(this._timer); }

    async _tick() {
      if (!this._running) return;
      const gen = this._generation;
      try {
        await this._fn();
        this._errorCount = 0;
      } catch (err) {
        if (err.name === 'AbortError') return;
        this._errorCount++;
      }
      if (!this._running || gen !== this._generation) return;
      const delay = this._errorCount > 0
        ? (this._backoff[Math.min(this._errorCount - 1, this._backoff.length - 1)] || this._backoff[this._backoff.length - 1])
        : this._interval;
      this._timer = setTimeout(() => this._tick(), delay);
    }
  }

  // ── Session storage helpers (UI-06 spec) ────────────────────────────────────

  const STORAGE_KEY = 'wored.ui.forecast.v1';

  function savePendingForecast({ key, payload, requestId, submittedAt, deadlineAt }) {
    try {
      const data = { key, payload, requestId, submittedAt, deadlineAt };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch { /* sessionStorage unavailable — inform user elsewhere */ }
  }

  function loadPendingForecast() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch { return null; }
  }

  function clearPendingForecast() {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch {}
  }

  function clearPendingForecastOnLogout() {
    clearPendingForecast();
    try { sessionStorage.clear(); } catch {}
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  return {
    NA,
    fmtUSDT, fmtPrice, fmtPct, fmtFraction, fmtPnL, fmtQty,
    fmtTime, fmtTimeAgo,
    stateLabel, stateLabelHtml, EXECUTION_STATES,
    apiFetch, abortPrevious,
    openDialog, closeDialog,
    Poller,
    savePendingForecast, loadPendingForecast, clearPendingForecast,
    clearPendingForecastOnLogout,
  };
})();

// Make available globally for non-module scripts that need it
if (typeof window !== 'undefined') window.WORED = WORED;

export default WORED;
