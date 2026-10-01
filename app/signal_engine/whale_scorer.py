"""
Whale Signal Scorer  (0–10 points)
────────────────────────────────────
Converts WhaleSentiment data into a ComponentScore for the signal engine.

Score table
───────────
  combined_sentiment  -1.0  →  raw_score = 0.0  (bearish whales)
  combined_sentiment   0.0  →  raw_score = 0.5  (neutral / no data)
  combined_sentiment  +1.0  →  raw_score = 1.0  (bullish whales)

Confidence gates:
  - If confidence < 0.3 (no/very little data), raw_score stays near 0.5
    so the component neither helps nor hurts the total signal.
  - If confidence > 0.6 (both 13F + options available), the score can
    swing the full range and contribute up to ±5 points.
"""
from __future__ import annotations

from app.schemas.signals import ComponentScore
from app.services.whale_service import WhaleSentiment
from app.utils.math_utils import clamp


def score_whale_sentiment(
    whale: WhaleSentiment,
    proposed_direction: int,   # +1 long, -1 short, 0 unknown
    weight: int = 10,
) -> ComponentScore:
    """
    Convert a WhaleSentiment object into a ComponentScore.

    Parameters
    ----------
    whale              : WhaleSentiment from whale_service
    proposed_direction : dominant direction from Phase 1 of the engine (+1/-1/0)
    weight             : point weight in the signal engine (default 10)
    """
    sentiment  = whale.combined_sentiment   # -1..+1
    confidence = whale.confidence           # 0..1

    # Convert sentiment to 0..1 raw score
    # 0.5 = neutral baseline (no opinion)
    raw_midpoint = (sentiment + 1.0) / 2.0   # -1→0, 0→0.5, +1→1.0

    # Blend toward 0.5 when confidence is low
    raw_score = 0.5 + (raw_midpoint - 0.5) * confidence

    raw_score = clamp(raw_score)

    # Determine the direction this component "votes"
    if sentiment > 0.15:
        direction = 1
    elif sentiment < -0.15:
        direction = -1
    else:
        direction = 0

    # Alignment bonus: if whale direction matches proposed trade direction,
    # give a small extra boost to the raw score (max +0.1)
    if direction != 0 and direction == proposed_direction:
        raw_score = clamp(raw_score + 0.08 * confidence)

    weighted = raw_score * weight

    # Build details for UI display
    holders = whale.institutional_holders
    top_holders = [
        {
            "fund":      h["fund_name"],
            "value_m":   round(h["value_usd"] / 1_000_000, 1),
            "shares":    h["shares"],
            "position":  h["put_call"],
            "sentiment": h["sentiment"],
        }
        for h in sorted(holders, key=lambda x: -x["value_usd"])[:6]
    ]

    opts = whale.options
    options_detail: dict = {}
    if opts and not opts.error:
        options_detail = {
            "pcr":           opts.put_call_ratio,
            "call_oi":       opts.call_oi,
            "put_oi":        opts.put_oi,
            "call_volume":   opts.call_volume,
            "put_volume":    opts.put_volume,
            "unusual_calls": opts.unusual_calls[:3],
            "unusual_puts":  opts.unusual_puts[:3],
            "summary":       opts.summary,
        }

    return ComponentScore(
        name="whale",
        raw_score=round(raw_score, 3),
        weight=weight,
        weighted_score=round(weighted, 2),
        direction=direction,
        details={
            "institutional_sentiment": round(sentiment, 2),
            "confidence":              round(confidence, 2),
            "num_funds_long":          whale.num_funds_long,
            "num_funds_put":           whale.num_funds_put,
            "total_value_m":           round(whale.total_value_usd / 1_000_000, 1),
            "top_holders":             top_holders,
            "options":                 options_detail,
            "summary":                 whale.summary,
        },
    )
