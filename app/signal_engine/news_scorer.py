"""
News alignment / contradiction scorer (0–15 points).

Uses a rules-based, deterministic sentiment model.
Optionally integrates LLM summarisation behind a feature flag.

The score is conservative by design:
 - positive news alone cannot create a trade
 - contradictory news reduces conviction
 - headline risk is flagged and penalises the score
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional

from app.config import settings
from app.schemas.news import NewsItemSchema
from app.schemas.signals import ComponentScore, NewsAlignment, NewsScoreResult
from app.utils.math_utils import clamp

# ── Keyword tables ─────────────────────────────────────────────────────────────

BULLISH_KEYWORDS = [
    "beats", "beat", "exceeds", "upgraded", "upgrade", "buy rating", "strong buy",
    "raises", "raised guidance", "record revenue", "record earnings", "profit",
    "expansion", "acquisition", "partnership", "contract won", "FDA approval",
    "positive data", "outperform", "bullish", "momentum", "rally", "surge",
    "breakthrough", "new high", "dividend increase",
]

BEARISH_KEYWORDS = [
    "misses", "missed", "below expectations", "downgraded", "downgrade", "sell rating",
    "cut guidance", "layoffs", "recall", "FDA rejection", "investigation",
    "lawsuit", "fraud", "miss", "loss", "decline", "falling", "disappoints",
    "warning", "risk", "concern", "lower guidance", "weak", "sell-off",
]

HEADLINE_RISK_KEYWORDS = [
    "sec investigation", "doj", "fraud", "class action", "bankruptcy",
    "halted", "trading halted", "restatement", "going private", "delisting",
    "ceo resign", "executive resignation", "accounting irregularities",
    "criminal", "indictment", "major recall",
]

HIGH_VOLATILITY_EVENTS = [
    "earnings", "earnings report", "results", "guidance", "fda", "fomc",
    "fed decision", "rate decision", "cpi", "jobs report",
]


def _normalise(text: str) -> str:
    return text.lower()


def _count_keywords(text: str, keywords: List[str]) -> int:
    t = _normalise(text)
    return sum(1 for kw in keywords if kw in t)


def score_news_item(item: NewsItemSchema, symbol: str) -> tuple[float, int]:
    """
    Score a single news item.
    Returns (sentiment: -1..+1, relevance: 0..1).
    """
    text = f"{item.headline or ''} {item.summary or ''}"

    bull_hits = _count_keywords(text, BULLISH_KEYWORDS)
    bear_hits = _count_keywords(text, BEARISH_KEYWORDS)

    # Raw sentiment: net bullish/bearish signals
    raw = (bull_hits - bear_hits) / max(bull_hits + bear_hits, 1)
    sentiment = clamp(raw, -1.0, 1.0)

    # Relevance: is this actually about the symbol?
    sym_in_headline = symbol.lower() in _normalise(item.headline or "")
    relevance = 1.0 if sym_in_headline else 0.5

    return sentiment, relevance


def assess_news_bundle(
    symbol: str,
    items: List[NewsItemSchema],
    proposed_direction: int,  # +1 long, -1 short, 0 unknown
    weight: int = 15,
) -> ComponentScore:
    """
    Aggregate news items into a ComponentScore for the signal engine.
    """
    result = _compute_news_score(symbol, items, proposed_direction)

    return ComponentScore(
        name="news",
        raw_score=result.score,
        weight=weight,
        weighted_score=result.score * weight,
        direction=result.direction,
        details={
            "alignment": result.alignment.value,
            "headline_risk": result.has_headline_risk,
            "contradictory": result.has_contradictory_news,
            "headline_count": len(items),
            "summary": result.summary,
            "recent_headlines": result.headlines[:3],
        },
    )


def _compute_news_score(
    symbol: str,
    items: List[NewsItemSchema],
    proposed_direction: int,
) -> NewsScoreResult:
    if not items:
        return NewsScoreResult(
            alignment=NewsAlignment.NEUTRAL,
            score=0.5,
            direction=0,
            summary="No recent news available.",
        )

    # Filter to recent items only
    cutoff_hours = settings.NEWS_MAX_AGE_HOURS
    now_utc = datetime.now(timezone.utc)
    recent_items = []
    for item in items:
        pub = item.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_h = (now_utc - pub).total_seconds() / 3600
        if age_h <= cutoff_hours:
            recent_items.append(item)

    if not recent_items:
        return NewsScoreResult(
            alignment=NewsAlignment.NEUTRAL,
            score=0.5,
            direction=0,
            summary="No recent news within lookback window.",
        )

    # Check for headline risk first (hard penalty)
    has_headline_risk = False
    for item in recent_items:
        if _count_keywords(f"{item.headline} {item.summary or ''}", HEADLINE_RISK_KEYWORDS) > 0:
            has_headline_risk = True
            break

    if has_headline_risk:
        return NewsScoreResult(
            alignment=NewsAlignment.HEADLINE_RISK,
            score=0.05,
            direction=0,
            headlines=[i.headline for i in recent_items[:3]],
            summary="HEADLINE RISK detected — major negative news event.",
            has_headline_risk=True,
        )

    # Score each item
    sentiments = []
    headlines = []
    for item in recent_items:
        s, r = score_news_item(item, symbol)
        sentiments.append(s * r)  # relevance-weighted
        headlines.append(item.headline)

    if not sentiments:
        avg_sentiment = 0.0
    else:
        avg_sentiment = sum(sentiments) / len(sentiments)

    # Determine alignment
    news_direction = 1 if avg_sentiment > 0.1 else (-1 if avg_sentiment < -0.1 else 0)

    has_contradictory = False
    alignment: NewsAlignment

    if news_direction == 0:
        alignment = NewsAlignment.NEUTRAL
        base_score = 0.5
    elif news_direction == proposed_direction:
        alignment = NewsAlignment.SUPPORTS_LONG if news_direction == 1 else NewsAlignment.SUPPORTS_SHORT
        base_score = 0.6 + abs(avg_sentiment) * 0.4
    else:
        alignment = NewsAlignment.CONTRADICTORY
        has_contradictory = True
        # News contradicts the technical signal — penalise
        base_score = 0.25 - abs(avg_sentiment) * 0.15

    # Check for high-volatility events (earnings etc.) — increases uncertainty
    has_vol_event = any(
        _count_keywords(f"{i.headline} {i.summary or ''}", HIGH_VOLATILITY_EVENTS) > 0
        for i in recent_items[:5]
    )
    if has_vol_event:
        base_score *= 0.85   # reduce confidence slightly when there's a catalyst

    score = clamp(base_score)

    summary_parts = []
    if alignment in (NewsAlignment.SUPPORTS_LONG, NewsAlignment.SUPPORTS_SHORT):
        summary_parts.append(f"News supports {'long' if news_direction == 1 else 'short'} thesis.")
    elif alignment == NewsAlignment.CONTRADICTORY:
        summary_parts.append("News contradicts technical direction — caution.")
    else:
        summary_parts.append("News is neutral.")
    if has_vol_event:
        summary_parts.append("High-volatility event detected (earnings/catalyst).")

    return NewsScoreResult(
        alignment=alignment,
        score=score,
        direction=news_direction,
        headlines=headlines[:5],
        summary=" ".join(summary_parts),
        has_headline_risk=has_headline_risk,
        has_contradictory_news=has_contradictory,
    )
