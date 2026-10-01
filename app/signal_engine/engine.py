"""
Main signal engine orchestrator.

Scores a single symbol by running all sub-modules and aggregating
their weighted results into a final SignalScore with color/label/thesis.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.schemas.market_data import SymbolState
from app.schemas.news import NewsItemSchema
from app.schemas.signals import (
    ComponentScore,
    SignalColor,
    SignalScore,
    TradeLabel,
    TradingThesis,
)
from app.signal_engine.candlestick import detect_all_patterns
from app.signal_engine.market_regime import score_market_regime
from app.signal_engine.news_scorer import assess_news_bundle
from app.signal_engine.technical import score_technical_trend
from app.signal_engine.thesis import build_thesis
from app.signal_engine.volume import score_volume
from app.signal_engine.vwap_scorer import score_vwap
from app.signal_engine.whale_scorer import score_whale_sentiment
from app.services.whale_service import get_whale_data

logger = logging.getLogger(__name__)


def _candlestick_score(state: SymbolState, weight: int = 20) -> ComponentScore:
    """Detect patterns on 1m bars with context, return a ComponentScore."""
    bars = state.bars_1m
    patterns = detect_all_patterns(
        bars,
        vwap=state.vwap,
        orh=state.opening_range_high,
        orl=state.opening_range_low,
        session_high=state.session_high,
    )

    if not patterns:
        return ComponentScore(
            name="candlestick",
            raw_score=0.3,   # neutral baseline
            weight=weight,
            weighted_score=0.3 * weight,
            direction=0,
            details={"patterns": [], "reason": "no_patterns_detected"},
        )

    # Tally direction-weighted strengths
    bull_score = sum(p.strength for p in patterns if p.direction == 1)
    bear_score = sum(p.strength for p in patterns if p.direction == -1)

    if bull_score > bear_score:
        direction = 1
        bull_count = sum(1 for p in patterns if p.direction == 1)
        raw_score = min(1.0, bull_score / max(bull_count, 1))
    elif bear_score > bull_score:
        direction = -1
        bear_count = sum(1 for p in patterns if p.direction == -1)
        raw_score = min(1.0, bear_score / max(bear_count, 1))
    else:
        direction = 0
        raw_score = 0.35

    return ComponentScore(
        name="candlestick",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={
            "patterns": [
                {"name": p.name, "direction": p.direction, "strength": round(p.strength, 2), "description": p.description}
                for p in patterns
            ],
            "bull_score": round(bull_score, 2),
            "bear_score": round(bear_score, 2),
        },
    )


def score_symbol(
    state: SymbolState,
    news_items: List[NewsItemSchema],
    spy_state: Optional[SymbolState] = None,
    qqq_state: Optional[SymbolState] = None,
    adaptive_weights: Optional[Dict[str, float]] = None,
) -> SignalScore:
    """
    Run the full signal engine for one symbol.

    1. Score each component independently
    2. Determine dominant direction from weighted component votes
    3. Re-score market regime with the agreed direction
    4. Aggregate total score
    5. Assign color / label
    6. Build thesis if actionable
    """
    if not state.last_price:
        return SignalScore(
            symbol=state.symbol,
            scored_at=datetime.now(timezone.utc),
            total_score=0.0,
            direction=0,
            color=SignalColor.NEUTRAL,
            label=TradeLabel.NO_TRADE,
            price=None,
            vwap=state.vwap,
        )

    # ── Resolve weights (static config OR learned adaptive) ───────────────────
    aw = adaptive_weights or {}
    W = lambda key, default: round(aw.get(key, default))

    # ── Phase 1: direction-agnostic scoring ───────────────────────────────────
    tech_score = score_technical_trend(state, weight=W("technical_trend", settings.WEIGHT_TECHNICAL_TREND))
    candle_score = _candlestick_score(state, weight=W("candlestick", settings.WEIGHT_CANDLESTICK))
    vol_score  = score_volume(state, weight=W("volume", settings.WEIGHT_VOLUME))
    vwap_score = score_vwap(state,   weight=W("vwap",   settings.WEIGHT_VWAP))

    # ── Determine dominant direction from first four components ────────────────
    # Only components with raw_score >= 0.50 are allowed to cast a direction vote.
    # This prevents weak/uncertain components (barely-directional, near-neutral)
    # from swinging the overall direction when the real picture is ambiguous.
    _DIRECTION_MIN_RAW = 0.50
    dir_votes: Dict[int, float] = {1: 0.0, -1: 0.0, 0: 0.0}
    for comp in (tech_score, candle_score, vol_score, vwap_score):
        if comp.direction != 0 and comp.raw_score >= _DIRECTION_MIN_RAW:
            dir_votes[comp.direction] += comp.weighted_score

    if dir_votes[1] > dir_votes[-1] and dir_votes[1] > 0:
        # Require at least 2 confident components to agree before declaring direction
        bull_consensus = sum(
            1 for c in (tech_score, candle_score, vol_score, vwap_score)
            if c.direction == 1 and c.raw_score >= _DIRECTION_MIN_RAW
        )
        dominant_direction = 1 if bull_consensus >= 2 else 0
    elif dir_votes[-1] > dir_votes[1] and dir_votes[-1] > 0:
        bear_consensus = sum(
            1 for c in (tech_score, candle_score, vol_score, vwap_score)
            if c.direction == -1 and c.raw_score >= _DIRECTION_MIN_RAW
        )
        dominant_direction = -1 if bear_consensus >= 2 else 0
    else:
        dominant_direction = 0

    # ── Phase 2: direction-aware scoring ─────────────────────────────────────
    regime_score = score_market_regime(
        dominant_direction, spy_state, qqq_state, weight=W("market_regime", settings.WEIGHT_MARKET_REGIME)
    )
    news_score = assess_news_bundle(
        state.symbol, news_items, dominant_direction, weight=W("news", settings.WEIGHT_NEWS)
    )
    whale_data  = get_whale_data(state.symbol)
    whale_score = score_whale_sentiment(
        whale_data, dominant_direction, weight=W("whale", settings.WEIGHT_WHALE)
    )

    components = [tech_score, candle_score, vol_score, vwap_score, regime_score, news_score, whale_score]

    # ── Phase 3: total score ──────────────────────────────────────────────────
    total = sum(c.weighted_score for c in components)
    # Normalise — should sum to 100 if weights are correct
    max_possible = sum(c.weight for c in components)
    if max_possible > 0:
        total = (total / max_possible) * 100.0

    # Headline risk hard-cap
    if news_score.details.get("headline_risk"):
        total = min(total, 30.0)

    # Counter-trend regime soft-cap — still allows high-conviction setups through
    if regime_score.details.get("aligned_with_trade") is False and dominant_direction != 0:
        total = min(total, 72.0)

    total = max(0.0, min(100.0, total))

    # ── Phase 4: color / label assignment ────────────────────────────────────
    if dominant_direction == 0 or total < settings.SIGNAL_SCORE_WATCH_THRESHOLD:
        color = SignalColor.NEUTRAL
        label = TradeLabel.NO_TRADE
    elif total >= settings.SIGNAL_SCORE_FLASH_THRESHOLD:
        color = SignalColor.FLASH_GREEN if dominant_direction == 1 else SignalColor.FLASH_RED
        label = TradeLabel.IMMEDIATE_TRADE
        # FLASH requires 3+ confident components; downgrade if not enough agree
        flash_consensus = sum(
            1 for c in (tech_score, candle_score, vol_score, vwap_score)
            if c.direction == dominant_direction and c.raw_score >= _DIRECTION_MIN_RAW
        )
        if flash_consensus < 3:
            color = SignalColor.GREEN if dominant_direction == 1 else SignalColor.RED
            label = TradeLabel.POSSIBLE_TRADE
    elif total >= settings.SIGNAL_SCORE_TRADE_THRESHOLD:
        color = SignalColor.GREEN if dominant_direction == 1 else SignalColor.RED
        label = TradeLabel.POSSIBLE_TRADE
    else:
        color = SignalColor.GREEN if dominant_direction == 1 else SignalColor.RED
        label = TradeLabel.WATCH

    # ── Phase 5: thesis (only for actionable signals) ─────────────────────────
    thesis: Optional[TradingThesis] = None
    signal = SignalScore(
        symbol=state.symbol,
        scored_at=datetime.now(timezone.utc),
        total_score=round(total, 1),
        direction=dominant_direction,
        color=color,
        label=label,
        price=state.last_price,
        vwap=state.vwap,
        components=components,
    )

    if dominant_direction != 0 and label != TradeLabel.NO_TRADE:
        try:
            thesis = build_thesis(signal, state)
        except Exception as exc:
            logger.warning("Thesis build failed for %s: %s — no stop/TP will be set", state.symbol, exc)
            # Explicit fallback so entry blocker can fire correctly (thesis_source="build_failed"
            # has no stop/TP → _maybe_auto_execute will reject the entry with a clear log line).
            thesis = TradingThesis(
                direction="long" if dominant_direction == 1 else "short",
                confidence=round(total, 1),
                why_now=f"{state.symbol} — thesis build error; no stop/TP",
                thesis_source="build_failed",
            )

    signal.thesis = thesis
    return signal
