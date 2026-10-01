#!/usr/bin/env python3
"""
Backtest threshold sensitivity analysis.

Usage (from project root):
    python scripts/backtest.py [--db path/to/trading.db] [--exclude-outliers]

What it does:
  1. Loads all closed_trades from the SQLite DB.
  2. For each entry-score threshold (45, 50, 55, 60, 65, 70, 75, 80, 85),
     reports: trade count, win rate, gross P&L, intraday-only P&L, profit
     factor, and avg hold time — both including and excluding overnight
     outlier trades (hold > 390 min).
  3. Shows a hold-time sensitivity table: for each max-hold cutoff
     (30, 60, 90, 120, 240, 390 min), reports what's left in the data.

The script uses only stdlib + sqlite3 — no FastAPI dependency.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_DB = Path(__file__).parent.parent / "stock_tracker.db"
OUTLIER_HOLD_MINS = 390   # overnight / multi-day hold threshold


# ── Data loading ──────────────────────────────────────────────────────────────

def load_trades(db_path: Path) -> list[dict]:
    """Load all closed_trades rows with entry score extracted from JSON."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            pnl,
            is_winner,
            close_reason,
            time_bucket,
            entry_signal_json,
            stop_price,
            take_profit_price,
            thesis_quality,
            CAST(
                json_extract(entry_signal_json, '$.total_score')
                AS REAL
            ) AS entry_score,
            (julianday(closed_at) - julianday(opened_at)) * 1440.0 AS hold_mins,
            opened_at,
            closed_at,
            session_date
        FROM closed_trades
        ORDER BY closed_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Analysis helpers ──────────────────────────────────────────────────────────

def _stats(trades: list[dict]) -> dict:
    """Compute summary stats for a list of trade dicts."""
    if not trades:
        return {
            "count": 0, "win_rate": 0.0, "gross_pnl": 0.0,
            "profit_factor": None, "avg_hold_mins": None, "avg_pnl": 0.0,
        }
    count    = len(trades)
    winners  = [t for t in trades if t["is_winner"]]
    losers   = [t for t in trades if not t["is_winner"]]
    gross    = sum(t["pnl"] for t in trades)
    win_pnl  = sum(t["pnl"] for t in winners)
    loss_pnl = abs(sum(t["pnl"] for t in losers))
    holds    = [t["hold_mins"] for t in trades if t["hold_mins"] is not None]

    return {
        "count":          count,
        "win_rate":       round(len(winners) / count * 100, 1),
        "gross_pnl":      round(gross, 2),
        "avg_pnl":        round(gross / count, 2),
        "profit_factor":  round(win_pnl / loss_pnl, 2) if loss_pnl > 0 else None,
        "avg_hold_mins":  round(sum(holds) / len(holds), 1) if holds else None,
    }


def _bar(pf: Optional[float]) -> str:
    if pf is None:
        return "  ∞ "
    stars = min(5, int(pf))
    return ("★" * stars).ljust(5)


# ── Report sections ───────────────────────────────────────────────────────────

def report_threshold_sensitivity(trades: list[dict]) -> None:
    scored = [t for t in trades if t["entry_score"] is not None]
    intra  = [t for t in scored if t["hold_mins"] is not None and t["hold_mins"] <= OUTLIER_HOLD_MINS]

    thresholds = [45, 50, 55, 60, 65, 70, 75, 80, 85]

    print("\n" + "═" * 100)
    print("  THRESHOLD SENSITIVITY  (entry score ≥ threshold)")
    print("═" * 100)
    print(f"  {'Threshold':>9}  {'Trades':>6}  {'Win%':>6}  {'Gross P&L':>10}  "
          f"{'Intra P&L':>10}  {'Intra Tr':>8}  {'PF':>6}  {'Avg Hold':>9}")
    print("─" * 100)

    for thr in thresholds:
        sub       = [t for t in scored if t["entry_score"] >= thr]
        sub_intra = [t for t in intra  if t["entry_score"] >= thr]
        s         = _stats(sub)
        si        = _stats(sub_intra)

        pf_str    = f"{s['profit_factor']:.2f}" if s['profit_factor'] else "  ∞"
        hold_str  = f"{s['avg_hold_mins']:.0f}m" if s['avg_hold_mins'] else "  —"

        pnl_sign  = "+" if s["gross_pnl"] >= 0 else ""
        i_sign    = "+" if si["gross_pnl"] >= 0 else ""

        print(
            f"  {thr:>9}  {s['count']:>6}  {s['win_rate']:>5.1f}%  "
            f"{pnl_sign}{s['gross_pnl']:>9.2f}  "
            f"{i_sign}{si['gross_pnl']:>9.2f}  "
            f"{si['count']:>8}  {pf_str:>6}  {hold_str:>9}"
        )

    print("─" * 100)
    print("  Intra P&L = gross P&L for intraday-only trades (hold ≤ 390 min)")
    print()


