"""
Pydantic schemas for news items.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class NewsItemSchema(BaseModel):
    id: Optional[int] = None
    provider_id: Optional[str] = None
    symbol: Optional[str] = None
    headline: str
    summary: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    published_at: datetime
    sentiment_score: Optional[float] = None
    relevance_score: Optional[float] = None
    alignment: Optional[str] = None

    class Config:
        from_attributes = True


class NewsBundle(BaseModel):
    """Recent news for a symbol with aggregated assessment."""

    symbol: str
    items: List[NewsItemSchema] = []
    overall_alignment: str = "neutral"
    sentiment_score: float = 0.0
    has_headline_risk: bool = False
    summary: str = ""
    last_updated: Optional[datetime] = None
