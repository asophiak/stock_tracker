/**
 * Dashboard — real-time updates via SSE + polling fallback.
 * Handles watchlist card rendering, best-trade banner, alerts, positions,
 * and per-card sparkline mini-charts.
 */

// ── CoverFlow carousel ─────────────────────────────────────────────────────────
(function initCoverFlow() {
  var _center = 0, _cards = [];
  var SLOTS = [-3,-2,-1,0,1,2,3];

  function apply() {
    _cards.forEach(function(c, i) {
      var slot = i - _center;
      c.dataset.slot = SLOTS.indexOf(slot) >= 0 ? String(slot) : 'hidden';
    });
    document.querySelectorAll('.cf-dot').forEach(function(d, i) {
      d.classList.toggle('active', i === _center);
    });
  }

  function buildDots() {
    var el = document.getElementById('cf-dots');
    if (!el) return;
    el.innerHTML = _cards.map(function(c, i) {
      return '<span class="cf-dot' + (i===0?' active':'') + '" onclick="cfFocus(' + i + ')"></span>';
    }).join('');
  }

  function init() {
    _cards = Array.from(document.querySelectorAll('.cf-card'));
    if (!_cards.length) return;
    apply();
    buildDots();

    // Keyboard arrows
    document.addEventListener('keydown', function(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'ArrowLeft')  { cfScroll(-1); e.preventDefault(); }
      if (e.key === 'ArrowRight') { cfScroll(1);  e.preventDefault(); }
    });

    // Touchpad / mouse-wheel horizontal scroll on the carousel
    var _wheelAccum = 0;
    var _wheelTimer = null;
    var wrap = document.querySelector('.coverflow-wrap');
    if (wrap) {
      wrap.addEventListener('wheel', function(e) {
        // Only sideways swipes move the carousel — vertical scrolling always
        // scrolls the page (it used to get trapped here mid-page).
        if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
        var delta = e.deltaX;
        _wheelAccum += delta;
        // Advance one card per ~80px of scroll accumulation, then reset
        if (_wheelAccum > 80)  { cfScroll(1);  _wheelAccum = 0; }
        if (_wheelAccum < -80) { cfScroll(-1); _wheelAccum = 0; }
        // Reset accumulator if the user pauses
        clearTimeout(_wheelTimer);
        _wheelTimer = setTimeout(function() { _wheelAccum = 0; }, 200);
        e.preventDefault();
      }, { passive: false });
    }
  }

  window.cfScroll = function(dir) {
    _center = Math.max(0, Math.min(_cards.length - 1, _center + dir));
    apply();
  };
  window.cfFocus = function(idx) { _center = idx; apply(); };

  window.cfCardClick = function(e, idx, url) {
    // Let button/link clicks inside the card pass through unchanged
    if (e.target.closest('button') || e.target.closest('a')) return;
    var card = _cards[idx];
    if (card && card.dataset.slot === '0') {
      // Card is already centered — navigate to its detail page
      window.location.href = url;
    } else {
      // Bring card to center
      cfFocus(idx);
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // Small delay so cards are in DOM
    setTimeout(init, 50);
  }
})();

const COLOR_CLASS = {
  NEUTRAL:     'neutral',
  GREEN:       'GREEN',
  RED:         'RED',
  FLASH_GREEN: 'FLASH_GREEN',
  FLASH_RED:   'FLASH_RED',
};

const LABEL_CLASS = {
  NO_TRADE:       'no-trade',
  WATCH:          'watch',
  POSSIBLE_TRADE: 'possible',
  IMMEDIATE_TRADE:'immediate',
};

// ── Sparkline state ────────────────────────────────────────────────────────────
const _sparkCharts = {};   // symbol → Chart instance
const _sparkData   = {};   // symbol → float[] (rolling window of close prices)
const SPARK_MAX    = 45;   // number of price points to show

function _sparkColor(symbol, signalColor) {
  if (signalColor === 'GREEN' || signalColor === 'FLASH_GREEN') return '#3fb950';
  if (signalColor === 'RED'   || signalColor === 'FLASH_RED')   return '#f85149';
  // Fallback: green if price trending up, red if down
  const prices = _sparkData[symbol] || [];
  if (prices.length >= 2) {
    return prices[prices.length - 1] > prices[0] ? '#3fb950' : '#f85149';
  }
  return '#58a6ff';
}

