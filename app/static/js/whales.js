/**
 * whales.js — Whale Wallet Tracker
 * Fetches /api/whales/portfolio-changes (13F position diffs) and renders
 * a fund overview + scrollable trades feed with fund/action filters.
 */

var _allTrades  = [];   // flat list of all trade rows
var _activeFilter = 'all';
var _activeFund   = null;  // null = all funds

var ACTION_META = {
  new_buy:   { label: 'NEW BUY',  cls: 'ww-act-buy',    icon: '▲' },
  added:     { label: 'ADDED',    cls: 'ww-act-buy',    icon: '+' },
  reduced:   { label: 'REDUCED',  cls: 'ww-act-sell',   icon: '−' },
  closed:    { label: 'SOLD OUT', cls: 'ww-act-sell',   icon: '×' },
  new_put:   { label: 'NEW PUT',  cls: 'ww-act-put',    icon: '▼' },
  added_put: { label: 'PUT ↑',    cls: 'ww-act-put',    icon: '▼' },
};

function fmtShares(n) {
  if (!n) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}
function fmtValue(v) {
  if (!v) return '—';
  if (v >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'K';
  return '$' + v.toLocaleString();
}
function fmtPct(p) {
  if (p == null) return '—';
  var sign = p > 0 ? '+' : '';
  return sign + p.toFixed(1) + '%';
}

// Fund colour palette (cycles through funds)
var FUND_COLORS = [
  '#58a6ff','#3fb950','#d29922','#bc8cff','#f78166',
  '#39d353','#ff7b72','#79c0ff','#ffa657','#a5d6ff',
];
var _fundColorMap = {};
var _colorIndex = 0;
function fundColor(name) {
  if (!_fundColorMap[name]) {
    _fundColorMap[name] = FUND_COLORS[_colorIndex % FUND_COLORS.length];
    _colorIndex++;
  }
  return _fundColorMap[name];
}

// ── Data loading ──────────────────────────────────────────────────────────────

var _pollTimer = null;

async function refreshWhales(force) {
  // Cancel any pending auto-poll
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }

  try {
    var url = '/api/whales/portfolio-changes' + (force ? '?force=1' : '');
    var r = await fetch(url);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();

    var updEl = document.getElementById('ww-updated');
    if (updEl && d.fetched_at) {
      updEl.textContent = 'Updated: ' + new Date(d.fetched_at).toLocaleTimeString();
    }

    if (!d.portfolio_changes || !Object.keys(d.portfolio_changes).length) {
      // EDGAR fetch still in progress — show a progress indicator and retry in 15s
      var feedEl  = document.getElementById('ww-feed');
      var cardsEl = document.getElementById('ww-fund-cards');
      if (feedEl)  feedEl.innerHTML  = '<p class="empty-state ww-loading" style="padding:2rem">⏳ Fetching 13F filings from SEC EDGAR… this takes ~60–90 seconds on first load.</p>';
      if (cardsEl) cardsEl.innerHTML = '<p class="empty-state ww-loading">Waiting for EDGAR…</p>';
      _pollTimer = setTimeout(function() { refreshWhales(false); }, 15000);
      return;
    }

    renderFundCards(d.portfolio_changes);
    buildFundPills(d.portfolio_changes);
    buildTradesList(d.portfolio_changes);
    renderFeed();
  } catch(e) {
    document.getElementById('ww-feed').innerHTML =
      '<p class="empty-state" style="color:var(--red);padding:2rem">Failed to load whale data: ' + e.message + '</p>';
    // Retry on error too, in case it was transient
    _pollTimer = setTimeout(function() { refreshWhales(false); }, 20000);
  }
}

// ── Fund cards ────────────────────────────────────────────────────────────────

