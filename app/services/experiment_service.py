"""
Exit Logic Experiment Service — Phase 4A.

Simulates alternative exit rules against the closed_trades dataset without
touching live strategy code.  Entry logic is never changed — only exit paths.

───────────────────────────────────────────────────────────────────────────────
SIMULATION METHODOLOGY (documented here, repeated in CLI output):

  For variants that delay or disable signal_reversed_bearish exits, the
  alternative outcome for each affected trade is estimated as:

    Conservative path (stop_hit_est):
      If stop_price was set → assume trade eventually hits stop.
      P&L = (stop_price − entry_price) × qty  [for longs, inverted for shorts]
      If no stop_price → actual P&L retained (no information without bar data).

    Optimistic path (target_hit_est):
      Used only for Variant 8 (TP priority in profit).
      If take_profit_price is set AND trade was profitable → assume TP is hit.
      If no TP or trade was losing → actual P&L retained.

  Variants 3 & 4 (N-bar confirmation):
    The scoring loop fires every ~30 s.  For a trade that lasted 44 min before
    a bearish reversal, that signal would almost certainly persist through one
    or two more cycles.  Simulation assumes all 9 exits re-fire within the
    confirmation window → result equals Baseline.  The value of these variants
    lies in the code diff (implementation guard against single-bar spikes),
    not in the historical simulation.
───────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

OUTLIER_HOLD_MINS = 390  # minutes — overnight / multi-day outlier threshold


# ── P&L estimators ────────────────────────────────────────────────────────────

def _stop_pnl(t: dict) -> Optional[float]:
    """Estimated P&L if trade hits its stop_price (conservative lower bound)."""
    if not t.get("stop_price"):
        return None
    entry = t["entry_price"]
    stop  = t["stop_price"]
    qty   = t["qty"]
    if t["side"] == "long":
        return round((stop - entry) * qty, 2)
    return round((entry - stop) * qty, 2)


def _tp_pnl(t: dict) -> Optional[float]:
    """Estimated P&L if trade hits its take_profit_price (optimistic upper bound)."""
    if not t.get("take_profit_price"):
        return None
    entry = t["entry_price"]
    tp    = t["take_profit_price"]
    qty   = t["qty"]
    if t["side"] == "long":
        return round((tp - entry) * qty, 2)
    return round((entry - tp) * qty, 2)


def _apply_stop(t: dict) -> dict:
    """
    Return a copy of the trade redirected to its stop_price outcome.
    If no stop is set, returns the original with close_reason = 'no_stop_held'
    so the caller can count how many trades were unestimable.
    """
    sp = _stop_pnl(t)
    if sp is None:
        # No stop set — cannot estimate alternative; retain actual P&L but flag it
        m = dict(t)
        m["close_reason"] = "no_stop_held"
        return m
    m = dict(t)
    m["pnl"]          = sp
    m["is_winner"]    = sp > 0
    m["close_reason"] = "stop_hit_est"
    return m


def _has_no_stop(t: dict) -> bool:
    return not t.get("stop_price")


def _apply_tp(t: dict) -> dict:
    """
    Return a copy of the trade redirected to its take_profit_price outcome.
    If no TP is set, returns the original (unchanged).
    """
    tp = _tp_pnl(t)
    if tp is None:
        return t
    m = dict(t)
    m["pnl"]          = tp
    m["is_winner"]    = tp > 0
    m["close_reason"] = "target_hit_est"
    return m


# ── Predicates ────────────────────────────────────────────────────────────────

def _is_bearish_rev(t: dict) -> bool:
    return t.get("close_reason") == "signal_reversed_bearish"


# ── Variant registry ──────────────────────────────────────────────────────────
#
# Each variant is a dict with:
#   id        — short identifier
#   name      — display name
#   desc      — one-line description
#   sim_note  — explains simulation methodology for this variant
#   affects   — predicate: True when this trade is changed by the variant
#   transform — function: trade → modified trade (only called when affects=True)

VARIANTS: list[dict] = [
    {
        "id":       "baseline",
        "name":     "Baseline (current logic)",
        "desc":     "Exact historical outcomes — no modifications.",
        "sim_note": "Ground truth.  No trades altered.",
        "affects":  lambda t: False,
        "transform": lambda t: t,
    },
    {
        "id":       "disable_reversal",
        "name":     "Disable signal_reversed_bearish",
        "desc":     "All bearish reversal exits are suppressed entirely.",
        "sim_note": (
            "Affected: all 9 bearish reversal trades.  "
            "Alternative: hit stop_price (conservative).  "
            "Trades with no stop_price are unchanged."
        ),
        "affects":   _is_bearish_rev,
        "transform": _apply_stop,
    },
    {
        "id":       "reversal_2bar",
        "name":     "Require 2-bar bearish confirmation",
        "desc":     "Exit only after 2 consecutive bearish scoring cycles (~30 s apart).",
        "sim_note": (
            "Simulation = Baseline.  "
            "Rationale: bearish signals that lasted 44 min (avg) almost certainly "
            "persist through one more 30-second cycle.  The value of this variant "
            "is guarding against single-spike reversals — see the code diff."
        ),
        "affects":  lambda t: False,   # no expected change given sustained 44m holds
        "transform": lambda t: t,
    },
    {
        "id":       "reversal_3bar",
        "name":     "Require 3-bar bearish confirmation",
        "desc":     "Exit only after 3 consecutive bearish scoring cycles (~60 s apart).",
        "sim_note": (
            "Simulation = Baseline.  "
            "Rationale: same as 2-bar — sustained 44m signals persist through 60s window.  "
            "Provides stronger protection vs single-spike reversals."
        ),
        "affects":  lambda t: False,
        "transform": lambda t: t,
    },
    {
        "id":       "reversal_if_loss",
        "name":     "Bearish reversal only when trade is losing",
        "desc":     (
            "Block the bearish reversal exit if the trade is currently profitable.  "
            "Let stop or TP manage winning trades."
        ),
        "sim_note": (
            "Affected: profitable bearish reversal exits (pnl ≥ 0 at reversal).  "
            "Alternative: hit stop_price (conservative).  "
            "Already-losing reversals (pnl < 0): unchanged from baseline."
        ),
        "affects":   lambda t: _is_bearish_rev(t) and (t.get("pnl") or 0) >= 0,
        "transform": _apply_stop,
    },
    {
        "id":       "reversal_below_vwap",
        "name":     "Bearish reversal only when price < VWAP",
        "desc":     (
            "Only exit via bearish reversal if price has already crossed below VWAP.  "
            "Above-VWAP longs are protected until price confirms the reversal."
        ),
        "sim_note": (
            "Proxy: entry_signal_json.vwap_position == 'above' AND pnl ≥ 0 → "
            "price likely still above VWAP → block exit → held to stop (conservative).  "
            "Per-bar VWAP at exit is not stored."
        ),
        "affects":  lambda t: (
            _is_bearish_rev(t)
            and (t.get("pnl") or 0) >= 0
            and t.get("vwap_position") == "above"
        ),
        "transform": _apply_stop,
    },
    {
        "id":       "min_hold_10",
        "name":     "Min 10-min hold before bearish reversal",
        "desc":     "Block the bearish reversal exit if trade has been open < 10 minutes.",
        "sim_note": (
            "Affected: bearish reversals with hold_mins < 10.  "
            "Alternative: hit stop_price (conservative)."
        ),
        "affects":   lambda t: _is_bearish_rev(t) and (t.get("hold_mins") or 0) < 10,
        "transform": _apply_stop,
    },
    {
        "id":       "min_hold_20",
        "name":     "Min 20-min hold before bearish reversal",
        "desc":     "Block the bearish reversal exit if trade has been open < 20 minutes.",
        "sim_note": (
            "Affected: bearish reversals with hold_mins < 20.  "
            "Alternative: hit stop_price (conservative)."
        ),
        "affects":   lambda t: _is_bearish_rev(t) and (t.get("hold_mins") or 0) < 20,
        "transform": _apply_stop,
    },
    {
        "id":       "min_hold_30",
        "name":     "Min 30-min hold before bearish reversal",
        "desc":     "Block the bearish reversal exit if trade has been open < 30 minutes.",
        "sim_note": (
            "Affected: bearish reversals with hold_mins < 30.  "
            "Alternative: hit stop_price (conservative)."
        ),
        "affects":   lambda t: _is_bearish_rev(t) and (t.get("hold_mins") or 0) < 30,
        "transform": _apply_stop,
    },
    {
        "id":       "tp_priority_in_profit",
        "name":     "TP takes priority when trade is profitable",
        "desc":     (
            "If trade is profitable AND take_profit_price is set, skip the bearish "
            "reversal exit and let the TP order close the position instead."
        ),
        "sim_note": (
            "Affected: profitable bearish reversals with a TP set.  "
            "Alternative: hit take_profit_price (OPTIMISTIC upper bound — not conservative).  "
            "No-TP or losing reversals: unchanged."
        ),
        "affects":  lambda t: (
            _is_bearish_rev(t)
            and (t.get("pnl") or 0) > 0
            and t.get("take_profit_price") is not None
        ),
        "transform": _apply_tp,
    },
]


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _max_drawdown(trades: list[dict]) -> float:
    """Maximum peak-to-trough drawdown in cumulative P&L, ordered by close time."""
    sorted_t = sorted(trades, key=lambda t: t.get("closed_at") or "")
    peak = cum = 0.0
    max_dd = 0.0
    for t in sorted_t:
        cum += t["pnl"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _worst_exit(trades: list[dict]) -> str:
    """Exit reason with the worst total P&L (≥ 2 occurrences)."""
    by_reason: dict[str, list] = defaultdict(list)
    for t in trades:
        r = t.get("close_reason") or "unknown"
        by_reason[r].append(t["pnl"])
    candidates = [(r, sum(ps)) for r, ps in by_reason.items() if len(ps) >= 2]
    if not candidates:
        return "none"
    return min(candidates, key=lambda x: x[1])[0]


def _compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "gross_pnl": 0.0, "intraday_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": None,
            "max_drawdown": 0.0, "target_hit_count": 0,
            "avg_hold_mins": None, "worst_exit": "none",
        }

    intra   = [t for t in trades if (t.get("hold_mins") or 0) <= OUTLIER_HOLD_MINS]
    winners = [t for t in trades if t.get("is_winner")]
    losers  = [t for t in trades if not t.get("is_winner")]

    gross    = sum(t["pnl"] for t in trades)
    intra_p  = sum(t["pnl"] for t in intra)
    win_sum  = sum(t["pnl"] for t in winners)
    loss_abs = abs(sum(t["pnl"] for t in losers))
    holds    = [t["hold_mins"] for t in trades if t.get("hold_mins") is not None]

    return {
        "trades":           len(trades),
        "win_rate":         round(len(winners) / len(trades) * 100, 1),
        "gross_pnl":        round(gross, 2),
        "intraday_pnl":     round(intra_p, 2),
        "avg_win":          round(win_sum / len(winners), 2) if winners else 0.0,
        "avg_loss":         round(sum(t["pnl"] for t in losers) / len(losers), 2) if losers else 0.0,
        "profit_factor":    round(win_sum / loss_abs, 2) if loss_abs > 0 else None,
        "max_drawdown":     _max_drawdown(trades),
        "target_hit_count": sum(1 for t in trades if t.get("close_reason") in ("target_hit", "target_hit_est")),
        "avg_hold_mins":    round(sum(holds) / len(holds), 1) if holds else None,
        "worst_exit":       _worst_exit(trades),
    }


# ── Main runner ───────────────────────────────────────────────────────────────

def run_experiments(trades: list[dict]) -> list[dict]:
    """
    Apply every variant to the trade list and return a ranked list of result dicts.
    Ranked by intraday P&L descending, then by profit_factor descending.
    """
    results: list[dict] = []

    for v in VARIANTS:
        modified: list[dict] = []
        affected_count = 0
        for t in trades:
            if v["affects"](t):
                modified.append(v["transform"](t))
                affected_count += 1
            else:
                modified.append(t)

        # Count how many affected trades had no stop (unestimable alternative)
        no_stop_blocked = sum(
            1 for orig, mod in zip(trades, modified)
            if v["affects"](orig) and mod.get("close_reason") == "no_stop_held"
        )

        stats = _compute_stats(modified)
        results.append({
            "variant_id":       v["id"],
            "variant_name":     v["name"],
            "variant_desc":     v["desc"],
            "sim_note":         v["sim_note"],
            "affected_trades":  affected_count,
            "no_stop_blocked":  no_stop_blocked,
            **stats,
        })

    # Sort: intraday P&L desc, then profit_factor desc
    results.sort(
        key=lambda r: (r["intraday_pnl"], r["profit_factor"] or 0.0),
        reverse=True,
    )
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


def load_trades_sync(db_path: str) -> list[dict]:
    """Load all closed trades from a SQLite file (used by CLI script only)."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id, symbol, side, qty,
            entry_price, exit_price,
            stop_price, take_profit_price,
            pnl, is_winner, close_reason,
            (julianday(closed_at) - julianday(opened_at)) * 1440.0 AS hold_mins,
            opened_at, closed_at,
            session_date, time_bucket,
            entry_signal_json,
            json_extract(entry_signal_json, '$.vwap_position') AS vwap_position
        FROM closed_trades
        ORDER BY closed_at
    """)
    rows: list[dict] = []
    for r in cur.fetchall():
        d = dict(r)
        d["is_winner"] = bool(d["is_winner"])
        rows.append(d)
    conn.close()
    return rows
