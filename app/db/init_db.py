"""
Database initialisation: creates all tables on startup.
Uses automatic table creation (no Alembic needed for SQLite dev usage).
"""
from __future__ import annotations

import logging

from app.db.base import Base
from app.db.session import engine

# Import all models so their metadata is registered before create_all
from app.models import (  # noqa: F401
    market_data,
    news,
    session_model,
    settings as settings_model,
    signals,
    symbol,
    trading,   # registers PaperOrder, PaperPosition, ClosedTrade, ComponentAccuracy
)

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Create all tables that don't yet exist. Safe to run on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_add_learning_columns()
    await _backfill_thesis_quality()
    await _backfill_strategy_version()
    logger.info("Database tables created / verified.")


async def _migrate_add_learning_columns() -> None:
    """
    Safely add learning-related columns to existing tables.
    Uses ALTER TABLE IF NOT EXISTS (SQLite ignores duplicate-column errors).
    """
    from sqlalchemy import text
    new_cols = [
        ("closed_trades",   "entry_signal_json", "TEXT"),
        ("closed_trades",   "time_bucket",        "VARCHAR(16)"),
        ("closed_trades",   "grade",              "VARCHAR(4)"),
        ("closed_trades",   "lesson",             "TEXT"),
        ("paper_positions", "entry_signal_json",  "TEXT"),
        ("closed_trades",   "thesis_quality",     "VARCHAR(16)"),
        ("closed_trades",   "strategy_version",   "VARCHAR(8)"),
        ("paper_positions", "strategy_version",   "VARCHAR(8)"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_cols:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
            except Exception:
                pass  # Column already exists — safe to ignore


async def _backfill_thesis_quality() -> None:
    """
    Backfill thesis_quality for all historical closed_trades that have no value yet.

      valid           → both stop_price AND take_profit_price are set
      partial         → only one of stop / TP is set
      missing_thesis  → neither is set (the common pre-Phase-4B state)

    Safe to run on every startup — the WHERE clause skips already-filled rows.
    """
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("""
            UPDATE closed_trades
            SET thesis_quality = CASE
                WHEN stop_price IS NOT NULL AND take_profit_price IS NOT NULL THEN 'valid'
                WHEN stop_price IS NOT NULL OR  take_profit_price IS NOT NULL THEN 'partial'
                ELSE 'missing_thesis'
            END
            WHERE thesis_quality IS NULL
        """))


async def _backfill_strategy_version() -> None:
    """
    Backfill strategy_version for historical closed_trades.

    All trades with NULL strategy_version predate Phase 4B deployment, so they
    are classified as "pre_4b".  New positions opened after this migration will
    have strategy_version="4b" set explicitly in _open_or_add_position.

    Safe to run every startup — WHERE clause skips already-filled rows.
    """
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("""
            UPDATE closed_trades
            SET    strategy_version = 'pre_4b'
            WHERE  strategy_version IS NULL
        """))


async def drop_all() -> None:
    """Drop ALL tables — development/testing use only."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.warning("All database tables dropped.")
