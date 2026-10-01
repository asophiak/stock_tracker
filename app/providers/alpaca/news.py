"""
Alpaca news provider: real-time streaming + historical REST news.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from app.providers.base import NewsCallback, NewsProvider
from app.providers.alpaca.client import get_historical_client, get_news_stream
from app.providers.alpaca.normalizers import normalise_news
from app.schemas.news import NewsItemSchema

logger = logging.getLogger(__name__)


class AlpacaNewsProvider(NewsProvider):

    def __init__(self) -> None:
        self._stream = None
        self._hist_client = None
        self._news_callback: Optional[NewsCallback] = None
        self._connected = False
        self._stream_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        self._hist_client = get_historical_client()
        self._stream = get_news_stream()
        if self._stream is None:
            logger.info("AlpacaNewsProvider: no news stream (credentials missing or unsupported).")
        else:
            logger.info("AlpacaNewsProvider: initialised.")

    async def disconnect(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._connected = False

    async def subscribe(self, symbols: List[str]) -> None:
        if not self._stream:
            return
        try:
            async def _handle_news(news) -> None:
                try:
                    item = normalise_news(news)
                    if self._news_callback:
                        self._news_callback(item)
                except Exception as exc:
                    logger.debug("News normalisation error: %s", exc)

            self._stream.subscribe_news(_handle_news, *symbols)
            self._stream_task = asyncio.create_task(self._run_stream())
            self._connected = True
            logger.info("Alpaca news stream subscribed to: %s", symbols)
        except Exception as exc:
            logger.warning("News stream subscribe failed: %s", exc)

    async def _run_stream(self) -> None:
        if not self._stream:
            return
        try:
            await self._stream._run_forever()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("News stream ended: %s", exc)
            self._connected = False

    async def fetch_recent_news(self, symbol: str, limit: int = 20) -> List[NewsItemSchema]:
        if not self._hist_client:
            return []
        try:
            from alpaca.data.requests import NewsRequest
            start = datetime.now(timezone.utc) - timedelta(hours=24)
            req = NewsRequest(
                symbols=[symbol],
                limit=limit,
                start=start,
            )
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: self._hist_client.get_news(req)
            )
            return [normalise_news(item, symbol) for item in (response.news if hasattr(response, "news") else response)]
        except Exception as exc:
            logger.debug("News fetch failed for %s: %s", symbol, exc)
            return []

    @property
    def is_connected(self) -> bool:
        return self._connected
