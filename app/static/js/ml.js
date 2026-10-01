/* Neural Net page — renders /api/ml/dashboard */

const BOT_COLORS = { 'Neural net': '#bc8cff', 'Scalp bot': '#58a6ff', 'Swing bot': '#ffa657' };
let raceChart = null;
let wfChart = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = v => v == null ? '—' : window.UI.money(v, { dp: 0 });
const signed = (v, d = 3) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d);
const pct = (v, d = 1) => v == null ? '—' : (v * 100).toFixed(d) + '%';
const cls = v => v == null ? 'muted' : v > 0 ? 'pos' : v < 0 ? 'neg' : 'muted';
const ago = iso => {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')) / 1000;
  return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : s < 172800 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};
const time = iso => iso ? new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';

function badge(id, text, kind) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = 'status-badge ' + kind;
}

function kpi(label, value, sub, klass = '') {
  return `<div class="kpi"><div class="k-label">${label}</div><div class="k-value ${klass}">${value}</div><div class="k-sub">${sub}</div></div>`;
}

function renderHeader(d) {
  const meta = d.auto.meta;
  badge('b-bot', d.settings.nn_bot_enabled ? 'BOT ON · paper' : 'BOT OFF', d.settings.nn_bot_enabled ? 'ok' : 'neutral');
  badge('b-model', meta ? `model ${time(meta.trained_at)}` : 'no model', meta ? 'info' : 'danger');
  badge('b-retrain', d.settings.retrain_enabled ? `retrains ${d.settings.retrain_time_et} ET` : 'retrain off', d.settings.retrain_enabled ? 'info' : 'neutral');

  const nn = d.bots.find(b => b.bot === 'Neural net') || { trades: 0, wins: 0, pnl: null };
  const t = meta?.test_metrics?.all || {};
  const top10 = t.top_k?.['10'];
  document.getElementById('kpis').innerHTML = [
    kpi('NN bot P&L · 30d', money(nn.pnl), `${nn.trades} trades · ${nn.trades ? pct(nn.wins / nn.trades, 0) : '—'} win`, cls(nn.pnl)),
    kpi('Open NN positions', d.nn_open.length, `max ${d.settings.max_positions} · ${d.settings.max_hold_minutes} min max hold`),
    ...(d.walkforward ? [
      kpi('Walk-forward AUC', d.walkforward.summary.nn.auc.mean.toFixed(3), `${d.walkforward.summary.nn.auc.n} monthly folds · 0.500 = coin flip`),
      kpi('Top-10/day, after costs', signed(d.walkforward.summary.nn.top10_avg_r.mean),
          `R per trade · random side ${signed(d.walkforward.summary.random_avg_r.mean)}`, cls(d.walkforward.summary.nn.top10_avg_r.mean)),
      kpi('Profitable months', pct(d.walkforward.summary.nn.top10_avg_r.positive_share, 0),
          `${d.walkforward.cost_bps} bp/side costs · ${d.walkforward.n_sessions} days of SIP data`),
    ] : [
      kpi('Held-out AUC', t.auc_long ? ((t.auc_long + t.auc_short) / 2).toFixed(3) : '—', '0.500 = coin flip'),
      kpi('Held-out avg R', signed(t.avg_r), `before costs · ${t.trades_per_day ? t.trades_per_day.toFixed(1) : '—'} trades/day`, cls(t.avg_r)),
      kpi('Top-10/day avg R', signed(top10?.avg_r), `before costs · ${top10 ? pct(top10.positive_days, 0) : '—'} positive days`, cls(top10?.avg_r)),
    ]),
    kpi('Parameters', d.auto.params ? d.auto.params.toLocaleString() : '—', meta ? `${meta.n_samples.toLocaleString()} training samples` : 'not trained'),
  ].join('');
}

