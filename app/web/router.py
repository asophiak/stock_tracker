"""
HTML page routes rendered via Jinja2 templates.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_state_manager
from app.services.market_session import session_status_dict
from app.services.signal_service import get_all_latest_signals
from app.utils.cache import StateManager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Increments on every server restart — busts browser cache for static assets
_STATIC_VER = str(int(time.time()))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, sm: StateManager = Depends(get_state_manager)):
    signals = get_all_latest_signals()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "v": _STATIC_VER,
            "session": session_status_dict(),
            "kill_switch": sm.kill_switch,
            "execution_mode": sm.execution_mode,
            "provider_connected": sm.provider_connected,
            "symbols": sm.all_symbols(),
            "best_trade": sm.best_trade_of_day,
        },
    )


@router.get("/symbol/{symbol}", response_class=HTMLResponse)
async def symbol_detail(request: Request, symbol: str, sm: StateManager = Depends(get_state_manager)):
    state = sm.get_state(symbol.upper())
    return templates.TemplateResponse(
        "symbol_detail.html",
        {
            "request": request,
            "v": _STATIC_VER,
            "symbol": symbol.upper(),
            "state": state,
            "session": session_status_dict(),
        },
    )


@router.get("/journal", response_class=HTMLResponse)
async def trade_journal(request: Request):
    return templates.TemplateResponse("trade_journal.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, sm: StateManager = Depends(get_state_manager)):
    from app.config import settings
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "kill_switch": sm.kill_switch,
            "execution_mode": sm.execution_mode,
        },
    )


@router.get("/whales", response_class=HTMLResponse)
async def whales_page(request: Request):
    from app.config import settings
    return templates.TemplateResponse(
        "whales.html",
        {
            "request": request,
            "symbols": settings.tradeable_symbols,
            "v": _STATIC_VER,
        },
    )


@router.get("/session-summary", response_class=HTMLResponse)
async def session_summary_page(request: Request):
    return templates.TemplateResponse("session_summary.html", {"request": request})


@router.get("/ml", response_class=HTMLResponse)
async def ml_page(request: Request, sm: StateManager = Depends(get_state_manager)):
    return templates.TemplateResponse("ml.html", {
        "request": request, "v": _STATIC_VER,
        "kill_switch": sm.kill_switch, "execution_mode": sm.execution_mode,
    })


@router.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics_page(request: Request):
    return templates.TemplateResponse("diagnostics.html", {"request": request, "v": _STATIC_VER})


# ── Server-Sent Events stream ─────────────────────────────────────────────────

@router.get("/api/events")
async def sse_events(request: Request, sm: StateManager = Depends(get_state_manager)):
    """
    SSE endpoint. Browsers connect here and receive push updates whenever
    signal scores, prices, or alerts change.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    sm.register_sse_queue(q)

    async def event_generator():
        try:
            # Send initial heartbeat
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield "data: {\"type\": \"heartbeat\"}\n\n"
        finally:
            sm.unregister_sse_queue(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
