"""Watchlist CRUD endpoints."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.settings import WatchlistUpdateRequest
from app.services.symbol_service import (
    add_symbol,
    get_watchlist,
    remove_symbol,
)

router = APIRouter(prefix="/api/watchlist")


@router.get("")
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    symbols = await get_watchlist(db)
    return [{"symbol": s.symbol, "active": s.active, "is_regime": s.is_regime_symbol} for s in symbols]


@router.post("")
async def update_watchlist(req: WatchlistUpdateRequest, db: AsyncSession = Depends(get_db)):
    added = []
    for sym in req.symbols:
        sym_obj = await add_symbol(db, sym)
        added.append(sym_obj.symbol)
    return {"added": added}


@router.delete("/{symbol}")
async def delete_from_watchlist(symbol: str, db: AsyncSession = Depends(get_db)):
    ok = await remove_symbol(db, symbol)
    if not ok:
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist")
    return {"removed": symbol.upper()}
