/**
 * diagnostics.js  —  Phase 3 Strategy Diagnostics
 *
 * Fetches all /api/diagnostics/* endpoints and renders:
 *   - Dual equity curve (all trades vs intraday-only)
 *   - Score bucket table + bar chart
 *   - Time-of-day table + bar chart
 *   - Component accuracy heat-table
 *   - Exit reason table + hold-time bar chart
 */

'use strict';

// ── Colour helpers ────────────────────────────────────────────────────────────

const PHASE_COLORS = {
  open:      '#f59e0b',
  morning:   '#10b981',
  midday:    '#3b82f6',
  afternoon: '#8b5cf6',
  unknown:   '#6b7280',
};

function _pnlCls(v) { return v >= 0 ? 'green' : 'red'; }

function _winCls(wr) {
  if (wr >= 55) return 'green';
  if (wr < 40)  return 'red';
  return 'yellow';
}

function _pfLabel(pf) {
  if (pf === null || pf === undefined) return '—';
  if (pf >= 1.5) return `<span class="green">${pf}</span>`;
  if (pf < 1.0)  return `<span class="red">${pf}</span>`;
  return pf;
}

function _fmt(v, prefix='$') {
  if (v === null || v === undefined) return '—';
  const s = Math.abs(v).toFixed(2);
  return (v < 0 ? '-' : '') + prefix + s;
}

// ── Chart instances (kept for destroy-before-recreate) ────────────────────────

const _charts = {};

function _mkChart(id, cfg) {
  if (_charts[id]) { _charts[id].destroy(); }
  const canvas = document.getElementById(id);
  if (!canvas) return;
  _charts[id] = new Chart(canvas.getContext('2d'), cfg);
}

// ── Equity curve ──────────────────────────────────────────────────────────────

async function loadEquityCurve() {
  const res = await fetch('/api/diagnostics/equity-curve');
  if (!res.ok) return;
  const data = await res.json();

  const all   = data.all_trades    || [];
  const intra = data.intraday_only || [];

  // Build outlier banner
  const allFinal   = all.length   ? all[all.length-1].equity   : 0;
  const intraFinal = intra.length ? intra[intra.length-1].equity : 0;
  const diff = allFinal - intraFinal;
  if (Math.abs(diff) > 0.01) {
    const banner = document.getElementById('outlier-banner');
    const msg    = document.getElementById('outlier-msg');
    banner.style.display = '';
    msg.textContent =
      `Overnight/multi-day holds contributed ${diff >= 0 ? '+' : ''}$${diff.toFixed(2)} to total P&L.` +
      ` Intraday-only equity: $${intraFinal.toFixed(2)} vs all-trades: $${allFinal.toFixed(2)}.`;
  }

  // Equity summary row
  const el = document.getElementById('equity-summary');
  if (el) {
    el.innerHTML = `
      <div class="diag-stat"><span class="diag-stat-val ${_pnlCls(allFinal)}">$${allFinal.toFixed(2)}</span><span class="diag-stat-lbl">Total P&L (all)</span></div>
      <div class="diag-stat"><span class="diag-stat-val ${_pnlCls(intraFinal)}">$${intraFinal.toFixed(2)}</span><span class="diag-stat-lbl">Intraday-only P&L</span></div>
      <div class="diag-stat"><span class="diag-stat-val ${_pnlCls(diff)}">${diff >= 0 ? '+' : ''}$${diff.toFixed(2)}</span><span class="diag-stat-lbl">Outlier contribution</span></div>
    `;
  }

  _mkChart('equity-chart', {
    type: 'line',
    data: {
      labels: all.map(d => d.date),
      datasets: [
        {
          label:           'All Trades',
          data:            all.map(d => d.equity),
          borderColor:     '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.08)',
          borderWidth:     2,
          fill:            true,
          tension:         0.3,
          pointRadius:     3,
        },
        {
          label:           'Intraday Only (≤6.5h)',
          data:            intra.map(d => d.equity),
          borderColor:     '#10b981',
          backgroundColor: 'rgba(16,185,129,0.08)',
          borderWidth:     2,
          fill:            true,
          tension:         0.3,
          pointRadius:     3,
          borderDash:      [5, 3],
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', font: { size: 10 }, maxRotation: 45 },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
          ticks: {
            color: '#94a3b8',
            font:  { size: 10 },
            callback: v => '$' + v.toFixed(0),
          },
          grid: { color: 'rgba(255,255,255,0.07)' },
        },
      },
    },
  });
}

