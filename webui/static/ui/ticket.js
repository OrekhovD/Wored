/**
 * WORED Ticket Dialog — UI-07
 * Long/Short position dialog with focus trap, preview debounce, confirm lifecycle.
 * Depends on: core.js (WORED.openDialog, WORED.closeDialog, WORED.apiFetch)
 */
import WORED from './core.js?v=20260910-1';

let _ticketState = {
  direction: 'long',
  symbol: 'btcusdt',
  margin: 10,
  leverage: 10,
  preview: null,
  previewSeq: 0,
  previewPending: false,
  postPending: false,
  abortCtrl: null,
};

// ── Open ticket ─────────────────────────────────────────────────────────────────

function openTicket(direction, symbol, options = {}) {
  _ticketState.direction = direction;
  _ticketState.symbol = symbol || 'btcusdt';
  _ticketState.margin = options.defaultMargin || 10;
  _ticketState.leverage = options.defaultLeverage || 10;
  _ticketState.preview = null;
  _ticketState.previewSeq = 0;
  _ticketState.previewPending = false;
  _ticketState.postPending = false;

  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;

  // Set direction label
  const dirLabel = dialog.querySelector('[data-ticket-dir]');
  if (dirLabel) {
    dirLabel.textContent = direction === 'long' ? 'Long' : 'Short';
    dirLabel.className = 'ticket-dir ' + direction;
  }

  // Set symbol
  const symLabel = dialog.querySelector('[data-ticket-symbol]');
  if (symLabel) symLabel.textContent = symbol.toUpperCase();

  // Set simulation badge
  const simBadge = dialog.querySelector('[data-ticket-sim]');
  if (simBadge) simBadge.textContent = 'Симуляция';

  // Reset fields
  const marginInput = dialog.querySelector('[data-ticket-margin]');
  const levInput = dialog.querySelector('[data-ticket-leverage]');
  if (marginInput) marginInput.value = _ticketState.margin;
  if (levInput) levInput.value = _ticketState.leverage;

  // Update display values
  updateTicketDisplay();

  // Show dialog with focus trap
  WORED.openDialog(dialog);

  // First preview
  debouncedPreview();
}

// ── Close ticket ────────────────────────────────────────────────────────────────

function closeTicket() {
  if (_ticketState.abortCtrl) { _ticketState.abortCtrl.abort(); _ticketState.abortCtrl = null; }
  WORED.closeDialog();
}

// ── Preview with debounce ────────────────────────────────────────────────────────

let _debounceTimer = null;

function debouncedPreview() {
  if (_debounceTimer) clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(doPreview, 250);
}