function initSparkline(symbol, signalColor) {
  const canvas = document.getElementById(`spark-${symbol}`);
  if (!canvas || _sparkCharts[symbol]) return;
  const prices = _sparkData[symbol] || [];
  const color = _sparkColor(symbol, signalColor);
  _sparkCharts[symbol] = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: prices.map((_, i) => i),
      datasets: [{
        data: prices,
        borderColor: color,
        backgroundColor: color + '18',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
}

function updateSparkline(symbol, price, signalColor) {
  if (!_sparkData[symbol]) _sparkData[symbol] = [];
  _sparkData[symbol].push(price);
  if (_sparkData[symbol].length > SPARK_MAX) _sparkData[symbol].shift();

  if (!_sparkCharts[symbol]) {
    initSparkline(symbol, signalColor);
    return;
  }

  const chart  = _sparkCharts[symbol];
  const prices = _sparkData[symbol];
  const color  = _sparkColor(symbol, signalColor);
  chart.data.labels = prices.map((_, i) => i);
  chart.data.datasets[0].data = prices;
  chart.data.datasets[0].borderColor = color;
  chart.data.datasets[0].backgroundColor = color + '18';
  chart.update('none');
}

async function loadSparklines() {
  // Fetch every card's recent bars in parallel (was sequential — the last
  // cards stayed blank for seconds), and fill charts that already exist.
  const cards = Array.from(document.querySelectorAll('.symbol-card'));
  await Promise.all(cards.map(async card => {
    const sym = card.dataset.symbol;
    try {
      const r = await fetch(`/api/symbol/${sym}`);
      if (r.ok) {
        const d = await r.json();
        const bars = d.market_data?.bars_1m || [];
        if (bars.length) _sparkData[sym] = bars.slice(-SPARK_MAX).map(b => b.c);
      }
    } catch (e) {}
    const chart = _sparkCharts[sym];
    if (chart && _sparkData[sym]) {
      chart.data.labels = _sparkData[sym].map((_, i) => i);
      chart.data.datasets[0].data = _sparkData[sym].slice();
      chart.update('none');
    } else {
      initSparkline(sym);
    }
  }));
}

// ── Bot wallet ─────────────────────────────────────────────────────────────────
async function loadBotWallet() {
  try {
    const r = await fetch('/api/paper/bot-status');
    if (!r.ok) return;
    const d = await r.json();
    renderBotWallet(d);
  } catch(e) {}
}

function renderBotWallet(d) {
  // Auto badge
  const badge = document.getElementById('bw-auto-badge');
  if (badge) {
    badge.textContent = d.auto_trading ? 'AUTO ON' : 'AUTO OFF';
    badge.className = 'bw-auto-badge' + (d.auto_trading ? ' on' : '');
  }

  // Stats
  _bwVal('bw-capital',    `${money(d.starting_capital)}`);

  const cash = d.bot_cash ?? 0;
  _bwVal('bw-cash', `${money(cash)}`);

  const realized = d.realized_pnl ?? 0;
  _bwVal('bw-realized', `${money(realized, {signed: true})}`, realized >= 0 ? 'pos' : 'neg');

  const unreal = d.unrealized_pnl ?? 0;
  _bwVal('bw-unrealized', `${money(unreal, {signed: true})}`, unreal >= 0 ? 'pos' : 'neg');

  const ret = d.total_return_pct ?? 0;
  _bwVal('bw-return', `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%`, ret >= 0 ? 'pos' : 'neg');

  _bwVal('bw-trades', `${d.trades_today ?? 0} / ${d.max_trades_per_day ? d.max_trades_per_day : '∞'}`);

  // Open position chip
  const posEl = document.getElementById('bw-position');
  if (posEl) {
    const pos = d.open_positions?.[0];
    if (pos) {
      const pnl   = pos.unrealized_pnl ?? 0;
      const pnlCl = pnl >= 0 ? 'pos' : 'neg';
      const sideCl = pos.side === 'long' ? 'long-pos' : 'short-pos';
      const stopStr   = pos.stop_price        ? ` | Stop ${money(pos.stop_price)}`        : '';
      const targetStr = pos.take_profit_price ? ` | Target ${money(pos.take_profit_price)}` : '';
      posEl.className = `bw-position ${sideCl}`;
      posEl.innerHTML =
        `<strong>${pos.symbol}</strong>
         <span class="${pos.side === 'long' ? 'green' : 'red'}">${pos.side.toUpperCase()}</span>
         <span>${pos.qty?.toFixed(4)} @ ${money(pos.avg_entry_price)}</span>
         <span class="${pnlCl}">${money(pnl, {signed: true})}</span>
         <span style="color:var(--text-dim);font-size:.7rem">${stopStr}${targetStr}</span>`;
      posEl.classList.remove('hidden');
    } else {
      posEl.classList.add('hidden');
    }
  }
}

function _bwVal(id, text, colorClass) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'bw-stat-value' + (colorClass ? ` ${colorClass}` : '');
}

// ── SSE handler ────────────────────────────────────────────────────────────────
window.handleSSEMessage = function(msg) {
  if (msg.type === 'signal_update') {
    updateCard(msg.data);
    if (window.galaxyUpdate) window.galaxyUpdate(msg.data);
  }
  if (msg.type === 'price_update') {
    const { symbol, price } = msg.data;
    updatePrice(symbol, price);
    // Grab current signal color for sparkline coloring
    const badge = document.getElementById(`color-${symbol}`);
    const sigColor = badge ? badge.className.replace('signal-badge ', '').trim() : 'neutral';
    updateSparkline(symbol, price, sigColor);
  }
  // Bot wallet events — refresh immediately
  if (msg.type === 'bot_trade' || msg.type === 'bot_exit') loadBotWallet();
};

function updatePrice(symbol, price) {
  const el = document.getElementById(`price-${symbol}`);
  if (el) el.textContent = money(price);
}

function updateCard(d) {
  const { symbol, score, color, label, direction, price, vwap } = d;

  const card = document.getElementById(`card-${symbol}`);
  if (!card) return;

  // ── Prominent status bar ──────────────────────────────────────────────────
  const statusBar    = document.getElementById(`status-bar-${symbol}`);
  const statusAction = document.getElementById(`status-action-${symbol}`);
  const statusScore  = document.getElementById(`status-score-${symbol}`);
  if (statusBar) statusBar.className = `card-status-bar ${color}`;
  if (statusAction) {
    if (color === 'GREEN' || color === 'FLASH_GREEN')   statusAction.textContent = color === 'FLASH_GREEN' ? 'BUY NOW' : 'BUY / HOLD';
    else if (color === 'RED' || color === 'FLASH_RED')  statusAction.textContent = color === 'FLASH_RED'   ? 'SELL NOW' : 'AVOID / SELL';
    else                                                 statusAction.textContent = 'NO TRADE';
  }
  if (statusScore) statusScore.textContent = score != null ? `${score.toFixed(0)}/100` : '--/100';

  // ── Price ─────────────────────────────────────────────────────────────────
  if (price) {
    const pel = document.getElementById(`price-${symbol}`);
    if (pel) pel.textContent = money(price);
    updateSparkline(symbol, price, color);
  }

  // ── Color dot ─────────────────────────────────────────────────────────────
  const dot = document.getElementById(`dot-${symbol}`);
  if (dot) dot.className = `card-color-dot ${color}`;

  // ── Signal badge ──────────────────────────────────────────────────────────
  const badge = document.getElementById(`color-${symbol}`);
  if (badge) {
    badge.textContent = color.replace('_', ' ');
    badge.className = `signal-badge ${color}`;
  }

  // ── Score pill ────────────────────────────────────────────────────────────
  const scorePill = document.getElementById(`score-${symbol}`);
  if (scorePill) scorePill.textContent = score.toFixed(0);

  // ── Label ─────────────────────────────────────────────────────────────────
  const labelEl = document.getElementById(`label-${symbol}`);
  if (labelEl) {
    labelEl.textContent = label.replace('_', ' ');
    labelEl.className = `card-label label-badge ${LABEL_CLASS[label] || 'no-trade'}`;
  }

  // ── VWAP ──────────────────────────────────────────────────────────────────
  if (vwap) {
    const vwapEl = document.getElementById(`vwap-${symbol}`);
    if (vwapEl) vwapEl.textContent = money(vwap);
    if (price) {
      const relEl = document.getElementById(`vwap-rel-${symbol}`);
      if (relEl) {
        const pct = ((price - vwap) / vwap * 100).toFixed(2);
        relEl.textContent = (pct >= 0 ? '+' : '') + pct + '%';
        relEl.className = 'vwap-rel ' + (pct >= 0 ? 'green' : 'red');
      }
    }
  }

  // ── Score ring + day change + glow ────────────────────────────────────────
  const ring = document.getElementById(`ring-${symbol}`);
  if (ring && score != null) {
    ring.style.setProperty('--v', score.toFixed(0));
    ring.style.setProperty('--ring', color.includes('GREEN') ? 'var(--green)' : color.includes('RED') ? 'var(--red)'
      : direction > 0 ? 'var(--blue)' : direction < 0 ? 'var(--purple)' : 'var(--text-dim)');
    document.getElementById(`ring-val-${symbol}`).textContent = score.toFixed(0);
  }
  if (d.change_pct != null) {
    const ch = document.getElementById(`change-${symbol}`);
    if (ch) {
      ch.textContent = (d.change_pct >= 0 ? '▲ +' : '▼ ') + d.change_pct.toFixed(2) + '%';
      ch.className = 'card-change ' + (d.change_pct >= 0 ? 'up' : 'down');
    }
  }
  card.classList.toggle('glow-green', color === 'GREEN' || color === 'FLASH_GREEN');
  card.classList.toggle('glow-red', color === 'RED' || color === 'FLASH_RED');

  // ── Card flash border ─────────────────────────────────────────────────────
  card.classList.remove('flash-green', 'flash-red');
  if (color === 'FLASH_GREEN') card.classList.add('flash-green');
  if (color === 'FLASH_RED')   card.classList.add('flash-red');

  // ── Best trade banner ─────────────────────────────────────────────────────
  if (d.is_best) updateBestTradeBanner(d);

  // ── Update sparkline color to match signal ────────────────────────────────
  const chart = _sparkCharts[symbol];
  if (chart) {
    const c = _sparkColor(symbol, color);
    chart.data.datasets[0].borderColor = c;
    chart.data.datasets[0].backgroundColor = c + '18';
    chart.update('none');
  }
}

function updateBestTradeBanner(d) {
  const banner = document.getElementById('best-trade-banner');
  if (!banner) return;
  banner.classList.remove('hidden');
  document.getElementById('best-trade-symbol').textContent = d.symbol;
  document.getElementById('best-trade-direction').textContent = d.direction === 1 ? 'LONG' : 'SHORT';
  document.getElementById('best-trade-score').textContent = d.score.toFixed(0) + '/100';
  if (d.thesis && d.thesis.why_now) {
    document.getElementById('best-trade-why').textContent = d.thesis.why_now;
  }
}

// ── Initial data load ──────────────────────────────────────────────────────────
async function loadDashboard() {
  // Signals
  try {
    const r = await fetch('/api/signals');
    const sigs = await r.json();
    Object.values(sigs).forEach(s => updateCard(s));
  } catch(e) {}

  // Best trade
  try {
    const r = await fetch('/api/best-trade');
    const d = await r.json();
    if (d.best_trade) {
      updateBestTradeBanner(d.best_trade);
      document.getElementById('best-trade-banner').classList.remove('hidden');
    }
  } catch(e) {}

  loadAlerts();
  loadPositions();
  loadSymbolDetails();
}

async function loadAlerts() {
  try {
    const r = await fetch('/api/alerts?limit=20');
    const alerts = await r.json();
    const el = document.getElementById('alerts-list');
    if (!el) return;
    if (!alerts.length) { el.innerHTML = '<p class="empty-state">No alerts yet.</p>'; return; }
    el.innerHTML = alerts.slice(0, 10).map(a => `
      <div class="alert-item ${a.type}">
        <strong>${a.symbol}</strong> — ${a.type.replace('_', ' ').toUpperCase()}
        ${a.score ? `<span class="score-pill">${a.score.toFixed(0)}</span>` : ''}
        <div class="alert-time">${new Date(a.sent_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', hour12:true})}</div>
      </div>
    `).join('');
  } catch(e) {}
}

async function loadPositions() {
  try {
    const r = await fetch('/api/paper/positions');
    const positions = await r.json();
    const el = document.getElementById('positions-list');
    if (!el) return;
    if (!positions.length) { el.innerHTML = '<p class="empty-state">No open positions.</p>'; return; }
    el.innerHTML = positions.map(p => {
      const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'green' : 'red';
      return `<div class="alert-item">
        <strong>${p.symbol}</strong>
        <span class="${p.side === 'long' ? 'green' : 'red'}">${p.side.toUpperCase()}</span>
        ${p.qty} @ ${money(p.avg_entry_price)}
        <span class="${pnlClass}"> P&L: ${money((p.unrealized_pnl || 0))}</span>
        <button onclick="quickClose('${p.symbol}')" class="btn btn-xs btn-red ml-2">Close</button>
      </div>`;
    }).join('');
  } catch(e) {}
}

async function loadSymbolDetails() {
  const cards = document.querySelectorAll('.symbol-card');
  for (const card of cards) {
    const sym = card.dataset.symbol;
    try {
      const r = await fetch(`/api/symbol/${sym}/signals`);
      const sig = await r.json();
      if (!sig.symbol) continue;

      const volComp = (sig.components || []).find(c => c.name === 'volume');
      if (volComp) {
        const rvolEl = document.getElementById(`rvol-${sym}`);
        const rvol = volComp.details?.rvol;
        if (rvolEl && rvol != null) rvolEl.textContent = `RVOL ${rvol.toFixed(1)}x`;
      }

      const candleComp = (sig.components || []).find(c => c.name === 'candlestick');
      if (candleComp && candleComp.details?.patterns?.length) {
        const p = candleComp.details.patterns[0];
        const tagEl = document.getElementById(`candle-${sym}`);
        if (tagEl) tagEl.textContent = p.name.replace(/_/g, ' ');
      }

      if (sig.thesis) {
        const thEl = document.getElementById(`thesis-${sym}`);
        if (thEl) thEl.textContent = sig.thesis.why_now?.slice(0, 80) + '...';
      }
    } catch(e) {}
  }
}

// ── Order modal ────────────────────────────────────────────────────────────────
function quickOrder(symbol, side) {
  document.getElementById('modal-symbol').value = symbol;
  document.getElementById('modal-side').value = side;
  document.getElementById('modal-error').classList.add('hidden');
  document.getElementById('order-modal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('order-modal').classList.add('hidden');
}

async function submitOrder() {
  const symbol = document.getElementById('modal-symbol').value;
  const side   = document.getElementById('modal-side').value;
  const qty    = parseFloat(document.getElementById('modal-qty').value);
  const stop   = parseFloat(document.getElementById('modal-stop').value) || null;
  const target = parseFloat(document.getElementById('modal-target').value) || null;

  const body = { symbol, side, qty, order_type: 'market', stop_price: stop, take_profit_price: target };

  try {
    const r = await fetch('/api/paper/order', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) {
      document.getElementById('modal-error').textContent = d.detail || 'Order failed.';
      document.getElementById('modal-error').classList.remove('hidden');
      return;
    }
    closeModal();
    loadPositions();
    alert(`Order submitted: ${side.toUpperCase()} ${qty} ${symbol} @ ${money(d.filled_price)}`);
  } catch(err) {
    document.getElementById('modal-error').textContent = 'Network error.';
    document.getElementById('modal-error').classList.remove('hidden');
  }
}

async function quickClose(symbol) {
  if (!confirm(`Close position in ${symbol}?`)) return;
  const r = await fetch('/api/paper/close', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({symbol, reason: 'manual'}),
  });
  const d = await r.json();
  if (r.ok) { loadPositions(); }
  else { alert('Error: ' + (d.detail || 'unknown')); }
}

async function toggleKillSwitch(enabled) {
  await fetch('/api/kill-switch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled, reason: 'Dashboard toggle'}),
  });
  window.location.reload();
}