// ── Score buckets ─────────────────────────────────────────────────────────────

async function loadScoreBuckets() {
  const res = await fetch('/api/diagnostics/score-buckets');
  if (!res.ok) return;
  const rows = await res.json();

  // Table
  const tEl = document.getElementById('score-bucket-table');
  if (!rows.length) {
    tEl.innerHTML = '<p class="empty-state">No scored trades yet.</p>';
  } else {
    let html = `<table class="data-table">
      <thead><tr>
        <th>Bucket</th><th>Trades</th><th>Win%</th>
        <th>Gross P&L</th><th>Profit Factor</th>
        <th>Avg Win</th><th>Avg Loss</th><th>Avg Hold</th>
      </tr></thead><tbody>`;
    rows.forEach(r => {
      html += `<tr>
        <td style="font-weight:600">${r.bucket}</td>
        <td>${r.trades}</td>
        <td class="${_winCls(r.win_rate)}">${r.win_rate}%</td>
        <td class="${_pnlCls(r.gross_pnl)}">${_fmt(r.gross_pnl)}</td>
        <td>${_pfLabel(r.profit_factor)}</td>
        <td class="green">${_fmt(r.avg_win)}</td>
        <td class="red">${_fmt(r.avg_loss)}</td>
        <td style="color:var(--text-muted)">${r.avg_hold_mins != null ? r.avg_hold_mins + 'm' : '—'}</td>
      </tr>`;
    });
    tEl.innerHTML = html + '</tbody></table>';
  }

  // Chart
  _mkChart('score-chart', {
    type: 'bar',
    data: {
      labels: rows.map(r => r.bucket),
      datasets: [
        {
          label:           'Win Rate %',
          data:            rows.map(r => r.win_rate),
          backgroundColor: rows.map(r => r.win_rate >= 50 ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)'),
          yAxisID:         'y',
        },
        {
          label:           'Gross P&L $',
          data:            rows.map(r => r.gross_pnl),
          type:            'line',
          borderColor:     '#f59e0b',
          backgroundColor: 'transparent',
          borderWidth:     2,
          pointRadius:     4,
          yAxisID:         'y2',
        },
      ],
    },
    options: _dualAxisOptions('Win Rate (%)', 'P&L ($)'),
  });
}

// ── Time buckets ──────────────────────────────────────────────────────────────

const BUCKET_LABELS = {
  open:      'Open (9:30–10:30)',
  morning:   'Morning (10:30–12:00)',
  midday:    'Midday (12:00–14:30)',
  afternoon: 'Afternoon (14:30–16:00)',
  unknown:   'Unknown',
};

