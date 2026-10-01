/**
 * charts.js — Charting layer for Stock Tracker
 *
 * Main price chart  → TradingView Lightweight Charts (proper OHLC candlesticks)
 * Dashboard sparklines → Chart.js (lightweight inline mini-charts)
 *
 * Exported functions used by symbol_detail.js:
 *   buildLightweightChart(containerId, bars, levels)  → chartObj
 *   updateLightweightChart(chartObj, bars)
 *   addThesisOverlays(chartObj, thesis)               → draws stop/target lines
 *   addSignalMarker(chartObj, bars, signal)            → buy ▲ / sell ▼ arrow
 *   setScoreRing(score)
 *
 * Used by dashboard.js (sparklines — Chart.js):
 *   initSparkline / updateSparkline  (defined inline in dashboard.js)
 */

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Convert an ISO datetime string to a Unix timestamp (seconds),
 * shifted by the browser's local timezone offset so LightweightCharts
 * (which renders all timestamps as UTC) displays local time.
 */
function _toSec(isoStr) {
  const d = new Date(isoStr);
  // getTimezoneOffset() = minutes behind UTC (e.g. PDT = +420)
  // Subtract that offset so LWC's UTC rendering matches local clock
  return Math.floor(d.getTime() / 1000) - (d.getTimezoneOffset() * 60);
}

/** Sort bars by time ascending and convert to LWC candlestick format. */
function _toCandleData(bars) {
  return bars
    .slice()
    .sort((a, b) => new Date(a.t) - new Date(b.t))
    .map(b => ({ time: _toSec(b.t), open: b.o, high: b.h, low: b.l, close: b.c }));
}

/** Convert bars to LWC histogram (volume) format. */
function _toVolData(bars) {
  return bars
    .slice()
    .sort((a, b) => new Date(a.t) - new Date(b.t))
    .map(b => ({
      time:  _toSec(b.t),
      value: b.v,
      color: b.c >= b.o ? '#3fb95055' : '#f8514955',
    }));
}

