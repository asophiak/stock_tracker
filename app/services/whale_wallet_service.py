"""
Whale Wallet Service
─────────────────────
Caches 13F portfolio-change diffs for 6 hours.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_cache: Optional[Dict] = None
_cache_time: Optional[datetime] = None
_CACHE_HOURS = 6
_lock = asyncio.Lock()


async def get_whale_portfolio_changes(force: bool = False) -> Dict:
    global _cache, _cache_time

    now = datetime.now(timezone.utc)
    if not force and _cache is not None and _cache_time is not None:
        age_h = (now - _cache_time).total_seconds() / 3600
        if age_h < _CACHE_HOURS:
            return _cache

    async with _lock:
        # Re-check inside lock
        now = datetime.now(timezone.utc)
        if not force and _cache is not None and _cache_time is not None:
            age_h = (now - _cache_time).total_seconds() / 3600
            if age_h < _CACHE_HOURS:
                return _cache

        logger.info("Fetching whale 13F portfolio changes from EDGAR…")
        from app.providers.whale_wallets import fetch_whale_portfolio_changes
        data = await fetch_whale_portfolio_changes()
        _cache = data
        _cache_time = now
        logger.info("Whale portfolio changes cached: %d funds", len(data))
        return data