function renderRace(d) {
  const dates = [...new Set(d.daily.map(r => r.session_date))].sort();
  const bots = Object.keys(BOT_COLORS).filter(b => d.daily.some(r => r.bot === b));
  const datasets = bots.map(bot => {
    let cum = 0;
    const byDate = Object.fromEntries(d.daily.filter(r => r.bot === bot).map(r => [r.session_date, r.pnl]));
    return {
      label: bot,
      data: dates.map(dt => (cum += byDate[dt] || 0)),
      borderColor: BOT_COLORS[bot], backgroundColor: BOT_COLORS[bot],
      borderWidth: bot === 'Neural net' ? 2.5 : 1.5, pointRadius: 0, tension: .2,
    };
  });
  if (raceChart) raceChart.destroy();
  raceChart = new Chart(document.getElementById('race'), {
    type: 'line',
    data: { labels: dates.map(s => s.slice(5)), datasets },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#8b949e', boxWidth: 10 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: '#8b949e', maxTicksLimit: 8 }, grid: { color: 'rgba(48,54,61,.5)' } },
        y: { ticks: { color: '#8b949e', callback: v => money(v) }, grid: { color: 'rgba(48,54,61,.5)' } },
      },
    },
  });

  const rows = Object.keys(BOT_COLORS).map(name => d.bots.find(b => b.bot === name) || { bot: name, trades: 0, wins: 0 });
  document.getElementById('bots').innerHTML =
    '<tr><th>Bot</th><th>Trades</th><th>Win rate</th><th>P&L</th><th>Avg / trade</th></tr>' +
    rows.map(b => `<tr><td><span style="color:${BOT_COLORS[b.bot]}">●</span> ${b.bot}</td><td>${b.trades}</td>
      <td>${b.trades ? pct(b.wins / b.trades, 0) : '—'}</td><td class="${cls(b.pnl)}">${money(b.pnl)}</td>
      <td class="${cls(b.avg_pnl)}">${money(b.avg_pnl)}</td></tr>`).join('');
}

// Probabilities cluster around 0.4–0.55, so bars use a zoomed 0.30–0.70 scale
const BAR_LO = 0.30, BAR_HI = 0.70;
const barPct = p => (Math.min(Math.max((p - BAR_LO) / (BAR_HI - BAR_LO), 0), 1) * 100).toFixed(1);
let showAllReadings = false;

function toggleReadings() {
  showAllReadings = !showAllReadings;
  document.getElementById('readings-toggle').textContent = showAllReadings ? 'top 12' : 'show all';
  loadML();
}

function probBar(p, thr, color) {
  return `<div class="pbar"><div class="fill" style="width:${barPct(p)}%;background:${color};opacity:${p >= thr ? 1 : .45}"></div>
    <div class="thr" style="left:${barPct(thr)}%"></div><span class="lbl">${p.toFixed(3)}</span></div>`;
}

function renderReadings(d) {
  const r = d.auto.readings || [];
  const el = document.getElementById('readings');
  if (!r.length) {
    el.innerHTML = `<tr><td class="muted">No readings yet — the network scores symbols during market hours once 15+ bars are in.</td></tr>`;
    return;
  }
  el.innerHTML = '<tr><th>Symbol</th><th>Price</th><th class="prob-cell">P(long profitable)</th><th class="prob-cell">P(short profitable)</th><th>Lean</th><th>Bar</th></tr>' +
    r.slice(0, showAllReadings ? r.length : 12).map(x => {
      const best = Math.max(x.p_long, x.p_short), hot = best >= x.threshold;
      const lean = x.p_long >= x.p_short ? '<span class="pos">LONG</span>' : '<span class="neg">SHORT</span>';
      return `<tr class="${hot ? 'hot' : ''}"><td><a href="/symbol/${esc(x.symbol)}">${esc(x.symbol)}</a></td><td>$${x.price.toFixed(2)}</td>
        <td class="prob-cell">${probBar(x.p_long, x.threshold, 'var(--green)')}</td>
        <td class="prob-cell">${probBar(x.p_short, x.threshold, 'var(--red)')}</td>
        <td>${lean}${hot ? ' <span class="status-badge info">signal</span>' : ''}</td><td class="muted">${ago(x.bar_time)}</td></tr>`;
    }).join('');
}

