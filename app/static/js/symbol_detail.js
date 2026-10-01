/**
 * symbol_detail.js — Symbol drill-down page
 *
 * Chart engine: TradingView Lightweight Charts (via charts.js)
 * Overlays:     VWAP (yellow dashed), Stop (red dashed), Target (green dashed),
 *               Opening-range H/L (blue dotted), BUY/SELL arrow marker
 * Live updates: SSE price_update + 10-second polling
 */

let _symbol       = '';
let _currentTf    = '1m';
let _chartObj     = null;   // { chart, candleSeries, volSeries, vwapSeries, ... }
let _chartExpanded = false;
let _lastBars     = [];     // most-recent bar array for marker placement
let _lastSignal   = null;   // most-recent signal for re-drawing overlays

// ── Init ───────────────────────────────────────────────────────────────────────
function initSymbolDetail(symbol) {
  _symbol = symbol;
  loadSymbolData();
  loadWhaleData();
  loadPatternHistory();
  setInterval(loadSymbolData,     10000);   // refresh every 10 s
  setInterval(loadWhaleData,     300000);   // refresh whale data every 5 min
  setInterval(loadPatternHistory, 60000);   // refresh history every 60 s
}

// ── Data load ──────────────────────────────────────────────────────────────────
async function loadSymbolData() {
  try {
    const r = await fetch(`/api/symbol/${_symbol}`);
    if (!r.ok) return;
    const d = await r.json();
    renderMarketData(d.market_data);
    renderSignal(d.signal);
    renderNews(d.news);
  } catch(e) {
    console.error('Symbol load error', e);
  }

  try {
    const r = await fetch('/api/paper/positions');
    const positions = await r.json();
    renderPosition(positions.find(p => p.symbol === _symbol));
  } catch(e) {}
}

// ── Market data & chart ────────────────────────────────────────────────────────
function renderMarketData(md) {
  if (!md) return;

  // Price header
  const price = md.last_price;
  if (price) setVal('sd-price', money(price));

  // Key levels
  setVal('sd-vwap', md.vwap          ? money(md.vwap)                 : '--');
  setVal('sd-orh',  md.opening_range_high ? money(md.opening_range_high) : '--');
  setVal('sd-orl',  md.opening_range_low  ? money(md.opening_range_low)  : '--');
  setVal('sd-sh',   md.session_high  ? money(md.session_high)          : '--');
  setVal('sd-sl',   md.session_low   ? money(md.session_low)           : '--');

  // Pick bars for current timeframe
  const bars = _currentTf === '1m' ? md.bars_1m :
               _currentTf === '5m' ? md.bars_5m  : md.bars_15m;
  if (!bars?.length) {
    // No bars yet for this timeframe — show a hint and retry shortly
    const container = document.getElementById('price-chart-container');
    if (container && !container.__lwChart) {
      container.innerHTML = '<p style="color:var(--text-dim);font-size:.75rem;padding:1rem;text-align:center">Waiting for bar data…</p>';
    }
    return;
  }

  _lastBars = bars;

  const levels = {
    orh:  md.opening_range_high,
    orl:  md.opening_range_low,
    vwap: md.vwap,
  };

  const container = document.getElementById('price-chart-container');
  if (!container) return;

  if (!_chartObj) {
    // First build
    _chartObj = buildLightweightChart('price-chart-container', bars, levels);
    // Expose resize hook for the panel drag engine
    window._lwcResizeChart = () => {
      const c = document.getElementById('price-chart-container');
      if (c && c.__lwChart) c.__lwChart.resize(c.clientWidth, c.clientHeight);
    };
  } else {
    // Live update — just replace series data, no full rebuild
    updateLightweightChart(_chartObj, bars);
  }

  // Re-apply overlays in case signal already loaded
  if (_lastSignal && _chartObj) {
    addThesisOverlays(_chartObj, _lastSignal.thesis);
    addSignalMarker(_chartObj, _lastBars, _lastSignal);
  }
}

