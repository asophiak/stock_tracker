from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

import requests

from app.schemas.macro import AAIIData, NAAIMData

logger = logging.getLogger(__name__)

_NAAIM_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}
_AAII_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}
_CACHE_TTL = 6 * 3600  # 6 hours

_NAAIM_PAGE_URL = "https://www.naaim.org/naaim-exposure-index/"
_AAII_PAGE_URL = "https://www.aaii.com/sentimentsurvey"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_naaim_sync() -> Optional[NAAIMData]:
    now = time.time()
    if _NAAIM_CACHE["data"] is not None and now < _NAAIM_CACHE["expires_at"]:
        return _NAAIM_CACHE["data"]

    try:
        from bs4 import BeautifulSoup

        resp = requests.get(_NAAIM_PAGE_URL, timeout=15, headers=_HEADERS)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tr")

        if len(rows) < 2:
            logger.warning("naaim: table not found or empty")
            return _NAAIM_CACHE.get("data")

        # Rows: header row then data rows newest-first
        data_rows = []
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if cells and len(cells) >= 2:
                try:
                    float(cells[1])  # value must be numeric
                    data_rows.append(cells)
                except (ValueError, IndexError):
                    continue

        if not data_rows:
            logger.warning("naaim: no numeric data rows found")
            return _NAAIM_CACHE.get("data")

        last_row = data_rows[0]   # newest first
        prev_row = data_rows[1] if len(data_rows) >= 2 else None

        value = float(last_row[1])
        prev_value = float(prev_row[1]) if prev_row else None
        reading_date = last_row[0]

        # Trend
        if prev_value is not None:
            if value > prev_value + 3:
                trend = "rising"
            elif value < prev_value - 3:
                trend = "falling"
            else:
                trend = "flat"
        else:
            trend = "flat"

        # Interpretation
        if value >= 80:
            interpretation = "risk-on"
        elif value >= 50:
            interpretation = "moderately-bullish"
        elif value >= 25:
            interpretation = "neutral"
        else:
            interpretation = "risk-off"

        result = NAAIMData(
            reading_date=reading_date,
            value=value,
            previous_value=prev_value,
            trend=trend,
            interpretation=interpretation,
        )
        _NAAIM_CACHE["data"] = result
        _NAAIM_CACHE["expires_at"] = time.time() + _CACHE_TTL
        return result

    except Exception as exc:
        logger.warning("naaim: fetch failed: %s", exc)
        return _NAAIM_CACHE.get("data")


def _fetch_aaii_sync() -> Optional[AAIIData]:
    """
    Attempt to scrape AAII sentiment from their public page.
    AAII blocks automated requests with bot protection, so this may return None.
    The UI handles None gracefully.
    """
    now = time.time()
    if _AAII_CACHE["data"] is not None and now < _AAII_CACHE["expires_at"]:
        return _AAII_CACHE["data"]

    try:
        from bs4 import BeautifulSoup

        s = requests.Session()
        s.headers.update(_HEADERS)
        resp = s.get(_AAII_PAGE_URL, timeout=15)

        # Check for bot protection
        if "Pardon Our Interruption" in resp.text or "bot" in resp.text.lower()[:500]:
            logger.debug("aaii: bot protection detected, skipping")
            return _AAII_CACHE.get("data")

        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Parse sentiment percentages from the page
        text = soup.get_text(separator=" ", strip=True)

        def _extract_pct(pattern: str) -> Optional[float]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                return val * 100 if val <= 1.0 else val
            return None

        bullish_pct = _extract_pct(r"[Bb]ullish[:\s]*([\d.]+)%")
        neutral_pct = _extract_pct(r"[Nn]eutral[:\s]*([\d.]+)%")
        bearish_pct = _extract_pct(r"[Bb]earish[:\s]*([\d.]+)%")

        if bullish_pct is None or bearish_pct is None:
            logger.debug("aaii: could not parse sentiment percentages")
            return _AAII_CACHE.get("data")

        if neutral_pct is None:
            neutral_pct = max(0.0, 100.0 - bullish_pct - bearish_pct)

        bull_bear_spread = round(bullish_pct - bearish_pct, 2)

        if bullish_pct > 45:
            interpretation = "bullish_crowding"
        elif bearish_pct > 45:
            interpretation = "bearish_crowding"
        else:
            interpretation = "balanced_sentiment"

        result = AAIIData(
            survey_date="Latest",
            bullish_pct=round(bullish_pct, 2),
            neutral_pct=round(neutral_pct, 2),
            bearish_pct=round(bearish_pct, 2),
            bull_bear_spread=bull_bear_spread,
            interpretation=interpretation,
        )
        _AAII_CACHE["data"] = result
        _AAII_CACHE["expires_at"] = time.time() + _CACHE_TTL
        return result

    except Exception as exc:
        logger.warning("aaii: fetch failed: %s", exc)
        return _AAII_CACHE.get("data")


async def get_naaim_data() -> Optional[NAAIMData]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch_naaim_sync)
    except Exception as exc:
        logger.warning("naaim: async error: %s", exc)
        return None


async def get_aaii_data() -> Optional[AAIIData]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch_aaii_sync)
    except Exception as exc:
        logger.warning("aaii: async error: %s", exc)
        return None