// ── Whale chips on dashboard cards ────────────────────────────────────────────
async function loadWhaleChips() {
  try {
    const r = await fetch('/api/whales');
    if (!r.ok) return;
    const d = await r.json();
    (d.whales || []).forEach(w => {
      const chip = document.getElementById(`whale-chip-${w.symbol}`);
      if (!chip) return;
      const label = w.sentiment_label || 'NEUTRAL';
      const sent  = w.combined_sentiment || 0;
      let arrow, cls;
      if (sent >= 0.4)       { arrow = '▲▲'; cls = 'whale-chip-bullish'; }
      else if (sent >= 0.15) { arrow = '▲';  cls = 'whale-chip-lean-bull'; }
      else if (sent <= -0.4) { arrow = '▼▼'; cls = 'whale-chip-bearish'; }
      else if (sent <= -0.15){ arrow = '▼';  cls = 'whale-chip-lean-bear'; }
      else                   { arrow = '';   cls = 'whale-chip-neutral'; }
      chip.innerHTML = window.icon('whale') + (arrow ? `<span class="wc-arrow">${arrow}</span>` : '');
      chip.className   = `whale-chip ${cls}`;
      chip.title       = `Whales: ${label}` +
        (w.pcr ? ` | P/C Ratio: ${w.pcr}` : '') +
        (w.num_funds_long ? ` | ${w.num_funds_long} funds long` : '') +
        (w.total_value_m ? ` | $${w.total_value_m}M held` : '');
    });
  } catch(e) { /* non-critical */ }
}