function renderHoldout(d) {
  const meta = d.auto.meta;
  const el = document.getElementById('holdout');
  if (!meta) { el.innerHTML = '<div class="note">No model trained yet.</div>'; return; }
  const t = meta.test_metrics.all;
  const topk = Object.entries(t.top_k || {}).map(([k, m]) =>
    `<tr><td>Top ${k} / day</td><td class="${cls(m.avg_r)}">${signed(m.avg_r)}</td><td>${pct(m.win_rate)}</td><td>${pct(m.positive_days, 0)}</td></tr>`).join('');
  el.innerHTML = `
    <div class="note">Trained on ${meta.sessions[0]} → ${meta.sessions[1]}. Scored on ${t.days} sessions it never saw
      (${meta.test_sessions[0]} → ${meta.test_sessions[1]}), ${t.n.toLocaleString()} decision points.
      R = profit in units of the stop distance (+2 = target hit, −1 = stopped out).
      <b>${meta.cost_bps ? `After ${meta.cost_bps} bp/side costs.` : 'Before trading costs — see the walk-forward test above for after-cost results.'}</b></div>
    <div class="table-wrapper mt-2"><table class="data-table">
      <tr><th>Picks</th><th>Avg R</th><th>Win rate</th><th>Positive days</th></tr>
      <tr><td>Random side (baseline)</td><td class="${cls(t.base_avg_r)}">${signed(t.base_avg_r)}</td><td>—</td><td>—</td></tr>
      <tr><td>At threshold ${meta.threshold}</td><td class="${cls(t.avg_r)}">${signed(t.avg_r)}</td><td>${pct(t.win_rate)}</td><td>${pct(t.positive_days, 0)}</td></tr>
      ${topk}
    </table></div>
    <div class="note mt-2">AUC long ${t.auc_long.toFixed(3)} · short ${t.auc_short.toFixed(3)} (0.5 = no skill).
      ${t.avg_r > 0 && (t.positive_days || 0) >= .5 ? '' : 'No reliable edge yet — the bot trades on paper so its live record can be measured, and nightly retraining only promotes a model that beats the current one on unseen days.'}</div>`;
}

function renderHistory(d) {
  const el = document.getElementById('history');
  if (!d.history.length) { el.innerHTML = '<tr><td class="muted">No retraining runs yet.</td></tr>'; return; }
  const v = m => !m ? '<span class="muted">—</span>' :
    `<span class="${m.promote ? 'verdict-yes' : 'verdict-no'}" title="${esc(m.reason)}">${m.promote ? '↑ promoted' : 'kept'}</span>`;
  el.innerHTML = '<tr><th>Run</th><th>Data through</th><th>Autonomous</th><th>Filter NN</th><th>Filter GBM</th></tr>' +
    [...d.history].reverse().map(h => `<tr><td>${time(h.started_at)}${h.status === 'failed' ? ' <span class="neg">failed</span>' : ''}</td>
      <td>${esc(h.latest_session || '—')}</td><td>${v(h.models?.auto_nn)}</td><td>${v(h.models?.filter_nn)}</td><td>${v(h.models?.filter_gbm)}</td></tr>
      ${h.models?.auto_nn ? `<tr><td colspan="5" class="note" style="padding-top:0">${esc(h.models.auto_nn.reason)}</td></tr>` : ''}`).join('');
}

function renderTrades(d) {
  document.getElementById('nn-open').innerHTML = d.nn_open.length
    ? '<tr><th>Open</th><th>Side</th><th>Entry</th><th>Now</th><th>Stop</th><th>Target</th><th>P</th><th>Unrealized</th><th>Opened</th></tr>' +
      d.nn_open.map(p => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>$${p.avg_entry_price.toFixed(2)}</td>
        <td>${p.current_price ? '$' + p.current_price.toFixed(2) : '—'}</td><td>$${(p.stop_price || 0).toFixed(2)}</td>
        <td>$${(p.take_profit_price || 0).toFixed(2)}</td><td>${p.prob != null ? p.prob.toFixed(3) : '—'}</td>
        <td class="${cls(p.unrealized_pnl)}">${money(p.unrealized_pnl)}</td><td>${ago(p.opened_at)}</td></tr>`).join('')
    : '<tr><td class="muted">No open neural-net positions.</td></tr>';
  document.getElementById('nn-trades').innerHTML = d.nn_trades.length
    ? '<tr><th>Closed</th><th>Side</th><th>Entry → Exit</th><th>P</th><th>P&L</th><th>Exit reason</th><th>When</th></tr>' +
      d.nn_trades.map(t => `<tr><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>$${t.entry_price.toFixed(2)} → $${t.exit_price.toFixed(2)}</td>
        <td>${t.prob != null ? t.prob.toFixed(3) : '—'}</td><td class="${cls(t.pnl)}">${money(t.pnl)}</td>
        <td>${esc(t.close_reason)}</td><td>${time(t.closed_at)}</td></tr>`).join('')
    : '<tr><td class="muted">No closed neural-net trades yet — it trades during market hours.</td></tr>';
}

function renderFilter(d) {
  const f = d.filter;
  document.getElementById('filter-mode').textContent =
    `· mode: ${d.settings.filter_mode} · model: ${d.settings.filter_model}${f.threshold ? ' · threshold ' + f.threshold : ''}`;
  document.getElementById('filter').innerHTML = f.recent.length
    ? '<tr><th>Symbol</th><th>Bot</th><th>Side</th><th>Engine score</th><th>P(profitable)</th><th>Verdict</th><th>When</th></tr>' +
      f.recent.map(p => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.engine)}</td><td>${p.direction > 0 ? 'long' : 'short'}</td>
        <td>${p.score.toFixed(0)}</td><td>${p.prob.toFixed(3)}</td>
        <td>${p.prob >= f.threshold ? '<span class="pos">pass</span>' : `<span class="neg">${d.settings.filter_mode === 'gate' ? 'blocked' : 'would block'}</span>`}</td>
        <td>${ago(p.at)}</td></tr>`).join('')
    : '<tr><td class="muted">No candidates scored yet this session.</td></tr>';
}

