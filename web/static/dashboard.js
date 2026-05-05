/* Dashboard frontend — vanilla JS, no build step.
 *
 * The page templates each call one of `Dashboard.initX()` after the DOM is
 * ready. Init functions wire DOM listeners, trigger the first render, and
 * start polling. All HTTP calls go through `api()` which throws on non-2xx.
 */
(function (window) {
  'use strict';

  const REFRESH_MS = 30000;
  const STATUS_MS = 4000;
  const LOG_MS = 2000;

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ── HTTP helpers ───────────────────────────────────────────────────

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error || ''; } catch (_) {}
      throw new Error(`${res.status} ${res.statusText}${detail ? ': ' + detail : ''}`);
    }
    return res.json();
  }

  // ── Formatting ─────────────────────────────────────────────────────

  function fmtMoney(n) {
    if (n == null || isNaN(n)) return '—';
    const v = Number(n);
    if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  }
  function fmtNumber(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString();
  }
  function fmtPct(n, places = 2) {
    if (n == null || isNaN(n)) return '—';
    return `${Number(n).toFixed(places)}%`;
  }
  function fmtRatio(n) {
    if (n == null || isNaN(n)) return '—';
    return `${(Number(n) * 100).toFixed(3)}%`;
  }
  function fmtDate(s) {
    if (!s) return '—';
    return String(s).replace('T', ' ').replace(/\.\d+/, '').replace(/Z$/, ' UTC');
  }
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── Bot status (sidebar) — runs on every page ──────────────────────

  let lastStatusEtag = null;
  async function refreshStatus() {
    try {
      const s = await api('/api/bot/status');
      const dot = $('#bot-status-dot');
      const text = $('#bot-status-text');
      const ticks = $('#bot-status-ticks');
      const last = $('#bot-status-last');
      if (dot)  dot.className = 'dot ' + (s.running ? 'running' : 'stopped');
      if (text) text.textContent = s.running ? 'Running' : 'Stopped';
      if (ticks) ticks.textContent = fmtNumber(s.ticks);
      if (last) last.textContent = s.last_tick_at
        ? fmtDate(s.last_tick_at) : '—';
      $('#bot-start-btn')?.toggleAttribute('disabled', !!s.running);
      $('#bot-stop-btn')?.toggleAttribute('disabled', !s.running);
      // Notify per-page hooks.
      window.dispatchEvent(new CustomEvent('bot:status', { detail: s }));
    } catch (e) {
      console.warn('status poll failed', e);
    }
  }

  function bindSidebar() {
    $('#bot-start-btn')?.addEventListener('click', async () => {
      try { await api('/api/bot/start', { method: 'POST' }); }
      catch (e) { alert('Start failed: ' + e.message); }
      refreshStatus();
    });
    $('#bot-stop-btn')?.addEventListener('click', async () => {
      if (!confirm('Stop the bot loop?')) return;
      try { await api('/api/bot/stop', { method: 'POST' }); }
      catch (e) { alert('Stop failed: ' + e.message); }
      refreshStatus();
    });
    $('#refresh-btn')?.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('dashboard:refresh'));
    });
    setInterval(refreshStatus, STATUS_MS);
    refreshStatus();
  }

  // ── Snapshot fetch with simple in-memory cache ─────────────────────

  async function getSnapshot(force = false) {
    const url = '/api/snapshot' + (force ? '?force=1' : '');
    const snap = await api(url);
    $('#last-refresh').textContent = `Updated ${fmtDate(snap.generated_at || '')}`;
    return snap;
  }

  // ── Health card renderer ───────────────────────────────────────────

  function renderHealth(snap) {
    const root = $('#health-grid');
    if (!root) return;
    const health = snap.health || {};
    const order = ['config', 'bot_state', 'usaspending', 'ticker_source', 'alpaca'];
    root.innerHTML = order.map((k) => {
      const h = health[k] || {};
      const statusVal = (h.status || 'unknown').toLowerCase();
      const cls = statusVal === 'fresh' || statusVal === 'ok' ? 'ok'
        : statusVal === 'stale' || statusVal === 'warn' ? 'warn'
        : statusVal === 'cold' || statusVal === 'error' ? 'bad'
        : 'unknown';
      return `
        <div class="health-cell ${cls}">
          <div class="h-label">${escapeHtml(k)}</div>
          <div class="h-status ${cls}">${escapeHtml(h.status || 'unknown')}</div>
          <div class="muted small">${escapeHtml(h.detail || '')}</div>
        </div>`;
    }).join('');
  }

  function renderSummary(snap) {
    const root = $('#summary-kpis');
    if (!root) return;
    const s = snap.summary || {};
    const stats = s.stats || {};
    const kpis = [
      { k: 'Contracts', v: fmtNumber(stats.count) },
      { k: 'Total $',  v: fmtMoney(stats.total) },
      { k: 'Avg $',    v: fmtMoney(stats.avg) },
      { k: 'Matched',  v: fmtNumber(s.matched) },
      { k: 'Validated', v: fmtNumber(s.validated) },
      { k: 'Material', v: fmtNumber(s.material) },
      { k: 'Material $', v: fmtMoney(s.material_total) },
      { k: 'Ambiguous', v: fmtNumber(s.ambiguous_matches) },
    ];
    root.innerHTML = kpis.map(kpi => kpiHtml(kpi)).join('');
  }

  function renderTwoPhase(snap) {
    const root = $('#twophase-kpis');
    if (!root) return;
    const tp = snap.two_phase || {};
    root.innerHTML = [
      { k: 'Phase 1 candidates', v: fmtNumber(tp.phase1_candidates) },
      { k: 'Phase 2 candidates', v: fmtNumber(tp.phase2_candidates) },
      { k: 'Phase 2 threshold', v: fmtRatio(tp.phase2_threshold) },
      { k: 'Phase 2 tickers', v: (tp.phase2_tickers || []).join(', ') || '—' },
    ].map(kpiHtml).join('');
  }

  function renderAlpacaAccount(snap) {
    const root = $('#alpaca-kpis');
    if (!root) return;
    const acct = (snap.alpaca || {}).account;
    if (!acct) {
      root.innerHTML = '<div class="muted">Alpaca not connected. Set ALPACA_API_KEY in Config.</div>';
      return;
    }
    const dayCls = acct.day_pl > 0 ? 'good' : acct.day_pl < 0 ? 'bad' : '';
    root.innerHTML = [
      { k: 'Portfolio', v: fmtMoney(acct.portfolio_value) },
      { k: 'Equity', v: fmtMoney(acct.equity) },
      { k: 'Cash', v: fmtMoney(acct.cash) },
      { k: 'Buying Power', v: fmtMoney(acct.buying_power) },
      { k: 'Day P/L', v: fmtMoney(acct.day_pl), cls: dayCls },
      { k: 'Day P/L %', v: fmtPct(acct.day_pl_pct), cls: dayCls },
    ].map(kpiHtml).join('');
  }

  function renderPositions(snap) {
    const tbody = $('#positions-table tbody');
    if (!tbody) return;
    const positions = (snap.alpaca || {}).positions || [];
    if (positions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">No open positions.</td></tr>';
      return;
    }
    tbody.innerHTML = positions.map((p) => {
      const cls = p.unrealized_pl > 0 ? 'pos-good' : p.unrealized_pl < 0 ? 'pos-bad' : '';
      return `
        <tr>
          <td><strong>${escapeHtml(p.symbol)}</strong></td>
          <td class="num">${fmtNumber(p.qty)}</td>
          <td class="num">${fmtMoney(p.avg_entry_price)}</td>
          <td class="num">${fmtMoney(p.current_price)}</td>
          <td class="num">${fmtMoney(p.market_value)}</td>
          <td class="num ${cls}">${fmtMoney(p.unrealized_pl)}</td>
          <td class="num ${cls}">${fmtPct(p.unrealized_plpc)}</td>
          <td><button class="danger small" data-sell="${escapeHtml(p.symbol)}">Sell</button></td>
        </tr>`;
    }).join('');
    $$('button[data-sell]', tbody).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.sell;
        if (!confirm(`Liquidate ${sym}?`)) return;
        btn.disabled = true;
        try {
          await api(`/api/positions/${encodeURIComponent(sym)}/sell`, { method: 'POST' });
          window.dispatchEvent(new CustomEvent('dashboard:refresh'));
        } catch (e) { alert('Sell failed: ' + e.message); btn.disabled = false; }
      });
    });
  }

  function renderAnomalies(snap) {
    const root = $('#anomalies-list');
    if (!root) return;
    const items = [];
    const cv = snap.config_validation || {};
    (cv.issues || []).forEach(t => items.push({ t, lvl: 'bad' }));
    (cv.warnings || []).forEach(t => items.push({ t, lvl: 'warn' }));
    ((snap.analytics || {}).anomalies || []).forEach((a) => {
      items.push({ t: `${a.kind || 'anomaly'}: ${a.detail || a.recipient || ''}`, lvl: 'warn' });
    });
    if (items.length === 0) {
      root.innerHTML = '<li class="muted">No anomalies detected.</li>';
      return;
    }
    root.innerHTML = items.map(i => `<li class="${i.lvl === 'bad' ? 'pos-bad' : ''}">${escapeHtml(i.t)}</li>`).join('');
  }

  function kpiHtml({ k, v, cls }) {
    return `<div class="kpi"><div class="k">${escapeHtml(k)}</div><div class="v ${cls || ''}">${escapeHtml(v ?? '—')}</div></div>`;
  }

  // ── Page initializers ──────────────────────────────────────────────

  function initOverview() {
    bindSidebar();
    const refresh = async () => {
      try {
        const snap = await getSnapshot();
        renderHealth(snap);
        renderSummary(snap);
        renderTwoPhase(snap);
        renderAlpacaAccount(snap);
        renderPositions(snap);
        renderAnomalies(snap);
      } catch (e) {
        console.error(e);
        $('#health-grid').textContent = 'Failed to load: ' + e.message;
      }
    };
    window.addEventListener('dashboard:refresh', () => refresh());
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  function applyContractFilters(snap) {
    const recipientQ = $('#filter-recipient').value.toLowerCase();
    const agencyQ = $('#filter-agency').value.toLowerCase();
    const minAmt = parseFloat($('#filter-min-amount').value) || 0;
    const sortBy = $('#filter-sort').value;
    let rows = (snap.contracts || []).map((c) => ({
      date: c['Action Date'] || '',
      recipient: c['Recipient Name'] || '',
      agency: c['Awarding Agency'] || '',
      amount: Number(c['Award Amount'] || 0),
      description: c['Description'] || '',
    }));
    rows = rows.filter(r =>
      (!recipientQ || r.recipient.toLowerCase().includes(recipientQ)) &&
      (!agencyQ || r.agency.toLowerCase().includes(agencyQ)) &&
      (r.amount >= minAmt)
    );
    rows.sort((a, b) => {
      switch (sortBy) {
        case 'date': return (b.date || '').localeCompare(a.date || '');
        case 'recipient': return a.recipient.localeCompare(b.recipient);
        case 'agency': return a.agency.localeCompare(b.agency);
        default: return b.amount - a.amount;
      }
    });
    return rows;
  }

  function initContracts() {
    bindSidebar();
    let lastSnap = null;
    const draw = () => {
      if (!lastSnap) return;
      const rows = applyContractFilters(lastSnap);
      const tbody = $('#contracts-table tbody');
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">No contracts match.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.slice(0, 200).map(r => `
        <tr>
          <td>${escapeHtml(r.date)}</td>
          <td>${escapeHtml(r.recipient)}</td>
          <td>${escapeHtml(r.agency)}</td>
          <td class="num">${fmtMoney(r.amount)}</td>
          <td>${escapeHtml((r.description || '').slice(0, 120))}</td>
        </tr>`).join('');
    };
    const refresh = async () => {
      try { lastSnap = await getSnapshot(); draw(); }
      catch (e) {
        $('#contracts-table tbody').innerHTML = `<tr><td colspan="5" class="pos-bad">Error: ${escapeHtml(e.message)}</td></tr>`;
      }
    };
    ['#filter-recipient', '#filter-agency', '#filter-min-amount', '#filter-sort']
      .forEach(sel => $(sel).addEventListener('input', draw));
    window.addEventListener('dashboard:refresh', refresh);
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  const TIER_ORDER = { high: 3, medium: 2, low: 1, none: 0 };

  function initTickers() {
    bindSidebar();
    let lastSnap = null;
    const draw = () => {
      if (!lastSnap) return;
      const minTier = $('#filter-tier').value;
      const materialOnly = $('#filter-material').checked;
      const sortBy = $('#filter-sort').value;
      let rows = (lastSnap.analyses || []).filter(a => a.ticker);
      if (materialOnly) rows = rows.filter(a => a.material);
      if (minTier) rows = rows.filter(a => (TIER_ORDER[(a.match || {}).tier] || 0) >= (TIER_ORDER[minTier] || 0));
      rows.sort((a, b) => {
        const ratioA = (a.info && a.info.market_cap ? a.amount / a.info.market_cap : 0);
        const ratioB = (b.info && b.info.market_cap ? b.amount / b.info.market_cap : 0);
        switch (sortBy) {
          case 'amount': return b.amount - a.amount;
          case 'confidence': return (TIER_ORDER[(b.match || {}).tier] || 0) - (TIER_ORDER[(a.match || {}).tier] || 0);
          case 'recipient': return (a.recipient || '').localeCompare(b.recipient || '');
          default: return ratioB - ratioA;
        }
      });
      const tbody = $('#tickers-table tbody');
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="muted">No matched tickers.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.slice(0, 200).map((a) => {
        const tier = (a.match || {}).tier || 'none';
        const score = (a.match || {}).score;
        const mc = (a.info || {}).market_cap;
        const elig = (a.eligibility || {}).status || '—';
        const ratio = mc ? a.amount / mc : null;
        return `
          <tr>
            <td>${escapeHtml(a.recipient)}</td>
            <td><strong>${escapeHtml(a.ticker)}</strong></td>
            <td><span class="tag ${tier}">${escapeHtml(tier)}</span></td>
            <td class="num">${score != null ? Number(score).toFixed(1) : '—'}</td>
            <td class="num">${fmtMoney(a.amount)}</td>
            <td class="num">${fmtMoney(mc)}</td>
            <td class="num">${ratio != null ? fmtRatio(ratio) : '—'}</td>
            <td><span class="tag ${elig}">${escapeHtml(elig)}</span></td>
          </tr>`;
      }).join('');
    };
    const refresh = async () => {
      try { lastSnap = await getSnapshot(); draw(); }
      catch (e) { $('#tickers-table tbody').innerHTML = `<tr><td colspan="8" class="pos-bad">Error: ${escapeHtml(e.message)}</td></tr>`; }
    };
    ['#filter-tier', '#filter-material', '#filter-sort'].forEach(sel => $(sel).addEventListener('change', draw));
    window.addEventListener('dashboard:refresh', refresh);
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  function initTrading() {
    bindSidebar();
    const renderLifecycle = (snap) => {
      const root = $('#lifecycle-kpis');
      const lc = ((snap.alpaca || {}).lifecycle) || {};
      root.innerHTML = [
        { k: 'Submitted', v: fmtNumber(lc.submitted) },
        { k: 'Filled', v: fmtNumber(lc.filled) },
        { k: 'Rejected', v: fmtNumber(lc.rejected), cls: lc.rejected ? 'bad' : '' },
        { k: 'Canceled', v: fmtNumber(lc.canceled) },
        { k: 'Aging', v: fmtNumber(lc.aging), cls: lc.aging ? 'warn' : '' },
      ].map(kpiHtml).join('');
    };
    const renderDrawdown = (snap) => {
      const tbody = $('#drawdown-table tbody');
      const rows = ((snap.alpaca || {}).drawdown_leaders) || [];
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="muted">No drawdowns.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(r => `
        <tr>
          <td><strong>${escapeHtml(r.symbol)}</strong></td>
          <td class="num pos-bad">${fmtMoney(r.unrealized_pl)}</td>
          <td class="num pos-bad">${fmtPct(r.unrealized_plpc)}</td>
        </tr>`).join('');
    };
    const renderOrders = (snap) => {
      const tbody = $('#orders-table tbody');
      const rows = ((snap.alpaca || {}).orders) || [];
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">No recent orders.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.slice(0, 50).map(o => `
        <tr>
          <td>${escapeHtml(fmtDate(o.submitted_at))}</td>
          <td><strong>${escapeHtml(o.symbol || '')}</strong></td>
          <td>${escapeHtml(o.side || '')}</td>
          <td>${escapeHtml(o.status || '')}</td>
          <td class="num">${o.notional != null ? fmtMoney(o.notional) : '—'}</td>
          <td class="num">${o.filled_dollar != null ? fmtMoney(o.filled_dollar) : '—'}</td>
        </tr>`).join('');
    };
    const refresh = async () => {
      try {
        const snap = await getSnapshot();
        renderAlpacaAccount(snap);
        renderPositions(snap);
        renderLifecycle(snap);
        renderDrawdown(snap);
        renderOrders(snap);
      } catch (e) { console.error(e); }
    };
    window.addEventListener('dashboard:refresh', refresh);
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  function initControl() {
    bindSidebar();

    const renderStatus = (s) => {
      const root = $('#control-kpis');
      if (!root) return;
      root.innerHTML = [
        { k: 'State', v: s.running ? 'Running' : 'Stopped', cls: s.running ? 'good' : 'bad' },
        { k: 'Ticks', v: fmtNumber(s.ticks) },
        { k: 'Awards processed', v: fmtNumber(s.awards_processed) },
        { k: 'Buys', v: fmtNumber(s.buys) },
        { k: 'Exit scans', v: fmtNumber(s.exit_scans) },
        { k: 'Last tick', v: fmtDate(s.last_tick_at) },
        { k: 'Started', v: fmtDate(s.started_at) },
        { k: 'Last error', v: s.last_error || '—', cls: s.last_error ? 'bad' : '' },
      ].map(kpiHtml).join('');
    };

    window.addEventListener('bot:status', (e) => renderStatus(e.detail));

    $('#ctrl-start').addEventListener('click', async () => {
      try { await api('/api/bot/start', { method: 'POST' }); refreshStatus(); }
      catch (e) { alert(e.message); }
    });
    $('#ctrl-stop').addEventListener('click', async () => {
      if (!confirm('Stop the bot loop?')) return;
      try { await api('/api/bot/stop', { method: 'POST' }); refreshStatus(); }
      catch (e) { alert(e.message); }
    });
    $('#ctrl-tick').addEventListener('click', async () => {
      const btn = $('#ctrl-tick'); btn.disabled = true; btn.textContent = 'Running…';
      try {
        const r = await api('/api/bot/tick', { method: 'POST' });
        alert(`Cycle finished. Awards processed: ${r.delta.awards_processed}, buys: ${r.delta.buys}, exit scans: ${r.delta.exit_scans}.`);
      } catch (e) { alert('Cycle failed: ' + e.message); }
      finally { btn.disabled = false; btn.textContent = 'Run one cycle now'; refreshStatus(); }
    });

    // Live log tail.
    let lastSeen = '';
    const pre = $('#log-tail');
    const autoscroll = () => $('#logs-autoscroll').checked;
    const tail = async () => {
      try {
        const r = await api('/api/bot/logs?since=' + encodeURIComponent(lastSeen) + '&limit=300');
        const entries = r.entries || [];
        if (entries.length) {
          if (lastSeen === '') pre.textContent = '';
          for (const e of entries) {
            lastSeen = e.ts;
            const line = document.createElement('span');
            line.className = 'lvl-' + e.level;
            line.textContent = `${e.ts} ${e.level.padEnd(8)} ${e.logger.padEnd(24)} ${e.message}\n`;
            pre.appendChild(line);
          }
          if (autoscroll()) pre.scrollTop = pre.scrollHeight;
        }
      } catch (e) { console.warn('log poll failed', e); }
    };
    $('#logs-clear').addEventListener('click', async () => {
      try { await api('/api/bot/logs/clear', { method: 'POST' }); pre.textContent = ''; lastSeen = ''; }
      catch (e) { alert(e.message); }
    });
    setInterval(tail, LOG_MS);
    tail();
  }

  function initConfig() {
    bindSidebar();
    $('#config-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const form = ev.target;
      const updates = {};
      $$('input', form).forEach((inp) => {
        // Skip empty secret inputs (placeholder shows the masked value).
        if (inp.type === 'password' && !inp.value) return;
        updates[inp.name] = inp.value;
      });
      const msg = $('#config-msg');
      msg.textContent = 'Saving…';
      try {
        await api('/api/config', { method: 'POST', body: JSON.stringify({ updates }) });
        msg.textContent = 'Saved. Bot will use the new values on the next cycle.';
        msg.className = 'pos-good small';
      } catch (e) {
        msg.textContent = 'Save failed: ' + e.message;
        msg.className = 'pos-bad small';
      }
    });
  }

  window.Dashboard = {
    initOverview, initContracts, initTickers, initTrading, initControl, initConfig,
  };
})(window);