// ── Top status bar: clock + wallets ───────────────────────────────────────────
function tickClock() {
  const now    = new Date();
  const timeEl = document.getElementById('tsb-time');
  const dateEl = document.getElementById('tsb-date');
  if (timeEl) timeEl.textContent = now.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', hour12:true });
  if (dateEl) dateEl.textContent = now.toLocaleDateString([], { month:'long', day:'numeric', year:'numeric' });
}

async function loadStatusWallets() {
  // Paper wallet — from bot-status
  try {
    const r = await fetch('/api/paper/bot-status');
    if (r.ok) {
      const d  = await r.json();
      const el = document.getElementById('tsb-paper-equity');
      if (el) {
        const equity = (d.bot_cash ?? 0) + (d.unrealized_pnl ?? 0);
        el.textContent = money(equity);
      }
    }
  } catch(e) {}

  try {
    const r = await fetch('/api/account');
    if (r.ok) {
      const d  = await r.json();
      const el = document.getElementById('tsb-live-equity');
      if (el) el.textContent = d.equity != null
        ? money(d.equity)
        : 'N/A';
    }
  } catch(e) {}
}

tickClock();
setInterval(tickClock, 1000);
loadStatusWallets();
setInterval(loadStatusWallets, 15000);

// ── Weekly Economic Calendar ──────────────────────────────────────────────────
async function loadWeeklyCalendar() {
  try {
    const r = await fetch('/api/macro/calendar');
    if (!r.ok) return;
    const d = await r.json();
    const lbl = document.getElementById('cal-week-label');
    if (lbl) lbl.textContent = d.week_label || '';
    renderCalGrid(d.days, 'weekly-cal-grid', 'economic');
    renderCalGrid(d.days, 'weekly-earnings-grid', 'earnings');
  } catch(e) {}
}

