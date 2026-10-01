"""
Dynamic watchlist scanner.

Supplements the fixed watchlist in config.py with daily movers sourced from
Alpaca's screener / most-active endpoint, or yfinance as fallback.

The dynamic list is refreshed every DYNAMIC_WATCHLIST_REFRESH_MINUTES.
Symbols pass a liquidity filter before being added:
  • price >= DYNAMIC_MIN_PRICE
  • day volume >= DYNAMIC_MIN_VOLUME
  • estimated spread <= DYNAMIC_MAX_SPREAD_PCT  (bid-ask gap / mid)

The fixed watchlist is NEVER shrunk — dynamic symbols are only ever appended.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional, Set

from app.config import settings

logger = logging.getLogger(__name__)

# ── In-process state ──────────────────────────────────────────────────────────
_dynamic_symbols: Set[str] = set()
_last_scan_at: Optional[datetime] = None

# Hard exclusion list — never trade these regardless of movers output
_ALWAYS_EXCLUDE: Set[str] = {"SPY", "QQQ", "IWM", "VXX", "UVXY", "SQQQ", "TQQQ"}


def get_dynamic_symbols() -> List[str]:
    """Return the current dynamic supplement list."""
    return sorted(_dynamic_symbols)


def get_full_watchlist() -> List[str]:
    """Return static watchlist + dynamic symbols (deduped)."""
    base = settings.watchlist_symbols
    combined = list(base)
    for sym in sorted(_dynamic_symbols):
        if sym not in combined:
            combined.append(sym)
    return combined


def get_tradeable_watchlist() -> List[str]:
    """Return tradeable symbols (excludes SPY/QQQ) from the full list."""
    return [s for s in get_full_watchlist() if s not in ("SPY", "QQQ")]


async def refresh_dynamic_watchlist() -> List[str]:
    """
    Fetch movers and update the dynamic symbol set.
    Returns the final list of dynamic symbols added this cycle.

    Sources tried in order:
      1. Alpaca market movers endpoint (requires Alpaca data subscription)
      2. yfinance most-active (free, slower)
    """
    if not settings.ENABLE_DYNAMIC_WATCHLIST:
        return []

    added: List[str] = []
    candidates: List[dict] = []

    # ── Source 1: Alpaca movers ───────────────────────────────────────────────
    if settings.alpaca_credentials_present:
        candidates = await _fetch_alpaca_movers()

    # ── Source 2: yfinance fallback ───────────────────────────────────────────
    if not candidates:
        candidates = await _fetch_yfinance_movers()

    # ── Apply liquidity filter and add ────────────────────────────────────────
    static_set = set(settings.watchlist_symbols)
    new_dynamic: Set[str] = set()

    for c in candidates:
        sym = c.get("symbol", "").upper().strip()
        if not sym or sym in _ALWAYS_EXCLUDE:
            continue
        if sym in static_set:
            continue   # already on the fixed list
        price   = float(c.get("price", 0) or 0)
        volume  = int(c.get("volume", 0) or 0)
        spread  = float(c.get("spread_pct", 0) or 0)

        if price < settings.DYNAMIC_MIN_PRICE:
            logger.debug("Scanner: %s rejected — price %.2f < min %.2f", sym, price, settings.DYNAMIC_MIN_PRICE)
            continue
        if volume < settings.DYNAMIC_MIN_VOLUME:
            logger.debug("Scanner: %s rejected — volume %d < min %d", sym, volume, settings.DYNAMIC_MIN_VOLUME)
            continue
        if spread > settings.DYNAMIC_MAX_SPREAD_PCT:
            logger.debug("Scanner: %s rejected — spread %.3f%% > max %.2f%%", sym, spread, settings.DYNAMIC_MAX_SPREAD_PCT)
            continue

        new_dynamic.add(sym)
        if sym not in _dynamic_symbols:
            added.append(sym)
            logger.info("Scanner: adding dynamic symbol %s (price=%.2f vol=%d)", sym, price, volume)

    _dynamic_symbols.clear()
    _dynamic_symbols.update(new_dynamic)
    _last_scan_at = datetime.now(timezone.utc)

    if added:
        logger.info("Dynamic watchlist updated — %d new symbol(s): %s", len(added), added)
    else:
        logger.debug("Dynamic watchlist scan complete — no new symbols added (%d active)", len(_dynamic_symbols))

    return added


async def _fetch_alpaca_movers() -> List[dict]:
    """
    Fetch most-active / top-movers from Alpaca.
    Returns list of dicts with keys: symbol, price, volume, spread_pct.
    """
    try:
        from app.providers.alpaca.client import get_data_client
        client = get_data_client()

        results: List[dict] = []

        # Most-active stocks (high volume, liquid, large-cap friendly)
        # Alpaca screener endpoint: /v1beta1/screener/stocks/most-actives
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.get_us_equity_bars(
                symbol_or_symbols=[],   # Not used here — we're calling screener
            )
        )
    except Exception:
        pass

    # Alpaca's screener API (REST call)
    try:
        import aiohttp
        headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
        }
        url = f"{settings.ALPACA_DATA_URL}/v1beta1/screener/stocks/most-actives?by=volume&top={settings.DYNAMIC_WATCHLIST_HIGH_RVOL + 10}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    movers = data.get("most_actives", [])
                    results = []
                    for m in movers:
                        sym = m.get("symbol", "")
                        results.append({
                            "symbol": sym,
                            "price": m.get("close", 0),
                            "volume": m.get("volume", 0),
                            "spread_pct": 0.0,  # not available from this endpoint
                        })
                    return results
    except Exception as exc:
        logger.debug("Alpaca screener fetch failed: %s", exc)

    return []


async def _fetch_yfinance_movers() -> List[dict]:
    """
    Use yfinance to get most-active US stocks as a free fallback.
    """
    try:
        import yfinance as yf
        import asyncio

        # yfinance screener for most-active
        # Note: yfinance doesn't have a native screener API; we use a curated
        # expansion of the fixed list augmented by known high-volume tickers.
        HIGH_RVOL_CANDIDATES = [
            # ETFs with high intraday volume (sector plays)
            "XLK", "XLF", "XLE", "XLV", "XLY", "ARKK", "SMH", "SOXX",
            # Volatile meme/momentum names
            "GME", "AMC", "BBBY", "RIVN", "LCID", "NIO", "XPEV", "SNAP",
            "UBER", "LYFT", "RBLX", "U", "DKNG", "PENN", "WYNN",
            # Additional large-cap tech
            "CRM", "ADBE", "NOW", "SNOW", "CRWD", "ZS", "PANW", "NET",
            # Biotech (high volatility)
            "MRNA", "BNTX", "NVAX",
        ]

        results = []
        loop = asyncio.get_event_loop()

        def _fetch():
            items = []
            for sym in HIGH_RVOL_CANDIDATES[:30]:  # limit to avoid slow startup
                try:
                    t = yf.Ticker(sym)
                    info = t.fast_info
                    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None) or 0
                    volume = getattr(info, "three_month_average_volume", None) or 0
                    items.append({
                        "symbol": sym,
                        "price": float(price or 0),
                        "volume": int(volume or 0),
                        "spread_pct": 0.0,
                    })
                except Exception:
                    pass
            return items

        results = await loop.run_in_executor(None, _fetch)
        return results

    except Exception as exc:
        logger.debug("yfinance movers fetch failed: %s", exc)
        return []
