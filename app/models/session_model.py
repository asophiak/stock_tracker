"""
Daily session summary ORM model.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailySessionSummary(Base):
    __tablename__ = "daily_session_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    trades_taken: Mapped[int] = mapped_column(Integer, default=0)
    winners: Mapped[int] = mapped_column(Integer, default=0)
    losers: Mapped[int] = mapped_column(Integer, default=0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    best_trade_symbol: Mapped[str | None] = mapped_column(String(16))
    best_trade_score: Mapped[float | None] = mapped_column(Float)
    best_trade_direction: Mapped[str | None] = mapped_column(String(8))
    best_trade_thesis: Mapped[str | None] = mapped_column(Text)
    regime_summary: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
