"""
Symbol / watchlist management service.
Syncs DB symbols with config watchlist and handles runtime add/remove.
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.symbol import Symbol

logger = logging.getLogger(__name__)


async def sync_watchlist(db: AsyncSession) -> List[str]:
    """
    Ensure all configured watchlist symbols exist in the DB.
    Returns the current active symbol list.
    """
    config_symbols = settings.watchlist_symbols
    regime_symbols = {"SPY", "QQQ"}

    for sym in config_symbols:
        result = await db.execute(select(Symbol).where(Symbol.symbol == sym))
        existing = result.scalar_one_or_none()
        if existing is None:
            db.add(Symbol(
                symbol=sym,
                active=True,
                is_regime_symbol=sym in regime_symbols,
            ))
        else:
            existing.active = True
    await db.commit()
    return config_symbols


async def get_active_symbols(db: AsyncSession) -> List[str]:
    result = await db.execute(
        select(Symbol.symbol).where(Symbol.active == True)
    )
    return [row[0] for row in result.all()]


async def add_symbol(db: AsyncSession, symbol: str) -> Symbol:
    symbol = symbol.upper().strip()
    result = await db.execute(select(Symbol).where(Symbol.symbol == symbol))
    existing = result.scalar_one_or_none()
    if existing:
        existing.active = True
        await db.commit()
        return existing
    new_sym = Symbol(symbol=symbol, active=True)
    db.add(new_sym)
    await db.commit()
    await db.refresh(new_sym)
    logger.info("Added symbol: %s", symbol)
    return new_sym


async def remove_symbol(db: AsyncSession, symbol: str) -> bool:
    symbol = symbol.upper().strip()
    result = await db.execute(select(Symbol).where(Symbol.symbol == symbol))
    existing = result.scalar_one_or_none()
    if existing:
        existing.active = False
        await db.commit()
        logger.info("Deactivated symbol: %s", symbol)
        return True
    return False


async def get_watchlist(db: AsyncSession) -> List[Symbol]:
    result = await db.execute(
        select(Symbol).where(Symbol.active == True).order_by(Symbol.symbol)
    )
    return list(result.scalars().all())
