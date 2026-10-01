"""
News cache service.

Maintains an in-memory cache of recent news per symbol,
and provides persistence to the DB for deduplication.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.news import NewsItem
from app.providers.base import NewsProvider
from app.schemas.news import NewsBundle, NewsItemSchema
from app.signal_engine.news_scorer import _compute_news_score

logger = logging.getLogger(__name__)

# In-memory news cache: symbol → list of NewsItemSchema (sorted newest first)
_news_cache: Dict[str, List[NewsItemSchema]] = defaultdict(list)
_MAX_PER_SYMBOL = 50


def get_cached_news(symbol: str) -> List[NewsItemSchema]:
    return list(_news_cache[symbol])


def cache_news_item(item: NewsItemSchema) -> None:
    sym = item.symbol or "general"
    existing_ids = {i.provider_id for i in _news_cache[sym] if i.provider_id}
    if item.provider_id and item.provider_id in existing_ids:
        return  # deduplicate
    _news_cache[sym].insert(0, item)
    _news_cache[sym] = _news_cache[sym][:_MAX_PER_SYMBOL]


def _fetch_yfinance_news(symbol: str) -> List[NewsItemSchema]:
    """Synchronous yfinance news fetch used as fallback when stream unavailable."""
    try:
        import yfinance as yf
        from datetime import timezone as _tz
        raw = yf.Ticker(symbol).news or []
        items = []
        for art in raw[:20]:
            # yfinance ≥0.2.50 uses nested content dict
            c = art.get("content") or art
            headline = c.get("title") or c.get("headline") or art.get("title") or ""
            summary  = c.get("summary") or c.get("description") or ""
            source   = (c.get("provider") or {}).get("displayName") or \
                       c.get("publisher") or art.get("publisher") or "Yahoo Finance"
            url = (c.get("canonicalUrl") or c.get("clickThroughUrl") or {}).get("url") or \
                  c.get("link") or art.get("link") or ""

            # Parse publication date — may be ISO string or unix timestamp
            raw_ts = c.get("pubDate") or c.get("displayTime") or \
                     art.get("providerPublishTime")
            if isinstance(raw_ts, str):
                try:
                    pub = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except Exception:
                    pub = datetime.now(_tz.utc)
            elif isinstance(raw_ts, (int, float)):
                pub = datetime.fromtimestamp(raw_ts, tz=_tz.utc)
            else:
                pub = datetime.now(_tz.utc)

            if not headline:
                continue
            items.append(NewsItemSchema(
                provider_id=art.get("id") or url or headline[:80],
                symbol=symbol,
                headline=headline,
                summary=summary,
                source=source,
                url=url,
                published_at=pub,
            ))
        return items
    except Exception as exc:
        logger.debug("yfinance news fallback failed for %s: %s", symbol, exc)
        return []


def get_news_bundle(symbol: str, proposed_direction: int = 0) -> NewsBundle:
    items = get_cached_news(symbol)

    # Fallback: pull from yfinance when cache is empty (no Alpaca stream)
    if not items:
        fresh = _fetch_yfinance_news(symbol)
        for item in fresh:
            cache_news_item(item)
        items = get_cached_news(symbol)

    if not items:
        return NewsBundle(
            symbol=symbol,
            items=[],
            overall_alignment="neutral",
            sentiment_score=0.0,
            summary="No recent news.",
            last_updated=None,
        )

    result = _compute_news_score(symbol, items, proposed_direction)
    last_updated = items[0].published_at if items else None

    return NewsBundle(
        symbol=symbol,
        items=items[:10],
        overall_alignment=result.alignment.value,
        sentiment_score=round(result.score, 2),
        has_headline_risk=result.has_headline_risk,
        summary=result.summary,
        last_updated=last_updated,
    )


async def fetch_and_cache_news(
    symbol: str,
    provider: NewsProvider,
    db: AsyncSession,
) -> List[NewsItemSchema]:
    """Fetch fresh news from provider, dedupe against DB, cache in memory."""
    try:
        items = await provider.fetch_recent_news(symbol, limit=20)
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", symbol, exc)
        return get_cached_news(symbol)

    new_count = 0
    for item in items:
        item.symbol = symbol
        cache_news_item(item)

        # Persist new items to DB
        if item.provider_id:
            result = await db.execute(
                select(NewsItem).where(NewsItem.provider_id == item.provider_id)
            )
            if result.scalar_one_or_none() is None:
                db.add(NewsItem(
                    provider_id=item.provider_id,
                    symbol=symbol,
                    headline=item.headline,
                    summary=item.summary,
                    source=item.source,
                    url=item.url,
                    published_at=item.published_at,
                ))
                new_count += 1

    if new_count > 0:
        await db.commit()
        logger.debug("Persisted %d new news items for %s", new_count, symbol)

    return get_cached_news(symbol)


def handle_incoming_news(item: NewsItemSchema) -> None:
    """Called by news stream callback for real-time news."""
    if item.symbol:
        cache_news_item(item)
        logger.info("📰 [%s] %s", item.symbol, item.headline[:80])
    else:
        # Try to match to watchlist symbols
        from app.config import settings
        for sym in settings.watchlist_symbols:
            if sym.lower() in (item.headline or "").lower():
                item.symbol = sym
                cache_news_item(item)
                logger.info("📰 [%s match] %s", sym, item.headline[:80])
                break
