"""Console alert channel."""
from __future__ import annotations

import logging

logger = logging.getLogger("alerts")


async def send_console_alert(symbol: str, message: str, alert_type: str, **kwargs) -> None:
    border = "=" * 60
    logger.info("\n%s\n%s\n%s", border, message, border)
