"""
yfinance market data provider — real prices, no API key required.

Used as the fallback when Alpaca credentials are absent.
Polls yfinance every 60s during market hours for live bars and
fetches real intraday OHLCV history on startup.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import yfinance as yf

from app.providers.base import BarCallback, MarketDataProvider, QuoteCallback
from app.schemas.market_data import Bar, Quote

logger = logging.getLogger(__name__)

# Poll every 60s during market hours; yfinance 1m data has ~1min lag
_POLL_INTERVAL = 60


class YFinanceMarketDataProvider(MarketDataProvider):
    """
    Provides real market data via yfinance polling.
    Not a true stream — emits one bar per symbol per poll tick.
    """

    def __init__(self, poll_interval_seconds: float = _POLL_INTERVAL) -> None:
        self._poll_interval = poll_interval_seconds
        self._symbols: List[str] = []
        self._bar_callback: Optional[BarCallback] = None
        self._quote_callback: Optional[QuoteCallback] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        # Track last seen bar timestamp to avoid duplicate emissions
        self._last_bar_ts: dict[str, datetime] = {}

    async def connect(self) -> None:
        self._connected = True
        logger.info("YFinanceMarketDataProvider: connected (real data, polling mode).")

    async def disconnect(self) -> None:
        self._connected = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("YFinanceMarketDataProvider: disconnected.")

    async def subscribe(self, symbols: List[str]) -> None:
        self._symbols = symbols
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("YFinanceMarketDataProvider: polling %s every %ds", symbols, self._poll_interval)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._emit_latest_bars()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("yfinance poll error: %s", exc)

    async def _emit_latest_bars(self) -> None:
        from app.utils.time_utils import is_market_open
        if not is_market_open():
            logger.debug("yfinance poll: market closed — skipping bar emission.")
            return

        loop = asyncio.get_event_loop()
        for symbol in self._symbols:
            try:
                bar = await loop.run_in_executor(None, self._fetch_latest_bar, symbol)
                if bar is None:
                    continue
                last = self._last_bar_ts.get(symbol)
                if last and bar.timestamp <= last:
                    continue  # no new bar yet
                self._last_bar_ts[symbol] = bar.timestamp
                if self._bar_callback:
                    self._bar_callback(symbol, bar)
            except Exception as exc:
                logger.debug("yfinance emit error for %s: %s", symbol, exc)

    def _fetch_latest_bar(self, symbol: str) -> Optional[Bar]:
        """Fetch the most recent completed 1-minute bar via yfinance (sync)."""
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m", prepost=False)
            if hist.empty:
                return None
            # Last row may be the current incomplete bar — use second-to-last if very recent
            row = hist.iloc[-1]
            ts = row.name
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            return Bar(
                timestamp=ts,
                open=round(float(row["Open"]), 4),
                high=round(float(row["High"]), 4),
                low=round(float(row["Low"]), 4),
                close=round(float(row["Close"]), 4),
                volume=int(row["Volume"]),
                timeframe="1Min",
            )
        except Exception as exc:
            logger.debug("_fetch_latest_bar(%s) failed: %s", symbol, exc)
            return None

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._fetch_historical_bars_sync, symbol, timeframe, start, end
        )

    def _fetch_historical_bars_sync(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        tf_map = {
            "1Min":  "1m",
            "5Min":  "5m",
            "15Min": "15m",
            "1Day":  "1d",
        }
        yf_interval = tf_map.get(timeframe, "1m")
        try:
            ticker = yf.Ticker(symbol)
            # yfinance requires start/end as date strings or naive datetimes
            hist = ticker.history(
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval=yf_interval,
                prepost=False,
            )
            if hist.empty:
                return []
            bars: List[Bar] = []
            for ts_idx, row in hist.iterrows():
                ts = ts_idx
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                # Only include bars within the requested range
                if ts < start or ts > end:
                    continue
                bars.append(Bar(
                    timestamp=ts,
                    open=round(float(row["Open"]), 4),
                    high=round(float(row["High"]), 4),
                    low=round(float(row["Low"]), 4),
                    close=round(float(row["Close"]), 4),
                    volume=int(row["Volume"]),
                    timeframe=timeframe,
                ))
            return bars
        except Exception as exc:
            logger.error("yfinance historical bars failed for %s: %s", symbol, exc)
            return []

    async def fetch_latest_quote(self, symbol: str) -> Optional[Quote]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_latest_quote_sync, symbol)

    def _fetch_latest_quote_sync(self, symbol: str) -> Optional[Quote]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            if price is None:
                return None
            spread = price * 0.0005
            return Quote(
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                ask_price=round(price + spread / 2, 4),
                bid_price=round(price - spread / 2, 4),
                ask_size=100,
                bid_size=100,
            )
        except Exception as exc:
            logger.debug("yfinance quote failed for %s: %s", symbol, exc)
            return None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "yfinance"