function renderCalGrid(days, containerId, type) {
  const el = document.getElementById(containerId);
  if (!el || !days) return;
  const COUNTRY_FLAG = { US:'🇺🇸', EU:'🇪🇺', UK:'🇬🇧', JP:'🇯🇵', CN:'🇨🇳', CA:'🇨🇦', AU:'🇦🇺' };
  const ANTICIPATION_LABEL = {
    bullish_watch: ['bullish', '▲ Bullish Watch'],
    bearish_watch: ['bearish', '▼ Bearish Watch'],
    high_volatility_watch: ['hvol', 'High Volatility'],
    mixed_expectations: ['mixed', '↔ Mixed'],
  };
  // Week labels for a 4-week (28-day) window starting last Monday
  const WEEK_LABELS = ['LAST WEEK', 'THIS WEEK', 'NEXT WEEK', 'WEEK +2'];
  const parts = [];
  days.forEach((day, idx) => {
    // Insert a week-label divider at the start of every 7-day block
    if (idx % 7 === 0) {
      const wLabel = WEEK_LABELS[idx / 7] || '';
      const isLast = WEEK_LABELS[idx / 7] === 'LAST WEEK';
      const labelColor = isLast ? 'var(--text-dim)' : idx === 7 ? 'var(--blue)' : 'var(--text-muted)';
      parts.push(`
        <div class="cal-week-divider" style="--label-color:${labelColor}">
          <span class="cal-week-label">${wLabel}</span>
        </div>`);
    }
    const events = type === 'economic' ? day.economic_events : day.earnings_events;
    // Determine if this day is in the past (before today)
    const isPast = !day.is_today && idx < 7; // last week block
    const headerCls = day.is_today ? 'cal-day-header today' : isPast ? 'cal-day-header past' : 'cal-day-header';
    let rows = '';
    if (!events || events.length === 0) {
      rows = `<div style="font-size:.65rem;color:var(--text-dim);padding:.3rem .4rem;font-style:italic">—</div>`;
    } else if (type === 'economic') {
      rows = events.map(e => {
        const safeTitle = e.title.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        // Build actual vs forecast badge for released events
        let actFcst = '';
        if (e.actual != null) {
          const hit = e.forecast != null
            ? (parseFloat(e.actual) >= parseFloat(e.forecast) ? 'act-beat' : 'act-miss')
            : '';
          actFcst = `<span class="cal-act ${hit}">${e.actual}</span>`;
          if (e.forecast != null) actFcst += `<span class="cal-fcst">est ${e.forecast}</span>`;
        } else if (e.forecast != null) {
          actFcst = `<span class="cal-fcst">est ${e.forecast}</span>`;
        }
        return `
        <div class="cal-event-row cal-event-clickable" data-indicator-title="${safeTitle}" title="Click for key notes & market impact">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:.2rem">
            <div class="cal-event-time">${e.time_str}</div>
            <span class="cal-impact-${e.impact}">${e.impact === 'high' ? '●' : '◦'}</span>
          </div>
          <div style="display:flex;align-items:flex-start;gap:.25rem;margin-top:.1rem">
            <span style="flex-shrink:0">${COUNTRY_FLAG[e.country] || e.country}</span>
            <span class="cal-event-title" style="flex:1;min-width:0;line-height:1.35">${e.title}</span>
          </div>
          ${actFcst ? `<div class="cal-actfcst-row">${actFcst}</div>` : ''}
        </div>`;
      }).join('');
    } else {
      rows = events.map(e => {
        const [chipCls, chipLabel] = ANTICIPATION_LABEL[e.anticipation] || ['mixed','Mixed'];
        return `<div class="cal-event-row">
          <span class="earnings-ticker">${e.ticker}</span>
          <span class="earnings-chip ${chipCls}">${chipLabel}</span>
        </div>`;
      }).join('');
    }
    parts.push(`<div class="cal-day-col">
      <div class="${headerCls}">${day.day_name}<span style="font-size:.6rem;font-weight:400;margin-left:.3rem;color:var(--text-dim)">${day.date_str}</span></div>
      ${rows}
    </div>`);
  });
  el.innerHTML = parts.join('');
}