async function loadTimeBuckets() {
  const res = await fetch('/api/diagnostics/time-buckets');
  if (!res.ok) return;
  const rows = await res.json();

  const tEl = document.getElementById('time-bucket-table');
  if (!rows.length) {
    tEl.innerHTML = '<p class="empty-state">No data yet.</p>';
  } else {
    let html = `<table class="data-table">
      <thead><tr>
        <th>Session</th><th>Trades</th><th>Win%</th>
        <th>Gross P&L</th><th>Profit Factor</th>
        <th>Avg Win</th><th>Avg Loss</th><th>Avg Hold</th>
      </tr></thead><tbody>`;
    rows.forEach(r => {
      html += `<tr>
        <td style="font-size:.78rem">${BUCKET_LABELS[r.bucket] || r.bucket}</td>
        <td>${r.trades}</td>
        <td class="${_winCls(r.win_rate)}">${r.win_rate}%</td>
        <td class="${_pnlCls(r.gross_pnl)}">${_fmt(r.gross_pnl)}</td>
        <td>${_pfLabel(r.profit_factor)}</td>
        <td class="green">${_fmt(r.avg_win)}</td>
        <td class="red">${_fmt(r.avg_loss)}</td>
        <td style="color:var(--text-muted)">${r.avg_hold_mins != null ? r.avg_hold_mins + 'm' : '—'}</td>
      </tr>`;
    });
    tEl.innerHTML = html + '</tbody></table>';
  }

  _mkChart('time-chart', {
    type: 'bar',
    data: {
      labels: rows.map(r => BUCKET_LABELS[r.bucket] || r.bucket),
      datasets: [
        {
          label:           'Gross P&L ($)',
          data:            rows.map(r => r.gross_pnl),
          backgroundColor: rows.map(r => PHASE_COLORS[r.bucket] || '#6b7280'),
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
      },
      scales: {
        x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
        y: {
          ticks: { color: '#94a3b8', font: { size: 10 }, callback: v => '$' + v },
          grid:  { color: 'rgba(255,255,255,0.07)' },
        },
      },
    },
  });
}

// ── Component accuracy ────────────────────────────────────────────────────────

async function loadComponentAccuracy() {
  const res = await fetch('/api/diagnostics/component-accuracy');
  if (!res.ok) return;
  const data = await res.json();

  const phases = data.phases || [];
  const comps  = data.components || {};
  const el     = document.getElementById('component-accuracy-table');

  if (!phases.length || !Object.keys(comps).length) {
    el.innerHTML = '<p class="empty-state">No component accuracy data yet.</p>';
    return;
  }

  // Header
  let html = `<table class="data-table">
    <thead><tr>
      <th>Component</th>
      ${phases.map(p => `<th>${BUCKET_LABELS[p] || p}</th>`).join('')}
      <th>Overall</th>
    </tr></thead><tbody>`;

  for (const [name, phaseData] of Object.entries(comps).sort()) {
    let totalCorrect = 0, totalAll = 0;
    const cells = phases.map(p => {
      const d = phaseData[p];
      if (!d) return '<td style="color:var(--text-muted)">—</td>';
      totalCorrect += d.correct;
      totalAll     += d.total;
      const pct    = d.accuracy_pct;
      const cls    = pct >= 55 ? 'green' : (pct < 45 ? 'red' : 'yellow');
      return `<td class="${cls}" style="font-weight:600">${pct}%<br><span style="font-size:.65rem;font-weight:400;color:var(--text-muted)">${d.correct}/${d.total}</span></td>`;
    }).join('');

    const overallPct = totalAll > 0 ? Math.round(totalCorrect / totalAll * 1000) / 10 : null;
    const oCls       = overallPct !== null ? (overallPct >= 55 ? 'green' : (overallPct < 45 ? 'red' : 'yellow')) : '';
    const overallCell = overallPct !== null
      ? `<td class="${oCls}" style="font-weight:700">${overallPct}%</td>`
      : '<td>—</td>';

    html += `<tr>
      <td style="font-family:var(--font-mono);font-size:.75rem">${name.replace(/_/g,' ')}</td>
      ${cells}
      ${overallCell}
    </tr>`;
  }

  el.innerHTML = html + '</tbody></table>';
}

// ── Exit analysis ─────────────────────────────────────────────────────────────

const EXIT_LABELS = {
  target_hit:              icon('target') + 'Target Hit',
  stop_hit:                icon('stop') + 'Stop Hit',
  signal_reversed_bearish: '↩ Sig. Reversed (Bear)',
  signal_reversed_bullish: '↩ Sig. Reversed (Bull)',
  signal_exhausted:        '⬇ Signal Exhausted',
  eod_flatten:             icon('bell') + 'EOD Close',
  manual:                  icon('hand') + 'Manual',
  unknown:                 '? Unknown',
};

async function loadExitAnalysis() {
  const res = await fetch('/api/diagnostics/exit-analysis');
  if (!res.ok) return;
  const rows = await res.json();

  const tEl = document.getElementById('exit-table');
  if (!rows.length) {
    tEl.innerHTML = '<p class="empty-state">No exit data yet.</p>';
  } else {
    let html = `<table class="data-table">
      <thead><tr>
        <th>Exit Reason</th><th>Trades</th><th>Win%</th>
        <th>Gross P&L</th><th>Avg P&L</th><th>Avg Hold</th>
      </tr></thead><tbody>`;
    rows.forEach(r => {
      html += `<tr>
        <td style="font-size:.78rem">${EXIT_LABELS[r.exit_reason] || r.exit_reason}</td>
        <td>${r.trades}</td>
        <td class="${_winCls(r.win_rate)}">${r.win_rate}%</td>
        <td class="${_pnlCls(r.gross_pnl)}">${_fmt(r.gross_pnl)}</td>
        <td class="${_pnlCls(r.avg_pnl)}">${_fmt(r.avg_pnl)}</td>
        <td style="color:var(--text-muted)">${r.avg_hold_mins != null ? r.avg_hold_mins + 'm' : '—'}</td>
      </tr>`;
    });
    tEl.innerHTML = html + '</tbody></table>';
  }

  _mkChart('exit-hold-chart', {
    type: 'bar',
    data: {
      labels: rows.map(r => EXIT_LABELS[r.exit_reason] || r.exit_reason),
      datasets: [{
        label:           'Avg Hold (min)',
        data:            rows.map(r => r.avg_hold_mins ?? 0),
        backgroundColor: rows.map(r => {
          if (r.exit_reason === 'target_hit') return 'rgba(16,185,129,0.75)';
          if (r.exit_reason === 'stop_hit')   return 'rgba(239,68,68,0.75)';
          return 'rgba(99,102,241,0.65)';
        }),
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.x.toFixed(1)} min` } },
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', font: { size: 10 }, callback: v => v + 'm' },
          grid:  { color: 'rgba(255,255,255,0.07)' },
        },
        y: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { display: false } },
      },
    },
  });
}

// ── Shared dual-axis chart options ────────────────────────────────────────────

function _dualAxisOptions(yLabel, y2Label) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
    },
    scales: {
      x: {
        ticks: { color: '#94a3b8', font: { size: 10 } },
        grid:  { color: 'rgba(255,255,255,0.05)' },
      },
      y: {
        type:     'linear',
        position: 'left',
        title:    { display: true, text: yLabel, color: '#94a3b8', font: { size: 10 } },
        ticks:    { color: '#94a3b8', font: { size: 10 }, callback: v => v + '%' },
        grid:     { color: 'rgba(255,255,255,0.07)' },
        min: 0, max: 100,
      },
      y2: {
        type:     'linear',
        position: 'right',
        title:    { display: true, text: y2Label, color: '#94a3b8', font: { size: 10 } },
        ticks:    { color: '#94a3b8', font: { size: 10 }, callback: v => '$' + v },
        grid:     { drawOnChartArea: false },
      },
    },
  };
}

// ── Clean Data Monitor ────────────────────────────────────────────────────────

async function loadCleanData() {
  const res = await fetch('/api/diagnostics/clean-data');
  if (!res.ok) return;
  const d = await res.json();
  const el = document.getElementById('clean-data-content');
  if (!el) return;

  const bd   = d.strategy_breakdown || {};
  const pre  = bd.pre_4b_all   || {};
  const prev = bd.pre_4b_valid || {};
  const post = bd.post_4b      || {};
  const rej  = d.rejection_summary || {};
  const days = d.daily_report  || [];
  const MIN  = d.post_4b_min_trades || 30;

  // ── Sample-size warning ───────────────────────────────────────────────────
  let warningHtml = '';
  if (post.small_sample_warning) {
    warningHtml = `<div class="alert alert-warning" style="font-size:.78rem;margin-bottom:.75rem">
      ${icon('warn')}<strong>Sample size too small for strategy conclusions.</strong>
      ${post.small_sample_msg}
    </div>`;
  } else if ((post.trades || 0) > 0) {
    warningHtml = `<div class="alert" style="border-color:#10b981;background:#10b98118;
        color:#10b981;font-size:.78rem;margin-bottom:.75rem">
      ${icon('check')}${post.trades} post-Phase-4B valid-thesis trades — sufficient for analysis.
    </div>`;
  }

  // ── Strategy cohort comparison ────────────────────────────────────────────
  function cohortRow(label, s, highlight) {
    if (!s || !s.trades) return `<tr><td colspan="8" style="color:var(--text-muted)">${label} — no data</td></tr>`;
    const style = highlight ? 'background:rgba(16,185,129,.07);font-weight:600' : '';
    return `<tr style="${style}">
      <td>${label}</td>
      <td>${s.trades}</td>
      <td class="${_winCls(s.win_rate || 0)}">${(s.win_rate || 0).toFixed(1)}%</td>
      <td class="${_pnlCls(s.gross_pnl || 0)}">${_fmt(s.gross_pnl)}</td>
      <td class="${_pnlCls(s.intraday_pnl || 0)}">${_fmt(s.intraday_pnl)}</td>
      <td class="${_pnlCls(s.expectancy || 0)}">${_fmt(s.expectancy)}</td>
      <td>${_pfLabel(s.profit_factor)}</td>
      <td style="color:var(--text-muted);font-size:.75rem">
        TP:${s.tp_hits||0} / Stop:${s.stop_hits||0} / Rev:${s.bearish_reversals||0}
      </td>
    </tr>`;
  }

  const cohortHtml = `
    <h4 style="font-size:.8rem;color:var(--text-muted);margin:.5rem 0 .35rem">Strategy Cohorts</h4>
    <table class="data-table">
      <thead><tr>
        <th>Cohort</th><th>Trades</th><th>Win%</th>
        <th>Gross P&amp;L</th><th>Intra P&amp;L</th><th>Expectancy</th>
        <th>P.Factor</th><th>Exits</th>
      </tr></thead>
      <tbody>
        ${cohortRow('Pre-4B (all 86)', pre, false)}
        ${cohortRow('Pre-4B valid-thesis', prev, false)}
        ${cohortRow('Post-4B ✦', post, true)}
      </tbody>
    </table>
    <p style="font-size:.7rem;color:var(--text-muted);margin:.3rem 0 0">
      ✦ post-4B = entries placed after Phase 4B blocker was deployed (stop + TP required).
      Need ${MIN} trades for conclusions.
    </p>`;

  // ── Rejection summary ─────────────────────────────────────────────────────
  let rejHtml = `<h4 style="font-size:.8rem;color:var(--text-muted);margin:.9rem 0 .35rem">
    Thesis Rejections (live session — resets on server restart)
  </h4>`;

  if ((rej.total_rejections || 0) === 0) {
    rejHtml += `<p style="color:var(--text-muted);font-size:.8rem">No rejections this session.</p>`;
  } else {
    rejHtml += `<p style="font-size:.82rem">Total this session: <strong>${rej.total_rejections}</strong></p>`;

    if ((rej.top_symbols || []).length) {
      rejHtml += `<div class="two-col-grid" style="gap:.5rem;margin-top:.4rem">
        <div>
          <p style="font-size:.73rem;color:var(--text-muted);margin:0 0 .25rem">Top rejected symbols</p>
          <table class="data-table" style="font-size:.75rem">
            <thead><tr><th>Symbol</th><th>Rejections</th></tr></thead><tbody>`;
      rej.top_symbols.forEach(r =>
        rejHtml += `<tr><td>${r.symbol}</td><td class="red">${r.rejections}</td></tr>`
      );
      rejHtml += `</tbody></table></div><div>
          <p style="font-size:.73rem;color:var(--text-muted);margin:0 0 .25rem">By score bucket</p>
          <table class="data-table" style="font-size:.75rem">
            <thead><tr><th>Bucket</th><th>Rejections</th></tr></thead><tbody>`;
      (rej.top_buckets || []).forEach(r =>
        rejHtml += `<tr><td>${r.bucket}</td><td class="red">${r.rejections}</td></tr>`
      );
      rejHtml += `</tbody></table></div></div>`;
    }

    if ((rej.recent_log || []).length) {
      rejHtml += `<details style="margin-top:.5rem">
        <summary style="font-size:.75rem;color:var(--text-muted);cursor:pointer">
          Recent rejection log (last ${rej.recent_log.length})
        </summary>
        <table class="data-table" style="font-size:.73rem;margin-top:.3rem">
          <thead><tr><th>Time</th><th>Symbol</th><th>Score</th><th>Bucket</th><th>Reason</th></tr></thead>
          <tbody>`;
      [...rej.recent_log].reverse().forEach(r => {
        const t = r.ts ? new Date(r.ts).toLocaleTimeString() : '—';
        rejHtml += `<tr>
          <td style="color:var(--text-muted)">${t}</td>
          <td>${r.symbol}</td>
          <td>${r.score}</td>
          <td style="color:var(--text-muted)">${r.bucket || '—'}</td>
          <td style="color:var(--text-muted)">${r.reason}</td>
        </tr>`;
      });
      rejHtml += `</tbody></table></details>`;
    }
  }

  // ── Daily report ──────────────────────────────────────────────────────────
  let dailyHtml = `<h4 style="font-size:.8rem;color:var(--text-muted);margin:.9rem 0 .35rem">
    Daily Clean-Data Report
  </h4>`;

  if (!days.length) {
    dailyHtml += `<p style="color:var(--text-muted);font-size:.8rem">No session data yet.</p>`;
  } else {
    dailyHtml += `<table class="data-table" style="font-size:.75rem">
      <thead><tr>
        <th>Date</th><th>Version</th><th>Entries</th>
        <th>Win%</th><th>Gross P&amp;L</th><th>Expectancy</th>
        <th>Valid%</th><th>TP</th><th>Stop</th><th>Rev</th><th>Other</th>
      </tr></thead><tbody>`;
    days.slice(0, 20).forEach(r => {
      const isPost = r.strategy_version === '4b';
      const rowStyle = isPost ? 'background:rgba(16,185,129,.05)' : '';
      const ver = isPost
        ? '<span style="color:#10b981;font-size:.7rem;font-weight:600">4b ✦</span>'
        : '<span style="color:var(--text-muted);font-size:.7rem">pre-4b</span>';
      const vPct = r.valid_thesis_rate ?? 0;
      const vColor = vPct >= 80 ? '#10b981' : (vPct >= 40 ? '#f59e0b' : '#ef4444');
      dailyHtml += `<tr style="${rowStyle}">
        <td>${r.session_date}</td>
        <td>${ver}</td>
        <td>${r.entries}</td>
        <td class="${_winCls(r.win_rate || 0)}">${(r.win_rate||0).toFixed(1)}%</td>
        <td class="${_pnlCls(r.gross_pnl || 0)}">${_fmt(r.gross_pnl)}</td>
        <td class="${_pnlCls(r.expectancy || 0)}">${_fmt(r.expectancy)}</td>
        <td style="color:${vColor}">${vPct.toFixed(0)}%</td>
        <td style="color:#10b981">${r.tp_hits||0}</td>
        <td style="color:#ef4444">${r.stop_hits||0}</td>
        <td style="color:#f59e0b">${r.bearish_reversals||0}</td>
        <td style="color:var(--text-muted)">${r.other_exits||0}</td>
      </tr>`;
    });
    dailyHtml += `</tbody></table>`;
    if (days.length > 20) {
      dailyHtml += `<p style="font-size:.7rem;color:var(--text-muted);margin:.25rem 0 0">
        Showing 20 most recent sessions of ${days.length} total.
      </p>`;
    }
  }

  el.innerHTML = warningHtml + cohortHtml + rejHtml + dailyHtml;
}

// ── Thesis coverage ───────────────────────────────────────────────────────────

const THESIS_COLORS = {
  valid:           '#10b981',  // green
  partial:         '#f59e0b',  // amber
  missing_thesis:  '#ef4444',  // red
};
const THESIS_LABELS = {
  valid:           'Valid (stop + TP)',
  partial:         'Partial (stop OR TP)',
  missing_thesis:  'Missing thesis',
};

async function loadThesisCoverage() {
  const res = await fetch('/api/diagnostics/thesis-coverage');
  if (!res.ok) return;
  const data = await res.json();

  const el = document.getElementById('thesis-coverage-table');
  if (!el) return;

  const overall = data.overall || [];
  const total   = overall.reduce((s, r) => s + r.trades, 0);

  if (!total) {
    el.innerHTML = '<p class="empty-state">No trade data.</p>';
    return;
  }

  // Summary row: coloured coverage bar
  let barHtml = '<div style="display:flex;height:18px;border-radius:4px;overflow:hidden;margin-bottom:.75rem">';
  overall.forEach(r => {
    const pct = (r.trades / total * 100).toFixed(1);
    barHtml += `<div title="${THESIS_LABELS[r.quality]}: ${r.trades} trades (${pct}%)"
      style="width:${pct}%;background:${THESIS_COLORS[r.quality] || '#6b7280'};
             display:flex;align-items:center;justify-content:center;
             font-size:.65rem;color:#fff;font-weight:700;white-space:nowrap;overflow:hidden">
      ${pct > 8 ? pct + '%' : ''}
    </div>`;
  });
  barHtml += '</div>';

  // Overall summary table
  let html = barHtml + `<table class="data-table">
    <thead><tr>
      <th>Quality</th><th>Trades</th><th>Share</th>
      <th>Win%</th><th>Gross P&amp;L</th>
    </tr></thead><tbody>`;

  overall.forEach(r => {
    const pct   = (r.trades / total * 100).toFixed(1);
    const color = THESIS_COLORS[r.quality] || '#6b7280';
    html += `<tr>
      <td><span style="color:${color};font-weight:600">${THESIS_LABELS[r.quality] || r.quality}</span></td>
      <td>${r.trades}</td>
      <td style="color:var(--text-muted)">${pct}%</td>
      <td class="${_winCls(r.win_rate)}">${r.win_rate}%</td>
      <td class="${_pnlCls(r.gross_pnl)}">${_fmt(r.gross_pnl)}</td>
    </tr>`;
  });

  html += '</tbody></table>';

  // Phase 4B status banner
  const validRow    = overall.find(r => r.quality === 'valid') || { trades: 0 };
  const validPct    = total ? (validRow.trades / total * 100).toFixed(0) : 0;
  const statusColor = validPct >= 80 ? '#10b981' : (validPct >= 40 ? '#f59e0b' : '#ef4444');
  const statusMsg   = validPct >= 80
    ? `${icon('check')}${validPct}% of trades have full stop + TP — Phase 4B gate is healthy.`
    : `${icon('warn')}Only ${validPct}% of trades have valid stop + TP — Phase 4B blocker is active for new entries.`;

  html = `<div class="alert" style="border-color:${statusColor};background:${statusColor}18;
            color:${statusColor};font-size:.78rem;margin-bottom:.5rem">${statusMsg}</div>` + html;

  el.innerHTML = html;
}

// ── Exit experiments ──────────────────────────────────────────────────────────

async function loadExitExperiments() {
  const res = await fetch('/api/diagnostics/exit-experiments');
  if (!res.ok) return;
  const results = await res.json();

  const tEl = document.getElementById('experiments-table');
  if (!results.length) {
    tEl.innerHTML = '<p class="empty-state">No experiment data.</p>';
    return;
  }

  const baseline = results.find(r => r.variant_id === 'baseline') || {};

  let html = `<table class="data-table">
    <thead><tr>
      <th>#</th><th>Variant</th><th>Δ Trades</th>
      <th>Win%</th><th>Intra P&L</th><th>vs Baseline</th>
      <th>All P&L</th><th>P.Factor</th><th>Max DD</th>
      <th>TGT</th><th>Avg Hold</th><th>Worst Exit</th>
    </tr></thead><tbody>`;

  results.forEach(r => {
    const isBase  = r.variant_id === 'baseline';
    const isWinner = r.rank === 1;
    const iDiff   = r.intraday_pnl - (baseline.intraday_pnl || 0);
    const diffStr = isBase ? '—'
      : (iDiff >= 0 ? `<span class="green">+$${iDiff.toFixed(0)}</span>`
                    : `<span class="red">-$${Math.abs(iDiff).toFixed(0)}</span>`);

    const rowStyle = isWinner ? 'background:rgba(16,185,129,.06);font-weight:600'
                   : (isBase  ? 'background:rgba(99,102,241,.05)' : '');
    const rankBadge = isWinner
      ? `<span style="color:#10b981;font-size:.7rem">▶ BEST</span>`
      : r.rank;

    html += `<tr style="${rowStyle}">
      <td>${rankBadge}</td>
      <td style="font-size:.77rem;max-width:220px">${r.variant_name}</td>
      <td style="text-align:center">${r.affected_trades}</td>
      <td class="${_winCls(r.win_rate)}">${r.win_rate}%</td>
      <td class="${_pnlCls(r.intraday_pnl)}">${_fmt(r.intraday_pnl)}</td>
      <td>${diffStr}</td>
      <td class="${_pnlCls(r.gross_pnl)}">${_fmt(r.gross_pnl)}</td>
      <td>${_pfLabel(r.profit_factor)}</td>
      <td class="red">$${r.max_drawdown?.toFixed(0) ?? '—'}</td>
      <td>${r.target_hit_count}</td>
      <td style="color:var(--text-muted)">${r.avg_hold_mins != null ? r.avg_hold_mins + 'm' : '—'}</td>
      <td style="font-size:.72rem;color:var(--text-muted)">${EXIT_LABELS[r.worst_exit] || r.worst_exit}</td>
    </tr>`;
  });

  tEl.innerHTML = html + '</tbody></table>';

  // Intraday P&L bar chart
  const sorted = [...results].sort((a, b) => b.intraday_pnl - a.intraday_pnl);
  _mkChart('experiments-chart', {
    type: 'bar',
    data: {
      labels: sorted.map(r => r.variant_name),
      datasets: [{
        label: 'Intraday P&L ($)',
        data: sorted.map(r => r.intraday_pnl),
        backgroundColor: sorted.map(r =>
          r.variant_id === 'baseline' ? 'rgba(99,102,241,0.6)'
          : r.intraday_pnl > (baseline.intraday_pnl || 0) ? 'rgba(16,185,129,0.75)'
          : 'rgba(239,68,68,0.6)'
        ),
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` $${ctx.parsed.x.toFixed(2)}` } },
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', font: { size: 10 }, callback: v => '$' + v },
          grid:  { color: 'rgba(255,255,255,0.07)' },
        },
        y: { ticks: { color: '#94a3b8', font: { size: 9 } }, grid: { display: false } },
      },
    },
  });
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

async function loadAll() {
  await Promise.all([
    loadCleanData(),
    loadEquityCurve(),
    loadScoreBuckets(),
    loadTimeBuckets(),
    loadComponentAccuracy(),
    loadThesisCoverage(),
    loadExitExperiments(),
    loadExitAnalysis(),
  ]);
}

loadAll();