// ── Signal rendering ───────────────────────────────────────────────────────────
function renderSignal(sig) {
  if (!sig?.symbol) return;
  _lastSignal = sig;

  // Header badges
  const colorBadge = document.getElementById('sd-color-badge');
  const labelBadge = document.getElementById('sd-label-badge');
  const LABEL_CLS  = { NO_TRADE:'no-trade', WATCH:'watch', POSSIBLE_TRADE:'possible', IMMEDIATE_TRADE:'immediate' };

  if (colorBadge) {
    colorBadge.textContent = sig.color?.replace('_', ' ');
    colorBadge.className   = `signal-badge ${sig.color}`;
  }
  if (labelBadge) {
    labelBadge.textContent = sig.label?.replace('_', ' ');
    labelBadge.className   = `label-badge ${LABEL_CLS[sig.label] || 'no-trade'}`;
  }

  // Score ring
  setScoreRing(sig.total_score || 0);

  // Component breakdown bars
  renderComponentBars(sig.components || []);

  // Detected candlestick patterns
  const candleComp = (sig.components || []).find(c => c.name === 'candlestick');
  renderPatterns(candleComp?.details?.patterns || []);

  // Trade thesis block
  renderThesis(sig.thesis);

  // Chart overlays — stop/target lines + buy/sell arrow
  if (_chartObj && _lastBars.length) {
    addThesisOverlays(_chartObj, sig.thesis);
    addSignalMarker(_chartObj, _lastBars, sig);
  }
}

// ── Component breakdown ────────────────────────────────────────────────────────
function renderComponentBars(components) {
  const el = document.getElementById('component-bars');
  if (!el) return;
  const maxWeights = { technical_trend:25, candlestick:20, volume:15, vwap:15, market_regime:10, news:15 };
  el.innerHTML = components.map(c => {
    const max = maxWeights[c.name] || c.weight || 1;
    const pct = Math.round((c.weighted_score / max) * 100);
    return `<div class="comp-row">
      <span class="comp-name">${c.name.replace(/_/g, ' ')}</span>
      <div class="comp-bar-wrap">
        <div class="comp-bar ${c.direction === -1 ? 'negative' : ''}" style="width:${pct}%"></div>
      </div>
      <span class="comp-score">${c.weighted_score?.toFixed(1)}/${max}</span>
    </div>`;
  }).join('');
}

// ── Pattern tags ───────────────────────────────────────────────────────────────
function renderPatterns(patterns) {
  const el = document.getElementById('patterns-list');
  if (!el) return;
  if (!patterns.length) { el.innerHTML = '<p class="empty-state">No patterns detected.</p>'; return; }
  el.innerHTML = patterns.map(p =>
    `<span class="pattern-tag ${p.direction === 1 ? 'bull' : 'bear'}" title="${p.description || ''}">
      ${p.name.replace(/_/g, ' ')}
      <span style="opacity:.6">${(p.strength * 100).toFixed(0)}%</span>
    </span>`
  ).join('');
}

// ── Pattern history ────────────────────────────────────────────────────────────
async function loadPatternHistory() {
  try {
    const r = await fetch(`/api/symbol/${_symbol}/pattern-history?limit=100`);
    if (!r.ok) return;
    const d = await r.json();
    renderPatternHistory(d.history || []);
  } catch (e) {
    // silently ignore — history is non-critical
  }
}

function renderPatternHistory(entries) {
  const el = document.getElementById('pattern-history');
  if (!el) return;

  if (!entries.length) {
    el.innerHTML = '<p class="ph-empty">No history yet — patterns detected during market hours appear here.</p>';
    return;
  }

  // Group by session date
  const byDate = {};
  entries.forEach(e => {
    const d = e.session_date || _dateStr(e.detected_at);
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(e);
  });

  const html = Object.entries(byDate).map(([date, rows]) => {
    const rowsHtml = rows.map(e => {
      const dir      = e.direction === 1 ? 'bull' : 'bear';
      const arrow    = e.direction === 1 ? '▲' : '▼';
      const name     = e.pattern_name.replace(/_/g, ' ');
      const pct      = Math.round((e.strength || 0) * 100);
      const price    = e.price   != null ? `<span class="ph-price">${money(e.price)}</span>` : '';
      const score    = e.signal_score != null ? `<span class="ph-score">${Number(e.signal_score).toFixed(1)}</span>` : '';
      const label    = e.signal_label ? `<span class="ph-label ${_labelCls(e.signal_label)}">${e.signal_label.replace(/_/g,' ').toLowerCase()}</span>` : '';
      const timeStr  = _timeStr(e.detected_at);
      const fullTime = _fullTimeStr(e.detected_at);
      return `<div class="ph-row" title="${e.description || name}">
        <div class="ph-left">
          <span class="ph-badge ${dir}">${arrow} ${name}</span>
          <span class="ph-strength">${pct}%</span>
        </div>
        <div class="ph-right">
          ${price}${score}${label}
          <span class="ph-time" title="${fullTime}">${timeStr}</span>
        </div>
      </div>`;
    }).join('');

    const label = _dateLabel(date);
    return `<div class="ph-day-group">
      <div class="ph-day-header">${label}</div>
      ${rowsHtml}
    </div>`;
  }).join('');

  el.innerHTML = html;
}

