"""
Bot Learning Service
────────────────────
After every trade closes the bot writes a detailed post-mortem:

  • Grade  (A–F)  based on execution quality
  • Lesson        IQ-160-style root-cause analysis
  • Component accuracy  stored per-trade so weights can adapt over time

Adaptive weights:
  Each signal component gets a running accuracy score.
  After ≥ 15 trades for a symbol, score_symbol() uses learned weights
  instead of the static config defaults.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trading import ClosedTrade, ComponentAccuracy

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_ADAPTATION = 15   # need this many trades before adjusting weights


# ── Time bucket ───────────────────────────────────────────────────────────────

def _time_bucket(dt: datetime) -> str:
    """Categorise entry time into named market sessions (ET)."""
    from zoneinfo import ZoneInfo
    # Ensure timezone-aware before converting — SQLite server_default returns naive UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt.astimezone(ZoneInfo("America/New_York"))
    mins_since_open = (et.hour * 60 + et.minute) - 570  # minutes after 9:30 ET
    if mins_since_open < 0:
        return "premarket"
    elif mins_since_open < 60:    # 9:30 – 10:30
        return "open"
    elif mins_since_open < 150:   # 10:30 – 12:00
        return "morning"
    elif mins_since_open < 270:   # 12:00 – 14:30
        return "midday"
    else:                          # 14:30 – 16:00
        return "afternoon"


# ── Trade grade ───────────────────────────────────────────────────────────────

def _grade_trade(trade: ClosedTrade, signal_data: dict) -> str:
    """
    Grade execution quality A–F.

    A  Target hit — perfect execution
    B  Profitable, clean exit on signal reversal
    C  Small loss or break-even; entry was marginal
    D  Stop hit; valid setup, poor entry timing
    F  Rapid stop-out or entered against signal
    """
    reason = trade.close_reason or ""
    pnl    = trade.pnl
    score  = signal_data.get("total_score", 0)

    if reason == "target_hit":
        return "A"

    if pnl > 0:
        return "B"

    # Loss branch
    opened = trade.opened_at if trade.opened_at.tzinfo else trade.opened_at.replace(tzinfo=timezone.utc)
    closed = trade.closed_at if trade.closed_at.tzinfo else trade.closed_at.replace(tzinfo=timezone.utc)
    hold_secs = (closed - opened).total_seconds()

    if reason == "stop_hit":
        if hold_secs < 90:
            return "F"   # Stopped within 90 seconds — extremely poor timing
        if score >= 80:
            return "D"   # High conviction but still lost
        return "C"

    if reason in ("signal_reversed_bearish", "signal_reversed_bullish", "signal_exhausted"):
        return "C"

    if reason == "eod_flatten":
        return "C"

    return "D"


# ── Lesson generation ─────────────────────────────────────────────────────────

def generate_lesson(
    trade: ClosedTrade,
    signal_data: dict,
    pattern_stats: Dict[str, dict],
    time_stats:    Dict[str, dict],
) -> str:
    """
    Generate an IQ-160 trader post-mortem lesson for one closed trade.
    Returns a multi-sentence plain-English analysis.
    """
    pnl      = trade.pnl
    reason   = trade.close_reason or "unknown"
    side     = trade.side
    patterns = signal_data.get("patterns", [])
    score    = signal_data.get("total_score", 0)
    bucket   = trade.time_bucket or "unknown"
    # Normalise both ends to UTC-aware before subtracting — SQLite server_default
    # returns naive datetimes which would raise TypeError against aware ones.
    opened = trade.opened_at if trade.opened_at.tzinfo else trade.opened_at.replace(tzinfo=timezone.utc)
    closed = trade.closed_at if trade.closed_at.tzinfo else trade.closed_at.replace(tzinfo=timezone.utc)
    hold_secs= (closed - opened).total_seconds()
    hold_str = f"{int(hold_secs//60)}m {int(hold_secs%60)}s"
    vwap_pos = signal_data.get("vwap_position", "")

    lines: List[str] = []

    if pnl > 0:
        # ── WIN ───────────────────────────────────────────────────────────────
        if reason == "target_hit":
            lines.append(f"Target hit in {hold_str} — flawless execution. The thesis played out exactly as planned.")
        else:
            lines.append(f"Profitable exit in {hold_str} via {reason.replace('_',' ')}.")

        if score >= 85:
            lines.append(f"FLASH signal (score {score:.0f}) delivered. High-conviction setups continue to perform — maintain trust in scores ≥ 85.")
        elif score >= 70:
            lines.append(f"Solid GREEN signal (score {score:.0f}) confirmed. The multi-component agreement was key.")

        bucket_wr = time_stats.get(bucket, {}).get("win_pct", 0)
        if bucket == "open" and bucket_wr >= 55:
            lines.append(f"Opening-hour entry — historically the highest-probability window ({bucket_wr:.0f}% win rate). Continue prioritising this session.")

        for p in patterns:
            p_stats = pattern_stats.get(p, {})
            if p_stats.get("total", 0) >= 3:
                lines.append(f"Pattern '{p.replace('_',' ')}' contributed — current win rate {p_stats.get('win_pct',50):.0f}% across {p_stats['total']} occurrences.")

    else:
        # ── LOSS ──────────────────────────────────────────────────────────────

        # 1. Speed of loss
        if hold_secs < 120:
            lines.append(
                f"Stopped out in {hold_str} — entry was likely too aggressive or placed in a high-noise zone. "
                "Patience: wait for the candle to close and confirm the pattern before entry."
            )
        elif reason == "stop_hit":
            lines.append(f"Stop triggered after {hold_str}. The initial thesis did not materialise.")

        # 2. High conviction failure
        if score >= 85:
            lines.append(
                f"FLASH signal (score {score:.0f}) failed — when high-conviction setups lose it usually means "
                "an intraday regime shift. Always check SPY/QQQ alignment and news tape before entry on FLASH signals."
            )
        elif score >= 70:
            lines.append(f"GREEN signal (score {score:.0f}) did not follow through. Review which components disagreed.")

        # 3. Time-of-day trap
        if bucket == "midday":
            bwr = time_stats.get("midday", {}).get("win_pct", 35)
            lines.append(
                f"Midday entry (12:00–14:30 ET) — historically the lowest-probability window "
                f"({bwr:.0f}% win rate in your history). Reduce position size or skip midday setups entirely."
            )
        elif bucket == "morning":
            lines.append("Morning session entry. Check if volume was decelerating — morning fades are common traps.")

        # 4. VWAP alignment error
        if vwap_pos == "below" and side == "long":
            lines.append(
                "Entered LONG while price was below VWAP. Buying against the mean is a low-probability play. "
                "Wait for a clean VWAP reclaim (close above VWAP) before entering longs."
            )
        elif vwap_pos == "above" and side == "short":
            lines.append(
                "Entered SHORT while price was above VWAP. Wait for VWAP to break down and hold as resistance "
                "before shorting — reduces false-breakout stops significantly."
            )

        # 5. Pattern performance
        for p in patterns:
            p_stats = pattern_stats.get(p, {})
            if p_stats.get("total", 0) >= 5 and p_stats.get("win_pct", 50) < 45:
                lines.append(
                    f"Pattern '{p.replace('_',' ')}' has only {p_stats['win_pct']:.0f}% win rate in your history "
                    f"({p_stats['total']} trades). Treat it as a warning sign, not a trigger."
                )

        # 6. Components that disagreed
        components = signal_data.get("components", [])
        disagreeing = [
            c["name"] for c in components
            if c.get("direction", 0) != signal_data.get("direction", 0)
            and c.get("direction", 0) != 0
        ]
        if disagreeing:
            names = ", ".join(d.replace("_"," ") for d in disagreeing[:3])
            lines.append(
                f"Component(s) disagreed at entry: {names}. "
                "Conflicting components are a red flag — require unanimous agreement before future entries."
            )

    if not lines:
        lines.append(
            f"Trade held {hold_str}, exited via {reason.replace('_',' ')}. "
            "Review entry conditions for patterns to refine future setups."
        )

    return " ".join(lines)


# ── Build entry signal snapshot ───────────────────────────────────────────────

def build_entry_snapshot(signal) -> str:
    """
    Serialise a SignalScore into a compact JSON string for storage.
    `signal` is an app.schemas.signals.SignalScore instance.
    """
    from app.schemas.signals import SignalScore

    if signal is None:
        return "{}"

    # Extract candlestick patterns list
    patterns: List[str] = []
    vwap_pos = ""
    comp_list = []

    for c in (signal.components or []):
        details = c.details or {}
        if c.name == "candlestick":
            patterns = [p["name"] for p in details.get("patterns", [])]
        if c.name == "vwap":
            vwap_pos = "above" if details.get("above_vwap") else "below"
        comp_list.append({
            "name":      c.name,
            "raw_score": round(c.raw_score, 3),
            "direction": c.direction,
            "weight":    c.weight,
        })

    snapshot = {
        "total_score":  round(signal.total_score, 1),
        "direction":    signal.direction,
        "color":        signal.color.value if hasattr(signal.color, "value") else str(signal.color),
        "label":        signal.label.value if hasattr(signal.label, "value") else str(signal.label),
        "price":        signal.price,
        "vwap":         signal.vwap,
        "vwap_position": vwap_pos,
        "patterns":     patterns,
        "components":   comp_list,
        "scored_at":    signal.scored_at.isoformat() if signal.scored_at else None,
        # ── Thesis quality fields (Phase 4B) ──────────────────────────────────
        "thesis_source":    getattr(signal.thesis, "thesis_source",    None),
        "suggested_stop":   getattr(signal.thesis, "suggested_stop",   None),
        "suggested_target": getattr(signal.thesis, "suggested_target", None),
        "risk_reward":      getattr(signal.thesis, "risk_reward",      None),
        "risk_per_share":   getattr(signal.thesis, "risk_per_share",   None),
    }
    return json.dumps(snapshot)


# ── Persist component accuracy ────────────────────────────────────────────────

async def record_component_accuracy(
    db: AsyncSession,
    trade: ClosedTrade,
    signal_data: dict,
) -> None:
    """
    For each signal component, record whether its direction vote was correct.
    A vote is "correct" if it agreed with the eventual winning direction.
    """
    if abs(trade.pnl) < 0.01:
        return  # break-even — not informative for component accuracy

    winning_direction = 0
    if trade.pnl > 0:
        winning_direction = 1 if trade.side == "long" else -1
    elif trade.pnl < 0:
        winning_direction = -1 if trade.side == "long" else 1

    for comp in signal_data.get("components", []):
        comp_dir = comp.get("direction", 0)
        correct  = (comp_dir == winning_direction) if (comp_dir != 0 and winning_direction != 0) else False
        record   = ComponentAccuracy(
            trade_id          = trade.id,
            symbol            = trade.symbol,
            component_name    = comp["name"],
            component_direction = comp_dir,
            was_correct       = correct,
            trade_won         = trade.pnl > 0,
            session_date      = trade.session_date,
        )
        db.add(record)


# ── Query helpers ─────────────────────────────────────────────────────────────

async def get_pattern_win_rates(db: AsyncSession) -> Dict[str, dict]:
    """
    Return {pattern_name: {total, wins, win_pct}} across all closed trades.
    """
    result = await db.execute(
        select(ClosedTrade).where(ClosedTrade.entry_signal_json.isnot(None))
    )
    trades = list(result.scalars().all())

    counts: Dict[str, list] = {}
    for t in trades:
        try:
            sig = json.loads(t.entry_signal_json or "{}")
        except Exception:
            continue
        for p in sig.get("patterns", []):
            if p not in counts:
                counts[p] = [0, 0]
            counts[p][0] += 1
            if t.is_winner:
                counts[p][1] += 1

    return {
        p: {
            "total":   v[0],
            "wins":    v[1],
            "losses":  v[0] - v[1],
            "win_pct": round(v[1] / v[0] * 100, 1) if v[0] else 0,
        }
        for p, v in counts.items()
        if v[0] >= 2
    }


async def get_time_win_rates(db: AsyncSession) -> Dict[str, dict]:
    """Return win rates by time bucket (open/morning/midday/afternoon)."""
    result = await db.execute(select(ClosedTrade))
    trades = list(result.scalars().all())

    counts: Dict[str, list] = {}
    for t in trades:
        bucket = t.time_bucket or "unknown"
        if bucket not in counts:
            counts[bucket] = [0, 0]
        counts[bucket][0] += 1
        if t.is_winner:
            counts[bucket][1] += 1

    return {
        b: {
            "total":   v[0],
            "wins":    v[1],
            "losses":  v[0] - v[1],
            "win_pct": round(v[1] / v[0] * 100, 1) if v[0] else 0,
        }
        for b, v in counts.items()
    }


async def get_adaptive_weights(db: AsyncSession, symbol: str) -> Optional[Dict[str, float]]:
    """
    Return adapted signal weights for `symbol` based on component accuracy history.
    Returns None if not enough trades exist yet (< MIN_TRADES_FOR_ADAPTATION).

    Weight adjustment: components with ≥ 65% accuracy get +20% boost;
    components with ≤ 40% accuracy get -20% penalty.
    Weights are renormalised to the original sum.
    """
    from app.config import settings

    result = await db.execute(
        select(ComponentAccuracy)
        .where(ComponentAccuracy.symbol == symbol)
        .order_by(ComponentAccuracy.id.desc())
        .limit(200)  # last 200 component records = last ~30 trades
    )
    records = list(result.scalars().all())

    if not records:
        return None

    # Count per component
    counts: Dict[str, list] = {}   # {name: [total, correct]}
    for r in records:
        n = r.component_name
        if n not in counts:
            counts[n] = [0, 0]
        counts[n][0] += 1
        if r.was_correct:
            counts[n][1] += 1

    # Need MIN_TRADES_FOR_ADAPTATION per component
    if any(v[0] < MIN_TRADES_FOR_ADAPTATION for v in counts.values()):
        return None

    default_weights = {
        "technical_trend": settings.WEIGHT_TECHNICAL_TREND,
        "candlestick":     settings.WEIGHT_CANDLESTICK,
        "volume":          settings.WEIGHT_VOLUME,
        "vwap":            settings.WEIGHT_VWAP,
        "market_regime":   settings.WEIGHT_MARKET_REGIME,
        "news":            settings.WEIGHT_NEWS,
        "whale":           settings.WEIGHT_WHALE,
    }

    adjusted = {}
    for name, w in default_weights.items():
        if name in counts and counts[name][0] > 0:
            acc = counts[name][1] / counts[name][0]
            if acc >= 0.65:
                adjusted[name] = w * 1.40
            elif acc <= 0.40:
                adjusted[name] = w * 0.65
            else:
                adjusted[name] = float(w)
        else:
            adjusted[name] = float(w)

    # Renormalise so sum == sum(defaults)
    total_orig = sum(default_weights.values())
    total_adj  = sum(adjusted.values())
    if total_adj > 0:
        scale = total_orig / total_adj
        adjusted = {k: round(v * scale, 2) for k, v in adjusted.items()}

    logger.debug("Adaptive weights for %s: %s", symbol, adjusted)
    return adjusted


# ── Main entry point — called after every trade closes ───────────────────────

async def analyze_and_annotate(
    db: AsyncSession,
    trade: ClosedTrade,
) -> None:
    """
    Generate lesson + grade, store component accuracy.
    Called immediately after a trade is written to the DB.
    """
    try:
        signal_data = json.loads(trade.entry_signal_json or "{}")

        # Time bucket
        if not trade.time_bucket:
            trade.time_bucket = _time_bucket(trade.opened_at)

        # Fetch historical stats for context-aware lessons
        pattern_stats = await get_pattern_win_rates(db)
        time_stats    = await get_time_win_rates(db)

        # Grade + lesson
        trade.grade  = _grade_trade(trade, signal_data)
        trade.lesson = generate_lesson(trade, signal_data, pattern_stats, time_stats)

        # Flush immediately so async SQLAlchemy tracks grade/lesson as dirty
        # before the caller's db.commit() fires. Without this, the changes can
        # be silently lost when the session expires the object.
        await db.flush()

        # Component accuracy records
        await record_component_accuracy(db, trade, signal_data)

        logger.info(
            "Learning: %s %s | grade=%s | %.0f pts | %s",
            trade.symbol, "WIN" if trade.pnl > 0 else "LOSS",
            trade.grade, signal_data.get("total_score", 0),
            trade.close_reason,
        )

    except Exception as exc:
        logger.warning("Learning annotation failed for trade %s: %s", trade.id, exc)
