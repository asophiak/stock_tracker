"""
News item ORM model.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Provider-assigned ID for deduplication
    provider_id: Mapped[str | None] = mapped_column(String(128), index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), index=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Sentiment scoring
    sentiment_score: Mapped[float | None] = mapped_column(Float)  # -1.0 to +1.0
    relevance_score: Mapped[float | None] = mapped_column(Float)  # 0.0 to 1.0
    # News alignment label
    alignment: Mapped[str | None] = mapped_column(String(32))  # supports_long, supports_short, neutral, contradictory, headline_risk
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<NewsItem {self.symbol} '{self.headline[:40]}'>"
