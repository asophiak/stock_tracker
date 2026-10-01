"""
Discord webhook alert channel.

Posts alerts to a Discord channel via an incoming webhook URL.
Requires DISCORD_WEBHOOK_URL in .env.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_discord_alert(
    symbol: str,
    message: str,
    alert_type: str,
    score: float = 0,
    direction: str = "",
    **kwargs,
) -> None:
    webhook_url = settings.DISCORD_WEBHOOK_URL
    if not webhook_url:
        return

    color = 0x00FF00 if direction == "long" else 0xFF0000  # green or red

    embed = {
        "title": f"{'🟢' if direction == 'long' else '🔴'} {symbol} — {alert_type.upper()}",
        "description": message[:2000],
        "color": color,
        "footer": {"text": f"Score: {score:.0f}/100"},
    }

    payload = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code not in (200, 204):
                logger.warning("Discord alert failed: %s", resp.status_code)
            else:
                logger.debug("Discord alert sent for %s", symbol)
    except Exception as exc:
        logger.warning("Discord send error: %s", exc)