/** Compute session VWAP incrementally across sorted bars. */
function _toVwapData(bars) {
  const sorted = bars.slice().sort((a, b) => new Date(a.t) - new Date(b.t));
  let cumTPVol = 0, cumVol = 0;
  return sorted.map(b => {
    cumTPVol += ((b.h + b.l + b.c) / 3) * b.v;
    cumVol   += b.v;
    return { time: _toSec(b.t), value: cumVol > 0 ? cumTPVol / cumVol : b.c };
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Main chart — TradingView Lightweight Charts
// ─────────────────────────────────────────────────────────────────────────────

const LWC_THEME = {
  layout: {
    background: { type: 'solid', color: '#161b22' },
    textColor:  '#8b949e',
    fontFamily: '"SF Mono","Fira Code",Consolas,monospace',
    fontSize:   11,
  },
  grid: {
    vertLines: { color: '#21273a' },
    horzLines: { color: '#21273a' },
  },
  crosshair: { mode: 1 /* Normal */ },
  rightPriceScale: {
    borderColor: '#30363d',
    scaleMargins: { top: 0.08, bottom: 0.22 },
  },
  timeScale: {
    borderColor:     '#30363d',
    timeVisible:     true,
    secondsVisible:  false,
    rightOffset:     8,
    fixLeftEdge:     false,
    lockVisibleTimeRangeOnResize: true,
  },
};

/**
 * Build a complete candlestick chart inside `containerId`.
 * Includes: candlestick series, volume histogram, VWAP line,
 * opening-range dotted lines.
 *
 * @param {string} containerId  — id of the container <div>
 * @param {Array}  bars         — bar objects {t,o,h,l,c,v}
 * @param {Object} levels       — {orh, orl, vwap} from market_data
 * @returns chartObj or null
 */
function buildLightweightChart(containerId, bars, levels = {}) {
  const container = document.getElementById(containerId);
  if (!container || !bars?.length) return null;

  // Destroy any existing chart
  if (container.__lwChart) {
    container.__lwChart.remove();
    container.__lwChart = null;
  }

  const chart = LightweightCharts.createChart(container, {
    ...LWC_THEME,
    width:  container.clientWidth,
    height: container.clientHeight || 280,
    handleScroll: { mouseWheel: true, pressedMouseMove: true },
    handleScale:  { mouseWheel: true, pinch: true },
  });
  container.__lwChart = chart;

  // ── Candlestick series ───────────────────────────────────────────────────
  const candleSeries = chart.addCandlestickSeries({
    upColor:         '#3fb950',
    downColor:       '#f85149',
    borderUpColor:   '#3fb950',
    borderDownColor: '#f85149',
    wickUpColor:     '#3fb950',
    wickDownColor:   '#f85149',
  });

  // ── Volume histogram (overlaid, bottom 22% of chart) ─────────────────────
  const volSeries = chart.addHistogramSeries({
    priceFormat:  { type: 'volume' },
    priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.78, bottom: 0 },
  });

  // ── VWAP line ─────────────────────────────────────────────────────────────
  const vwapSeries = chart.addLineSeries({
    color:                '#e3b341',
    lineWidth:            1,
    lineStyle:            LightweightCharts.LineStyle.Dashed,
    priceLineVisible:     false,
    lastValueVisible:     true,
    title:                'VWAP',
    crosshairMarkerVisible: false,
  });

  // ── Set data ──────────────────────────────────────────────────────────────
  candleSeries.setData(_toCandleData(bars));
  volSeries.setData(_toVolData(bars));
  vwapSeries.setData(_toVwapData(bars));

  // ── Opening range dotted lines ────────────────────────────────────────────
  if (levels.orh && levels.orl && bars.length >= 2) {
    const sorted = bars.slice().sort((a, b) => new Date(a.t) - new Date(b.t));
    const t0 = _toSec(sorted[0].t);
    const tN = _toSec(sorted[sorted.length - 1].t);
    const orStyle = {
      color: '#58a6ff70', lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    };
    const orhS = chart.addLineSeries({ ...orStyle, title: 'ORH' });
    const orlS = chart.addLineSeries({ ...orStyle, title: 'ORL' });
    orhS.setData([{ time: t0, value: levels.orh }, { time: tN, value: levels.orh }]);
    orlS.setData([{ time: t0, value: levels.orl }, { time: tN, value: levels.orl }]);
  }

  chart.timeScale().fitContent();

  // Auto-resize when the container changes size
  const ro = new ResizeObserver(() => {
    chart.resize(container.clientWidth, container.clientHeight);
  });
  ro.observe(container);
  container.__lwRO = ro;

  const chartObj = { chart, candleSeries, volSeries, vwapSeries,
                     _stopLine: null, _targetLine: null };
  container.__lwChartObj = chartObj;
  return chartObj;
}

/**
 * Efficiently update an existing chart with fresh bars.
 * Only replaces series data; does not rebuild the chart.
 */
function updateLightweightChart(chartObj, bars) {
  if (!chartObj?.candleSeries || !bars?.length) return;
  chartObj.candleSeries.setData(_toCandleData(bars));
  chartObj.volSeries?.setData(_toVolData(bars));
  chartObj.vwapSeries?.setData(_toVwapData(bars));
}

// ─────────────────────────────────────────────────────────────────────────────
// Thesis overlays — stop / target price lines
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Draw dashed horizontal stop (red) and target (green) price lines
 * directly on the candlestick series.  Removes old lines first.
 *
 * @param {Object} chartObj   — returned by buildLightweightChart
 * @param {Object} thesis     — signal.thesis from the API
 */
function addThesisOverlays(chartObj, thesis) {
  if (!chartObj?.candleSeries) return;

  // Remove previous lines
  if (chartObj._stopLine) {
    try { chartObj.candleSeries.removePriceLine(chartObj._stopLine); } catch(_) {}
    chartObj._stopLine = null;
  }
  if (chartObj._targetLine) {
    try { chartObj.candleSeries.removePriceLine(chartObj._targetLine); } catch(_) {}
    chartObj._targetLine = null;
  }

  if (!thesis) return;

  if (thesis.suggested_stop) {
    chartObj._stopLine = chartObj.candleSeries.createPriceLine({
      price:            thesis.suggested_stop,
      color:            '#f85149',
      lineWidth:        1,
      lineStyle:        LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title:            `✕ Stop  $${thesis.suggested_stop.toFixed(2)}`,
    });
  }

  if (thesis.suggested_target) {
    chartObj._targetLine = chartObj.candleSeries.createPriceLine({
      price:            thesis.suggested_target,
      color:            '#3fb950',
      lineWidth:        1,
      lineStyle:        LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title:            `✓ Target  $${thesis.suggested_target.toFixed(2)}`,
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Signal marker — buy ▲ / sell ▼ arrow on the latest bar
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Place a BUY (green ▲) or SELL (red ▼) marker on the most recent bar.
 *
 * @param {Object} chartObj — returned by buildLightweightChart
 * @param {Array}  bars     — current bar array (for the timestamp)
 * @param {Object} signal   — {direction, total_score, color, label}
 */
function addSignalMarker(chartObj, bars, signal) {
  if (!chartObj?.candleSeries || !bars?.length) return;
  if (!signal?.direction || signal.direction === 0) {
    chartObj.candleSeries.setMarkers([]);
    return;
  }

  const sorted  = bars.slice().sort((a, b) => new Date(a.t) - new Date(b.t));
  const lastBar = sorted[sorted.length - 1];
  const time    = _toSec(lastBar.t);
  const isLong  = signal.direction === 1;
  const score   = signal.total_score?.toFixed(0) ?? '';
  const flash   = signal.color === 'FLASH_GREEN' || signal.color === 'FLASH_RED';

  chartObj.candleSeries.setMarkers([{
    time,
    position: isLong ? 'belowBar' : 'aboveBar',
    color:    isLong ? (flash ? '#00ff88' : '#3fb950') : (flash ? '#ff3b30' : '#f85149'),
    shape:    isLong ? 'arrowUp' : 'arrowDown',
    text:     `${isLong ? 'BUY' : 'SELL'} ${score}`,
    size:     flash ? 2 : 1,
  }]);
}

// ─────────────────────────────────────────────────────────────────────────────
// Score ring (SVG gauge on symbol detail page)
// ─────────────────────────────────────────────────────────────────────────────

function setScoreRing(score) {
  const circle = document.getElementById('score-ring-circle');
  const numEl  = document.getElementById('sd-score-num');
  if (!circle || !numEl) return;
  const circumference = 213.6;
  const offset = circumference - (score / 100) * circumference;
  circle.style.strokeDashoffset = offset;
  if      (score >= 85) circle.style.stroke = '#00ff88';
  else if (score >= 70) circle.style.stroke = '#3fb950';
  else if (score >= 55) circle.style.stroke = '#e3b341';
  else                  circle.style.stroke = '#8b949e';
  numEl.textContent = Math.round(score);
}
