"""
Trade thesis builder.

Synthesises component scores into a human-readable TradingThesis object
with stop/target levels and risk notes.
"""
from __future__ import annotations

from typing import List, Optional

from app.schemas.market_data import SymbolState
from app.schemas.signals import ComponentScore, SignalScore, TradingThesis
from app.utils.math_utils import clamp


def build_thesis(
    signal: SignalScore,
    state: SymbolState,
) -> TradingThesis:
    direction_str = "long" if signal.direction == 1 else ("short" if signal.direction == -1 else "neutral")
    price = signal.price or state.last_price

    tech = signal.component_by_name("technical_trend")
    candle = signal.component_by_name("candlestick")
    vol = signal.component_by_name("volume")
    vwap_comp = signal.component_by_name("vwap")
    regime = signal.component_by_name("market_regime")
    news = signal.component_by_name("news")

    # ── Technical evidence ────────────────────────────────────────────────────
    tech_evidence: List[str] = []
    if tech:
        d = tech.details
        ts = d.get("trend_structure", 0)
        if ts == 1:
            tech_evidence.append("Bullish trend structure: higher highs and higher lows on 5m bars.")
        elif ts == -1:
            tech_evidence.append("Bearish trend structure: lower highs and lower lows on 5m bars.")
        if d.get("above_ema9"):
            tech_evidence.append("Price above 9-EMA.")
        if d.get("above_ema21"):
            tech_evidence.append("Price above 21-EMA.")
        mom = d.get("momentum_10")
        if mom and abs(mom) > 0.005:
            tech_evidence.append(f"{'Positive' if mom > 0 else 'Negative'} 10-bar momentum ({mom*100:.2f}%).")

    # ── Candlestick evidence ──────────────────────────────────────────────────
    candle_evidence: List[str] = []
    if candle:
        patterns = candle.details.get("patterns", [])
        for p in patterns[:3]:
            candle_evidence.append(p.get("description", p.get("name", "Pattern detected")))

    # ── Volume evidence ───────────────────────────────────────────────────────
    vol_evidence: List[str] = []
    if vol:
        rvol = vol.details.get("rvol")
        if rvol and rvol >= 1.5:
            vol_evidence.append(f"Elevated relative volume: {rvol:.1f}× average.")

    # Merge volume into tech evidence for thesis
    if vol_evidence:
        tech_evidence.extend(vol_evidence)

    # VWAP context
    if vwap_comp:
        d = vwap_comp.details
        dist_pct = d.get("dist_from_vwap_pct")
        orh = d.get("opening_range_high")
        orl = d.get("opening_range_low")
        if d.get("above_vwap") and signal.direction == 1:
            tech_evidence.append(f"Price above VWAP ({dist_pct:+.2f}%)." if dist_pct else "Price above VWAP.")
        elif not d.get("above_vwap") and signal.direction == -1:
            tech_evidence.append(f"Price below VWAP ({dist_pct:+.2f}%)." if dist_pct else "Price below VWAP.")
        if orh and price and price > orh and signal.direction == 1:
            tech_evidence.append(f"Opened range breakout above {orh:.2f}.")
        elif orl and price and price < orl and signal.direction == -1:
            tech_evidence.append(f"Opening range breakdown below {orl:.2f}.")

    # ── News evidence ─────────────────────────────────────────────────────────
    news_evidence: List[str] = []
    if news:
        headlines = news.details.get("recent_headlines", [])
        summary_text = news.details.get("summary", "")
        if summary_text:
            news_evidence.append(summary_text)
        if headlines:
            news_evidence.extend([f"• {h}" for h in headlines[:2]])

    # ── Stop and target ───────────────────────────────────────────────────────
    suggested_stop: Optional[float] = None
    suggested_target: Optional[float] = None
    stop_pct: Optional[float] = None
    target_pct: Optional[float] = None
    rr: Optional[float] = None
    thesis_source: str = "no_price"
    risk_per_share: Optional[float] = None

    if price:
        # Stop: use ATR-based or structural level
        from app.signal_engine.indicators import compute_atr
        bars = state.bars_5m if len(state.bars_5m) >= 5 else state.bars_1m
        atr_val = compute_atr(bars, period=10)
        if atr_val:
            # Stop = 1.2× ATR — tighter than 2×, limits loss per trade while
            # keeping the formula from triggering on normal tick noise.
            # Target = 3:1 R/R — unchanged.
            stop_dist = atr_val * 1.2
            if signal.direction == 1:
                suggested_stop = price - stop_dist
                suggested_target = price + stop_dist * 3.0   # 3:1 R/R
            else:
                suggested_stop = price + stop_dist
                suggested_target = price - stop_dist * 3.0
            stop_pct = stop_dist / price
            target_pct = stop_dist * 3.0 / price
            rr = 3.0
            thesis_source = "atr_based"
            risk_per_share = round(stop_dist, 4)
        else:
            # Fallback: 2% stop, 6% target (3:1 R/R)
            stop_pct = 0.02
            target_pct = 0.06
            if signal.direction == 1:
                suggested_stop = price * (1 - stop_pct)
                suggested_target = price * (1 + target_pct)
            else:
                suggested_stop = price * (1 + stop_pct)
                suggested_target = price * (1 - target_pct)
            rr = 3.0
            thesis_source = "pct_fallback"
            risk_per_share = round(price * 0.02, 4)

    # ── Invalidation ─────────────────────────────────────────────────────────
    invalidation_parts: List[str] = []
    if state.vwap and signal.direction == 1:
        invalidation_parts.append(f"Close below VWAP ({state.vwap:.2f}).")
    elif state.vwap and signal.direction == -1:
        invalidation_parts.append(f"Close above VWAP ({state.vwap:.2f}).")
    if state.opening_range_low and signal.direction == 1:
        invalidation_parts.append(f"Break below OR low ({state.opening_range_low:.2f}).")
    if state.opening_range_high and signal.direction == -1:
        invalidation_parts.append(f"Break above OR high ({state.opening_range_high:.2f}).")
    if suggested_stop:
        invalidation_parts.append(f"Stop hit at {suggested_stop:.2f}.")
    invalidation = " ".join(invalidation_parts) or "Price action invalidates thesis."

    # ── Risk note ─────────────────────────────────────────────────────────────
    risk_parts: List[str] = []
    if news and news.details.get("contradictory"):
        risk_parts.append("News contradicts technical direction.")
    if news and news.details.get("headline_risk"):
        risk_parts.append("Headline risk present — size down or avoid.")
    if regime and regime.details.get("aligned_with_trade") is False:
        risk_parts.append("Broad market regime not aligned — counter-trend risk.")
    if vol and vol.details.get("rvol") and vol.details["rvol"] < 0.8:
        risk_parts.append("Low relative volume — lack of conviction.")
    risk_note = " ".join(risk_parts) or "Standard intraday risk. Honour stop."

    # ── Why now ───────────────────────────────────────────────────────────────
    score_label = f"Score {signal.total_score:.0f}/100"
    why_now = (
        f"{direction_str.upper()} setup on {signal.symbol}. {score_label}. "
        f"{'Trend, VWAP, and volume all aligned.' if signal.total_score >= 70 else 'Developing setup — watch for confirmation.'}"
    )

    return TradingThesis(
        direction=direction_str,
        confidence=signal.total_score,
        why_now=why_now,
        technical_evidence=tech_evidence,
        candlestick_evidence=candle_evidence,
        news_evidence=news_evidence,
        invalidation=invalidation,
        risk_note=risk_note,
        suggested_stop=round(suggested_stop, 2) if suggested_stop else None,
        suggested_target=round(suggested_target, 2) if suggested_target else None,
        stop_pct=round(stop_pct * 100, 2) if stop_pct else None,
        target_pct=round(target_pct * 100, 2) if target_pct else None,
        risk_reward=rr,
        thesis_source=thesis_source,
        risk_per_share=risk_per_share,
    )