function renderWalkforward(d) {
  const wf = d.walkforward;
  if (!wf) return;
  const s = wf.summary;
  document.getElementById('wf-sub').textContent =
    `· ${wf.source.toUpperCase()} data ${wf.sessions[0]} → ${wf.sessions[1]} · ${wf.samples.toLocaleString()} samples · ${wf.cost_bps} bp/side costs`;
  const row = (label, c, hint = '') => c && c.mean != null
    ? `<tr><td>${label}${hint ? ` <span class="muted">${hint}</span>` : ''}</td><td class="${cls(c.mean)}">${signed(c.mean)}</td>
       <td class="muted">${signed(c.lo)} … ${signed(c.hi)}</td><td>${pct(c.positive_share, 0)}</td></tr>` : '';
  const verdict = (c) => c && c.lo > 0 ? '<span class="pos">edge (CI above 0)</span>'
    : c && c.hi < 0 ? '<span class="neg">loses after costs</span>' : '<span class="muted">not distinguishable from 0</span>';
  document.getElementById('wf').innerHTML = `
    <div class="wf-grid">
      <div class="table-wrapper"><table class="data-table">
        <tr><th>Strategy (avg R per trade, after costs)</th><th>Mean</th><th>95% CI across folds</th><th>Folds &gt; 0</th></tr>
        ${row('Random side', s.random_avg_r, 'baseline')}
        ${row('Always long', s.always_long_avg_r, 'baseline')}
        ${row('Neural net — top 10/day', s.nn.top10_avg_r)}
        ${row('Neural net — top 5/day', s.nn.top5_avg_r)}
        ${row('Neural net — at threshold', s.nn.avg_r)}
        ${row('Tree model — top 10/day', s.gbm.top10_avg_r)}
        ${row('Tree model — at threshold', s.gbm.avg_r)}
      </table></div>
      <div><div class="chart-box" style="height:220px"><canvas id="wf-chart"></canvas></div></div>
    </div>
    <div class="note">${s.nn.top10_avg_r.n} monthly folds. Neural net AUC ${s.nn.auc.mean.toFixed(3)}
      (tree ${s.gbm.auc.mean.toFixed(3)}; 0.5 = no skill). Verdict for the network's top picks: ${verdict(s.nn.top10_avg_r)}.</div>`;

  if (wfChart) wfChart.destroy();
  wfChart = new Chart(document.getElementById('wf-chart'), {
    type: 'bar',
    data: {
      labels: wf.folds.map(f => f.test[0].slice(2, 7)),
      datasets: [
        { label: 'Neural net top-10', data: wf.folds.map(f => f.nn_top10), backgroundColor: '#bc8cff' },
        { label: 'Tree top-10', data: wf.folds.map(f => f.gbm_top10), backgroundColor: '#ffa657' },
        { label: 'Random', data: wf.folds.map(f => f.random), backgroundColor: '#4d5566' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8b949e', boxWidth: 10 } },
                 tooltip: { callbacks: { label: c => `${c.dataset.label}: ${signed(c.parsed.y)}R` } } },
      scales: { x: { ticks: { color: '#8b949e' }, grid: { display: false } },
                y: { ticks: { color: '#8b949e', callback: v => signed(v, 2) }, grid: { color: 'rgba(48,54,61,.5)' } } },
    },
  });
}

async function loadML() {
  try {
    const d = await (await fetch('/api/ml/dashboard')).json();
    renderHeader(d); renderRace(d); renderReadings(d); renderHoldout(d); renderHistory(d); renderTrades(d); renderFilter(d); renderWalkforward(d);
  } catch (e) {
    console.error('ML dashboard load failed', e);
  }
}

loadML();
setInterval(loadML, 15000);

window.handleSSEMessage = msg => {
  if (['nn_trade', 'bot_exit', 'ml_retrain'].includes(msg.type)) loadML();
};