def report_hold_sensitivity(trades: list[dict]) -> None:
    scored = [t for t in trades if t["entry_score"] is not None and t["hold_mins"] is not None]

    cutoffs = [30, 60, 90, 120, 180, 240, 390]

    print("═" * 80)
    print("  MAX-HOLD SENSITIVITY  (trades held ≤ N minutes)")
    print("═" * 80)
    print(f"  {'Max Hold':>9}  {'Trades':>6}  {'Win%':>6}  {'Gross P&L':>10}  {'PF':>6}  {'Avg P&L':>9}")
    print("─" * 80)

    for cutoff in cutoffs:
        sub  = [t for t in scored if t["hold_mins"] <= cutoff]
        s    = _stats(sub)
        pf_s = f"{s['profit_factor']:.2f}" if s['profit_factor'] else "  ∞"
        sign = "+" if s["gross_pnl"] >= 0 else ""
        print(
            f"  {cutoff:>7}m  {s['count']:>6}  {s['win_rate']:>5.1f}%  "
            f"{sign}{s['gross_pnl']:>9.2f}  {pf_s:>6}  {sign}{s['avg_pnl']:>8.2f}"
        )

    print("─" * 80)
    print()


def report_score_x_hold(trades: list[dict]) -> None:
    """2D grid: score threshold × max hold time — shows intraday gross P&L."""
    scored = [t for t in trades if t["entry_score"] is not None and t["hold_mins"] is not None]

    thresholds = [55, 60, 65, 70, 75, 80]
    cutoffs    = [30, 60, 90, 120, 180, 240, 390]

    print("═" * 80)
    print("  SCORE × HOLD GRID  (gross P&L per cell)")
    print("═" * 80)
    header = f"  {'Score≥':>6}  " + "  ".join(f"{c:>5}m" for c in cutoffs)
    print(header)
    print("─" * 80)

    for thr in thresholds:
        cells = []
        for cutoff in cutoffs:
            sub   = [t for t in scored if t["entry_score"] >= thr and t["hold_mins"] <= cutoff]
            gross = sum(t["pnl"] for t in sub)
            sign  = "+" if gross >= 0 else ""
            cells.append(f"{sign}{gross:>5.0f}")
        print(f"  {thr:>6}   " + "  ".join(cells))

    print("─" * 80)
    print("  Values are gross P&L $ for trades with score ≥ row AND hold ≤ col")
    print()


def report_exit_breakdown(trades: list[dict]) -> None:
    from collections import defaultdict

    intra = [t for t in trades if t["hold_mins"] is not None and t["hold_mins"] <= OUTLIER_HOLD_MINS]

    by_reason: dict[str, list] = defaultdict(list)
    for t in intra:
        by_reason[t["close_reason"] or "unknown"].append(t)

    print("═" * 80)
    print("  EXIT REASON BREAKDOWN  (intraday trades only)")
    print("═" * 80)
    print(f"  {'Reason':<30}  {'Trades':>6}  {'Win%':>6}  {'Gross P&L':>10}  {'Avg P&L':>9}  {'Avg Hold':>9}")
    print("─" * 80)

    rows = sorted(by_reason.items(), key=lambda kv: len(kv[1]), reverse=True)
    for reason, ts in rows:
        s    = _stats(ts)
        sign = "+" if s["gross_pnl"] >= 0 else ""
        hold = f"{s['avg_hold_mins']:.0f}m" if s["avg_hold_mins"] else "—"
        print(
            f"  {reason:<30}  {s['count']:>6}  {s['win_rate']:>5.1f}%  "
            f"{sign}{s['gross_pnl']:>9.2f}  "
            f"{sign}{s['avg_pnl']:>8.2f}  {hold:>9}"
        )

    print("─" * 80)
    print()


def report_summary(trades: list[dict]) -> None:
    all_s    = _stats(trades)
    scored   = [t for t in trades if t["entry_score"] is not None]
    intra    = [t for t in trades if t["hold_mins"] is not None and t["hold_mins"] <= OUTLIER_HOLD_MINS]
    outliers = [t for t in trades if t["hold_mins"] is not None and t["hold_mins"] > OUTLIER_HOLD_MINS]

    print()
    print("╔" + "═" * 60 + "╗")
    print("║  BACKTEST SUMMARY                                          ║")
    print("╠" + "═" * 60 + "╣")
    print(f"║  Total trades       : {all_s['count']:>5}                              ║")
    print(f"║  With entry score   : {len(scored):>5}                              ║")
    print(f"║  Intraday (≤390min) : {len(intra):>5}                              ║")
    print(f"║  Outliers (>390min) : {len(outliers):>5}                              ║")
    print(f"║  Overall win rate   : {all_s['win_rate']:>5.1f}%                             ║")
    print(f"║  Gross P&L (all)    : ${all_s['gross_pnl']:>9.2f}                         ║")
    si = _stats(intra)
    print(f"║  Gross P&L (intra)  : ${si['gross_pnl']:>9.2f}                         ║")
    so = _stats(outliers)
    print(f"║  Gross P&L (outlier): ${so['gross_pnl']:>9.2f}                         ║")
    print("╚" + "═" * 60 + "╝")
    print()