function _labelCls(label) {
  if (!label) return '';
  if (label.includes('IMMEDIATE')) return 'immediate';
  if (label.includes('POSSIBLE'))  return 'possible';
  if (label.includes('WATCH'))     return 'watch';
  return '';
}

function _timeStr(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

function _fullTimeStr(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
           d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

function _dateStr(iso) {
  try { return iso.slice(0, 10); } catch { return ''; }
}

function _dateLabel(dateStr) {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const yest  = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (dateStr === today) return 'Today';
    if (dateStr === yest)  return 'Yesterday';
    const d = new Date(dateStr + 'T12:00:00');
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  } catch { return dateStr; }
}

// ── Trade thesis ───────────────────────────────────────────────────────────────
function renderThesis(thesis) {
  const el = document.getElementById('thesis-content');
  if (!el) return;
  if (!thesis) { el.innerHTML = '<p class="empty-state">No actionable thesis yet.</p>'; return; }

  const dirClass      = thesis.direction === 'long' ? 'green' : 'red';
  const techEvidence  = (thesis.technical_evidence   || []).map(e => `<li>${e}</li>`).join('');
  const candleEvidence= (thesis.candlestick_evidence || []).map(e => `<li>${e}</li>`).join('');
  const newsEvidence  = (thesis.news_evidence         || []).map(e => `<li>${e}</li>`).join('');

  el.innerHTML = `
    <div class="thesis-block">
      <div class="thesis-direction ${dirClass}">
        ${thesis.direction?.toUpperCase()} — ${thesis.confidence?.toFixed(0)}/100
      </div>
      <div class="thesis-row"><span class="thesis-lbl">Why now</span><span class="thesis-val">${thesis.why_now || '--'}</span></div>
      ${techEvidence   ? `<div class="thesis-row"><span class="thesis-lbl">Technical</span><ul class="thesis-evidence">${techEvidence}</ul></div>` : ''}
      ${candleEvidence ? `<div class="thesis-row"><span class="thesis-lbl">Candles</span><ul class="thesis-evidence">${candleEvidence}</ul></div>` : ''}
      ${newsEvidence   ? `<div class="thesis-row"><span class="thesis-lbl">News</span><ul class="thesis-evidence">${newsEvidence}</ul></div>` : ''}
      <div class="thesis-row"><span class="thesis-lbl">Invalidation</span><span class="thesis-val">${thesis.invalidation || '--'}</span></div>
      ${thesis.risk_note ? `<div class="risk-block">${icon('warn')}${thesis.risk_note}</div>` : ''}
      ${(thesis.suggested_stop || thesis.suggested_target) ? `
        <div class="stop-target-row">
          ${thesis.suggested_stop   ? `<span class="stop-pill">✕ Stop: ${money(thesis.suggested_stop)}</span>`     : ''}
          ${thesis.suggested_target ? `<span class="target-pill">✓ Target: ${money(thesis.suggested_target)}</span>` : ''}
          ${thesis.risk_reward      ? `<span style="font-size:.75rem;color:var(--text-muted)">R:R ${thesis.risk_reward.toFixed(1)}:1</span>` : ''}
        </div>` : ''}
    </div>`;

  // Pre-fill quick-order form
  if (thesis.suggested_stop)   document.getElementById('qo-stop').value   = thesis.suggested_stop.toFixed(2);
  if (thesis.suggested_target) document.getElementById('qo-target').value = thesis.suggested_target.toFixed(2);
  if (thesis.direction)        document.getElementById('qo-side').value   = thesis.direction === 'long' ? 'buy' : 'sell';
}

// ── Whale Tracker ──────────────────────────────────────────────────────────────
async function loadWhaleData() {
  try {
    const r = await fetch(`/api/whales/${_symbol}`);
    if (!r.ok) return;
    const d = await r.json();
    renderWhaleData(d);
  } catch(e) {
    console.warn('Whale data load error', e);
  }
}

async function refreshWhaleData() {
  const content = document.getElementById('whale-content');
  if (content) content.innerHTML = '<p class="empty-state">Refreshing…</p>';
  try {
    const r = await fetch(`/api/whales/${_symbol}/refresh`, { method: 'POST' });
    if (!r.ok) return;
    const d = await r.json();
    renderWhaleData(d);
  } catch(e) {
    console.warn('Whale refresh error', e);
  }
}

function renderWhaleData(d) {
  // Update badge in panel header
  const badge = document.getElementById('whale-sentiment-badge');
  if (badge) {
    const label = d.sentiment_label || 'NEUTRAL';
    badge.textContent  = label;
    badge.className    = 'whale-badge whale-' + label.replace(/\s+/g, '-').toLowerCase();
  }

  const content = document.getElementById('whale-content');
  if (!content) return;

  const conf    = Math.round((d.confidence || 0) * 100);
  const sentVal = (d.combined_sentiment || 0);
  const sentPct = Math.round(((sentVal + 1) / 2) * 100);  // -1..+1 → 0..100%

  // ── Options flow section ─────────────────────────────────────────────────
  let optHtml = '';
  const opts = d.options;
  if (opts && !opts.error) {
    const pcr     = opts.put_call_ratio?.toFixed(2) ?? '—';
    const pcrClass= opts.put_call_ratio < 0.7 ? 'green' : (opts.put_call_ratio > 1.3 ? 'red' : 'muted');

    let unusualCallRows = '';
    (opts.unusual_calls || []).slice(0, 3).forEach(c => {
      unusualCallRows += `<tr>
        <td>CALL</td><td>$${c.strike}</td>
        <td>${c.expiry}</td>
        <td class="green">${c.volume.toLocaleString()}</td>
        <td>${c.oi.toLocaleString()}</td>
      </tr>`;
    });
    let unusualPutRows = '';
    (opts.unusual_puts || []).slice(0, 3).forEach(p => {
      unusualPutRows += `<tr>
        <td>PUT</td><td>$${p.strike}</td>
        <td>${p.expiry}</td>
        <td class="red">${p.volume.toLocaleString()}</td>
        <td>${p.oi.toLocaleString()}</td>
      </tr>`;
    });
    const unusualRows = unusualCallRows + unusualPutRows;

    optHtml = `
      <div class="whale-section">
        <div class="whale-section-title">${icon('zap')}Live Options Flow</div>
        <div class="whale-stats-row">
          <div class="whale-stat">
            <span class="whale-stat-label">Put/Call Ratio</span>
            <span class="whale-stat-value ${pcrClass}">${pcr}</span>
          </div>
          <div class="whale-stat">
            <span class="whale-stat-label">Call OI</span>
            <span class="whale-stat-value green">${(opts.call_oi||0).toLocaleString()}</span>
          </div>
          <div class="whale-stat">
            <span class="whale-stat-label">Put OI</span>
            <span class="whale-stat-value red">${(opts.put_oi||0).toLocaleString()}</span>
          </div>
          <div class="whale-stat">
            <span class="whale-stat-label">Call Vol</span>
            <span class="whale-stat-value green">${(opts.call_volume||0).toLocaleString()}</span>
          </div>
          <div class="whale-stat">
            <span class="whale-stat-label">Put Vol</span>
            <span class="whale-stat-value red">${(opts.put_volume||0).toLocaleString()}</span>
          </div>
        </div>
        <p class="whale-summary-text">${opts.summary || ''}</p>
        ${unusualRows ? `
        <div class="whale-section-title" style="margin-top:8px">Unusual Activity</div>
        <table class="whale-table">
          <thead><tr><th>Type</th><th>Strike</th><th>Expiry</th><th>Vol</th><th>OI</th></tr></thead>
          <tbody>${unusualRows}</tbody>
        </table>` : ''}
      </div>`;
  } else if (opts?.error) {
    optHtml = `<div class="whale-section"><p class="muted" style="font-size:0.75rem">Options data unavailable: ${opts.error}</p></div>`;
  }

  // ── Institutional 13F section ───────────────────────────────────────────
  let instHtml = '';
  const holders = d.institutional_holders || [];
  if (holders.length > 0) {
    const rows = holders.slice(0, 8).map(h => {
      const cls = h.sentiment >= 0 ? 'green' : 'red';
      const pos = h.position || 'LONG';
      return `<tr>
        <td class="whale-fund-name">${h.fund_name}</td>
        <td class="${cls}">${pos}</td>
        <td>$${h.value_m?.toFixed(1) ?? '—'}M</td>
        <td>${(h.shares||0).toLocaleString()}</td>
      </tr>`;
    }).join('');
    instHtml = `
      <div class="whale-section">
        <div class="whale-section-title">${icon('bank')}Institutional 13F Holdings</div>
        <table class="whale-table">
          <thead><tr><th>Fund</th><th>Position</th><th>Value</th><th>Shares</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } else {
    instHtml = `<div class="whale-section"><p class="empty-state" style="font-size:0.75rem">No 13F holdings found yet — data refreshes every 6 hours.</p></div>`;
  }

  // ── Sentiment bar ────────────────────────────────────────────────────────
  const sentClass = sentVal >= 0.15 ? 'green' : (sentVal <= -0.15 ? 'red' : 'muted');
  const sentBar   = `
    <div class="whale-sentiment-bar-wrap">
      <div class="whale-sentiment-bar">
        <div class="whale-sentiment-fill" style="left:50%;width:${Math.abs(sentPct - 50)}%;
             background:${sentVal >= 0 ? 'var(--green)' : 'var(--red)'};
             ${sentVal < 0 ? 'transform:translateX(-100%)' : ''}"></div>
        <div class="whale-sentiment-center"></div>
      </div>
      <div class="whale-sentiment-labels">
        <span class="red" style="font-size:0.7rem">Bearish</span>
        <span class="${sentClass}" style="font-size:0.7rem;font-weight:600">${d.sentiment_label} (${conf}% confidence)</span>
        <span class="green" style="font-size:0.7rem">Bullish</span>
      </div>
    </div>`;

  content.innerHTML = sentBar + optHtml + instHtml;
}

// ── News ───────────────────────────────────────────────────────────────────────
function renderNews(news) {
  const el = document.getElementById('news-content');
  if (!el) return;
  if (!news?.items?.length) {
    el.innerHTML = '<p class="empty-state">No recent news found.</p>';
    return;
  }
  el.innerHTML = news.items.slice(0, 15).map(item => {
    const pub   = new Date(item.published_at).toLocaleString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:true});
    const align = item.alignment || 'neutral';
    const url   = item.url || `https://finance.yahoo.com/quote/${_symbol}/news/`;
    return `<div class="news-item">
      <a class="news-headline" href="${url}" target="_blank" rel="noopener noreferrer">${item.headline}</a>
      <div class="news-meta">
        <span class="news-source">${item.source || 'News'}</span>
        <span class="news-time">${pub}</span>
        <span class="news-alignment ${align}">${align.replace('_',' ')}</span>
      </div>
    </div>`;
  }).join('');
}