// ── Panels with no data are hidden, and the panels below slide up to fill the gap
function hideDashPanel(id) {
  const el = document.getElementById(id);
  if (!el || el.style.display === 'none') return;
  el.style.display = 'none';
  const panels = Array.from(document.querySelectorAll('#dash-canvas .fp-panel'))
    .filter(p => p.style.display !== 'none')
    .sort((a, b) => (parseInt(a.style.top) || 0) - (parseInt(b.style.top) || 0));
  const placed = [];
  for (const p of panels) {
    const l = parseInt(p.style.left) || 0, w = p.offsetWidth;
    let top = 0;
    for (const q of placed) {                       // sit just below the lowest overlapping panel above
      if (l < q.l + q.w && q.l < l + w) top = Math.max(top, q.t + q.h + 12);
    }
    top = Math.min(top, parseInt(p.style.top) || 0);
    p.style.top = top + 'px';
    placed.push({ l, w, t: top, h: p.offsetHeight });
  }
  const canvas = document.getElementById('dash-canvas');
  if (canvas) canvas.style.height = Math.max(...placed.map(q => q.t + q.h), 0) + 20 + 'px';
}

// ── Macro Dashboard (NAAIM, AAII, Treasury) ───────────────────────────────────
async function loadMacroDashboard() {
  try {
    const r = await fetch('/api/macro/dashboard');
    if (!r.ok) return;
    const d = await r.json();
    renderNAAIM(d.naaim);
    renderAAII(d.aaii);
    renderTreasury(d.treasury_yields, d.macro_rates);
  } catch(e) {}
}

