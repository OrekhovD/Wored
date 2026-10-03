/**
 * V3 session constructor (P3.2) — SimulationPlanV1 draft → approve → start.
 *
 * Talks to /api/v3/simulation/*; every gate (limits, catalog, stale quote,
 * missing replay history) is decided SERVER-side — this controller only
 * renders the exact reason codes and pins what the server returned.  Values
 * reload from the latest saved draft so a refresh never loses the plan
 * (MC-09); an approved plan cannot be edited in place (MC-11) — starting a
 * change makes a new version.
 */

const SIM_API = '/api/v3/simulation';
const FALLBACK_INSTRUMENT = 'htx:linear-swap:BTC-USDT';

const el = (root, role) => root.querySelector(`[data-role="${role}"]`);

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

function instrumentKey() {
  const sel = document.querySelector('[data-v3-market-root] [data-role="instrument"]');
  const value = sel && sel.value ? sel.value : '';
  return value || FALLBACK_INSTRUMENT;
}

/** datetime-local (naive, labelled UTC) → ISO-8601 with explicit offset. */
function toIso(localValue) {
  if (!localValue) return null;
  const withSeconds = localValue.length === 16 ? `${localValue}:00` : localValue;
  return `${withSeconds}+00:00`;
}

/** ISO → value accepted by datetime-local inputs. */
function toLocalInput(iso) {
  return typeof iso === 'string' && iso.length >= 16 ? iso.slice(0, 16) : '';
}

async function simFetch(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error body */ }
  return { ok: res.ok, status: res.status, data };
}

