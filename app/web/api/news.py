"""News endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.news_service import get_news_bundle, _news_cache

router = APIRouter(prefix="/api/news")


@router.get("/{symbol}")
async def symbol_news(symbol: str):
    return get_news_bundle(symbol.upper()).model_dump()


@router.post("/{symbol}/refresh")
async def refresh_news(symbol: str):
    """Force-clear the cache for this symbol so next fetch pulls fresh yfinance news."""
    sym = symbol.upper()
    _news_cache[sym] = []
    bundle = get_news_bundle(sym)
    return bundle.model_dump()