function renderNAAIM(data) {
  const el = document.getElementById('naaim-content');
  if (!el) return;
  if (!data) { hideDashPanel('dp-naaim'); return; }
  const BADGE = { 'risk-on': 'var(--green)', 'moderately-bullish': '#3b9' , 'neutral': 'var(--text-dim)', 'risk-off': 'var(--red)' };
  const TREND_ARROW = { rising: '↑', falling: '↓', flat: '→' };
  el.innerHTML = `
    <div class="macro-stat-row">
      <div>
        <div class="macro-label">Exposure Index</div>
        <div class="macro-big-val">${data.value.toFixed(1)}<span style="font-size:1rem;margin-left:.3rem;color:var(--text-dim)">${TREND_ARROW[data.trend]||''}</span></div>
      </div>
      <div>
        <span class="macro-badge" style="background:${BADGE[data.interpretation]}22;color:${BADGE[data.interpretation]};border:1px solid ${BADGE[data.interpretation]}55">
          ${data.interpretation.replace('-',' ').toUpperCase()}
        </span>
        ${data.previous_value != null ? `<div style="font-size:.68rem;color:var(--text-dim);margin-top:.3rem">Prev: ${data.previous_value.toFixed(1)}</div>` : ''}
        <div style="font-size:.65rem;color:var(--text-dim);margin-top:.2rem">${data.reading_date}</div>
      </div>
    </div>`;
}

function renderAAII(data) {
  const el = document.getElementById('aaii-content');
  if (!el) return;
  if (!data) { hideDashPanel('dp-aaii'); return; }
  const INTERP_COLOR = { bullish_crowding: 'var(--green)', bearish_crowding: 'var(--red)', balanced_sentiment: 'var(--text-dim)' };
  const interpLabel = data.interpretation.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  const color = INTERP_COLOR[data.interpretation] || 'var(--text-dim)';
  el.innerHTML = `
    <div>
      <div class="sentiment-bar-row">
        <span style="font-size:.65rem;color:var(--green);width:52px">Bullish</span>
        <div class="sentiment-bar" style="flex:1"><div class="sentiment-bar-fill" style="width:${data.bullish_pct}%;background:var(--green)"></div></div>
        <span style="font-size:.7rem;font-family:var(--font-mono);width:38px;text-align:right;color:var(--green)">${data.bullish_pct.toFixed(1)}%</span>
      </div>
      <div class="sentiment-bar-row">
        <span style="font-size:.65rem;color:var(--text-dim);width:52px">Neutral</span>
        <div class="sentiment-bar" style="flex:1"><div class="sentiment-bar-fill" style="width:${data.neutral_pct}%;background:var(--text-dim)"></div></div>
        <span style="font-size:.7rem;font-family:var(--font-mono);width:38px;text-align:right;color:var(--text-muted)">${data.neutral_pct.toFixed(1)}%</span>
      </div>
      <div class="sentiment-bar-row">
        <span style="font-size:.65rem;color:var(--red);width:52px">Bearish</span>
        <div class="sentiment-bar" style="flex:1"><div class="sentiment-bar-fill" style="width:${data.bearish_pct}%;background:var(--red)"></div></div>
        <span style="font-size:.7rem;font-family:var(--font-mono);width:38px;text-align:right;color:var(--red)">${data.bearish_pct.toFixed(1)}%</span>
      </div>
      <div style="margin-top:.5rem;display:flex;align-items:center;gap:.75rem">
        <span class="macro-badge" style="background:${color}22;color:${color};border:1px solid ${color}55;font-size:.62rem">${interpLabel}</span>
        <span style="font-size:.65rem;color:var(--text-dim)">Spread: ${data.bull_bear_spread > 0 ? '+' : ''}${data.bull_bear_spread.toFixed(1)}%</span>
        <span style="font-size:.65rem;color:var(--text-dim)">${data.survey_date}</span>
      </div>
    </div>`;
}

