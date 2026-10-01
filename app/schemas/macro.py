from __future__ import annotations
from pydantic import BaseModel
from typing import List, Optional
from datetime import date


class EconomicEvent(BaseModel):
    date: date
    day_of_week: str    # "Monday", "Tuesday", etc.
    time_str: str       # "8:30 AM ET"
    country: str        # "US", "EU", "UK", etc.
    title: str          # plain name — used for indicator lookup
    impact: str         # "high", "medium"
    actual: Optional[str] = None    # "0.2%" or "219" — set once released
    forecast: Optional[str] = None  # "0.3%" — analyst consensus


class EarningsEvent(BaseModel):
    date: date
    day_of_week: str
    ticker: str
    company: str
    anticipation: str  # "bullish_watch", "bearish_watch", "high_volatility_watch", "mixed_expectations"
    importance: int    # 1-5


class CalendarDay(BaseModel):
    day_name: str
    date_str: str  # "Apr 17"
    is_today: bool
    economic_events: List[EconomicEvent] = []
    earnings_events: List[EarningsEvent] = []


class WeeklyCalendarResponse(BaseModel):
    week_label: str  # "Apr 14 – Apr 20, 2026"
    days: List[CalendarDay]


class TreasuryYield(BaseModel):
    tenor: str        # "13W", "2Y", "5Y", "10Y", "30Y"
    label: str        # "13-Week", "2-Year", etc.
    yield_pct: float
    change_1d: float
    direction: str    # "rising", "falling", "flat"


class MacroRates(BaseModel):
    fed_funds_rate: Optional[float]
    fed_funds_range: str  # "4.25% – 4.50%"
    direction: str        # "hiking", "cutting", "holding"
    last_updated: str


class NAAIMData(BaseModel):
    reading_date: str
    value: float
    previous_value: Optional[float]
    trend: str            # "rising", "falling", "flat"
    interpretation: str   # "risk-on", "neutral", "risk-off"


class AAIIData(BaseModel):
    survey_date: str
    bullish_pct: float
    neutral_pct: float
    bearish_pct: float
    bull_bear_spread: float
    interpretation: str   # "bullish_crowding", "balanced_sentiment", "bearish_crowding"


class MacroDashboardResponse(BaseModel):
    treasury_yields: List[TreasuryYield]
    macro_rates: Optional[MacroRates]
    naaim: Optional[NAAIMData]
    aaii: Optional[AAIIData]