async function refreshNews() {
  const el = document.getElementById('news-content');
  if (el) el.innerHTML = '<p class="empty-state">Refreshing…</p>';
  try {
    const r = await fetch(`/api/news/${_symbol}/refresh`, { method: 'POST' });
    if (r.ok) {
      const d = await r.json();
      renderNews(d);
    }
  } catch(e) {}
}

// ── Open position ──────────────────────────────────────────────────────────────
function renderPosition(pos) {
  const el = document.getElementById('symbol-positions');
  if (!el) return;
  if (!pos) { el.innerHTML = '<p class="empty-state">No open position.</p>'; return; }
  const pnlClass = (pos.unrealized_pnl || 0) >= 0 ? 'green' : 'red';
  el.innerHTML = `<div class="alert-item">
    <strong>${pos.symbol}</strong>
    <span class="${pos.side === 'long' ? 'green' : 'red'}">${pos.side?.toUpperCase()}</span>
    ${pos.qty} @ ${money(pos.avg_entry_price)}
    <span class="${pnlClass}"> Unr. P&L: ${money((pos.unrealized_pnl || 0))}</span>
    <button onclick="closeSymbolPosition()" class="btn btn-xs btn-red ml-2">Close</button>
  </div>`;
}

// ── Timeframe switch ───────────────────────────────────────────────────────────
function switchTimeframe(tf) {
  _currentTf = tf;

  // Update active button style
  ['1m','5m','15m'].forEach(t => {
    const btn = document.getElementById(`tf-${t}`);
    if (btn) btn.classList.toggle('active', t === tf);
  });

  // Destroy existing chart — it will rebuild on the next data load
  const container = document.getElementById('price-chart-container');
  if (container?.__lwChart) {
    container.__lwRO?.disconnect();
    container.__lwChart.remove();
    container.__lwChart    = null;
    container.__lwChartObj = null;
  }
  _chartObj  = null;
  _lastBars  = [];

  loadSymbolData();
}

