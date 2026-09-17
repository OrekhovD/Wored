/**
 * trader-positions.js — WORED Trader Deck positions table logic
 *
 * Features:
 *   - Tab filtering (open/closed/liq/all)
 *   - Account/side/result filters
 *   - Win rate, net PnL, fees, liquidations summary
 *   - Mobile card view
 */
(function () {
  'use strict';

  var positions = [];
  var currentTab = 'all';
  var STATUS = {
    open: ['open', 'Открыта'],
    tp: ['tp', 'TP'],
    sl: ['sl', 'SL'],
    timeout: ['', 'Timeout'],
    liq: ['liq', 'LIQ']
  };
  var TABS_DEF = [['open', 'Открытые'], ['closed', 'Закрытые'], ['liq', 'Ликвидированные'], ['all', 'Все']];

  function fmt(v, d) { d = d == null ? 1 : d; return v == null || !isFinite(v) ? '—' : v.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function fmtUsd(v, d) { d = d == null ? 2 : d; return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d); }
  function ddhh(t) { var s = new Date(t * 1000).toISOString(); return s.slice(8, 10) + '.' + s.slice(5, 7) + ' ' + s.slice(11, 16); }
  function dur(t) {
    var now = Math.floor(Date.now() / 1000);
    var end = t.exit_time || now;
    var m = Math.max(1, Math.round((end - t.open_time) / 60));
    return m >= 60 ? Math.floor(m / 60) + ' ч ' + String(m % 60).padStart(2, '0') + ' м' : m + ' м';
  }

  function filtered() {
    var acc = (document.getElementById('trFAcc') || {}).value || 'all';
    var side = (document.getElementById('trFSide') || {}).value || 'all';
    var res = (document.getElementById('trFRes') || {}).value || 'all';
    return positions.filter(function (t) {
      if (currentTab === 'open' && t.status !== 'open') return false;
      if (currentTab === 'closed' && (t.status === 'open' || t.status === 'liq')) return false;
      if (currentTab === 'liq' && t.status !== 'liq') return false;
      if (acc !== 'all' && t.profile !== acc) return false;
      if (side !== 'all' && t.side !== side) return false;
      if (res === 'win' && !(t.net > 0)) return false;
      if (res === 'loss' && !(t.net <= 0)) return false;
      return true;
    });
  }

  function statusTag(t) {
    var s = STATUS[t.status] || ['', t.status];
    return '<span class="tr-st-tag ' + s[0] + '" title="' + (t.reason || '') + '">' + s[1] + '</span>';
  }

  function renderTabs() {
    var counts = { open: 0, closed: 0, liq: 0, all: positions.length };
    positions.forEach(function (t) {
      if (t.status === 'open') counts.open++;
      else if (t.status === 'liq') counts.liq++;
      else counts.closed++;
    });
    var posTabs = document.getElementById('trPosTabs');
    if (!posTabs) return;
    posTabs.innerHTML = TABS_DEF.map(function (def) {
      return '<button class="tr-tab" role="tab" aria-selected="' + (currentTab === def[0]) + '" data-k="' + def[0] + '">' + def[1] + '<span class="c">' + counts[def[0]] + '</span></button>';
    }).join('');
    posTabs.querySelectorAll('.tr-tab').forEach(function (b) {
      b.onclick = function () { currentTab = b.dataset.k; renderPositions(); };
    });
  }

  function renderPositions() {
    renderTabs();
    var rows = filtered();

    // Table view (desktop)
    var posBody = document.getElementById('trPosBody');
    if (posBody) {
      posBody.innerHTML = rows.map(function (t) {
        return '<tr class="' + (t.status === 'liq' ? 'liq' : '') + '">' +
          '<td class="tr-num">' + ddhh(t.open_time) + '</td>' +
          '<td class="l"><span class="tr-prof ' + (t.profile === 'P3' ? 'p3' : '') + '">' + t.account + '</span></td>' +
          '<td class="l"><span class="tr-side-tag ' + t.side + '">' + (t.side === 'long' ? 'Long' : 'Short') + '</span></td>' +
          '<td class="tr-num">' + t.leverage + 'x</td>' +
          '<td class="tr-num">' + fmt(t.margin, 2) + (t.extra_margin ? ' + ' + fmt(t.extra_margin, 2) : '') + '</td>' +
          '<td class="tr-num">' + t.size.toFixed(3) + ' <span class="tr-muted">(' + t.contracts + ')</span></td>' +
          '<td class="tr-num">' + fmt(t.entry) + '</td>' +
          '<td class="tr-num">' + fmt(t.exit) + '</td>' +
          '<td class="tr-num ' + (t.status === 'liq' ? 'tr-down' : 'tr-muted') + '">' + fmt(t.liq) + '</td>' +
          '<td class="tr-num tr-muted">' + fmt(t.tp, 0) + ' / ' + fmt(t.sl, 0) + '</td>' +
          '<td class="tr-num ' + (t.gross >= 0 ? 'tr-up' : 'tr-down') + '">' + fmtUsd(t.gross) + '</td>' +
          '<td class="tr-num tr-muted">−' + t.fees.toFixed(2) + '</td>' +
          '<td class="tr-num ' + (t.net >= 0 ? 'tr-up' : 'tr-down') + '"><b>' + fmtUsd(t.net) + '</b></td>' +
          '<td class="l">' + statusTag(t) + '</td>' +
          '<td class="tr-num tr-muted">' + dur(t) + '</td>' +
          '</tr>';
      }).join('') || '<tr><td class="l tr-muted" colspan="15">Нет позиций под выбранные фильтры.</td></tr>';
    }

    // Card view (mobile)
    var posCards = document.getElementById('trPosCards');
    if (posCards) {
      posCards.innerHTML = rows.map(function (t) {
        return '<div class="tr-pcard">' +
          '<div class="top"><span class="tr-side-tag ' + t.side + '">' + (t.side === 'long' ? 'Long' : 'Short') + ' ' + t.leverage + 'x</span>' +
          '<span class="tr-prof ' + (t.profile === 'P3' ? 'p3' : '') + '">' + t.account + '</span>' +
          statusTag(t) + '<span class="tr-spacer"></span><span class="tr-num tr-muted">' + ddhh(t.open_time) + '</span></div>' +
          '<span class="tr-muted">Вход → ' + (t.status === 'open' ? 'mark' : 'выход') + '</span><span class="tr-num">' + fmt(t.entry) + ' → ' + fmt(t.exit) + '</span>' +
          '<span class="tr-muted">Маржа · размер</span><span class="tr-num">' + fmt(t.margin, 2) + ' · ' + t.contracts + ' cont</span>' +
          '<span class="tr-muted">Ликвидация</span><span class="tr-num">' + fmt(t.liq) + '</span>' +
          '<span class="tr-muted">Net (комиссии ' + t.fees.toFixed(2) + ')</span><span class="tr-num ' + (t.net >= 0 ? 'tr-up' : 'tr-down') + '"><b>' + fmtUsd(t.net) + ' $</b></span>' +
          '</div>';
      }).join('') || '<p class="tr-muted">Нет позиций под выбранные фильтры.</p>';
    }

    // Summary bar
    renderSummary(rows);
  }

  function renderSummary(rows) {
    var summary = document.getElementById('trPosSummaryBar');
    if (!summary) return;
    var closed = rows.filter(function (t) { return t.status !== 'open'; });
    var wins = closed.filter(function (t) { return t.net > 0; }).length;
    var net = rows.reduce(function (s, t) { return s + t.net; }, 0);
    var fees = rows.reduce(function (s, t) { return s + t.fees; }, 0);
    var liq = closed.filter(function (t) { return t.status === 'liq'; }).length;
    var avgMin = closed.length ? Math.round(closed.reduce(function (s, t) { return s + ((t.exit_time || 0) - t.open_time) / 60; }, 0) / closed.length) : 0;

    summary.innerHTML =
      '<span>Позиций <b class="tr-num">' + rows.length + '</b></span>' +
      '<span>Win rate <b class="tr-num">' + (closed.length ? Math.round(wins / closed.length * 100) : 0) + '%</b> <span class="tr-muted">(' + wins + '/' + closed.length + ')</span></span>' +
      '<span>Net PnL <b class="tr-num ' + (net >= 0 ? 'tr-up' : 'tr-down') + '">' + fmtUsd(net) + ' $</b></span>' +
      '<span>Комиссии <b class="tr-num">−' + fees.toFixed(2) + ' $</b></span>' +
      '<span>Funding <b class="tr-num">0.00 $</b></span>' +
      '<span>Ликвидаций <b class="tr-num ' + (liq ? 'tr-down' : '') + '">' + liq + '</b></span>' +
      '<span>Средняя длительность <b class="tr-num">' + avgMin + ' м</b></span>';
  }

  async function loadPositions() {
    try {
      var resp = await fetch('/api/trader/positions', { credentials: 'same-origin' });
      if (resp.ok) {
        var data = await resp.json();
        positions = data.positions || [];
        renderPositions();
      }
    } catch (e) {
      console.error('[trader-positions] fetch failed', e);
    }
  }

  function init() {
    // Filter listeners
    ['trFAcc', 'trFSide', 'trFRes'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', renderPositions);
    });
    loadPositions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.TraderPositions = { loadPositions: loadPositions, renderPositions: renderPositions };
})();