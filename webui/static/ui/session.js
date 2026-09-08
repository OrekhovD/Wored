/**
 * WORED Daily Session — UI-08
 * Readiness banner, session info, plan, execution controls.
 * Depends on: core.js (WORED), async-patterns.js (Poller)
 */
import WORED from './core.js';
import { Poller } from './async-patterns.js';

let _sessionPoller = null;

// ── Readiness states ────────────────────────────────────────────────────────────

const READINESS = {
  no_entries: {
    title: 'Ожидание: нет допустимых входов',
    text: (data) => data.reason || 'Причина не указана. Валидатор не нашёл подходящих условий для входа.',
  },
  no_indicators: {
    title: 'Ожидание данных',
    text: (data) => {
      const missing = data.missing_indicators || [];
      const age = data.indicators_age || '—';
      return 'Отсутствуют данные: ' + (missing.join(', ') || 'неизвестно') + '. Возраст данных: ' + age;
    },
  },
  armed_ready: {
    title: 'План активен: ожидаем условие входа',
    text: (data) => 'Триггер: ' + (data.trigger || 'не указан') + '. План revision: ' + (data.revision || '—'),
  },
  paused: {
    title: 'Сессия приостановлена',
    text: (data) => (data.paused_by ? 'Приостановил: ' + data.paused_by + '. ' : '') + (data.paused_at ? 'Когда: ' + WORED.fmtTime(data.paused_at, { title: false }) + '. ' : '') + 'Можно продолжить.',
  },
  in_position: {
    title: 'Есть открытые учебные позиции',
    text: (data) => 'Количество: ' + (data.position_count || 0) + '. ' + (data.net_pnl != null ? 'Net PnL: ' + WORED.fmtPnL(data.net_pnl) : ''),
  },
  stopped: {
    title: 'Сессия завершена',
    text: (data) => 'Итог: ' + (data.result || '—') + '. История доступна.',
  },
  unknown: {
    title: 'Готовность не подтверждена',
    text: () => 'Read-only просмотр. Новые входы недоступны до подтверждения готовности.',
  },
};

function renderReadiness(container, data) {
  if (!container) return;
  const state = data.readiness_state || data.state || 'unknown';
  const config = READINESS[state] || READINESS.unknown;
  let html = '<div class="session-readiness session-readiness-' + state + '">';
  html += '<h3 class="session-readiness-title">' + config.title + '</h3>';
  html += '<p class="session-readiness-text">' + config.text(data) + '</p>';
  if (data.next_check) {
    html += '<p class="session-readiness-next">Следующая проверка: ' + WORED.fmtTime(data.next_check, { title: false }) + '</p>';
  }
  html += '</div>';
  container.innerHTML = html;
}

// ── Session info ──────────────────────────────────────────────────────────────────

function renderSessionInfo(container, data) {
  if (!container) return;
  if (!data || !data.id) {
    container.innerHTML = '<div class="wored-empty-state">Нет активной сессии. <button class="wored-btn" onclick="document.getElementById(\'sessionSetup\').hidden=false">Создать учебную сессию</button></div>';
    return;
  }
  let html = '<div class="session-info-grid">';
  html += '<div class="session-info-item"><span class="session-info-label">ID</span><span class="session-info-value">' + data.id + '</span></div>';
  if (data.session_start) html += '<div class="session-info-item"><span class="session-info-label">Начало</span><span class="session-info-value">' + WORED.fmtTime(data.session_start) + '</span></div>';
  if (data.session_end) html += '<div class="session-info-item"><span class="session-info-label">Окончание</span><span class="session-info-value">' + WORED.fmtTime(data.session_end) + '</span></div>';
  if (data.time_remaining) html += '<div class="session-info-item"><span class="session-info-label">Осталось</span><span class="session-info-value">' + data.time_remaining + '</span></div>';
  if (data.target_net_profit_usdt != null) html += '<div class="session-info-item"><span class="session-info-label">Цель Net</span><span class="session-info-value">' + WORED.fmtUSDT(data.target_net_profit_usdt) + '</span></div>';
  if (data.net_pnl != null) html += '<div class="session-info-item"><span class="session-info-label">Net PnL</span><span class="session-info-value ' + (data.net_pnl >= 0 ? 'ui-text-success' : 'ui-text-danger') + '">' + WORED.fmtPnL(data.net_pnl) + '</span></div>';
  html += '</div>';
  container.innerHTML = html;
}

