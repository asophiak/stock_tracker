from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from app.schemas.macro import MacroRates, TreasuryYield

logger = logging.getLogger(__name__)

_YIELD_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}
_RATES_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}

_YIELD_TTL = 5 * 60       # 5 minutes
_RATES_TTL = 60 * 60      # 1 hour

_YIELD_TICKERS = [
    ("^IRX", "13W", "13-Week"),
    ("^FVX", "5Y",  "5-Year"),
    ("^TNX", "10Y", "10-Year"),
    ("^TYX", "30Y", "30-Year"),
]

_FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS"


def _fetch_yields_sync() -> List[TreasuryYield]:
    now = time.time()
    if _YIELD_CACHE["data"] is not None and now < _YIELD_CACHE["expires_at"]:
        return _YIELD_CACHE["data"]

    import yfinance as yf

    results: List[TreasuryYield] = []
    for ticker_sym, tenor, label in _YIELD_TICKERS:
        try:
            t = yf.Ticker(ticker_sym)
            fi = t.fast_info
            yield_pct = fi.last_price
            prev_close = fi.previous_close

            if yield_pct is None:
                continue

            change_1d = (yield_pct - prev_close) if prev_close is not None else 0.0

            if change_1d > 0.05:
                direction = "rising"
            elif change_1d < -0.05:
                direction = "falling"
            else:
                direction = "flat"

            results.append(
                TreasuryYield(
                    tenor=tenor,
                    label=label,
                    yield_pct=round(yield_pct, 4),
                    change_1d=round(change_1d, 4),
                    direction=direction,
                )
            )
        except Exception as exc:
            logger.debug("treasury: skipping %s: %s", ticker_sym, exc)

    _YIELD_CACHE["data"] = results
    _YIELD_CACHE["expires_at"] = time.time() + _YIELD_TTL
    return results


def _fetch_rates_sync() -> MacroRates:
    now = time.time()
    if _RATES_CACHE["data"] is not None and now < _RATES_CACHE["expires_at"]:
        return _RATES_CACHE["data"]

    _fallback = MacroRates(
        fed_funds_rate=None,
        fed_funds_range="N/A",
        direction="holding",
        last_updated="unavailable",
    )

    try:
        resp = requests.get(_FRED_URL, timeout=15)
        resp.raise_for_status()
        lines = [l.strip() for l in resp.text.strip().splitlines() if l.strip()]

        # Skip header
        data_lines = [l for l in lines if not l.startswith("DATE")]
        if not data_lines:
            return _fallback

        # Get last two non-empty rows
        last_rows = []
        for line in reversed(data_lines):
            parts = line.split(",")
            if len(parts) >= 2 and parts[1].strip() and parts[1].strip() != ".":
                last_rows.append(parts)
            if len(last_rows) >= 2:
                break

        if not last_rows:
            return _fallback

        rate = float(last_rows[0][1].strip())
        prev_rate = float(last_rows[1][1].strip()) if len(last_rows) >= 2 else rate
        last_updated = last_rows[0][0].strip()

        if rate > prev_rate:
            direction = "hiking"
        elif rate < prev_rate:
            direction = "cutting"
        else:
            direction = "holding"

        fed_funds_range = f"{rate - 0.25:.2f}% \u2013 {rate:.2f}%"

        result = MacroRates(
            fed_funds_rate=rate,
            fed_funds_range=fed_funds_range,
            direction=direction,
            last_updated=last_updated,
        )
        _RATES_CACHE["data"] = result
        _RATES_CACHE["expires_at"] = time.time() + _RATES_TTL
        return result

    except Exception as exc:
        logger.warning("treasury: fed funds fetch failed: %s", exc)
        return _RATES_CACHE.get("data") or _fallback


async def get_treasury_yields() -> List[TreasuryYield]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch_yields_sync)
    except Exception as exc:
        logger.warning("treasury: yields async error: %s", exc)
        return []


async def get_macro_rates() -> MacroRates:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch_rates_sync)
    except Exception as exc:
        logger.warning("treasury: rates async error: %s", exc)
        return MacroRates(
            fed_funds_rate=None,
            fed_funds_range="N/A",
            direction="holding",
            last_updated="unavailable",
        )
