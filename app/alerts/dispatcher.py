"""
Alert dispatcher: routes alerts to all enabled channels.

Persists alert records to DB for audit trail.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.alerts.console import send_console_alert
from app.alerts.discord import send_discord_alert
from app.alerts.telegram import send_telegram_alert
from app.config import settings
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)


class AlertDispatcher:

    def __init__(self, db_factory) -> None:
        self._db_factory = db_factory

    async def dispatch(
        self,
        symbol: str,
        message: str,
        alert_type: str,
        score: float = 0.0,
        direction: str = "",
    ) -> None:
        channels: list[str] = []

        # Console always fires
        await send_console_alert(symbol, message, alert_type, score=score, direction=direction)
        channels.append("console")

        # Telegram
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            await send_telegram_alert(symbol, message, alert_type, score=score, direction=direction)
            channels.append("telegram")

        # Discord
        if settings.DISCORD_WEBHOOK_URL:
            await send_discord_alert(symbol, message, alert_type, score=score, direction=direction)
            channels.append("discord")

        # Persist
        await self._persist(symbol, message, alert_type, score, direction, channels)

    async def _persist(
        self,
        symbol: str,
        message: str,
        alert_type: str,
        score: float,
        direction: str,
        channels: list[str],
    ) -> None:
        try:
            from app.models.signals import AlertRecord
            async with self._db_factory() as db:
                record = AlertRecord(
                    symbol=symbol,
                    alert_type=alert_type,
                    message=message[:1000],
                    score=score,
                    direction=direction,
                    channels=",".join(channels),
                    session_date=session_date_str(),
                )
                db.add(record)
                await db.commit()
        except Exception as exc:
            logger.warning("Alert persistence failed: %s", exc)