def report_thesis_quality(trades: list[dict]) -> None:
    """
    Phase 4B — thesis quality breakdown.

    Classifies all trades as valid / partial / missing_thesis based on whether
    stop_price and take_profit_price were set at entry.  Compares P&L and win
    rate across the three tiers so the impact of the Phase 4B blocker is clear.
    """
    total   = len(trades)
    if not total:
        return

    valid   = [t for t in trades if t.get("stop_price") and t.get("take_profit_price")]
    partial = [t for t in trades if (t.get("stop_price") or t.get("take_profit_price"))
                                    and not (t.get("stop_price") and t.get("take_profit_price"))]
    missing = [t for t in trades if not t.get("stop_price") and not t.get("take_profit_price")]

    tiers = [
        ("valid  (stop + TP)", valid),
        ("partial (stop|TP) ", partial),
        ("missing thesis    ", missing),
    ]

    print("═" * 80)
    print("  THESIS QUALITY (Phase 4B)  — stop/TP coverage at entry")
    print("═" * 80)
    print(f"  {'Tier':<22}  {'Trades':>6}  {'Share':>6}  {'Win%':>6}  "
          f"{'Gross P&L':>10}  {'Avg P&L':>9}  {'PF':>6}")
    print("─" * 80)

    for label, ts in tiers:
        if not ts:
            print(f"  {label:<22}  {'0':>6}  {'  —':>6}")
            continue
        s    = _stats(ts)
        pct  = len(ts) / total * 100
        sign = "+" if s["gross_pnl"] >= 0 else ""
        pf_s = f"{s['profit_factor']:.2f}" if s["profit_factor"] else "  ∞"
        print(
            f"  {label:<22}  {s['count']:>6}  {pct:>5.0f}%  {s['win_rate']:>5.1f}%  "
            f"{sign}{s['gross_pnl']:>9.2f}  {sign}{s['avg_pnl']:>8.2f}  {pf_s:>6}"
        )

    print("─" * 80)
    valid_pct = len(valid) / total * 100
    if valid_pct >= 80:
        print(f"  ✅  {valid_pct:.0f}% of trades are valid — Phase 4B gate is healthy.")
    else:
        print(f"  ⚠   Only {valid_pct:.0f}% of trades are valid (pre-Phase-4B data).")
        print("      New entries are blocked until ATR-based stop + TP can be computed.")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest threshold sensitivity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default: analysis runs on valid-thesis trades only\n"
            "  (stop_price AND take_profit_price set at entry — post-Phase-4B standard).\n"
            "Use --include-missing-thesis to include all 86 historical trades."
        ),
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to stock_tracker.db")
    parser.add_argument(
        "--include-missing-thesis",
        action="store_true",
        help="Include pre-Phase-4B trades that have no stop_price / take_profit_price",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        raise SystemExit(1)

    print(f"Loading trades from {db_path} …")
    all_trades = load_trades(db_path)
    print(f"Loaded {len(all_trades)} closed trades.")

    # Always show the quality breakdown for the full dataset first
    report_thesis_quality(all_trades)

    if args.include_missing_thesis:
        trades = all_trades
        print(f"  ⚠  --include-missing-thesis: analysing all {len(trades)} trades "
              f"(including {sum(1 for t in trades if not t.get('stop_price'))} with no stop).\n")
    else:
        trades = [t for t in all_trades
                  if t.get("stop_price") and t.get("take_profit_price")]
        post_4b = [t for t in trades if t.get("strategy_version") == "4b"]
        print(
            f"  Filtered to {len(trades)} valid-thesis trades "
            f"({len(post_4b)} post-4B, {len(trades)-len(post_4b)} pre-4B valid).\n"
            f"  Use --include-missing-thesis to analyse all {len(all_trades)} trades.\n"
        )

    report_summary(trades)
    report_threshold_sensitivity(trades)
    report_hold_sensitivity(trades)
    report_score_x_hold(trades)
    report_exit_breakdown(trades)


if __name__ == "__main__":
    main()
