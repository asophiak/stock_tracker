#!/usr/bin/env python3
"""
Exit Logic Experiment Runner — Phase 4A

Compares the current strategy against exit-rule variants using the existing
closed_trades dataset.  NO code changes are applied — read-only analysis only.

Usage (from project root):
    python scripts/experiment_runner.py [--db path/to/stock_tracker.db]
    python scripts/experiment_runner.py --show-diffs         # print all code diffs
    python scripts/experiment_runner.py --diff <variant_id>  # print one diff
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.experiment_service import (
    VARIANTS,
    load_trades_sync,
    run_experiments,
)

DEFAULT_DB = Path(__file__).parent.parent / "stock_tracker.db"


# ── Code diffs ────────────────────────────────────────────────────────────────
# Each diff shows the minimal change to app/main.py (_maybe_auto_execute,
# long-position block, lines ~454–478) required to implement that variant.
# Keeping these in the runner rather than the service keeps the service
# importable without needing diff strings in the web API.
#
# Format: unified diff excerpt (--- before / +++ after), focused on the
# signal_reversed_bearish block.  Line numbers are approximate.
# ─────────────────────────────────────────────────────────────────────────────

_CURRENT_BLOCK = """\
  # Current code (app/main.py ~lines 454-464, long-position block):
  if pos.side == "long":
      # Tier 1: high-conviction reversal
      if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
          should_exit = True
          exit_reason = "signal_reversed_bearish"
          _exhausted_strikes.pop(pos.symbol, None)
      # Tier 2: direction reversed, low score
      elif sig.direction == -1 and sig.total_score < 55:
          should_exit = True
          exit_reason = "signal_reversed_bearish"
          _exhausted_strikes.pop(pos.symbol, None)
"""

VARIANT_DIFFS: dict[str, str] = {

    "baseline": "  (no change — this is the current production code)\n",

    # ── Variant 2: disable ────────────────────────────────────────────────────
    "disable_reversal": """\
  # Remove Tier 1 and Tier 2 bearish reversal blocks entirely.
  # Positions are closed only by: stop_price hit, take_profit hit,
  # signal_exhausted (Tier 3), or EOD flatten.
  #
  # In app/main.py, delete lines 456-464 (long block):
  #
  - if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  - elif sig.direction == -1 and sig.total_score < 55:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  #
  # (Apply analogous removal to the short-position block for symmetry.)
""",

    # ── Variant 3: 2-bar confirmation ─────────────────────────────────────────
    "reversal_2bar": """\
  # Add module-level counter (after _exhausted_strikes declaration):
  + _reversal_strikes: dict[str, int] = {}
  #
  # Replace Tier 1 & Tier 2 long-block with:
  - if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  - elif sig.direction == -1 and sig.total_score < 55:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  + _bearish_now = (
  +     (color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1)
  +     or (sig.direction == -1 and sig.total_score < 55)
  + )
  + if _bearish_now:
  +     _reversal_strikes[pos.symbol] = _reversal_strikes.get(pos.symbol, 0) + 1
  +     if _reversal_strikes[pos.symbol] >= 2:   # ← change to 3 for 3-bar variant
  +         should_exit = True
  +         exit_reason = "signal_reversed_bearish"
  +         _reversal_strikes.pop(pos.symbol, None)
  +         _exhausted_strikes.pop(pos.symbol, None)
  + else:
  +     _reversal_strikes.pop(pos.symbol, None)  # reset on non-bearish cycle
  #
  # Also: clear _reversal_strikes[pos.symbol] after any exit (after set_symbol_cooldown call).
""",

    # ── Variant 4: 3-bar confirmation ─────────────────────────────────────────
    "reversal_3bar": """\
  # Same as reversal_2bar diff above, but change the threshold:
  -     if _reversal_strikes[pos.symbol] >= 2:
  +     if _reversal_strikes[pos.symbol] >= 3:
""",

    # ── Variant 5: only when in loss ─────────────────────────────────────────
    "reversal_if_loss": """\
  # In app/main.py, wrap Tier 1 and Tier 2 with a P&L guard.
  # current_price is already computed before this block.
  #
  - if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  - elif sig.direction == -1 and sig.total_score < 55:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  + _bearish_now = (
  +     (color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1)
  +     or (sig.direction == -1 and sig.total_score < 55)
  + )
  + if _bearish_now:
  +     _cur_pnl = (
  +         (current_price - pos.avg_entry_price) * pos.qty
  +         if current_price else None
  +     )
  +     if _cur_pnl is not None and _cur_pnl < 0:
  +         # trade is losing → exit immediately
  +         should_exit = True
  +         exit_reason = "signal_reversed_bearish"
  +         _exhausted_strikes.pop(pos.symbol, None)
  +     # else: trade is profitable → let stop/TP manage the exit
""",

    # ── Variant 6: only when below VWAP ──────────────────────────────────────
    "reversal_below_vwap": """\
  # In app/main.py, add a VWAP guard to the bearish reversal block.
  #
  - if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  - elif sig.direction == -1 and sig.total_score < 55:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  + _bearish_now = (
  +     (color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1)
  +     or (sig.direction == -1 and sig.total_score < 55)
  + )
  + if _bearish_now:
  +     _below_vwap = sig.vwap is None or (current_price and current_price < sig.vwap)
  +     if _below_vwap:
  +         should_exit = True
  +         exit_reason = "signal_reversed_bearish"
  +         _exhausted_strikes.pop(pos.symbol, None)
  +     # else: price still above VWAP → reversal is premature for a long position
""",

    # ── Variant 7a: min 10-min hold ───────────────────────────────────────────
    "min_hold_10": """\
  # In app/main.py, add a minimum-hold guard (replace bearish reversal block):
  #
  - if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  - elif sig.direction == -1 and sig.total_score < 55:
  -     should_exit = True
  -     exit_reason = "signal_reversed_bearish"
  -     _exhausted_strikes.pop(pos.symbol, None)
  + _bearish_now = (
  +     (color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1)
  +     or (sig.direction == -1 and sig.total_score < 55)
  + )
  + if _bearish_now:
  +     from datetime import timezone
  +     _hold_mins = (
  +         datetime.now(timezone.utc) - pos.opened_at
  +     ).total_seconds() / 60.0
  +     if _hold_mins >= 10:   # ← 10 / 20 / 30 for the three min-hold variants
  +         should_exit = True
  +         exit_reason = "signal_reversed_bearish"
  +         _exhausted_strikes.pop(pos.symbol, None)
""",

    "min_hold_20": """\
  # Same as min_hold_10 diff above, but change the threshold:
  -     if _hold_mins >= 10:
  +     if _hold_mins >= 20:
""",

    "min_hold_30": """\
  # Same as min_hold_10 diff above, but change the threshold:
  -     if _hold_mins >= 10:
  +     if _hold_mins >= 30:
""",

    # ── Variant 8: TP priority in profit ─────────────────────────────────────
    "tp_priority_in_profit": """\
  # In app/main.py, add a TP-grace guard at the TOP of the long-position block
  # (before the Tier 1 / Tier 2 checks):
  #
  + # TP grace: if we're in profit and a TP is set, let the target close the trade
  + # instead of reacting to a single bearish reversal cycle.
  + _in_profit_long = current_price and current_price > pos.avg_entry_price
  + if _in_profit_long and pos.take_profit_price:
  +     # Skip ALL signal-reversal checks — TP order handles the exit
  +     _exhausted_strikes.pop(pos.symbol, None)
  + else:
      if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
          should_exit = True
          exit_reason = "signal_reversed_bearish"
          _exhausted_strikes.pop(pos.symbol, None)
      elif sig.direction == -1 and sig.total_score < 55:
          should_exit = True
          exit_reason = "signal_reversed_bearish"
          _exhausted_strikes.pop(pos.symbol, None)
      elif sig.total_score < 48:
          ...  # (Tier 3 — signal_exhausted — unchanged)
