"""
Trade frequency diagnostic report.

Generates end-of-day and end-of-week reports answering:
  "Why are we only getting ~6.5 trades/day?"
  "Are we correctly filtering bad trades or accidentally rejecting good ones?"

Reports are written to logs/ as JSON (machine-readable) and also
returned as dicts for the API endpoint.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trading import ClosedTrade, RejectedSetup, ShadowTrade
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("logs/diagnostic_reports")


# ── Main entry points ─────────────────────────────────────────────────────────

async def generate_daily_report(
    db: AsyncSession,
    session_date: Optional[str] = None,
    save_to_disk: bool = True,
) -> Dict[str, Any]:
    """
    Generate a full diagnostic report for one trading session.
    Returns the report dict (also saved to logs/diagnostic_reports/).
    """
    date = session_date or session_date_str()

    trades       = await _load_closed_trades(db, date)
    rejections   = await _load_rejections(db, date)
    shadows      = await _load_shadows(db, date)

    report = {
        "report_type": "daily",
        "session_date": date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary_stats(trades, rejections, shadows),
        "rejection_breakdown": _rejection_breakdown(rejections),
        "trades_by_symbol": _group_by_symbol(trades),
        "trades_by_hour": _group_by_hour(trades),
        "tier_performance": _tier_performance(trades, rejections, shadows),
        "shadow_performance": _shadow_performance(shadows),
        "best_symbols": _best_worst_symbols(trades, n=5, best=True),
        "worst_symbols": _best_worst_symbols(trades, n=5, best=False),
        "best_hours": _best_worst_hours(trades, n=3, best=True),
        "worst_hours": _best_worst_hours(trades, n=3, best=False),
        "frequency_diagnosis": _frequency_diagnosis(trades, rejections, shadows),
    }

    if save_to_disk:
        _save_report(report, f"daily_{date}.json")

    logger.info(
        "Diagnostic report for %s: %d trades, %d rejections, %d shadows",
        date, len(trades), len(rejections), len(shadows),
    )
    return report


async def generate_weekly_report(
    db: AsyncSession,
    dates: List[str],
    save_to_disk: bool = True,
) -> Dict[str, Any]:
    """
    Aggregate daily data across a list of session dates into a weekly report.
    """
    all_trades:     List[ClosedTrade]  = []
    all_rejections: List[RejectedSetup] = []
    all_shadows:    List[ShadowTrade]  = []

    for date in dates:
        all_trades     += await _load_closed_trades(db, date)
        all_rejections += await _load_rejections(db, date)
        all_shadows    += await _load_shadows(db, date)

    report = {
        "report_type": "weekly",
        "dates": dates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary_stats(all_trades, all_rejections, all_shadows),
        "rejection_breakdown": _rejection_breakdown(all_rejections),
        "trades_by_symbol": _group_by_symbol(all_trades),
        "trades_by_hour": _group_by_hour(all_trades),
        "tier_performance": _tier_performance(all_trades, all_rejections, all_shadows),
        "shadow_performance": _shadow_performance(all_shadows),
        "best_symbols": _best_worst_symbols(all_trades, n=5, best=True),
        "worst_symbols": _best_worst_symbols(all_trades, n=5, best=False),
        "best_hours": _best_worst_hours(all_trades, n=3, best=True),
        "worst_hours": _best_worst_hours(all_trades, n=3, best=False),
        "frequency_diagnosis": _frequency_diagnosis(all_trades, all_rejections, all_shadows),
        "daily_breakdown": {d: {"trades": 0} for d in dates},  # will be filled from summary
    }

    # Per-day trade count
    for t in all_trades:
        d = t.session_date or ""
        if d in report["daily_breakdown"]:
            report["daily_breakdown"][d]["trades"] = report["daily_breakdown"][d].get("trades", 0) + 1

    if save_to_disk:
        label = f"{dates[0]}_to_{dates[-1]}" if dates else "unknown"
        _save_report(report, f"weekly_{label}.json")

    return report


# ── Data loaders ──────────────────────────────────────────────────────────────

async def _load_closed_trades(db: AsyncSession, date: str) -> List[ClosedTrade]:
    result = await db.execute(
        select(ClosedTrade).where(ClosedTrade.session_date == date)
    )
    return list(result.scalars().all())


async def _load_rejections(db: AsyncSession, date: str) -> List[RejectedSetup]:
    result = await db.execute(
        select(RejectedSetup).where(RejectedSetup.session_date == date)
    )
    return list(result.scalars().all())


async def _load_shadows(db: AsyncSession, date: str) -> List[ShadowTrade]:
    result = await db.execute(
        select(ShadowTrade).where(ShadowTrade.session_date == date)
    )
    return list(result.scalars().all())


# ── Report sections ───────────────────────────────────────────────────────────

def _summary_stats(
    trades: List[ClosedTrade],
    rejections: List[RejectedSetup],
    shadows: List[ShadowTrade],
) -> Dict:
    a_trades = [r for r in rejections if r.tier == "A_TRADE"]
    b_trades = [r for r in rejections if r.tier == "B_TRADE"]
    shadow_setups = [r for r in rejections if r.tier == "SHADOW"]
    rejected = [r for r in rejections if r.tier == "REJECTED"]

    total_signals = len(rejections)
    executed_a = len([t for t in trades if (t.strategy_version or "").startswith("A")])
    executed_b = len([t for t in trades if (t.strategy_version or "").startswith("B")])
    total_executed = len(trades)

    winners = [t for t in trades if t.is_winner]
    losers  = [t for t in trades if not t.is_winner]
    win_rate = len(winners) / len(trades) * 100 if trades else 0.0
    total_pnl = sum(t.pnl for t in trades)
    avg_win   = sum(t.pnl for t in winners) / len(winners) if winners else 0.0
    avg_loss  = sum(t.pnl for t in losers)  / len(losers)  if losers  else 0.0
    expectancy = total_pnl / len(trades) if trades else 0.0

    gross_wins  = sum(t.pnl for t in winners)
    gross_losses = abs(sum(t.pnl for t in losers))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    return {
        "total_signals_scanned": total_signals,
        "a_trades_evaluated": len(a_trades),
        "b_trades_evaluated": len(b_trades),
        "shadow_setups": len(shadow_setups) + len(shadows),
        "rejected_setups": len(rejected),
        "total_trades_executed": total_executed,
        "winners": len(winners),
        "losers": len(losers),
        "win_rate_pct": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
    }


def _rejection_breakdown(rejections: List[RejectedSetup]) -> Dict:
    reason_counts: Dict[str, int] = defaultdict(int)
    for r in rejections:
        if r.rejection_reasons:
            for reason in r.rejection_reasons.split(","):
                reason_counts[reason.strip()] += 1

    return {
        "counts_by_reason": dict(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)),
        "total_rejections": len([r for r in rejections if r.tier == "REJECTED"]),
        "total_shadows_not_executed": len([r for r in rejections if r.tier == "SHADOW"]),
    }


def _group_by_symbol(trades: List[ClosedTrade]) -> Dict:
    by_sym: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
    for t in trades:
        entry = by_sym[t.symbol]
        entry["count"] += 1
        entry["pnl"]   = round(entry["pnl"] + t.pnl, 2)
        if t.is_winner:
            entry["wins"] += 1
        else:
            entry["losses"] += 1
    for sym, entry in by_sym.items():
        total = entry["wins"] + entry["losses"]
        entry["win_rate_pct"] = round(entry["wins"] / total * 100, 1) if total else 0.0
        entry["expectancy"]   = round(entry["pnl"] / total, 2) if total else 0.0
    return dict(sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True))


def _group_by_hour(trades: List[ClosedTrade]) -> Dict:
    by_hour: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        if t.opened_at:
            from zoneinfo import ZoneInfo
            et = t.opened_at.astimezone(ZoneInfo("America/New_York"))
            key = f"{et.hour:02d}:00"
        else:
            key = "unknown"
        by_hour[key]["count"] += 1
        by_hour[key]["pnl"]    = round(by_hour[key]["pnl"] + t.pnl, 2)
        if t.is_winner:
            by_hour[key]["wins"] += 1
    for key, entry in by_hour.items():
        total = entry["count"]
        entry["win_rate_pct"] = round(entry["wins"] / total * 100, 1) if total else 0.0
        entry["expectancy"]   = round(entry["pnl"] / total, 2) if total else 0.0
    return dict(sorted(by_hour.items()))


def _tier_performance(
    trades: List[ClosedTrade],
    rejections: List[RejectedSetup],
    shadows: List[ShadowTrade],
) -> Dict:
    """Per-tier win rate, expectancy, avg win/loss, profit factor."""
    tiers = ["A_TRADE", "B_TRADE", "SHADOW", "scalp"]
    result = {}

    for tier in tiers:
        if tier == "scalp":
            tier_trades = [t for t in trades if (t.strategy_version or "") == "scalp"]
        elif tier == "SHADOW":
            tier_trades = []  # shadow trades don't appear in closed_trades
        else:
            tier_trades = [t for t in trades if (t.strategy_version or "").upper().startswith(tier[:1])]

        if tier == "SHADOW":
            closed_shadows = [s for s in shadows if s.status != "open" and s.pnl_per_share is not None]
            wins    = len([s for s in closed_shadows if s.is_winner])
            losses  = len([s for s in closed_shadows if not s.is_winner])
            avg_w   = sum(s.pnl_per_share for s in closed_shadows if s.is_winner) / wins if wins else 0.0
            avg_l   = sum(s.pnl_per_share for s in closed_shadows if not s.is_winner) / losses if losses else 0.0
            total   = wins + losses
            result[tier] = {
                "count": total,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": round(wins / total * 100, 1) if total else 0.0,
                "avg_win_per_share": round(avg_w, 4),
                "avg_loss_per_share": round(avg_l, 4),
                "note": "hypothetical per-share PnL (no sizing applied)",
            }
            continue

        wins   = [t for t in tier_trades if t.is_winner]
        losses = [t for t in tier_trades if not t.is_winner]
        total  = len(tier_trades)
        pnl    = sum(t.pnl for t in tier_trades)
        avg_w  = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0.0
        avg_l  = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
        gross_w = sum(t.pnl for t in wins)
        gross_l = abs(sum(t.pnl for t in losses))
        pf = gross_w / gross_l if gross_l > 0 else float("inf")

        result[tier] = {
            "count": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / total * 100, 1) if total else 0.0,
            "total_pnl": round(pnl, 2),
            "expectancy": round(pnl / total, 2) if total else 0.0,
            "avg_win": round(avg_w, 2),
            "avg_loss": round(avg_l, 2),
            "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        }

    return result


def _shadow_performance(shadows: List[ShadowTrade]) -> Dict:
    closed = [s for s in shadows if s.status != "open" and s.pnl_per_share is not None]
    if not closed:
        return {"note": "No closed shadow trades yet"}

    wins   = [s for s in closed if s.is_winner]
    losses = [s for s in closed if not s.is_winner]
    total  = len(closed)

    by_exit: Dict[str, int] = defaultdict(int)
    for s in closed:
        by_exit[s.exit_reason or "unknown"] += 1

    avg_mfe = sum(s.max_favorable or 0 for s in closed) / total if total else 0.0
    avg_mae = sum(s.max_adverse   or 0 for s in closed) / total if total else 0.0

    return {
        "total_shadows_closed": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / total * 100, 1) if total else 0.0,
        "avg_pnl_per_share": round(sum(s.pnl_per_share or 0 for s in closed) / total, 4),
        "avg_mfe_per_share": round(avg_mfe, 4),
        "avg_mae_per_share": round(avg_mae, 4),
        "exits_by_reason": dict(by_exit),
        "would_have_been_profitable": len(wins) > len(losses),
    }


def _best_worst_symbols(trades: List[ClosedTrade], n: int, best: bool) -> List[Dict]:
    by_sym = _group_by_symbol(trades)
    sorted_syms = sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=best)
    return [{"symbol": sym, **data} for sym, data in sorted_syms[:n]]


def _best_worst_hours(trades: List[ClosedTrade], n: int, best: bool) -> List[Dict]:
    by_hour = _group_by_hour(trades)
    sorted_hours = sorted(by_hour.items(), key=lambda x: x[1]["expectancy"], reverse=best)
    return [{"hour": h, **data} for h, data in sorted_hours[:n]]


def _frequency_diagnosis(
    trades: List[ClosedTrade],
    rejections: List[RejectedSetup],
    shadows: List[ShadowTrade],
) -> Dict:
    """
    Plain-language diagnosis of what's limiting trade frequency.
    """
    days = len(set(t.session_date for t in trades if t.session_date)) or 1
    avg_per_day = len(trades) / days

    total_signals = len(rejections)
    total_rejected = len([r for r in rejections if r.tier == "REJECTED"])
    total_shadows  = len([r for r in rejections if r.tier == "SHADOW"])
    total_b        = len([r for r in rejections if r.tier == "B_TRADE"])
    total_a        = len([r for r in rejections if r.tier == "A_TRADE"])

    # Top rejection reasons
    reason_counts: Dict[str, int] = defaultdict(int)
    for r in rejections:
        if r.rejection_reasons:
            for reason in r.rejection_reasons.split(","):
                reason_counts[reason.strip()] += 1

    top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    findings = []
    if avg_per_day < 10:
        findings.append(f"Low trade frequency: {avg_per_day:.1f} trades/day across {days} day(s).")

    if total_signals > 0:
        pass_rate = (total_a + total_b) / total_signals * 100
        findings.append(
            f"Signal pass rate: {pass_rate:.1f}% ({total_a} A-trades, {total_b} B-trades "
            f"of {total_signals} evaluated setups)."
        )

    if total_shadows > 0:
        closed_sh = [s for s in shadows if s.is_winner is not None]
        if closed_sh:
            sh_wr = sum(1 for s in closed_sh if s.is_winner) / len(closed_sh) * 100
            findings.append(
                f"Shadow trade win rate: {sh_wr:.1f}% — "
                + ("these setups were probably worth executing."
                   if sh_wr > 45 else
                   "current filtering appears correct for shadow-tier setups.")
            )

    if top_reasons:
        findings.append(
            "Top rejection reasons: "
            + ", ".join(f"{r} ({c}x)" for r, c in top_reasons)
            + "."
        )

    return {
        "avg_trades_per_day": round(avg_per_day, 1),
        "total_days": days,
        "findings": findings,
        "top_rejection_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
    }


# ── Disk persistence ──────────────────────────────────────────────────────────

def _save_report(report: Dict, filename: str) -> None:
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / filename
        path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Diagnostic report saved: %s", path)
    except Exception as exc:
        logger.warning("Could not save diagnostic report: %s", exc)