function renderFundCards(changes) {
  var el = document.getElementById('ww-fund-cards');
  if (!el) return;
  var html = Object.entries(changes).map(function(entry) {
    var name = entry[0], d = entry[1];
    var color = fundColor(name);
    var buys  = d.changes.filter(function(c){ return c.action_key === 'new_buy' || c.action_key === 'added'; }).length;
    var sells = d.changes.filter(function(c){ return c.action_key === 'reduced' || c.action_key === 'closed'; }).length;
    var puts  = d.changes.filter(function(c){ return c.action_key === 'new_put' || c.action_key === 'added_put'; }).length;
    return '<div class="ww-fund-card" style="--fund-color:' + color + '" onclick="toggleFund(\'' + name.replace(/'/g,"\\'") + '\')" id="fcard-' + _fundId(name) + '">' +
      '<div class="ww-fc-name">' + name + '</div>' +
      '<div class="ww-fc-quarter">' + (d.quarter || '') + '</div>' +
      '<div class="ww-fc-stats">' +
        '<span class="ww-fc-stat bullish">▲ ' + buys + ' buys</span>' +
        '<span class="ww-fc-stat bearish">▼ ' + sells + ' sells</span>' +
        (puts ? '<span class="ww-fc-stat put">▼ ' + puts + ' puts</span>' : '') +
      '</div>' +
      '<div class="ww-fc-total">' + d.total_positions + ' positions tracked · ' + d.total_changes + ' changes</div>' +
    '</div>';
  }).join('');
  el.innerHTML = html || '<p class="empty-state">No fund data.</p>';
}

function _fundId(name) {
  return name.replace(/[^a-zA-Z0-9]/g, '_');
}

function buildFundPills(changes) {
  var el = document.getElementById('ww-fund-pills');
  if (!el) return;
  var html = Object.keys(changes).map(function(name) {
    var color = fundColor(name);
    return '<button class="ww-fund-pill" data-fund="' + name + '" ' +
      'style="--fund-color:' + color + '" onclick="toggleFund(\'' + name.replace(/'/g,"\\'") + '\')">' +
      name + '</button>';
  }).join('');
  el.innerHTML = html;
}

// ── Trades list ───────────────────────────────────────────────────────────────

function buildTradesList(changes) {
  _allTrades = [];
  Object.entries(changes).forEach(function(entry) {
    var fund = entry[0], d = entry[1];
    d.changes.forEach(function(c) {
      _allTrades.push(Object.assign({}, c, { fund: fund, quarter: d.quarter || '', date: d.date_new || '' }));
    });
  });
  // Sort: buys first within each fund, then by $ value
  _allTrades.sort(function(a, b) {
    var aVal = a.new_value || a.old_value || 0;
    var bVal = b.new_value || b.old_value || 0;
    return bVal - aVal;
  });
}

// ── Filters ───────────────────────────────────────────────────────────────────

function setFilter(f) {
  _activeFilter = f;
  document.querySelectorAll('.ww-filter-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.filter === f);
  });
  renderFeed();
}

function toggleFund(name) {
  _activeFund = _activeFund === name ? null : name;
  // Update pill + card active states
  document.querySelectorAll('.ww-fund-pill').forEach(function(p) {
    p.classList.toggle('active', p.dataset.fund === _activeFund);
  });
  document.querySelectorAll('.ww-fund-card').forEach(function(c) {
    var isActive = _activeFund === null || c.id === 'fcard-' + _fundId(_activeFund);
    c.classList.toggle('dimmed', !isActive);
  });
  renderFeed();
}

// ── Render feed ───────────────────────────────────────────────────────────────

function renderFeed() {
  var el = document.getElementById('ww-feed');
  if (!el) return;

  var trades = _allTrades.filter(function(t) {
    if (_activeFund && t.fund !== _activeFund) return false;
    if (_activeFilter === 'bullish') return t.action_key === 'new_buy' || t.action_key === 'added';
    if (_activeFilter === 'bearish') return t.action_key === 'reduced' || t.action_key === 'closed' || t.action_key === 'new_put' || t.action_key === 'added_put';
    return true;
  });

  if (!trades.length) {
    el.innerHTML = '<p class="empty-state" style="padding:1.5rem">No trades match the current filter.</p>';
    return;
  }

  var rows = trades.map(function(t) {
    var meta   = ACTION_META[t.action_key] || { label: t.action_key, cls: '', icon: '' };
    var color  = fundColor(t.fund);
    var pctCls = (t.pct_change == null) ? '' : (t.pct_change >= 0 ? 'green' : 'red');
    var ticker = t.ticker || '';
    // If ticker looks like a real ticker (short, alphabetic), link it
    var tickerHtml = (ticker && /^[A-Z]{1,5}$/.test(ticker))
      ? '<a href="/symbol/' + ticker + '" class="ww-ticker-link">' + ticker + '</a>'
      : (ticker || '');

    return '<div class="ww-row ' + meta.cls + '" data-fund="' + t.fund + '" data-action="' + t.action_key + '">' +
      '<span class="ww-feed-col ww-col-fund"><span class="ww-fund-tag" style="background:' + color + '22;color:' + color + ';border-color:' + color + '44">' + t.fund + '</span></span>' +
      '<span class="ww-feed-col ww-col-action"><span class="ww-action-badge ' + meta.cls + '">' + meta.icon + ' ' + meta.label + '</span></span>' +
      '<span class="ww-feed-col ww-col-ticker">' +
        (tickerHtml ? '<span class="ww-ticker">' + tickerHtml + '</span> ' : '') +
        '<span class="ww-issuer">' + t.issuer + '</span>' +
        (t.put_call ? '<span class="ww-pc-tag">' + t.put_call + '</span>' : '') +
      '</span>' +
      '<span class="ww-feed-col ww-col-shares">' + fmtShares(t.new_shares || t.old_shares) + '</span>' +
      '<span class="ww-feed-col ww-col-value">' + fmtValue(t.new_value || t.old_value) + '</span>' +
      '<span class="ww-feed-col ww-col-chg ' + pctCls + '">' + fmtPct(t.pct_change) + '</span>' +
      '<span class="ww-feed-col ww-col-qtr">' + t.quarter + '</span>' +
    '</div>';
  }).join('');

  el.innerHTML = rows;
}

// ── Init ──────────────────────────────────────────────────────────────────────
refreshWhales(false);
// No auto-refresh — 13F data is quarterly, no need to poll