export function mountSimulationWorkspace(root) {
  const form = el(root, 'sim-form');
  const statusEl = el(root, 'sim-status');
  const violationsEl = el(root, 'sim-violations');
  const hashEl = el(root, 'sim-hash');
  const validateBtn = el(root, 'sim-validate');
  const saveBtn = el(root, 'sim-save');
  const approveBtn = el(root, 'sim-approve');
  const startBtn = el(root, 'sim-start-btn');
  const modeSel = el(root, 'sim-mode');
  const seedField = root.querySelector('.v3-sim-seed');
  const proposeBtn = el(root, 'sim-propose');
  const acceptBtn = el(root, 'sim-accept');
  const proposalStatusEl = el(root, 'sim-proposal-status');
  const compareEl = el(root, 'sim-proposal-compare');

  const state = {
    planId: null,       // saved draft / approved plan
    planStatus: null,   // draft | approved
    canApprove: false,
    idemKey: null,      // one key per approval: retries replay, never double-start
    proposalDraft: null,  // MC-12: AI answer is ONLY a draft until accepted
  };

  function collectPlan() {
    const sides = [];
    if (el(root, 'sim-side-long').checked) sides.push('long');
    if (el(root, 'sim-side-short').checked) sides.push('short');
    const plan = {
      instrument_key: instrumentKey(),
      mode: modeSel.value,
      start_at: toIso(el(root, 'sim-start').value),
      end_at: toIso(el(root, 'sim-end').value),
      timezone: el(root, 'sim-tz').value.trim() || 'Etc/UTC',
      manual_budget: el(root, 'sim-manual-budget').value.trim(),
      auto_budget: el(root, 'sim-auto-budget').value.trim(),
      strategy_id: el(root, 'sim-strategy').value,
      strategy_version: el(root, 'sim-strategy').value,
      style: el(root, 'sim-style').value,
      allowed_sides: sides,
      max_leverage: Number(el(root, 'sim-leverage').value),
      max_risk_per_order: el(root, 'sim-risk').value.trim(),
      max_daily_loss: el(root, 'sim-daily-loss').value.trim(),
      max_session_loss: el(root, 'sim-session-loss').value.trim(),
      max_total_exposure: el(root, 'sim-exposure').value.trim(),
      max_open_positions: Number(el(root, 'sim-positions').value),
      cooldown_minutes: Number(el(root, 'sim-cooldown').value),
      trading_hours: el(root, 'sim-hours').value.trim() || 'any',
      stop_policy: 'mark',
      take_profit_policy: el(root, 'sim-tp').value,
      close_at_end: true,
      fee_schedule_version: 'taker_6bps_v1',
      slippage_model_version: 'adverse_2bps_v1',
      funding_model_version: 'htx_mark_funding_v1',
      market_data_policy: 'closed_candles_only',
    };
    if (modeSel.value === 'historical_replay') {
      plan.seed = Number(el(root, 'sim-seed').value);
    }
    return plan;
  }

  function fillForm(plan) {
    if (!plan) return;
    modeSel.value = plan.mode || 'live_paper';
    seedField.hidden = plan.mode !== 'historical_replay';
    el(root, 'sim-start').value = toLocalInput(plan.start_at);
    el(root, 'sim-end').value = toLocalInput(plan.end_at);
    el(root, 'sim-tz').value = plan.timezone || 'Etc/UTC';
    el(root, 'sim-manual-budget').value = plan.manual_budget ?? '1000';
    el(root, 'sim-auto-budget').value = plan.auto_budget ?? '500';
    el(root, 'sim-side-long').checked = (plan.allowed_sides || []).includes('long');
    el(root, 'sim-side-short').checked = (plan.allowed_sides || []).includes('short');
    el(root, 'sim-leverage').value = plan.max_leverage ?? 10;
    el(root, 'sim-risk').value = plan.max_risk_per_order ?? '10';
    el(root, 'sim-daily-loss').value = plan.max_daily_loss ?? '50';
    el(root, 'sim-session-loss').value = plan.max_session_loss ?? '100';
    el(root, 'sim-exposure').value = plan.max_total_exposure ?? '200';
    el(root, 'sim-positions').value = plan.max_open_positions ?? 2;
    el(root, 'sim-cooldown').value = plan.cooldown_minutes ?? 5;
    el(root, 'sim-hours').value = plan.trading_hours || 'any';
    el(root, 'sim-tp').value = plan.take_profit_policy || 'mark';
    if (plan.seed != null) el(root, 'sim-seed').value = plan.seed;
  }

  function renderViolations(violations) {
    violationsEl.textContent = '';
    if (!violations || violations.length === 0) {
      violationsEl.hidden = true;
      return;
    }
    violationsEl.hidden = false;
    for (const v of violations) {
      const li = document.createElement('li');
      li.dataset.code = v.code || 'unknown';
      li.dataset.field = v.field || '_plan';
      li.className = 'v3-sim-violation';
      li.textContent = `${v.field}: ${v.message || v.code}`;
      violationsEl.appendChild(li);
    }
  }

  function setStatus(text, verdict, extra = {}) {
    statusEl.textContent = text;
    statusEl.dataset.simVerdict = verdict;
    if (extra.planId) statusEl.dataset.simPlanId = extra.planId;
    if (extra.sessionId) statusEl.dataset.simSessionId = extra.sessionId;
    if (extra.hash) {
      hashEl.hidden = false;
      hashEl.textContent = `plan_hash: ${extra.hash}`;
      hashEl.dataset.simHash = extra.hash;
      statusEl.dataset.simHash = extra.hash;  // one readable hook for QA
    }
    approveBtn.disabled = !(state.canApprove && verdict !== 'started');
    startBtn.disabled = !(state.planStatus === 'approved' && state.idemKey);
  }

  function describeError(data, fallback) {
    const detail = data && data.detail;
    if (detail && typeof detail === 'object') {
      return `${detail.reason_code || 'error'}: ${detail.message || ''}`.trim();
    }
    return fallback;
  }

  async function validate() {
    const { ok, data } = await simFetch(`${SIM_API}/plans/validate`, {
      method: 'POST', body: { plan: collectPlan() },
    });
    if (!ok || !data) {
      state.canApprove = false;
      setStatus(describeError(data, 'сервер проверки недоступен'), 'error');
      return false;
    }
    renderViolations(data.violations);
    state.canApprove = Boolean(data.can_approve);
    if (state.canApprove) {
      const worst = data.worst_case_risk ? data.worst_case_risk.worst_allowed_loss_usdt : '?';
      setStatus(`План валиден. Худший допустимый убыток: ${worst} USDT.`, 'ok', { hash: data.plan_hash });
    } else {
      setStatus('План не проходит проверку — см. причины ниже.', 'violations');
      hashEl.hidden = true;
    }
    approveBtn.disabled = !state.canApprove;
    return state.canApprove;
  }

  async function saveDraft() {
    const body = { plan: collectPlan() };
    const res = state.planId && state.planStatus === 'draft'
      ? await simFetch(`${SIM_API}/plans/${state.planId}`, { method: 'POST', body })
      : await simFetch(`${SIM_API}/plans`, { method: 'POST', body });
    if (!res.ok || !res.data || !res.data.plan) {
      setStatus(describeError(res.data, 'не удалось сохранить черновик'), 'error');
      return null;
    }
    const rec = res.data.plan;
    state.planId = rec.plan_id;
    state.planStatus = rec.status;
    renderViolations(rec.violations);
    setStatus(`Черновик v${rec.version} сохранён (plan_id=${rec.plan_id.slice(0, 8)}…).`, 'saved',
      { planId: rec.plan_id, hash: rec.plan_hash || undefined });
    return rec;
  }

  async function approve() {
    if (!(await validate())) return;
    const saved = await saveDraft();
    if (!saved) return;
    const res = await simFetch(`${SIM_API}/plans/${state.planId}/approve`, { method: 'POST', body: {} });
    if (!res.ok || !res.data || !res.data.plan) {
      const violations = res.data && res.data.violations;
      if (violations) renderViolations(violations);
      setStatus(describeError(res.data, 'утверждение отклонено'), 'error');
      return;
    }
    state.planStatus = 'approved';
    state.idemKey = crypto.randomUUID();
    setStatus(`План утверждён; hash зафиксирован. Можно запустить сессию.`, 'approved',
      { planId: state.planId, hash: res.data.plan.plan_hash });
  }

  async function startSession() {
    if (!state.planId || !state.idemKey) return;
    // Idempotency-Key rides as a header; retry-safe: one key per approval,
    // so a repeated click replays the same session instead of double-starting.
    let response;
    try {
      response = await fetch(`${SIM_API}/sessions`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken(),
          'Idempotency-Key': state.idemKey,
        },
        body: JSON.stringify({ plan_id: state.planId }),
      });
    } catch {
      setStatus('сессия не создана: сеть недоступна', 'error');
      return;
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || !data.session) {
      setStatus(describeError(data, 'запуск сессии отклонён'), 'error');
      return;
    }
    const sess = data.session;
    setStatus(
      `Сессия ${sess.session_id.slice(0, 8)}… ${data.replayed ? 'восстановлена (идемпотентный повтор)' : 'создана'}; статус: ${sess.status}.`,
      'started',
      { sessionId: sess.session_id, hash: sess.plan_hash },
    );
  }

  async function restoreLatest() {
    const { ok, data } = await simFetch(`${SIM_API}/plans?limit=5`);
    if (!ok || !data || !Array.isArray(data.plans) || data.plans.length === 0) return;
    const rec = data.plans[0];
    state.planId = rec.plan_id;
    state.planStatus = rec.status;
    fillForm(rec.plan);
    renderViolations(rec.violations);
    if (rec.status === 'approved') {
      state.idemKey = state.idemKey || crypto.randomUUID();
      setStatus(`Восстановлен утверждённый план v${rec.version}; хеш неизменен.`, 'approved',
        { planId: rec.plan_id, hash: rec.plan_hash });
    } else if (rec.plan_hash && (rec.violations || []).length === 0) {
      state.canApprove = true;
      setStatus(`Восстановлен черновик v${rec.version} после refresh.`, 'saved',
        { planId: rec.plan_id, hash: rec.plan_hash });
    } else {
      setStatus(`Восстановлен черновик v${rec.version} (есть незакрытые проверки).`, 'saved',
        { planId: rec.plan_id });
    }
    approveBtn.disabled = !(state.canApprove && rec.status !== 'approved');
    startBtn.disabled = !(rec.status === 'approved');
  }

  // ── P4 AI proposal (MC-12/MC-13) ───────────────────────────────────────────
  // Contract: the agent returns a DRAFT + rationale; the server runs the SAME
  // deterministic validator (veto).  Accepting only fills the manual form —
  // approve/start stay separate user confirmations.  A failed agent (timeout/
  // quota/invalid) must never disable the manual path and never triggers a
  // paid fallback (the default source is the free deterministic engine).

  function collectQuestionnaire() {
    const q = {
      goal: el(root, 'simq-goal').value,
      duration_hours: Number(el(root, 'simq-duration').value),
      side_preference: el(root, 'simq-side').value,
      involvement: el(root, 'simq-involvement').value,
      max_acceptable_loss: el(root, 'simq-maxloss').value.trim(),
      trading_hours: el(root, 'simq-hours').value.trim() || 'any',
    };
    const manual = el(root, 'simq-manual').value.trim();
    const auto = el(root, 'simq-auto').value.trim();
    if (manual && auto) { q.manual_budget = manual; q.auto_budget = auto; }
    else if (manual || auto) { q.total_budget = manual || auto; }
    return q;
  }

  function setProposalStatus(text, verdict, extra = {}) {
    proposalStatusEl.textContent = text;
    proposalStatusEl.dataset.propVerdict = verdict;
    if (extra.reasonCode) proposalStatusEl.dataset.propFailCode = extra.reasonCode;
    else delete proposalStatusEl.dataset.propFailCode;
    if (extra.adoptable !== undefined) proposalStatusEl.dataset.propAdoptable = String(extra.adoptable);
    acceptBtn.disabled = extra.adoptable !== true;
  }

  function clearCompare() {
    compareEl.textContent = '';
    compareEl.hidden = true;
    compareEl.dataset.propRows = '0';
  }

  function makeRow(field, proposed, rationale) {
    const tr = document.createElement('tr');
    tr.dataset.field = field;
    const tf = document.createElement('td'); tf.textContent = field;
    const tp = document.createElement('td'); tp.textContent = proposed;
    tr.appendChild(tf); tr.appendChild(tp);
    if (rationale != null) {
      const trr = document.createElement('td');
      trr.className = 'v3-sim-prop-rationale';
      trr.textContent = rationale;
      tr.appendChild(trr);
    }
    return tr;
  }

  function renderProposal(p) {
    compareEl.textContent = '';
    compareEl.hidden = false;
    const table = document.createElement('table');
    table.className = 'v3-sim-prop-table';
    const thead = document.createElement('thead');
    const htr = document.createElement('tr');
    for (const h of ['Параметр', 'Предложено', 'Почему']) {
      const th = document.createElement('th'); th.textContent = h; htr.appendChild(th);
    }
    thead.appendChild(htr); table.appendChild(thead);
    const tbody = document.createElement('tbody');
    const draft = p.draft || {};
    const rationale = p.rationale || {};
    const current = collectPlan();
    const fields = [
      'mode', 'timezone', 'manual_budget', 'auto_budget', 'allowed_sides',
      'max_leverage', 'max_risk_per_order', 'max_daily_loss', 'max_session_loss',
      'max_total_exposure', 'max_open_positions', 'cooldown_minutes', 'trading_hours',
    ];
    for (const f of fields) {
      if (draft[f] === undefined) continue;
      const shown = Array.isArray(draft[f]) ? draft[f].join(', ') : String(draft[f]);
      tbody.appendChild(makeRow(f, shown, rationale[f] !== undefined
        ? rationale[f]
        : `сейчас: ${Array.isArray(current[f]) ? current[f].join(', ') : String(current[f])}`));
    }
    table.appendChild(tbody);
    compareEl.appendChild(table);
    compareEl.dataset.propRows = String(tbody.children.length);
    const meta = document.createElement('div');
    meta.className = 'v3-sim-prop-meta';
    const bits = [];
    if (p.model_version) bits.push(`модель: ${p.model_version}`);
    if (p.plan_hash) bits.push(`plan_hash: ${p.plan_hash}`);
    for (const a of (p.assumptions || [])) bits.push(`допущение: ${a}`);
    for (const m of (p.missing_data || [])) bits.push(`нет данных: ${m}`);
    meta.textContent = bits.join(' · ');
    compareEl.appendChild(meta);
    const viols = (p.validator && p.validator.violations) || [];
    if (viols.length > 0) {
      const ul = document.createElement('ul');
      ul.className = 'v3-sim-prop-violations';
      for (const v of viols) {
        const li = document.createElement('li');
        li.dataset.code = v.code || 'unknown';
        li.dataset.field = v.field || '_plan';
        li.textContent = `${v.field}: ${v.message || v.code}`;
        ul.appendChild(li);
      }
      compareEl.appendChild(ul);
    }
  }

  async function pollProposal(pid) {
    for (let i = 0; i < 100; i += 1) {
      const { ok, data } = await simFetch(`${SIM_API}/plan-proposals/${pid}`);
      if (ok && data && data.proposal && data.proposal.status !== 'pending') return data.proposal;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
  }

  async function requestProposal() {
    proposeBtn.disabled = true;
    state.proposalDraft = null;
    clearCompare();
    setProposalStatus('Запрос отправлен; agent отвечает…', 'pending');
    try {
      const { ok, status, data } = await simFetch(`${SIM_API}/plan-proposals`, {
        method: 'POST', body: { questionnaire: collectQuestionnaire() },
      });
      if (!ok || !data || !data.proposal) {
        const detail = data && data.detail;
        const code = (detail && detail.reason_code)
          || (data && data.reason_code) || 'proposal_error';
        setProposalStatus(`Предложение не создано (${code}, HTTP ${status}); ручная форма доступна.`,
          'failed', { reasonCode: code, adoptable: false });
        return;
      }
      const pid = data.proposal.proposal_id;
      proposalStatusEl.dataset.propId = pid;
      const p = await pollProposal(pid);
      if (!p) {
        setProposalStatus('Agent не ответил вовремя; ручная форма доступна.', 'failed',
          { reasonCode: 'poll_stuck', adoptable: false });
        return;
      }
      if (p.status === 'failed') {
        setProposalStatus(`AI недоступен (${p.reason_code}): ${p.reason_message || ''} Ручная проверка и запуск доступны, платный fallback не вызывался.`,
          'failed', { reasonCode: p.reason_code, adoptable: false });
        return;
      }
      renderProposal(p);
      const adoptable = Boolean(p.validator && p.validator.adoptable);
      if (adoptable) {
        state.proposalDraft = p.draft;
        setProposalStatus('Предложение готово и прошло детерминированный валидатор. Это черновик: проверьте и примите отдельно.',
          'completed', { adoptable: true });
      } else {
        setProposalStatus('Предложение отклонено детерминированным валидатором — поля не исправлялись молча; принятие недопустимо.',
          'vetoed', { adoptable: false });
      }
    } finally {
      proposeBtn.disabled = false;
    }
  }

  function acceptProposal() {
    if (!state.proposalDraft) return;
    // MC-12: accepting ONLY fills the manual form.  No save/approve/start here.
    fillForm(state.proposalDraft);
    acceptBtn.disabled = true;
    proposalStatusEl.dataset.propAccepted = 'true';
    setStatus('Черновик из AI-предложения загружен в форму. Проверьте, затем «Проверить» → «Утвердить».',
      'ok');
  }

  form.addEventListener('submit', (e) => e.preventDefault());
  validateBtn.addEventListener('click', () => { void validate(); });
  saveBtn.addEventListener('click', () => {
    if (!state.canApprove) { void validate().then((ok) => { if (ok) void saveDraft(); }); }
    else void saveDraft();
  });
  approveBtn.addEventListener('click', () => void approve());
  startBtn.addEventListener('click', () => void startSession());
  proposeBtn.addEventListener('click', () => void requestProposal());
  acceptBtn.addEventListener('click', () => acceptProposal());
  modeSel.addEventListener('change', () => {
    seedField.hidden = modeSel.value !== 'historical_replay';
  });

  void restoreLatest();
  return {
    destroy() { /* handlers live with the page instance */ },
  };
}

const root = document.querySelector('[data-v3-simulation-root]');
if (root) mountSimulationWorkspace(root);
