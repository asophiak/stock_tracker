"""
FastAPI dependency injection helpers.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.utils.cache import StateManager


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session, rolling back on exception."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_state_manager() -> StateManager:
    """Return the application-wide in-memory state manager."""
    from app.main import state_manager  # avoid circular at module load
    return state_manager