function renderTreasury(yields, rates) {
  const el = document.getElementById('treasury-content');
  if (!el) return;
  let html = '<div class="treasury-row">';
  // Fed funds first
  if (rates) {
    const dirColor = rates.direction === 'cutting' ? 'var(--green)' : rates.direction === 'hiking' ? 'var(--red)' : 'var(--text-muted)';
    html += `<div class="treasury-card" style="border-color:${dirColor}44">
      <div class="treasury-tenor">Fed Funds</div>
      <div class="treasury-yield" style="color:${dirColor}">${rates.fed_funds_range}</div>
      <div style="font-size:.62rem;color:${dirColor};text-transform:uppercase;margin-top:.15rem">${rates.direction}</div>
    </div>`;
  }
  if (yields && yields.length) {
    html += yields.map(y => {
      const chgColor = y.change_1d > 0 ? 'var(--red)' : y.change_1d < 0 ? 'var(--green)' : 'var(--text-dim)';
      const arrow = y.direction === 'rising' ? '↑' : y.direction === 'falling' ? '↓' : '→';
      return `<div class="treasury-card">
        <div class="treasury-tenor">${y.label}</div>
        <div class="treasury-yield">${y.yield_pct.toFixed(2)}<span style="font-size:.7rem;color:var(--text-dim)">%</span></div>
        <div class="treasury-change" style="color:${chgColor}">${arrow} ${y.change_1d >= 0 ? '+' : ''}${y.change_1d.toFixed(2)}bp</div>
      </div>`;
    }).join('');
  }
  html += '</div>';
  if (!yields?.length && !rates) html = '<p class="empty-state">Treasury data unavailable.</p>';
  el.innerHTML = html;
}

// ── Economic Indicator Info Modal ─────────────────────────────────────────────
async function openIndicatorModal(title) {
  const modal = document.getElementById('indicator-modal');
  if (!modal) return;

  // Show modal immediately with loading state
  document.getElementById('ind-full-name').textContent = title;
  document.getElementById('ind-meta').textContent = 'Loading…';
  document.getElementById('ind-measures').textContent = '';
  document.getElementById('ind-matters').textContent = '';
  document.getElementById('ind-reaction').innerHTML = '';
  document.getElementById('ind-thresholds').textContent = '';
  document.getElementById('ind-related').textContent = '';
  modal.classList.remove('hidden');

  try {
    const r = await fetch('/api/macro/indicator?title=' + encodeURIComponent(title));
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();

    document.getElementById('ind-full-name').textContent = d.full_name || title;
    document.getElementById('ind-meta').textContent =
      `${d.frequency || ''}${d.source ? '  ·  ' + d.source : ''}`;

    document.getElementById('ind-measures').textContent = d.what_it_measures || '—';
    document.getElementById('ind-matters').textContent  = d.why_it_matters  || '—';

    // Market reaction — show beat/miss as two colour-coded rows
    const rx = d.market_reaction || {};
    let rxHtml = '';
    if (rx.beat) rxHtml += `<div class="ind-reaction-row green"><span class="ind-reaction-label">${icon('check')}Beat</span><span>${rx.beat}</span></div>`;
    if (rx.miss) rxHtml += `<div class="ind-reaction-row red"><span class="ind-reaction-label">${icon('x')}Miss</span><span>${rx.miss}</span></div>`;
    document.getElementById('ind-reaction').innerHTML = rxHtml || '—';

    const thresh = d.key_thresholds || '';
    document.getElementById('ind-thresholds').textContent = thresh;
    document.getElementById('ind-thresholds-wrap').style.display = thresh ? '' : 'none';

    const rel = d.related_releases || '';
    document.getElementById('ind-related').textContent = rel;
    document.getElementById('ind-related-wrap').style.display = (rel && rel !== '—') ? '' : 'none';

  } catch(e) {
    document.getElementById('ind-meta').textContent = 'Could not load details.';
  }
}

function closeIndicatorModal() {
  const modal = document.getElementById('indicator-modal');
  if (modal) modal.classList.add('hidden');
}

// Delegated click: open indicator modal when a calendar event row is clicked
document.addEventListener('click', function(e) {
  const row = e.target.closest('[data-indicator-title]');
  if (row) {
    openIndicatorModal(row.dataset.indicatorTitle);
    return;
  }
  // Close indicator modal on backdrop click
  const modal = document.getElementById('indicator-modal');
  if (modal && !modal.classList.contains('hidden') && e.target === modal) {
    closeIndicatorModal();
  }
});

// ── Init ───────────────────────────────────────────────────────────────────────
loadDashboard();
loadSparklines();
loadBotWallet();
loadWhaleChips();
loadWeeklyCalendar();
loadMacroDashboard();
setInterval(loadDashboard,  15000);
setInterval(loadSparklines, 120000);
setInterval(loadBotWallet,  10000);
setInterval(loadWhaleChips, 300000);
setInterval(loadWeeklyCalendar,  3600000);
setInterval(loadMacroDashboard,  300000);
