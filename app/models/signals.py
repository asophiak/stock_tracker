"""
Signal snapshot and alert ORM models.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalSnapshot(Base):
    """Persisted record of a scored signal event."""

    __tablename__ = "signal_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[int] = mapped_column(Integer)   # +1 long, -1 short, 0 neutral
    color: Mapped[str] = mapped_column(String(16))    # NEUTRAL, GREEN, RED, FLASH_GREEN, FLASH_RED
    label: Mapped[str] = mapped_column(String(24))    # NO_TRADE, WATCH, POSSIBLE_TRADE, IMMEDIATE_TRADE
    price: Mapped[float | None] = mapped_column(Float)
    vwap: Mapped[float | None] = mapped_column(Float)

    # Component scores
    score_technical: Mapped[float | None] = mapped_column(Float)
    score_candlestick: Mapped[float | None] = mapped_column(Float)
    score_volume: Mapped[float | None] = mapped_column(Float)
    score_vwap: Mapped[float | None] = mapped_column(Float)
    score_regime: Mapped[float | None] = mapped_column(Float)
    score_news: Mapped[float | None] = mapped_column(Float)

    # Thesis (JSON text)
    thesis_json: Mapped[str | None] = mapped_column(Text)

    is_best_trade_of_day: Mapped[bool] = mapped_column(Boolean, default=False)
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)  # YYYY-MM-DD

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PatternHistoryEntry(Base):
    """One row per detected candlestick pattern event per symbol."""

    __tablename__ = "pattern_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pattern_name: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[int] = mapped_column(Integer)       # +1 bull, -1 bear
    strength: Mapped[float] = mapped_column(Float)        # 0.0 – 1.0
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    signal_score: Mapped[float | None] = mapped_column(Float)
    signal_label: Mapped[str | None] = mapped_column(String(24))
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)


class AlertRecord(Base):
    """Persisted alert for deduplication and audit trail."""

    __tablename__ = "alert_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(32))  # flash_green, flash_red, trade, etc.
    message: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(8))
    channels: Mapped[str | None] = mapped_column(String(128))  # comma-sep: console,telegram,discord
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)
