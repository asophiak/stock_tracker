"""
Bot wallet persistence service.

Saves bot_cash and bot_realized_pnl to the system_settings key-value table
after every trade so the values survive the daily launchd restart.

On startup, main.py calls load_bot_state() and restores the values into
StateManager rather than resetting to BOT_PAPER_CAPITAL each morning.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import SystemSetting

logger = logging.getLogger(__name__)

_KEY_CASH    = "bot_cash"
_KEY_PNL     = "bot_realized_pnl"
_KEY_RESTART = "bot_last_restart"


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get(db: AsyncSession, key: str) -> Optional[str]:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def _set(db: AsyncSession, key: str, value: str, description: str = "") -> None:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value, description=description))
    await db.flush()


# ── Public API ────────────────────────────────────────────────────────────────

async def load_bot_state(db: AsyncSession) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (bot_cash, bot_realized_pnl) from the DB.
    Returns (None, None) if no saved state exists yet (first ever run).
    """
    cash_str = await _get(db, _KEY_CASH)
    pnl_str  = await _get(db, _KEY_PNL)
    try:
        cash = float(cash_str) if cash_str is not None else None
        pnl  = float(pnl_str)  if pnl_str  is not None else None
        return cash, pnl
    except (ValueError, TypeError):
        logger.warning(
            "bot_state: could not parse persisted values (cash=%s pnl=%s) — "
            "will start from default capital.",
            cash_str, pnl_str,
        )
        return None, None


async def save_bot_state(
    db: AsyncSession,
    bot_cash: float,
    bot_realized_pnl: float,
) -> None:
    """
    Persist current wallet values. Call after every trade open/close so
    a restart never loses more than one trade's worth of state.
    """
    await _set(db, _KEY_CASH, str(round(bot_cash, 6)),
               "Bot available cash — persisted across restarts")
    await _set(db, _KEY_PNL,  str(round(bot_realized_pnl, 6)),
               "Bot cumulative realized P&L")
    await db.commit()


async def record_restart_time(db: AsyncSession) -> None:
    """Stamp the current UTC time as the last server startup."""
    ts = datetime.now(timezone.utc).isoformat()
    await _set(db, _KEY_RESTART, ts, "Last server startup timestamp (UTC ISO-8601)")
    await db.commit()


async def get_restart_time(db: AsyncSession) -> Optional[str]:
    """Return the ISO-8601 UTC timestamp of the last server startup, or None."""
    return await _get(db, _KEY_RESTART)
