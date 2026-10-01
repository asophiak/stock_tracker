"""
Strategy diagnostics and exit-experiment API endpoints.

These endpoints feed the /diagnostics page with the data needed to evaluate
whether the bot has a real edge, independent of outlier trades.

All queries are read-only against the existing closed_trades and
component_accuracy tables — no schema changes required.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.utils.cache import StateManager

router = APIRouter(prefix="/api/diagnostics")

# Hold-time threshold (minutes) beyond which a trade is classified as an
# outlier (overnight / multi-day gap-up hold).  A normal intraday session is
# at most 390 minutes (6h 30m), so anything beyond that indicates an
# overnight carry and is excluded from the "intraday only" equity curve.
_OUTLIER_HOLD_MINS = 390


# ── Equity curve ──────────────────────────────────────────────────────────────

@router.get("/equity-curve")
async def equity_curve(db: AsyncSession = Depends(get_db)):
    """
    Day-by-day cumulative equity in two series:
      all_trades    — every closed trade
      intraday_only — excludes overnight / multi-day holds (> 390 min)

    Each element: { date, equity, daily_pnl }
    """
    result = await db.execute(text("""
        SELECT
            session_date,
            pnl,
            (julianday(closed_at) - julianday(opened_at)) * 1440.0 AS hold_mins
        FROM closed_trades
        WHERE session_date IS NOT NULL
        ORDER BY session_date, closed_at
    """))
    rows = result.fetchall()

    all_by_day: dict[str, float] = defaultdict(float)
    intra_by_day: dict[str, float] = defaultdict(float)

    for session_date, pnl, hold_mins in rows:
        all_by_day[session_date] += pnl
        if hold_mins is not None and hold_mins <= _OUTLIER_HOLD_MINS:
            intra_by_day[session_date] += pnl

    dates = sorted(all_by_day.keys())

    all_cum = 0.0
    intra_cum = 0.0
    series_all: list[dict] = []
    series_intra: list[dict] = []

    for d in dates:
        all_cum   += all_by_day[d]
        intra_cum += intra_by_day.get(d, 0.0)
        series_all.append({
            "date":      d,
            "equity":    round(all_cum, 2),
            "daily_pnl": round(all_by_day[d], 2),
        })
        series_intra.append({
            "date":      d,
            "equity":    round(intra_cum, 2),
            "daily_pnl": round(intra_by_day.get(d, 0.0), 2),
        })

    return {
        "all_trades":              series_all,
        "intraday_only":           series_intra,
        "outlier_threshold_mins":  _OUTLIER_HOLD_MINS,
    }


# ── Score buckets ─────────────────────────────────────────────────────────────

@router.get("/score-buckets")
async def score_buckets(db: AsyncSession = Depends(get_db)):
    """
    Win-rate, gross P&L, profit-factor, and avg hold time for each entry
    score bucket.  Only includes trades that have a valid entry_signal_json.

    Buckets: <60 | 60-64 | 65-69 | 70-74 | 75-79 | 80-84 | 85+
    """
    result = await db.execute(text("""
        SELECT
            CASE
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 60 THEN '<60'
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 65 THEN '60-64'
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 70 THEN '65-69'
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 75 THEN '70-74'
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 80 THEN '75-79'
                WHEN CAST(json_extract(entry_signal_json,'$.total_score') AS REAL) < 85 THEN '80-84'
                ELSE '85+'
            END AS bucket,
            COUNT(*) AS trades,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(SUM(pnl), 2) AS gross_pnl,
            ROUND(
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END)
                / NULLIF(ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)), 0),
            2) AS profit_factor,
            ROUND(AVG((julianday(closed_at) - julianday(opened_at)) * 1440.0), 1) AS avg_hold_mins,
            ROUND(
                SUM(CASE WHEN is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_win,
            ROUND(
                SUM(CASE WHEN NOT is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN NOT is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_loss
        FROM closed_trades
        WHERE entry_signal_json IS NOT NULL
          AND json_extract(entry_signal_json,'$.total_score') IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in rows]


# ── Time buckets ──────────────────────────────────────────────────────────────

@router.get("/time-buckets")
async def time_buckets(db: AsyncSession = Depends(get_db)):
    """
    Win-rate, gross P&L, profit-factor, and avg hold time by time-of-day
    bucket at entry (open / morning / midday / afternoon).
    """
    result = await db.execute(text("""
        SELECT
            COALESCE(time_bucket, 'unknown') AS bucket,
            COUNT(*) AS trades,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(SUM(pnl), 2) AS gross_pnl,
            ROUND(
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END)
                / NULLIF(ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)), 0),
            2) AS profit_factor,
            ROUND(AVG((julianday(closed_at) - julianday(opened_at)) * 1440.0), 1) AS avg_hold_mins,
            ROUND(
                SUM(CASE WHEN is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_win,
            ROUND(
                SUM(CASE WHEN NOT is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN NOT is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_loss
        FROM closed_trades
        GROUP BY time_bucket
        ORDER BY
            CASE COALESCE(time_bucket, 'unknown')
                WHEN 'open'      THEN 1
                WHEN 'morning'   THEN 2
                WHEN 'midday'    THEN 3
                WHEN 'afternoon' THEN 4
                ELSE 5
            END
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in rows]


# ── Component accuracy ────────────────────────────────────────────────────────

@router.get("/component-accuracy")
async def component_accuracy(db: AsyncSession = Depends(get_db)):
    """
    Per-component directional accuracy split by time-of-day bucket (used as a
    proxy for market phase: open / morning / midday / afternoon).

    Returns:
      phases     — ordered list of phase names
      components — { component_name: { phase: { total, correct, accuracy_pct } } }
    """
    result = await db.execute(text("""
        SELECT
            ca.component_name,
            COALESCE(ct.time_bucket, 'unknown') AS market_phase,
            COUNT(*)                                                      AS total,
            SUM(CASE WHEN ca.was_correct THEN 1 ELSE 0 END)              AS correct,
            ROUND(
                100.0 * SUM(CASE WHEN ca.was_correct THEN 1 ELSE 0 END)
                / COUNT(*),
            1) AS accuracy_pct
        FROM component_accuracy ca
        JOIN closed_trades ct ON ca.trade_id = ct.id
        GROUP BY ca.component_name, ct.time_bucket
        ORDER BY ca.component_name, ct.time_bucket
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    raw = [dict(zip(cols, row)) for row in rows]

    shaped: dict[str, dict] = defaultdict(dict)
    all_phases: set[str] = set()

    for r in raw:
        shaped[r["component_name"]][r["market_phase"]] = {
            "total":        r["total"],
            "correct":      r["correct"],
            "accuracy_pct": r["accuracy_pct"],
        }
        all_phases.add(r["market_phase"])

    _phase_order = {"open": 1, "morning": 2, "midday": 3, "afternoon": 4}
    ordered_phases = sorted(all_phases, key=lambda p: _phase_order.get(p, 5))

    return {
        "phases":     ordered_phases,
        "components": dict(shaped),
    }


# ── Exit experiments ─────────────────────────────────────────────────────────

@router.get("/exit-experiments")
async def exit_experiments(db: AsyncSession = Depends(get_db)):
    """
    Run all exit-rule variants against the full closed_trades dataset.

    Returns a ranked list of experiment results (ranked by intraday P&L).
    Does NOT modify any live strategy code.

    Each result includes:
      variant_id, variant_name, variant_desc, sim_note,
      affected_trades, rank,
      trades, win_rate, gross_pnl, intraday_pnl,
      avg_win, avg_loss, profit_factor, max_drawdown,
      target_hit_count, avg_hold_mins, worst_exit
    """
    from app.services.experiment_service import run_experiments

    result = await db.execute(text("""
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
    """))
    rows = result.fetchall()
    cols = list(result.keys())

    trades = []
    for row in rows:
        d = dict(zip(cols, row))
        d["is_winner"] = bool(d["is_winner"])
        trades.append(d)

    return run_experiments(trades)


# ── Thesis coverage ──────────────────────────────────────────────────────────

@router.get("/thesis-coverage")
async def thesis_coverage(db: AsyncSession = Depends(get_db)):
    """
    Thesis quality breakdown for all closed trades.

    Returns:
      by_session — list of { session_date, quality, trades, gross_pnl, win_rate }
      overall    — aggregated { quality, trades, gross_pnl, win_rate } per quality tier
    """
    result = await db.execute(text("""
        SELECT
            COALESCE(session_date, 'unknown')                                 AS session_date,
            COALESCE(thesis_quality, 'missing_thesis')                        AS quality,
            COUNT(*)                                                           AS trades,
            ROUND(SUM(pnl), 2)                                                AS gross_pnl,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                                               AS win_rate,
            SUM(CASE WHEN is_winner THEN 1 ELSE 0 END)                        AS wins
        FROM closed_trades
        GROUP BY session_date, thesis_quality
        ORDER BY session_date, quality
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    raw = [dict(zip(cols, row)) for row in rows]

    # Aggregate overall across all sessions
    overall: dict[str, dict] = {}
    for r in raw:
        q = r["quality"]
        if q not in overall:
            overall[q] = {"quality": q, "trades": 0, "gross_pnl": 0.0, "wins": 0}
        overall[q]["trades"]    += r["trades"]
        overall[q]["gross_pnl"] += r["gross_pnl"] or 0.0
        overall[q]["wins"]      += r["wins"] or 0

    for d in overall.values():
        d["gross_pnl"] = round(d["gross_pnl"], 2)
        d["win_rate"]  = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0.0

    # Order: valid > partial > missing_thesis
    _q_order = {"valid": 0, "partial": 1, "missing_thesis": 2}
    overall_list = sorted(overall.values(), key=lambda x: _q_order.get(x["quality"], 9))

    return {
        "by_session": raw,
        "overall":    overall_list,
    }


# ── Clean data monitor ───────────────────────────────────────────────────────

_POST_4B_MIN_TRADES = 30   # warn below this sample size

@router.get("/clean-data")
async def clean_data(
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    """
    Phase 4C clean-data monitoring endpoint.

    Returns:
      strategy_breakdown  — stats for pre_4b_all / pre_4b_valid / post_4b cohorts
      rejection_summary   — live-session thesis rejection counters from StateManager
      daily_report        — per-session: entries, TP/stop/reversal exits, expectancy
    """

    # ── Per-cohort stats ──────────────────────────────────────────────────────
    cohort_result = await db.execute(text("""
        SELECT
            strategy_version,
            thesis_quality,
            COUNT(*)                                                            AS trades,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                                                AS win_rate,
            ROUND(SUM(pnl), 2)                                                 AS gross_pnl,
            ROUND(SUM(CASE WHEN (julianday(closed_at) - julianday(opened_at))
                              * 1440.0 <= 390 THEN pnl ELSE 0 END), 2)        AS intraday_pnl,
            ROUND(AVG(pnl), 2)                                                 AS expectancy,
            ROUND(
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END)
                / NULLIF(ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)), 0),
            2)                                                                  AS profit_factor,
            ROUND(AVG((julianday(closed_at) - julianday(opened_at)) * 1440.0), 1)
                                                                                AS avg_hold_mins,
            SUM(CASE WHEN close_reason = 'target_hit'              THEN 1 ELSE 0 END) AS tp_hits,
            SUM(CASE WHEN close_reason = 'stop_hit'                THEN 1 ELSE 0 END) AS stop_hits,
            SUM(CASE WHEN close_reason = 'signal_reversed_bearish' THEN 1 ELSE 0 END) AS bearish_reversals
        FROM closed_trades
        GROUP BY strategy_version, thesis_quality
        ORDER BY strategy_version, thesis_quality
    """))
    cohort_rows = [dict(zip(cohort_result.keys(), r)) for r in cohort_result.fetchall()]

    def _agg(rows: list[dict]) -> dict:
        """Aggregate a list of cohort rows into one summary."""
        if not rows:
            return {}
        t = sum(r["trades"] for r in rows)
        wins = sum(round((r["win_rate"] or 0) * r["trades"] / 100) for r in rows)
        gross = sum(r["gross_pnl"] or 0 for r in rows)
        intra = sum(r["intraday_pnl"] or 0 for r in rows)
        tp    = sum(r["tp_hits"] or 0 for r in rows)
        stop  = sum(r["stop_hits"] or 0 for r in rows)
        rev   = sum(r["bearish_reversals"] or 0 for r in rows)
        win_pnl  = sum(r["gross_pnl"] for r in rows if (r["gross_pnl"] or 0) > 0)
        loss_pnl = abs(sum(r["gross_pnl"] for r in rows if (r["gross_pnl"] or 0) < 0))
        return {
            "trades":            t,
            "win_rate":          round(wins / t * 100, 1) if t else 0.0,
            "gross_pnl":         round(gross, 2),
            "intraday_pnl":      round(intra, 2),
            "expectancy":        round(gross / t, 2) if t else 0.0,
            "profit_factor":     round(win_pnl / loss_pnl, 2) if loss_pnl > 0 else None,
            "tp_hits":           tp,
            "stop_hits":         stop,
            "bearish_reversals": rev,
        }

    pre_4b_all   = _agg([r for r in cohort_rows if r["strategy_version"] == "pre_4b"])
    pre_4b_valid = _agg([r for r in cohort_rows
                         if r["strategy_version"] == "pre_4b"
                         and r["thesis_quality"] == "valid"])
    post_4b_rows = [r for r in cohort_rows if r["strategy_version"] == "4b"]
    post_4b      = _agg(post_4b_rows)

    post_4b_trade_count = post_4b.get("trades", 0)
    post_4b["small_sample_warning"] = (
        post_4b_trade_count < _POST_4B_MIN_TRADES
    )
    post_4b["small_sample_msg"] = (
        f"Only {post_4b_trade_count} post-Phase-4B trades — "
        f"need {_POST_4B_MIN_TRADES} for reliable strategy conclusions."
        if post_4b["small_sample_warning"] else ""
    )

    strategy_breakdown = {
        "pre_4b_all":   pre_4b_all,
        "pre_4b_valid": pre_4b_valid,
        "post_4b":      post_4b,
    }

    # ── Rejection summary (live StateManager data) ────────────────────────────
    by_symbol = sorted(
        sm.thesis_rejection_by_symbol.items(), key=lambda kv: kv[1], reverse=True
    )
    by_bucket = sorted(
        sm.thesis_rejection_by_bucket.items(), key=lambda kv: kv[1], reverse=True
    )
    rejection_summary = {
        "total_rejections":  sm.thesis_rejections,
        "top_symbols":  [{"symbol": s, "rejections": n} for s, n in by_symbol[:5]],
        "top_buckets":  [{"bucket": b, "rejections": n} for b, n in by_bucket],
        "recent_log":   sm.thesis_rejection_log[-10:],
        "note": "Rejection counts reset on server restart — live session only.",
    }

    # ── Daily clean-data report ───────────────────────────────────────────────
    daily_result = await db.execute(text("""
        SELECT
            COALESCE(session_date, 'unknown')                                   AS session_date,
            COALESCE(strategy_version, 'pre_4b')                               AS strategy_version,
            COUNT(*)                                                             AS entries,
            ROUND(100.0 * SUM(CASE WHEN is_winner     THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                                                 AS win_rate,
            ROUND(SUM(pnl), 2)                                                  AS gross_pnl,
            ROUND(AVG(pnl), 2)                                                  AS expectancy,
            ROUND(100.0 * SUM(CASE WHEN thesis_quality = 'valid' THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                                                 AS valid_thesis_rate,
            SUM(CASE WHEN close_reason = 'target_hit'              THEN 1 ELSE 0 END) AS tp_hits,
            SUM(CASE WHEN close_reason = 'stop_hit'                THEN 1 ELSE 0 END) AS stop_hits,
            SUM(CASE WHEN close_reason = 'signal_reversed_bearish' THEN 1 ELSE 0 END) AS bearish_reversals,
            SUM(CASE WHEN close_reason NOT IN (
                          'target_hit','stop_hit','signal_reversed_bearish',
                          'eod_flatten','manual')
                     THEN 1 ELSE 0 END)                                         AS other_exits
        FROM closed_trades
        GROUP BY session_date, strategy_version
        ORDER BY session_date DESC, strategy_version
    """))
    daily_report = [dict(zip(daily_result.keys(), r)) for r in daily_result.fetchall()]

    return {
        "strategy_breakdown": strategy_breakdown,
        "rejection_summary":  rejection_summary,
        "daily_report":       daily_report,
        "post_4b_min_trades": _POST_4B_MIN_TRADES,
    }


# ── Exit analysis ─────────────────────────────────────────────────────────────

@router.get("/exit-analysis")
async def exit_analysis(db: AsyncSession = Depends(get_db)):
    """
    P&L, win-rate, avg P&L per trade, and average hold time broken down by
    exit reason (close_reason column on closed_trades).
    """
    result = await db.execute(text("""
        SELECT
            COALESCE(close_reason, 'unknown')                            AS exit_reason,
            COUNT(*)                                                     AS trades,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END)
                / COUNT(*), 1)                                           AS win_rate,
            ROUND(SUM(pnl), 2)                                          AS gross_pnl,
            ROUND(AVG(pnl), 2)                                          AS avg_pnl,
            ROUND(AVG((julianday(closed_at) - julianday(opened_at)) * 1440.0), 1)
                                                                         AS avg_hold_mins,
            ROUND(
                SUM(CASE WHEN is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_win,
            ROUND(
                SUM(CASE WHEN NOT is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN NOT is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_loss
        FROM closed_trades
        GROUP BY close_reason
        ORDER BY trades DESC
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in rows]


# ── Trade tier performance ────────────────────────────────────────────────────

@router.get("/tier-performance")
async def tier_performance(db: AsyncSession = Depends(get_db)):
    """
    Win rate, expectancy, avg win/loss, and profit factor broken down by
    trade tier (strategy_version: A | B | scalp | pre_4b / 4b / legacy).
    Helps compare old model vs A-only vs A+B vs scalp.
    """
    result = await db.execute(text("""
        SELECT
            COALESCE(strategy_version, 'legacy')                         AS tier,
            COUNT(*)                                                      AS trades,
            ROUND(100.0 * SUM(CASE WHEN is_winner THEN 1 ELSE 0 END)
                / COUNT(*), 1)                                            AS win_rate,
            ROUND(SUM(pnl), 2)                                           AS gross_pnl,
            ROUND(AVG(pnl), 2)                                           AS expectancy,
            ROUND(
                SUM(CASE WHEN is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_win,
            ROUND(
                SUM(CASE WHEN NOT is_winner THEN pnl ELSE 0 END)
                / NULLIF(SUM(CASE WHEN NOT is_winner THEN 1 ELSE 0 END), 0),
            2) AS avg_loss,
            ROUND(
                SUM(CASE WHEN is_winner THEN pnl ELSE 0 END)
                / NULLIF(ABS(SUM(CASE WHEN NOT is_winner THEN pnl ELSE 0 END)), 0),
            2) AS profit_factor
        FROM closed_trades
        GROUP BY strategy_version
        ORDER BY gross_pnl DESC
    """))
    rows = result.fetchall()
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in rows]


# ── Rejected setup log ────────────────────────────────────────────────────────

@router.get("/rejected-setups")
async def rejected_setups(
    session_date: str | None = None,
    tier: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    """
    Return recent rejected / shadow-tier setups from the rejected_setups table.
    Filter by session_date and/or tier (A_TRADE, B_TRADE, SHADOW, REJECTED).
    """
    from sqlalchemy import select
    from app.models.trading import RejectedSetup

    stmt = select(RejectedSetup).order_by(RejectedSetup.evaluated_at.desc()).limit(limit)
    if session_date:
        stmt = stmt.where(RejectedSetup.session_date == session_date)
    if tier:
        stmt = stmt.where(RejectedSetup.tier == tier.upper())

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "evaluated_at": r.evaluated_at.isoformat() if r.evaluated_at else None,
            "session_date": r.session_date,
            "symbol": r.symbol,
            "price": r.price,
            "direction": r.direction,
            "score": r.score,
            "tier": r.tier,
            "rejection_reasons": r.rejection_reasons,
            "vwap": r.vwap,
            "vwap_condition": r.vwap_condition,
            "rsi": r.rsi,
            "market_regime": r.market_regime,
            "suggested_stop": r.suggested_stop,
            "suggested_target": r.suggested_target,
            "outcome_pnl": r.outcome_pnl,
            "outcome_winner": r.outcome_winner,
        }
        for r in rows
    ]


# ── Shadow trade log ──────────────────────────────────────────────────────────

@router.get("/shadow-trades")
async def shadow_trades_endpoint(
    session_date: str | None = None,
    status: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    """
    Return shadow trades (hypothetical setups that were tracked but not executed).
    """
    from sqlalchemy import select
    from app.models.trading import ShadowTrade

    stmt = select(ShadowTrade).order_by(ShadowTrade.entered_at.desc()).limit(limit)
    if session_date:
        stmt = stmt.where(ShadowTrade.session_date == session_date)
    if status:
        stmt = stmt.where(ShadowTrade.status == status)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "session_date": r.session_date,
            "symbol": r.symbol,
            "strategy": r.strategy,
            "direction": r.direction,
            "score": r.score,
            "entry_price": r.entry_price,
            "stop_price": r.stop_price,
            "target_price": r.target_price,
            "entered_at": r.entered_at.isoformat() if r.entered_at else None,
            "max_favorable": r.max_favorable,
            "max_adverse": r.max_adverse,
            "exit_price": r.exit_price,
            "exit_reason": r.exit_reason,
            "pnl_per_share": r.pnl_per_share,
            "is_winner": r.is_winner,
            "exited_at": r.exited_at.isoformat() if r.exited_at else None,
            "status": r.status,
        }
        for r in rows
    ]


# ── Frequency diagnostic report ───────────────────────────────────────────────

@router.get("/frequency-report")
async def frequency_report(
    session_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Full trade-frequency diagnostic report for one session day (or today).
    Shows why we're getting a certain number of trades, top rejection reasons,
    best/worst symbols and time windows, and per-tier performance.
    """
    from app.services.diagnostic_report_service import generate_daily_report
    return await generate_daily_report(db, session_date=session_date, save_to_disk=False)


# ── Rejection breakdown summary ───────────────────────────────────────────────

@router.get("/rejection-summary")
async def rejection_summary(
    session_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Summary of why setups were rejected, grouped by reason.
    Useful for diagnosing over-filtering.
    """
    from sqlalchemy import select
    from app.models.trading import RejectedSetup

    stmt = select(RejectedSetup)
    if session_date:
        stmt = stmt.where(RejectedSetup.session_date == session_date)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    from collections import defaultdict
    reason_counts: dict = defaultdict(int)
    tier_counts: dict = defaultdict(int)

    for r in rows:
        tier_counts[r.tier] = tier_counts.get(r.tier, 0) + 1
        if r.rejection_reasons:
            for reason in r.rejection_reasons.split(","):
                reason_counts[reason.strip()] += 1

    return {
        "total_setups_evaluated": len(rows),
        "by_tier": dict(sorted(tier_counts.items(), key=lambda x: x[1], reverse=True)),
        "by_rejection_reason": dict(
            sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)
        ),
    }
