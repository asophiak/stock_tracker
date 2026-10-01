"""
Pydantic schemas for signal scoring output and trading thesis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class SignalColor(str, Enum):
    NEUTRAL = "NEUTRAL"
    GREEN = "GREEN"
    RED = "RED"
    FLASH_GREEN = "FLASH_GREEN"
    FLASH_RED = "FLASH_RED"


class TradeLabel(str, Enum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    POSSIBLE_TRADE = "POSSIBLE_TRADE"
    IMMEDIATE_TRADE = "IMMEDIATE_TRADE"


class TradeTier(str, Enum):
    """
    Classification assigned to each evaluated setup before execution decisions.

    A_TRADE   — score >= A_TRADE_MIN_SCORE, passes all quality gates → full size, live
    B_TRADE   — score >= B_TRADE_MIN_SCORE, passes basic quality gates → reduced size
    SHADOW    — score >= SHADOW_TRADE_MIN_SCORE → tracked hypothetically, NOT executed
    REJECTED  — below shadow threshold or fails a hard filter → counted in diagnostics only
    """
    A_TRADE  = "A_TRADE"
    B_TRADE  = "B_TRADE"
    SHADOW   = "SHADOW"
    REJECTED = "REJECTED"


class NewsAlignment(str, Enum):
    SUPPORTS_LONG = "supports_long"
    SUPPORTS_SHORT = "supports_short"
    NEUTRAL = "neutral"
    CONTRADICTORY = "contradictory"
    HEADLINE_RISK = "headline_risk"


@dataclass
class ComponentScore:
    """Score from one sub-module of the signal engine."""

    name: str
    raw_score: float       # 0.0 – 1.0
    weight: int            # max points this component contributes
    weighted_score: float  # raw_score * weight
    direction: int         # +1 bullish, -1 bearish, 0 neutral
    details: Dict = field(default_factory=dict)


@dataclass
class TradingThesis:
    """Human-readable trade thesis attached to every actionable signal."""

    direction: str           # "long" | "short"
    confidence: float        # 0–100
    why_now: str
    technical_evidence: List[str] = field(default_factory=list)
    candlestick_evidence: List[str] = field(default_factory=list)
    news_evidence: List[str] = field(default_factory=list)
    invalidation: str = ""
    risk_note: str = ""
    suggested_stop: Optional[float] = None
    suggested_target: Optional[float] = None
    stop_pct: Optional[float] = None
    target_pct: Optional[float] = None
    risk_reward: Optional[float] = None
    is_best_trade_of_day: bool = False
    thesis_source: str = "unknown"       # "atr_based" | "pct_fallback" | "no_price" | "build_failed"
    risk_per_share: Optional[float] = None


@dataclass
class SignalScore:
    """Complete output of the signal engine for one symbol."""

    symbol: str
    scored_at: datetime
    total_score: float              # 0–100
    direction: int                  # +1 long, -1 short, 0 neutral
    color: SignalColor
    label: TradeLabel
    price: Optional[float]
    vwap: Optional[float]
    components: List[ComponentScore] = field(default_factory=list)
    thesis: Optional[TradingThesis] = None
    is_best_trade_of_day: bool = False
    trade_tier: Optional["TradeTier"] = None   # set by bot after quality gates

    def component_by_name(self, name: str) -> Optional[ComponentScore]:
        for c in self.components:
            if c.name == name:
                return c
        return None


@dataclass
class NewsScoreResult:
    """Output of the news scoring sub-module."""

    alignment: NewsAlignment
    score: float        # 0.0 – 1.0
    direction: int      # +1 supports long, -1 supports short, 0 neutral
    headlines: List[str] = field(default_factory=list)
    summary: str = ""
    has_headline_risk: bool = False
    has_contradictory_news: bool = False
