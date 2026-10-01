"""
Persisted bar snapshots and daily volume baseline.
Runtime bar data lives in the in-memory StateManager; this table holds
the 20-day average volume baseline and any snapshots we choose to persist.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailyVolumeBaseline(Base):
    """Rolling 20-day average daily volume per symbol."""

    __tablename__ = "daily_volume_baselines"
    __table_args__ = (UniqueConstraint("symbol", name="uq_dvb_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    avg_volume: Mapped[float] = mapped_column(Float, nullable=False)
    sample_days: Mapped[int] = mapped_column(Integer, default=20)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BarSnapshot(Base):
    """
    Optional persistence of key bar snapshots (e.g. opening range candle).
    Primary bar storage is in-memory; this is a lightweight audit trail.
    """

    __tablename__ = "bar_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)  # 1Min, 5Min, 15Min
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    vwap: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