""",
}


# ── Formatting ────────────────────────────────────────────────────────────────

def _pf(v) -> str:
    if v is None:
        return "  ∞ "
    return f"{v:.2f}"


def _hold(v) -> str:
    if v is None:
        return "  — "
    return f"{v:.0f}m"


def _sign(v: float) -> str:
    return "+" if v >= 0 else ""


def _bar(v: float, width: int = 20) -> str:
    """ASCII progress bar scaled to ±$2000."""
    if v == 0:
        return " " * width
    scale = min(abs(v) / 2000, 1.0)
    filled = max(1, int(scale * width))
    char = "█" if v >= 0 else "░"
    return (char * filled).ljust(width)


# ── Report sections ───────────────────────────────────────────────────────────

def print_stop_coverage(trades: list[dict]) -> None:
    """Print a stop-loss coverage breakdown for bearish reversal exits."""
    br_trades = [t for t in trades if t.get("close_reason") == "signal_reversed_bearish"]
    if not br_trades:
        return

    with_stop = [t for t in br_trades if t.get("stop_price")]
    without   = [t for t in br_trades if not t.get("stop_price")]

    print()
    print("╔" + "═" * 78 + "╗")
    print("║  ⚠  CRITICAL FINDING: STOP-LOSS COVERAGE ON BEARISH REVERSAL TRADES       ║")
    print("╠" + "═" * 78 + "╣")
    print(f"║  signal_reversed_bearish exits: {len(br_trades):<3}  │  "
          f"with stop_price: {len(with_stop):<3}  │  WITHOUT stop: {len(without):<3}  ║")
    print("║                                                                              ║")
    print("║  10/11 bearish reversal trades had NO stop_price set.  This means:          ║")
    print("║    • signal_reversed_bearish IS the de-facto stop-loss for these trades.     ║")
    print("║    • Simulation CANNOT estimate alternative outcomes for no-stop trades.     ║")
    print("║    • Disabling/delaying reversal without fixing stop generation is unsafe.   ║")
    print("║                                                                              ║")
    print("║  ROOT CAUSE: thesis.suggested_stop is None for most entries.                ║")
    print("║  FIX FIRST:  Ensure stop_price is always computed (ATR/VWAP/S&R) in        ║")
    print("║              _maybe_auto_execute before experimenting with exit rules.       ║")
    print("╚" + "═" * 78 + "╝")

    if without:
        print()
        print("  Unestimable bearish reversal trades (no stop_price):")
        print(f"  {'Symbol':<8} {'P&L':>8}  {'Hold':>7}  {'Side':<6}")
        print("  " + "─" * 35)
        for t in without:
            print(
                f"  {t['symbol']:<8} {t['pnl']:>8.2f}  "
                f"{(t['hold_mins'] or 0):>6.1f}m  {t['side']:<6}"
            )
    print()


def print_header(total_trades: int, bearish_rev_count: int) -> None:
    print()
    print("╔" + "═" * 78 + "╗")
    print("║  EXIT LOGIC EXPERIMENT RUNNER — Phase 4A                                    ║")
    print("╠" + "═" * 78 + "╣")
    print(f"║  Dataset: {total_trades} closed trades  │  "
          f"signal_reversed_bearish: {bearish_rev_count} trades               ║")
    print("║                                                                              ║")
    print("║  Ranking metric: intraday P&L (hold ≤ 390 min, excludes overnight outliers) ║")
    print("║  NO code changes applied — simulation only.                                  ║")
    print("╚" + "═" * 78 + "╝")


def print_ranked_table(results: list[dict]) -> None:
    W = 106
    print()
    print("═" * W)
    print("  RANKED RESULTS")
    print("═" * W)
    print(
        f"  {'#':>2}  {'Variant':<42}  {'Affected':>8}  {'NoStop':>6}  "
        f"{'Win%':>5}  {'Intra P&L':>10}  {'Δ Intra':>8}  "
        f"{'P.Factor':>9}  {'Max DD':>7}  {'TGT':>3}  {'AvgHold':>7}"
    )
    print("─" * W)

    baseline = next(r for r in results if r["variant_id"] == "baseline")

    for r in results:
        is_base    = r["variant_id"] == "baseline"
        intra      = r["intraday_pnl"]
        intra_b    = baseline["intraday_pnl"]
        intra_diff = intra - intra_b
        diff_str   = (
            f"{_sign(intra_diff)}${intra_diff:.0f}"
            if not is_base
            else "  base"
        )
        no_stop = r.get("no_stop_blocked", 0)
        ns_str  = f"({no_stop})" if no_stop > 0 else "   — "

        print(
            f"  {r['rank']:>2}  {r['variant_name']:<42}  "
            f"{r['affected_trades']:>8}  {ns_str:>6}  "
            f"{r['win_rate']:>4.1f}%  "
            f"{_sign(intra)}${intra:>8.2f}  "
            f"{diff_str:>8}  "
            f"{_pf(r['profit_factor']):>9}  "
            f"${r['max_drawdown']:>6.0f}  "
            f"{r['target_hit_count']:>3}  "
            f"{_hold(r['avg_hold_mins']):>7}"
        )

    print("─" * W)
    print("  Affected = trades whose outcome was changed by this variant")
    print("  NoStop   = affected trades with NO stop_price (simulation unchanged — flagged)")
    print("  Intra P&L = P&L of intraday-only trades (hold ≤ 390 min)")
    print("  P.Factor = gross wins / gross losses   |   Max DD = max peak-to-trough")
    print()


def print_intraday_bar_chart(results: list[dict]) -> None:
    print("  INTRADAY P&L COMPARISON (bar chart)  — scale: each █ ≈ $100")
    print("  " + "─" * 70)
    for r in results:
        v = r["intraday_pnl"]
        bar = _bar(v, 30)
        print(f"  {r['rank']:>2}. {r['variant_name']:<38}  {bar}  {_sign(v)}${v:.2f}")
    print()


def print_simulation_notes(results: list[dict]) -> None:
    print("═" * 70)
    print("  SIMULATION METHODOLOGY NOTES")
    print("═" * 70)
    for r in results:
        print(f"\n  [{r['rank']}] {r['variant_name']}")
        for line in r["sim_note"].split(".  "):
            print(f"       {line.strip()}.")
    print()


def print_diff(variant_id: str) -> None:
    diff = VARIANT_DIFFS.get(variant_id)
    if not diff:
        print(f"  No diff defined for variant '{variant_id}'")
        return
    v = next((x for x in VARIANTS if x["id"] == variant_id), None)
    name = v["name"] if v else variant_id
    print()
    print(f"  CODE DIFF — {name}")
    print("  " + "─" * 70)
    print(f"  File: app/main.py  (_maybe_auto_execute, long-position block)")
    print()
    print(_CURRENT_BLOCK)
    print("  ─── Proposed change ───")
    print(diff)


def print_thesis_quality(trades: list[dict], results: list[dict]) -> None:
    """
    Phase 4B section: show thesis quality breakdown and re-run experiments
    restricted to trades that had a valid stop + TP at entry.
    """
    total = len(trades)
    valid   = [t for t in trades if t.get("stop_price") and t.get("take_profit_price")]
    partial = [t for t in trades if (t.get("stop_price") or t.get("take_profit_price"))
                                    and not (t.get("stop_price") and t.get("take_profit_price"))]
    missing = [t for t in trades if not t.get("stop_price") and not t.get("take_profit_price")]

    print()
    print("╔" + "═" * 78 + "╗")
    print("║  PHASE 4B — THESIS QUALITY COVERAGE                                         ║")
    print("╠" + "═" * 78 + "╣")
    print(f"║  Total trades       : {total:>4}                                                ║")
    print(f"║  Valid  (stop + TP) : {len(valid):>4}  ({len(valid)/total*100:.0f}%)                                         ║")
    print(f"║  Partial (stop|TP)  : {len(partial):>4}  ({len(partial)/total*100:.0f}%)                                         ║")
    print(f"║  Missing thesis     : {len(missing):>4}  ({len(missing)/total*100:.0f}%)  ← pre-Phase-4B entries                ║")
    print("║                                                                              ║")

    if len(valid) >= len(missing):
        print("║  ✅  Majority of trades now have full stop + TP coverage.                    ║")
    else:
        pct_missing = len(missing) / total * 100
        print(f"║  ⚠   {pct_missing:.0f}% of trades are missing thesis — Phase 4B blocker now active.      ║")
        print("║      New entries will be blocked until ATR/fallback stop+TP can be set.     ║")
    print("╚" + "═" * 78 + "╝")

    if not valid:
        print("\n  (No valid trades to re-run experiments against — all pre-4B data)")
        return

    from app.services.experiment_service import run_experiments

    valid_results = run_experiments(valid)
    baseline_all   = next(r for r in results       if r["variant_id"] == "baseline")
    baseline_valid = next(r for r in valid_results if r["variant_id"] == "baseline")

    print()
    print("  Baseline comparison — all trades vs. valid-thesis-only:")
    print(f"  {'Metric':<22}  {'All trades':>12}  {'Valid only':>12}")
    print("  " + "─" * 50)
    print(f"  {'Trades':<22}  {baseline_all['trades']:>12}  {baseline_valid['trades']:>12}")
    print(f"  {'Win rate':<22}  {baseline_all['win_rate']:>11.1f}%  {baseline_valid['win_rate']:>11.1f}%")
    print(f"  {'Intraday P&L':<22}  ${baseline_all['intraday_pnl']:>11.2f}  ${baseline_valid['intraday_pnl']:>11.2f}")
    print(f"  {'Profit factor':<22}  {baseline_all['profit_factor'] or '∞':>12}  {baseline_valid['profit_factor'] or '∞':>12}")
    print(f"  {'Max drawdown':<22}  ${baseline_all['max_drawdown']:>11.2f}  ${baseline_valid['max_drawdown']:>11.2f}")
    print()


def print_daily_clean_report(trades: list[dict]) -> None:
    """
    Phase 4C — daily clean-data report.

    Groups valid-thesis trades by session_date and prints:
      date | entries | win% | gross P&L | expectancy | valid% | TP | stop | reversal | other
    Highlights post-4B sessions in green.
    """
    from collections import defaultdict
    by_day: dict[str, list] = defaultdict(list)
    for t in trades:
        key = t.get("session_date") or "unknown"
        by_day[key].append(t)

    if not by_day:
        return

    W = 110
    print()
    print("═" * W)
    print("  DAILY CLEAN-DATA REPORT  (valid-thesis trades only)")
    print("═" * W)
    print(
        f"  {'Date':<12}  {'Ver':<6}  {'Entries':>7}  {'Win%':>5}  "
        f"{'Gross P&L':>10}  {'Expect':>7}  {'Valid%':>6}  "
        f"{'TP':>3}  {'Stop':>4}  {'Rev':>3}  {'Other':>5}"
    )
    print("─" * W)

    POST_4B_WARN = 30
    total_post_4b = sum(
        1 for t in trades if t.get("strategy_version") == "4b"
    )

    for date in sorted(by_day.keys(), reverse=True):
        ts   = by_day[date]
        n    = len(ts)
        wins = sum(1 for t in ts if t["is_winner"])
        gross = sum(t["pnl"] for t in ts)
        expect = gross / n if n else 0
        valid  = sum(1 for t in ts if t.get("stop_price") and t.get("take_profit_price"))
        valid_pct = valid / n * 100 if n else 0
        tp    = sum(1 for t in ts if t.get("close_reason") == "target_hit")
        stop  = sum(1 for t in ts if t.get("close_reason") == "stop_hit")
        rev   = sum(1 for t in ts if t.get("close_reason") == "signal_reversed_bearish")
        other = n - tp - stop - rev

        is_post = any(t.get("strategy_version") == "4b" for t in ts)
        ver_str = "4b ✦" if is_post else "pre"
        sign    = "+" if gross >= 0 else ""
        e_sign  = "+" if expect >= 0 else ""

        print(
            f"  {date:<12}  {ver_str:<6}  {n:>7}  {wins/n*100:>4.0f}%  "
            f"  {sign}{gross:>8.2f}  {e_sign}{expect:>6.2f}  "
            f"{valid_pct:>5.0f}%  "
            f"{tp:>3}  {stop:>4}  {rev:>3}  {other:>5}"
        )

    print("─" * W)

    if total_post_4b < POST_4B_WARN:
        print(f"\n  ⚠  Only {total_post_4b} post-4B valid trades so far "
              f"(need {POST_4B_WARN} for reliable strategy conclusions).")
    else:
        print(f"\n  ✅  {total_post_4b} post-4B valid trades — sufficient for analysis.")
    print("  ✦ = strategy_version='4b' (Phase 4B entry blocker was active)")
    print()


def print_top_diffs(results: list[dict], top_n: int = 3) -> None:
    """Print code diffs for the top N unique variants (skip baseline)."""
    shown = 0
    for r in results:
        if r["variant_id"] == "baseline":
            continue
        # Skip duplicates that have identical simulation to baseline (no affected trades)
        if r["affected_trades"] == 0 and r["variant_id"] not in ("reversal_2bar", "reversal_3bar"):
            continue
        print_diff(r["variant_id"])
        shown += 1
        if shown >= top_n:
            break


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exit logic experiment runner — Phase 4A/4C",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default: experiments run on valid-thesis trades only\n"
            "  (stop_price AND take_profit_price set — post-Phase-4B standard).\n"
            "Use --include-missing-thesis to include all historical trades."
        ),
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to stock_tracker.db")
    parser.add_argument("--show-diffs", action="store_true",
                        help="Print code diffs for top 3 variants")
    parser.add_argument("--diff", metavar="VARIANT_ID",
                        help="Print code diff for a specific variant ID")
    parser.add_argument("--notes", action="store_true",
                        help="Print full simulation methodology notes")
    parser.add_argument(
        "--include-missing-thesis",
        action="store_true",
        help="Include pre-Phase-4B trades with no stop/TP (shows full historical dataset)",
    )
    parser.add_argument(
        "--daily", action="store_true",
        help="Print daily clean-data report (valid-thesis sessions)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        raise SystemExit(1)

    # Load all trades first (needed for quality banner regardless of filter)
    print(f"Loading trades from {db_path} …")
    all_trades = load_trades_sync(str(db_path))
    print(f"Loaded {len(all_trades)} closed trades.")

    bearish_rev_count = sum(
        1 for t in all_trades if t.get("close_reason") == "signal_reversed_bearish"
    )

    # Always show thesis-quality + stop-coverage for the full set
    print_stop_coverage(all_trades)

    if args.include_missing_thesis:
        trades = all_trades
        print(f"\n  --include-missing-thesis: using all {len(trades)} trades.\n")
    else:
        trades = [t for t in all_trades
                  if t.get("stop_price") and t.get("take_profit_price")]
        post_4b = sum(1 for t in trades if t.get("strategy_version") == "4b")
        print(
            f"\n  Filtered to {len(trades)} valid-thesis trades "
            f"({post_4b} post-4B, {len(trades)-post_4b} pre-4B valid).\n"
            f"  Use --include-missing-thesis for full historical dataset.\n"
        )

    results = run_experiments(trades)
    print_thesis_quality(all_trades, results)   # always show full-dataset quality banner

    print_header(len(trades), bearish_rev_count)
    print_ranked_table(results)
    print_intraday_bar_chart(results)

    if args.daily:
        print_daily_clean_report(all_trades)

    if args.notes:
        print_simulation_notes(results)

    if args.diff:
        print_diff(args.diff)
    elif args.show_diffs:
        print_top_diffs(results, top_n=3)
    else:
        print("  RECOMMENDED NEXT STEP: Accumulate post-4B valid-thesis trades,")
        print("  then re-run experiments on clean data to evaluate exit variants.\n")

    print()
    print("  ⚠  This is a simulation only. No code changes have been applied.")
    print("  ⚠  To implement a variant, apply the diff above to app/main.py manually.")
    print()


if __name__ == "__main__":
    main()
