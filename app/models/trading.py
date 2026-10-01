"""
Paper trading ORM models: orders, positions, closed trades,
rejected setups, and shadow trades.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaperOrder(Base):
    """A simulated or Alpaca-paper order record."""

    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8))    # buy | sell
    order_type: Mapped[str] = mapped_column(String(16))  # market | limit | stop | bracket
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    take_profit_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|filled|cancelled|rejected
    filled_price: Mapped[float | None] = mapped_column(Float)
    filled_qty: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_mode: Mapped[str] = mapped_column(String(16), default="paper_local")
    signal_id: Mapped[int | None] = mapped_column(Integer)  # FK to signal_snapshots
    thesis_summary: Mapped[str | None] = mapped_column(Text)
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)


class ClosedTrade(Base):
    """Historical record of a completed paper trade."""

    __tablename__ = "closed_trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float)
    take_profit_price: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str | None] = mapped_column(String(32))
    thesis_summary: Mapped[str | None] = mapped_column(Text)
    execution_mode: Mapped[str] = mapped_column(String(16), default="paper_local")
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Learning fields (added via migration) ──────────────────────────────────
    entry_signal_json: Mapped[str | None] = mapped_column(Text)   # JSON snapshot of signal at entry
    time_bucket: Mapped[str | None] = mapped_column(String(16))   # open|morning|midday|afternoon
    grade: Mapped[str | None] = mapped_column(String(4))          # A/B/C/D/F
    lesson: Mapped[str | None] = mapped_column(Text)              # auto-generated post-mortem
    thesis_quality: Mapped[str | None] = mapped_column(String(16))  # valid | partial | missing_thesis
    strategy_version: Mapped[str | None] = mapped_column(String(8))  # "pre_4b" | "4b"


class PaperPosition(Base):
    """Currently open paper position."""

    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float)
    take_profit_price: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    execution_mode: Mapped[str] = mapped_column(String(16), default="paper_local")
    order_id: Mapped[str | None] = mapped_column(String(64))
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)
    entry_signal_json: Mapped[str | None] = mapped_column(Text)   # snapshot at open, copied to ClosedTrade
    strategy_version: Mapped[str | None] = mapped_column(String(8))  # "pre_4b" | "4b"


class RejectedSetup(Base):
    """
    Every potential trade setup that was evaluated but NOT executed as an A or B trade.

    Tier values: "A_TRADE" | "B_TRADE" | "SHADOW" | "REJECTED"
    Rejection reasons (comma-separated when multiple apply):
      no_thesis | missing_stop_or_tp | vwap_misaligned | score_below_threshold |
      max_positions | daily_loss_limit | kill_switch | symbol_cooldown | tod_blocked |
      consecutive_losses | spread_too_wide | b_trades_disabled | ...

    outcome_pnl / outcome_winner are filled in later by shadow_trade_service
    after a defined holding period, even for fully rejected setups.
    """

    __tablename__ = "rejected_setups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    session_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    price: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[int | None] = mapped_column(Integer)   # +1 | -1
    score: Mapped[float | None] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # A_TRADE | B_TRADE | SHADOW | REJECTED
    rejection_reasons: Mapped[str | None] = mapped_column(Text)    # comma-separated

    # Condition snapshot
    vwap: Mapped[float | None] = mapped_column(Float)
    vwap_condition: Mapped[str | None] = mapped_column(String(32))  # above | below | no_vwap
    rsi: Mapped[float | None] = mapped_column(Float)
    macd_hist: Mapped[float | None] = mapped_column(Float)
    volume_rvol: Mapped[float | None] = mapped_column(Float)
    spread_pct: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(32))  # bullish | bearish | neutral
    trend_condition: Mapped[str | None] = mapped_column(String(32))

    # Stop/target from thesis (if available)
    suggested_stop: Mapped[float | None] = mapped_column(Float)
    suggested_target: Mapped[float | None] = mapped_column(Float)

    # Hypothetical outcome — filled after holding period by shadow_trade_service
    outcome_price: Mapped[float | None] = mapped_column(Float)
    outcome_pnl: Mapped[float | None] = mapped_column(Float)     # per-share
    outcome_winner: Mapped[bool | None] = mapped_column(Boolean)
    outcome_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Full signal JSON for post-mortem analysis
    signal_json: Mapped[str | None] = mapped_column(Text)


class ShadowTrade(Base):
    """
    A setup that almost qualified but did NOT execute live.
    Tracks hypothetical entry/stop/target and records final outcome.

    strategy: "predictor" | "scalp"
    status: "open" | "closed_target" | "closed_stop" | "closed_signal" | "closed_eod"
    """

    __tablename__ = "shadow_trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(16), default="predictor")  # predictor | scalp
    direction: Mapped[int] = mapped_column(Integer)       # +1 long | -1 short
    score: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Excursion tracking (updated on price ticks)
    max_favorable: Mapped[float | None] = mapped_column(Float)  # MFE per share
    max_adverse: Mapped[float | None] = mapped_column(Float)    # MAE per share (always positive)

    # Outcome
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    pnl_per_share: Mapped[float | None] = mapped_column(Float)
    is_winner: Mapped[bool | None] = mapped_column(Boolean)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="open")

    # Full signal JSON at entry
    signal_json: Mapped[str | None] = mapped_column(Text)


class ComponentAccuracy(Base):
    """
    Per-trade accuracy record for each signal component.
    Used by the learning service to adapt weights over time.
    """

    __tablename__ = "component_accuracy"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    component_name: Mapped[str] = mapped_column(String(32), nullable=False)
    component_direction: Mapped[int] = mapped_column(Integer)     # +1 / -1 / 0
    was_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trade_won: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_date: Mapped[str | None] = mapped_column(String(10))
