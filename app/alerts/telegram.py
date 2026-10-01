"""
Telegram bot alert channel.

Sends alerts via the Telegram Bot API.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram_alert(
    symbol: str,
    message: str,
    alert_type: str,
    **kwargs,
) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        return

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram alert failed: %s %s", resp.status_code, resp.text[:200])
            else:
                logger.debug("Telegram alert sent for %s", symbol)
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)
