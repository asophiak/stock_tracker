from __future__ import annotations

from fastapi import APIRouter

from app.providers.macro.indicator_info import lookup_indicator
from app.schemas.macro import MacroDashboardResponse, WeeklyCalendarResponse
from app.services import macro_service

router = APIRouter(prefix="/api/macro")


@router.get("/calendar", response_model=WeeklyCalendarResponse)
async def get_calendar() -> WeeklyCalendarResponse:
    return await macro_service.get_weekly_calendar()


@router.get("/dashboard", response_model=MacroDashboardResponse)
async def get_dashboard() -> MacroDashboardResponse:
    return await macro_service.get_macro_dashboard()


@router.get("/indicator")
async def get_indicator_info(title: str) -> dict:
    """Return key notes and market-impact highlights for a given economic indicator title."""
    return lookup_indicator(title)


@router.post("/refresh")
async def refresh_macro():
    await macro_service.refresh_macro_data()
    return {"status": "ok"}
