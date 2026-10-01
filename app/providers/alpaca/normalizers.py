"""
Normalise Alpaca SDK objects into our internal schemas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.schemas.market_data import Bar, Quote
from app.schemas.news import NewsItemSchema


def normalise_bar(alpaca_bar, symbol: str, timeframe: str = "1Min") -> Bar:
    """Convert an alpaca-py Bar object to our internal Bar dataclass."""
    ts = alpaca_bar.timestamp
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        timestamp=ts,
        open=float(alpaca_bar.open),
        high=float(alpaca_bar.high),
        low=float(alpaca_bar.low),
        close=float(alpaca_bar.close),
        volume=int(alpaca_bar.volume),
        vwap=float(alpaca_bar.vwap) if hasattr(alpaca_bar, "vwap") and alpaca_bar.vwap else None,
        timeframe=timeframe,
    )


def normalise_quote(alpaca_quote, symbol: str) -> Optional[Quote]:
    """Convert an alpaca-py Quote object to our internal Quote dataclass."""
    try:
        ts = alpaca_quote.timestamp
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return Quote(
            timestamp=ts,
            symbol=symbol,
            ask_price=float(alpaca_quote.ask_price),
            bid_price=float(alpaca_quote.bid_price),
            ask_size=int(alpaca_quote.ask_size),
            bid_size=int(alpaca_quote.bid_size),
        )
    except Exception:
        return None


def normalise_news(alpaca_news, symbol: Optional[str] = None) -> NewsItemSchema:
    """Convert an alpaca-py News object to our internal NewsItemSchema."""
    ts = alpaca_news.created_at if hasattr(alpaca_news, "created_at") else datetime.now(timezone.utc)
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # alpaca-py news has symbols list
    item_symbol = symbol
    if item_symbol is None and hasattr(alpaca_news, "symbols") and alpaca_news.symbols:
        item_symbol = alpaca_news.symbols[0]

    return NewsItemSchema(
        provider_id=str(alpaca_news.id) if hasattr(alpaca_news, "id") else None,
        symbol=item_symbol,
        headline=alpaca_news.headline if hasattr(alpaca_news, "headline") else "",
        summary=alpaca_news.summary if hasattr(alpaca_news, "summary") else None,
        source=alpaca_news.source if hasattr(alpaca_news, "source") else None,
        url=alpaca_news.url if hasattr(alpaca_news, "url") else None,
        published_at=ts,
    )