async function doPreview() {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;

  // Get current field values
  const marginInput = dialog.querySelector('[data-ticket-margin]');
  const levInput = dialog.querySelector('[data-ticket-leverage]');
  if (!marginInput || !levInput) return;

  _ticketState.margin = parseFloat(marginInput.value) || 0;
  _ticketState.leverage = parseInt(levInput.value) || 1;

  // Invalidate previous preview
  _ticketState.preview = null;
  updateConfirmButton();

  // Abort previous request
  if (_ticketState.abortCtrl) _ticketState.abortCtrl.abort();
  _ticketState.abortCtrl = new AbortController();

  const seq = ++_ticketState.previewSeq;
  _ticketState.previewPending = true;

  const url = '/api/trade/preview?direction=' + _ticketState.direction +
    '&leverage=' + _ticketState.leverage +
    '&margin=' + _ticketState.margin +
    '&symbol=' + encodeURIComponent(_ticketState.symbol);

  try {
    const resp = await WORED.apiFetch(url, {
      resourceKey: 'ticket-preview',
      timeout: 10000,
      signal: _ticketState.abortCtrl.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      showTicketError(err.detail || 'Ошибка ' + resp.status);
      return;
    }

    const data = await resp.json();

    // Verify sequence matches (not stale)
    if (seq !== _ticketState.previewSeq) return;

    // Verify fingerprint matches current fields
    if (parseFloat(marginInput.value) !== _ticketState.margin ||
        parseInt(levInput.value) !== _ticketState.leverage) {
      // Fields changed during request — skip this response
      return;
    }

    _ticketState.preview = data;
    _ticketState.previewPending = false;
    renderTicketPreview(data);
    updateConfirmButton();
  } catch (err) {
    if (err.name === 'AbortError') return;
    showTicketError('Нет связи. Проверьте подключение.');
  }
}

// ── Render preview ──────────────────────────────────────────────────────────────

function renderTicketPreview(data) {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;

  const set = (selector, value) => {
    const el = dialog.querySelector(selector);
    if (el) el.textContent = value;
  };

  set('[data-preview-entry]', WORED.fmtPrice(data.entry_price));
  set('[data-preview-notional]', WORED.fmtUSDT(data.notional));
  set('[data-preview-size]', WORED.fmtQty(data.size, _ticketState.symbol.replace('usdt', '').toUpperCase()));
  set('[data-preview-fee]', WORED.fmtUSDT(data.taker_fee));
  set('[data-preview-liq]', WORED.fmtPrice(data.liquidation_price));
  set('[data-preview-liq-dist]', WORED.fmtPct(data.liq_distance_pct));

  // Scenarios
  const scenContainer = dialog.querySelector('[data-preview-scenarios]');
  if (scenContainer && data.scenarios) {
    let html = '';
    for (const [label, pnl] of Object.entries(data.scenarios)) {
      const isPositive = pnl >= 0;
      const cls = isPositive ? 'ui-text-success' : 'ui-text-danger';
      html += '<div class="ticket-scenario"><span class="ticket-scen-label">' + label + '</span><span class="ticket-scen-pnl ' + cls + '">' + WORED.fmtPnL(pnl) + '</span></div>';
    }
    scenContainer.innerHTML = html;
  }

  // Clear error
  const errEl = dialog.querySelector('[data-ticket-error]');
  if (errEl) errEl.style.display = 'none';
}

// ── Confirm button state ─────────────────────────────────────────────────────────

function updateConfirmButton() {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;

  const btn = dialog.querySelector('[data-ticket-confirm]');
  if (!btn) return;

  const hasPreview = _ticketState.preview !== null;
  const notPending = !_ticketState.previewPending && !_ticketState.postPending;
  const entryPrice = _ticketState.preview?.entry_price;
  const priceValid = entryPrice && entryPrice > 0;

  btn.disabled = !(hasPreview && notPending && priceValid);
}

// ── Show error ───────────────────────────────────────────────────────────────────

function showTicketError(message) {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;
  const errEl = dialog.querySelector('[data-ticket-error]');
  if (errEl) {
    errEl.textContent = message;
    errEl.style.display = '';
    errEl.className = 'ticket-error ui-text-danger';
  }
  updateConfirmButton();
}

// ── Update display (leverage, margin) ────────────────────────────────────────────

function updateTicketDisplay() {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;
  const levDisplay = dialog.querySelector('[data-ticket-lev-display]');
  const marDisplay = dialog.querySelector('[data-ticket-mar-display]');
  if (levDisplay) levDisplay.textContent = _ticketState.leverage + 'x';
  if (marDisplay) marDisplay.textContent = WORED.fmtUSDT(_ticketState.margin);
}

// ── Confirm (open position) ────────────────────────────────────────────────────────

async function confirmTicket() {
  const dialog = document.getElementById('ticketDialog');
  if (!dialog) return;

  const btn = dialog.querySelector('[data-ticket-confirm]');
  if (!btn || btn.disabled) return;

  _ticketState.postPending = true;
  btn.disabled = true;
  btn.textContent = 'Отправка…';

  const csrfToken = dialog.querySelector('[data-csrf]')?.value || '';

  try {
    const resp = await fetch('/api/positions/open', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      credentials: 'same-origin',
      body: JSON.stringify({
        symbol: _ticketState.symbol,
        direction: _ticketState.direction,
        leverage: _ticketState.leverage,
        margin: _ticketState.margin,
        simulation: true,
      }),
    });

    const data = await resp.json();

    if (resp.ok && data.ok) {
      WORED.closeDialog();
      // Refresh positions
      window.WORED_TOAST?.('Позиция #' + data.id + ' открыта', 'ok');
      // Trigger positions refresh
      window.dispatchEvent(new CustomEvent('wored:position-opened', { detail: data }));
    } else if (resp.status === 401) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = '/login?next=' + next;
    } else {
      showTicketError(data.detail || 'Ошибка ' + resp.status);
    }
  } catch (err) {
    showTicketError('Ответ не получен. Проверяем, было ли действие выполнено.');
  } finally {
    _ticketState.postPending = false;
    btn.disabled = false;
    btn.textContent = _ticketState.direction === 'long' ? 'Открыть Long' : 'Открыть Short';
    updateConfirmButton();
  }
}

// ── Close position ──────────────────────────────────────────────────────────────────

async function closePosition(positionId) {
  if (!confirm('Закрыть учебную позицию #' + positionId + '?')) return;

  try {
    const resp = await fetch('/api/positions/' + positionId + '/close', {
      method: 'POST',
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      window.WORED_TOAST?.('Позиция #' + positionId + ' закрыта. PnL: ' + WORED.fmtPnL(data.realized_pnl), 'ok');
      window.dispatchEvent(new CustomEvent('wored:position-closed', { detail: { id: positionId, ...data } }));
    } else {
      window.WORED_TOAST?.('Не удалось закрыть: ' + (data.detail || 'ошибка'), 'bad');
    }
  } catch (err) {
    window.WORED_TOAST?.('Нет связи', 'bad');
  }
}

// ── Export ──────────────────────────────────────────────────────────────────────────

export { openTicket, closeTicket, confirmTicket, closePosition, debouncedPreview };
