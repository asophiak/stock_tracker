"""
Economic calendar provider using TradingView's public events API.
Covers a 4-week window: last week, this week, next week, week+2.
No API key required.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from app.schemas.macro import EconomicEvent

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0, "retry_after": 0.0}
_CACHE_TTL = 3600          # 1 hour on success
_RETRY_BACKOFF = 120       # seconds before retrying after a failure

_TV_URL = "https://economic-calendar.tradingview.com/events"

# TradingView country codes we care about
_TV_COUNTRIES = "US,EU,GB,JP,CN,CA,AU"

# TradingView importance: 1=high, 0=medium, -1=low
_TV_IMPORTANCE_HIGH   = 1
_TV_IMPORTANCE_MEDIUM = 0

# Map TV country code → display label
_COUNTRY_DISPLAY: Dict[str, str] = {
    "US": "US",
    "EU": "EU",
    "GB": "UK",
    "JP": "JP",
    "CN": "CN",
    "CA": "CA",
    "AU": "AU",
    "NZ": "NZ",
    "CH": "CH",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "Accept": "application/json, text/plain, */*",
}


def _get_window() -> tuple[date, date]:
    """4-week window: last Monday → 3 Sundays from now."""
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    end = last_monday + timedelta(days=27)
    return last_monday, end


def _fetch_sync() -> List[EconomicEvent]:
    now = time.time()

    # Return cached data if still fresh
    if _CACHE["data"] is not None and now < _CACHE["expires_at"]:
        return _CACHE["data"]

    # Honour failure back-off
    if now < _CACHE["retry_after"]:
        return _CACHE["data"] or []

    start_date, end_date = _get_window()

    try:
        resp = requests.get(
            _TV_URL,
            params={
                "from": start_date.strftime("%Y-%m-%dT00:00:00.000Z"),
                "to":   end_date.strftime("%Y-%m-%dT23:59:59.000Z"),
                "countries": _TV_COUNTRIES,
                # Fetch both high (1) and medium (0) importance
            },
            timeout=12,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        raw_events = resp.json().get("result", [])
    except Exception as exc:
        logger.warning("economic_calendar: fetch failed: %s", exc)
        _CACHE["retry_after"] = time.time() + _RETRY_BACKOFF
        return _CACHE["data"] or []

    events: List[EconomicEvent] = []
    for item in raw_events:
        importance_val = item.get("importance", -1)
        # Only include high (1) and medium (0); skip low (-1)
        if importance_val not in (_TV_IMPORTANCE_HIGH, _TV_IMPORTANCE_MEDIUM):
            continue

        impact = "high" if importance_val == _TV_IMPORTANCE_HIGH else "medium"

        # Parse date — stored as UTC ISO string e.g. "2026-04-21T12:30:00.000Z"
        date_str = item.get("date", "")
        if not date_str:
            continue
        try:
            utc_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            # Convert to ET (UTC-4 EDT / UTC-5 EST). Use fixed -4 for simplicity
            # (most US economic events are released during EDT season Apr–Oct)
            et_offset = timedelta(hours=-4)
            et_dt = utc_dt + et_offset
            event_date = et_dt.date()
            hour = et_dt.hour
            minute = et_dt.minute
            period = "AM" if hour < 12 else "PM"
            display_hour = hour % 12 or 12
            time_str = f"{display_hour}:{minute:02d} {period} ET"
        except Exception:
            continue

        country_raw = (item.get("country") or "").upper()
        country = _COUNTRY_DISPLAY.get(country_raw, country_raw)
        title = (item.get("title") or "").strip()
        if not title:
            continue

        day_of_week = event_date.strftime("%A")

        # Actual and forecast (if available)
        raw_actual   = item.get("actual")
        raw_forecast = item.get("forecast")
        unit = item.get("unit") or ""

        actual_str   = f"{raw_actual}{unit}"   if raw_actual   is not None else None
        forecast_str = f"{raw_forecast}{unit}" if raw_forecast is not None else None

        events.append(
            EconomicEvent(
                date=event_date,
                day_of_week=day_of_week,
                time_str=time_str,
                country=country,
                title=title,          # plain name — no suffix
                impact=impact,
                actual=actual_str,
                forecast=forecast_str,
            )
        )

    _CACHE["data"] = events
    _CACHE["expires_at"] = time.time() + _CACHE_TTL
    logger.info(
        "economic_calendar: cached %d events (%s – %s)",
        len(events),
        start_date.isoformat(),
        end_date.isoformat(),
    )
    return events


async def get_weekly_economic_events() -> List[EconomicEvent]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch_sync)
    except Exception as exc:
        logger.warning("economic_calendar: async fetch failed: %s", exc)
        return []