// ── Execution controls ───────────────────────────────────────────────────────────

const COMMAND_LABELS = {
  continue: 'Продолжить',
  tighten: 'Ужесточить риск',
  reduce: 'Снизить риск',
  pause: 'Приостановить',
  close_all: 'Закрыть все учебные позиции',
};

function renderControls(container, data) {
  if (!container) return;
  if (!data || !data.allowed_commands) {
    container.innerHTML = '';
    return;
  }
  const allowed = data.allowed_commands;
  let html = '<div class="session-controls">';
  for (const cmd of ['continue', 'tighten', 'reduce', 'pause']) {
    if (allowed.includes(cmd)) {
      html += '<button class="wored-btn session-cmd" data-cmd="' + cmd + '">' + COMMAND_LABELS[cmd] + '</button>';
    }
  }
  html += '</div>';
  // close_all — separate, destructive
  if (allowed.includes('close_all')) {
    html += '<div class="session-close-all">';
    html += '<button class="wored-btn wored-btn-danger session-cmd" data-cmd="close_all">' + COMMAND_LABELS.close_all + '</button>';
    html += '<span class="wored-action-reason">Сессия: ' + (data.id || '—') + ', позиций: ' + (data.position_count || 0) + '</span>';
    html += '</div>';
  }
  container.innerHTML = html;

  // Wire up buttons
  container.querySelectorAll('.session-cmd').forEach(btn => {
    btn.addEventListener('click', () => executeCommand(btn.dataset.cmd, data.id));
  });
}

async function executeCommand(command, sessionId) {
  if (!sessionId) return;
  if (command === 'close_all') {
    if (!confirm('Закрыть все учебные позиции? Это действие необратимо.')) return;
  }
  const btn = document.querySelector('.session-cmd[data-cmd="' + command + '"]');
  if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }

  try {
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta?.content || '';
    const resp = await fetch('/api/daily-session/revision', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      credentials: 'same-origin',
      body: JSON.stringify({ session_id: sessionId, command }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      const newStatus = data.new_status || 'updated';
      window.WORED_TOAST?.('Команда выполнена: ' + COMMAND_LABELS[command] + ' → ' + newStatus, 'ok');
      // Refresh session data
      loadSession();
    } else if (resp.status === 401) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = '/login?next=' + next;
    } else {
      window.WORED_TOAST?.('Не удалось: ' + (data.detail || 'ошибка ' + resp.status), 'bad');
    }
  } catch (err) {
    window.WORED_TOAST?.('Нет связи. Проверьте подключение.', 'bad');
  } finally {
    if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
  }
}

// ── Load session data ───────────────────────────────────────────────────────────

async function loadSession() {
  try {
    const resp = await WORED.apiFetch('/api/daily-session/active', { resourceKey: 'session', timeout: 10000 });
    if (resp.status === 401) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = '/login?next=' + next;
      return;
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();

    const readiness = document.getElementById('sessionReadiness');
    const info = document.getElementById('sessionInfo');
    const controls = document.getElementById('sessionControls');

    if (readiness) renderReadiness(readiness, data);
    if (info) renderSessionInfo(info, data.session || data);
    if (controls) renderControls(controls, data);
  } catch (err) {
    const readiness = document.getElementById('sessionReadiness');
    if (readiness) {
      readiness.innerHTML = '<div class="session-readiness session-readiness-unknown"><h3>Нет связи</h3><p>Показаны данные на последнее обновление.</p></div>';
    }
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

function initSession() {
  const page = document.querySelector('.daily-session-page');
  if (!page) return;

  loadSession();
  _sessionPoller = new Poller(loadSession, 15000);
  _sessionPoller.start();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSession);
} else {
  initSession();
}

export { initSession, loadSession, executeCommand };