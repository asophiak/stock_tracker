from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from app.providers.macro.earnings_calendar import get_weekly_earnings_events
from app.providers.macro.economic_calendar import get_weekly_economic_events
from app.providers.macro.sentiment import get_aaii_data, get_naaim_data
from app.providers.macro.treasury import get_macro_rates, get_treasury_yields
from app.schemas.macro import (
    CalendarDay,
    MacroDashboardResponse,
    WeeklyCalendarResponse,
)

logger = logging.getLogger(__name__)


def _get_week_dates() -> list[date]:
    """
    Return 28 days spanning:
      • last week  (Mon –– Sun, already past)
      • this week  (Mon –– Sun, current)
      • next week  (Mon –– Sun, upcoming)
      • week +2    (Mon –– Sun, future)
    Window always starts on the Monday of last week so weeks align cleanly.
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(weeks=1)
    return [last_monday + timedelta(days=i) for i in range(28)]


async def get_weekly_calendar() -> WeeklyCalendarResponse:
    week_dates = _get_week_dates()
    today = date.today()

    econ_events, earnings_events = await asyncio.gather(
        get_weekly_economic_events(),
        get_weekly_earnings_events(),
        return_exceptions=True,
    )

    if isinstance(econ_events, Exception):
        logger.warning("macro_service: econ events error: %s", econ_events)
        econ_events = []
    if isinstance(earnings_events, Exception):
        logger.warning("macro_service: earnings events error: %s", earnings_events)
        earnings_events = []

    # Build lookup dicts by date
    econ_by_date: dict[date, list] = {}
    for e in econ_events:
        econ_by_date.setdefault(e.date, []).append(e)

    earnings_by_date: dict[date, list] = {}
    for e in earnings_events:
        earnings_by_date.setdefault(e.date, []).append(e)

    # Sort earnings per day by importance desc, keep top 5
    for d, evts in earnings_by_date.items():
        earnings_by_date[d] = sorted(evts, key=lambda e: -e.importance)[:5]

    days = []
    for d in week_dates:
        day_name = d.strftime("%A")
        date_str = d.strftime("%b %-d")
        is_today = d == today
        day_econ = sorted(econ_by_date.get(d, []), key=lambda e: e.time_str)
        day_earn = earnings_by_date.get(d, [])
        days.append(
            CalendarDay(
                day_name=day_name,
                date_str=date_str,
                is_today=is_today,
                economic_events=day_econ,
                earnings_events=day_earn,
            )
        )

    start = week_dates[0].strftime("%b %-d")
    end = week_dates[-1].strftime("%b %-d, %Y")
    week_label = f"{start} \u2013 {end}"

    return WeeklyCalendarResponse(week_label=week_label, days=days)


async def get_macro_dashboard() -> MacroDashboardResponse:
    treasury_yields, macro_rates, naaim, aaii = await asyncio.gather(
        get_treasury_yields(),
        get_macro_rates(),
        get_naaim_data(),
        get_aaii_data(),
        return_exceptions=True,
    )

    if isinstance(treasury_yields, Exception):
        logger.warning("macro_service: treasury yields error: %s", treasury_yields)
        treasury_yields = []
    if isinstance(macro_rates, Exception):
        logger.warning("macro_service: macro rates error: %s", macro_rates)
        macro_rates = None
    if isinstance(naaim, Exception):
        logger.warning("macro_service: naaim error: %s", naaim)
        naaim = None
    if isinstance(aaii, Exception):
        logger.warning("macro_service: aaii error: %s", aaii)
        aaii = None

    return MacroDashboardResponse(
        treasury_yields=treasury_yields,
        macro_rates=macro_rates,
        naaim=naaim,
        aaii=aaii,
    )


async def refresh_macro_data() -> None:
    try:
        cal = await get_weekly_calendar()
        logger.info("macro_service: calendar warmed (%d days)", len(cal.days))
    except Exception as exc:
        logger.warning("macro_service: calendar warm failed: %s", exc)

    try:
        dash = await get_macro_dashboard()
        logger.info(
            "macro_service: dashboard warmed (yields=%d, naaim=%s, aaii=%s)",
            len(dash.treasury_yields),
            dash.naaim is not None,
            dash.aaii is not None,
        )
    except Exception as exc:
        logger.warning("macro_service: dashboard warm failed: %s", exc)
