"""Aggregates all API sub-routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.web.api import (
    diagnostics,
    health,
    ml,
    news,
    session,
    settings_api,
    signals,
    symbols,
    trading,
    watchlist,
    whales,
)
from app.web.api import macro as macro_api

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(watchlist.router)
api_router.include_router(symbols.router)
api_router.include_router(signals.router)
api_router.include_router(news.router)
api_router.include_router(trading.router)
api_router.include_router(settings_api.router)
api_router.include_router(session.router)
api_router.include_router(whales.router)
api_router.include_router(macro_api.router)
api_router.include_router(diagnostics.router)
api_router.include_router(ml.router)
