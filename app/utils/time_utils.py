"""
Market session time helpers.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.config import settings

_TZ = ZoneInfo(settings.SESSION_TIMEZONE)


def now_et() -> datetime:
    """Current datetime in Eastern Time."""
    return datetime.now(_TZ)


def today_et() -> date:
    return now_et().date()


def session_date_str() -> str:
    return today_et().isoformat()


def to_et(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to Eastern Time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_TZ)


def market_open_dt() -> datetime:
    h, m = map(int, settings.MARKET_OPEN_TIME.split(":"))
    return datetime.combine(today_et(), time(h, m), tzinfo=_TZ)


def market_close_dt() -> datetime:
    h, m = map(int, settings.MARKET_CLOSE_TIME.split(":"))
    return datetime.combine(today_et(), time(h, m), tzinfo=_TZ)


def premarket_start_dt() -> datetime:
    h, m = map(int, settings.PREMARKET_START_TIME.split(":"))
    return datetime.combine(today_et(), time(h, m), tzinfo=_TZ)


def afterhours_end_dt() -> datetime:
    h, m = map(int, settings.AFTERHOURS_END_TIME.split(":"))
    return datetime.combine(today_et(), time(h, m), tzinfo=_TZ)


def is_weekday(dt: datetime | None = None) -> bool:
    now = to_et(dt) if dt else now_et()
    return now.weekday() < 5  # Monday=0 … Friday=4


def is_regular_hours(dt: datetime | None = None) -> bool:
    now = to_et(dt) if dt else now_et()
    return is_weekday(now) and market_open_dt() <= now < market_close_dt()


def is_premarket(dt: datetime | None = None) -> bool:
    now = to_et(dt) if dt else now_et()
    return is_weekday(now) and premarket_start_dt() <= now < market_open_dt()


def is_afterhours(dt: datetime | None = None) -> bool:
    now = to_et(dt) if dt else now_et()
    return is_weekday(now) and market_close_dt() <= now < afterhours_end_dt()


def is_market_open(dt: datetime | None = None) -> bool:
    """True during regular session."""
    return is_regular_hours(dt)


def minutes_until_close() -> float:
    remaining = (market_close_dt() - now_et()).total_seconds() / 60
    return max(remaining, 0.0)


def opening_range_end_dt() -> datetime:
    open_dt = market_open_dt()
    from datetime import timedelta
    return open_dt + timedelta(minutes=settings.OPENING_RANGE_MINUTES)


def is_opening_range_active() -> bool:
    now = now_et()
    return market_open_dt() <= now < opening_range_end_dt()