// ── Chart expand / collapse ────────────────────────────────────────────────────
function toggleChartExpand() {
  _chartExpanded = !_chartExpanded;
  const container = document.getElementById('price-chart-container');
  const btn       = document.getElementById('chart-expand-btn');
  if (container) container.classList.toggle('expanded', _chartExpanded);
  if (btn) btn.innerHTML = _chartExpanded ? '&#x2921; Collapse' : '&#x2922;';
  // ResizeObserver in buildLightweightChart handles the resize automatically
}

// ── Quick order ────────────────────────────────────────────────────────────────
async function submitDetailOrder() {
  const side   = document.getElementById('qo-side').value;
  const qty    = parseFloat(document.getElementById('qo-qty').value);
  const stop   = parseFloat(document.getElementById('qo-stop').value)   || null;
  const target = parseFloat(document.getElementById('qo-target').value) || null;

  const statusEl = document.getElementById('order-status');
  statusEl.className   = 'order-status';
  statusEl.textContent = 'Submitting...';

  try {
    const r = await fetch('/api/paper/order', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({ symbol: _symbol, side, qty, order_type: 'market',
                                stop_price: stop, take_profit_price: target }),
    });
    const d = await r.json();
    if (!r.ok) {
      statusEl.className   = 'order-status err';
      statusEl.textContent = d.detail || 'Order failed.';
      return;
    }
    statusEl.className   = 'order-status ok';
    statusEl.textContent = `Filled: ${side.toUpperCase()} ${qty} @ ${money(d.filled_price)}`;
    loadSymbolData();
  } catch(e) {
    statusEl.className   = 'order-status err';
    statusEl.textContent = 'Network error.';
  }
}

async function closeSymbolPosition() {
  if (!confirm(`Close position in ${_symbol}?`)) return;
  const r = await fetch('/api/paper/close', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({ symbol: _symbol, reason: 'manual' }),
  });
  const d = await r.json();
  if (r.ok) loadSymbolData();
  else alert('Error: ' + (d.detail || 'unknown'));
}

// ── SSE live updates ───────────────────────────────────────────────────────────
window.handleSSEMessage = function(msg) {
  // Live price tick — update price display and extend last candle
  if (msg.type === 'price_update' && msg.data.symbol === _symbol) {
    setVal('sd-price', money(msg.data.price));
  }

  // Signal update — refresh badges and chart overlays immediately
  if (msg.type === 'signal_update' && msg.data.symbol === _symbol) {
    const badge = document.getElementById('sd-color-badge');
    if (badge) {
      badge.textContent = msg.data.color.replace('_', ' ');
      badge.className   = `signal-badge ${msg.data.color}`;
    }
    setScoreRing(msg.data.score || 0);
    // Full signal refresh will happen on next 10-second poll
  }
};

// ── Utility ────────────────────────────────────────────────────────────────────
function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
