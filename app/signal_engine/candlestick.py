"""
Candlestick pattern detection.

Patterns are only meaningful when context agrees:
  - level interaction (near VWAP, opening range, S/R)
  - volume confirmation
  - momentum alignment
  - market regime
  - news alignment

Each detector returns (pattern_name, direction, strength) or None.
  direction: +1 bullish, -1 bearish
  strength: 0.0 – 1.0
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.schemas.market_data import Bar


@dataclass
class PatternMatch:
    name: str
    direction: int       # +1 bullish, -1 bearish
    strength: float      # 0.0 – 1.0
    description: str


def _body_ratio(bar: Bar) -> float:
    """Body size as a fraction of total range."""
    if bar.range == 0:
        return 0.0
    return bar.body_size / bar.range


def _avg_volume(bars: List[Bar], lookback: int = 10) -> float:
    if not bars:
        return 0.0
    recent = bars[-lookback:]
    return sum(b.volume for b in recent) / len(recent)


# ── Individual patterns ───────────────────────────────────────────────────────

def detect_bullish_engulfing(bars: List[Bar]) -> Optional[PatternMatch]:
    """Current bar fully engulfs previous bearish bar and is bullish."""
    if len(bars) < 2:
        return None
    prev, curr = bars[-2], bars[-1]
    if (
        prev.is_bearish
        and curr.is_bullish
        and curr.open <= prev.close
        and curr.close >= prev.open
    ):
        strength = min(1.0, _body_ratio(curr) * 1.2)
        return PatternMatch("bullish_engulfing", +1, strength, "Bullish candle fully engulfs prior bearish candle")
    return None


def detect_bearish_engulfing(bars: List[Bar]) -> Optional[PatternMatch]:
    if len(bars) < 2:
        return None
    prev, curr = bars[-2], bars[-1]
    if (
        prev.is_bullish
        and curr.is_bearish
        and curr.open >= prev.close
        and curr.close <= prev.open
    ):
        strength = min(1.0, _body_ratio(curr) * 1.2)
        return PatternMatch("bearish_engulfing", -1, strength, "Bearish candle fully engulfs prior bullish candle")
    return None


def detect_hammer(bars: List[Bar]) -> Optional[PatternMatch]:
    """
    Hammer: small body, long lower wick (>= 60% of range), minimal upper wick (<= 15% of range).
    Bullish signal when appearing after a downtrend.
    """
    if not bars:
        return None
    c = bars[-1]
    if c.range == 0:
        return None
    lower_w = c.lower_wick
    upper_w = c.upper_wick
    # Lower wick must dominate; body+upper wick should be small
    if lower_w >= 0.60 * c.range and upper_w <= 0.15 * c.range:
        strength = min(1.0, lower_w / c.range)
        return PatternMatch("hammer", +1, strength, "Hammer: long lower wick rejection")
    return None


def detect_shooting_star(bars: List[Bar]) -> Optional[PatternMatch]:
    """
    Shooting star: small body, long upper wick (>= 60% of range), minimal lower wick (<= 15% of range).
    Bearish signal when appearing after an uptrend.
    """
    if not bars:
        return None
    c = bars[-1]
    if c.range == 0:
        return None
    upper_w = c.upper_wick
    lower_w = c.lower_wick
    # Upper wick must dominate
    if upper_w >= 0.60 * c.range and lower_w <= 0.15 * c.range:
        strength = min(1.0, upper_w / c.range)
        return PatternMatch("shooting_star", -1, strength, "Shooting star: long upper wick rejection")
    return None


def detect_strong_trend_candle(bars: List[Bar]) -> Optional[PatternMatch]:
    """
    Strong trend candle: large body (>= 70% of range), closes near extremes,
    volume above average.
    """
    if len(bars) < 5:
        return None
    c = bars[-1]
    if c.range == 0:
        return None
    br = _body_ratio(c)
    avg_vol = _avg_volume(bars[:-1], 10)
    volume_surge = c.volume > avg_vol * 1.3 if avg_vol > 0 else False
    if br >= 0.70 and volume_surge:
        direction = +1 if c.is_bullish else -1
        return PatternMatch(
            "strong_trend_candle",
            direction,
            br,
            f"{'Bullish' if direction > 0 else 'Bearish'} trend candle with volume surge",
        )
    return None


def detect_breakout_candle(bars: List[Bar], session_high: Optional[float] = None, orh: Optional[float] = None) -> Optional[PatternMatch]:
    """
    Breakout candle: closes above a key level (session high or ORH) on volume.
    """
    if len(bars) < 3:
        return None
    c = bars[-1]
    avg_vol = _avg_volume(bars[:-1], 10)
    if avg_vol == 0:
        return None

    level = None
    if session_high and c.close > session_high and c.close > bars[-2].high:
        level = session_high
    elif orh and c.close > orh and c.close > bars[-2].high:
        level = orh

    if level and c.volume > avg_vol * 1.5:
        strength = min(1.0, (c.volume / avg_vol - 1.0) / 2.0 + 0.5)
        return PatternMatch("breakout_candle", +1, strength, f"Breakout above {level:.2f} on strong volume")
    return None


def detect_rejection_candle(bars: List[Bar], level: Optional[float] = None) -> Optional[PatternMatch]:
    """
    Rejection candle: tests a level (VWAP, S/R) and closes back through it,
    creating a long wick.
    """
    if not bars:
        return None
    c = bars[-1]
    if c.range == 0:
        return None
    # Bearish rejection: upper wick dominates
    if c.upper_wick >= 0.5 * c.range and c.is_bearish:
        return PatternMatch("rejection_candle", -1, c.upper_wick / c.range, "Bearish rejection candle at resistance")
    # Bullish rejection: lower wick dominates
    if c.lower_wick >= 0.5 * c.range and c.is_bullish:
        return PatternMatch("rejection_candle", +1, c.lower_wick / c.range, "Bullish rejection candle at support")
    return None


def detect_failed_breakout(bars: List[Bar], orh: Optional[float] = None) -> Optional[PatternMatch]:
    """
    Failed breakout: previous bar closed above a key level; current bar
    closes back below it (bull trap).
    """
    if len(bars) < 2 or not orh:
        return None
    prev, curr = bars[-2], bars[-1]
    if prev.close > orh and curr.close < orh:
        return PatternMatch("failed_breakout", -1, 0.75, "Failed breakout above ORH — bull trap")
    return None


def detect_failed_breakdown(bars: List[Bar], orl: Optional[float] = None) -> Optional[PatternMatch]:
    """
    Failed breakdown: previous bar closed below a key level; current bar
    closes back above it (bear trap).
    """
    if len(bars) < 2 or not orl:
        return None
    prev, curr = bars[-2], bars[-1]
    if prev.close < orl and curr.close > orl:
        return PatternMatch("failed_breakdown", +1, 0.75, "Failed breakdown below ORL — bear trap")
    return None


def detect_vwap_reclaim(bars: List[Bar], vwap: Optional[float] = None) -> Optional[PatternMatch]:
    """
    VWAP reclaim: previous bar closed below VWAP, current bar closes above.
    """
    if len(bars) < 2 or not vwap:
        return None
    prev, curr = bars[-2], bars[-1]
    if prev.close < vwap and curr.close > vwap:
        strength = min(1.0, abs(curr.close - vwap) / vwap * 20 + 0.5)
        return PatternMatch("vwap_reclaim", +1, strength, "VWAP reclaim: closed above VWAP after being below")
    return None


def detect_vwap_rejection(bars: List[Bar], vwap: Optional[float] = None) -> Optional[PatternMatch]:
    """
    VWAP rejection: tested VWAP from below and closed back below it.
    """
    if len(bars) < 2 or not vwap:
        return None
    prev, curr = bars[-2], bars[-1]
    if prev.close < vwap and curr.high > vwap and curr.close < vwap:
        return PatternMatch("vwap_rejection", -1, 0.7, "VWAP rejection: tested VWAP from below, rejected")
    return None


# ── Master detector ───────────────────────────────────────────────────────────

def detect_all_patterns(
    bars: List[Bar],
    vwap: Optional[float] = None,
    orh: Optional[float] = None,
    orl: Optional[float] = None,
    session_high: Optional[float] = None,
) -> List[PatternMatch]:
    """
    Run all pattern detectors and return a list of matches.
    Patterns are filtered: only the highest-strength one per direction is kept
    to avoid over-counting.
    """
    candidates: List[PatternMatch] = []

    detectors = [
        lambda: detect_bullish_engulfing(bars),
        lambda: detect_bearish_engulfing(bars),
        lambda: detect_hammer(bars),
        lambda: detect_shooting_star(bars),
        lambda: detect_strong_trend_candle(bars),
        lambda: detect_breakout_candle(bars, session_high, orh),
        lambda: detect_rejection_candle(bars),
        lambda: detect_failed_breakout(bars, orh),
        lambda: detect_failed_breakdown(bars, orl),
        lambda: detect_vwap_reclaim(bars, vwap),
        lambda: detect_vwap_rejection(bars, vwap),
    ]

    for fn in detectors:
        try:
            match = fn()
            if match:
                candidates.append(match)
        except Exception:
            continue

    # Keep only the strongest pattern per direction to avoid score inflation
    best_bull = max((p for p in candidates if p.direction == 1), key=lambda x: x.strength, default=None)
    best_bear = max((p for p in candidates if p.direction == -1), key=lambda x: x.strength, default=None)
    neutral = [p for p in candidates if p.direction == 0]
    return [p for p in [best_bull, best_bear] + neutral if p is not None]
