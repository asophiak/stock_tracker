/* ══════════════════════════════════════════════════════════════════════════════
   ui.js — shared formatting + icons (loaded on every page before page scripts)
     money(v, {signed, dp})   "$1,234.56" · "−$12,532.45" · "+$116.85"
     pct(v, {signed, dp})     "+0.54%" · "−0.04%"
     num(v, dp)               "1,234.5"
     icon(name, cls)          inline SVG line icon (24×24, currentColor)
   Templates use <i data-icon="name"></i>; those are filled in on load.
   ══════════════════════════════════════════════════════════════════════════════ */
(function () {
  const MINUS = '−';

  function money(v, { signed = false, dp = 2 } = {}) {
    if (v == null || v === '' || isNaN(v)) return '—';
    const n = +v;
    const body = '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
    if (n < 0) return MINUS + body;
    return (signed && n > 0 ? '+' : '') + body;
  }
  function pct(v, { signed = true, dp = 2 } = {}) {
    if (v == null || isNaN(v)) return '—';
    const n = +v;
    const body = Math.abs(n).toFixed(dp) + '%';
    return n < 0 ? MINUS + body : (signed && n > 0 ? '+' : '') + body;
  }
  function num(v, dp = 0) {
    if (v == null || isNaN(v)) return '—';
    const n = +v;
    const body = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
    return n < 0 ? MINUS + body : body;
  }

  // Simple 24×24 line icons (stroke = currentColor)
  const P = {
    brain: '<path d="M9 4a3 3 0 0 0-3 3v.2A3 3 0 0 0 4 10a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3 3 3 0 0 0 3-3V7a3 3 0 0 0-3-3z"/><path d="M15 4a3 3 0 0 1 3 3v.2A3 3 0 0 1 20 10a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3 3 3 0 0 1-3-3"/><path d="M12 9h2M12 14h2.5"/>',
    whale: '<path d="M3 13c0 3.5 3.5 6 8.5 6 4.5 0 7.5-2.5 8.5-6 .3-1 1-1.5 2-1.5-1-1-2.2-1.3-3.2-.8C18 8 15 6 11.5 6 6.5 6 3 9 3 13z"/><circle cx="8" cy="11.5" r=".8" fill="currentColor"/><path d="M11 6c0-1.5.8-2.5 2-3M11 6c-.5-1.3-1.5-2-2.8-2"/>',
    microscope: '<path d="M6 18h8M3 22h18M14 22a7 7 0 1 0 0-14h-1"/><path d="M9 14h2M9 12a2 2 0 0 1-2-2V6h6v4a2 2 0 0 1-2 2z"/><path d="M12 6V3a1 1 0 0 0-1-1H9a1 1 0 0 0-1 1v3"/>',
    bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M9 14h.01M15 14h.01"/><circle cx="12" cy="3.5" r="1"/><path d="M2 13v2M22 13v2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    stop: '<path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z"/><path d="M12 8v5M12 16h.01"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    coins: '<circle cx="9" cy="9" r="6"/><path d="M15.5 9.3A6 6 0 1 1 9.3 15.5"/><path d="M9 6.5v5M7 8h3a1 1 0 0 1 0 2H8"/>',
    bank: '<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
    bars: '<path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="7"/><rect x="12" y="7" width="3" height="11"/><rect x="17" y="13" width="3" height="5"/>',
    zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
    link: '<path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    x: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17h.01"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    hand: '<path d="M9 11V5a1.5 1.5 0 0 1 3 0v5M12 10V4a1.5 1.5 0 0 1 3 0v6M15 10V6a1.5 1.5 0 0 1 3 0v7a7 7 0 0 1-7 7h-1a6 6 0 0 1-5-3l-2.5-4.5a1.5 1.5 0 0 1 2.5-1.5L9 13"/>',
    book: '<path d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    trending: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    dot: '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>',
  };
  function icon(name, cls = '') {
    const body = P[name];
    if (!body) return '';
    return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }
  function hydrate(root = document) {
    root.querySelectorAll('i[data-icon]').forEach(el => {
      if (el.dataset.done) return;
      el.innerHTML = icon(el.dataset.icon);
      el.dataset.done = '1';
    });
  }

  window.money = money; window.pct = pct; window.num = num; window.icon = icon;
  window.UI = { money, pct, num, icon, hydrate };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => hydrate());
  else hydrate();
})();
