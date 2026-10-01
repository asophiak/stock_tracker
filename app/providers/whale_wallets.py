"""
Whale Portfolio Change Tracker
───────────────────────────────
Diffs consecutive 13F-HR filings for each tracked fund to surface
new buys, additions, reductions, and closed positions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.providers.sec_edgar import (
    TRACKED_WHALES, _BASE, _HEADERS, get_13f_holdings
)

logger = logging.getLogger(__name__)

ACTION_META = {
    "new_buy":   {"label": "NEW BUY",  "color": "bullish", "icon": "🟢"},
    "added":     {"label": "ADDED",    "color": "bullish", "icon": "➕"},
    "reduced":   {"label": "REDUCED",  "color": "bearish", "icon": "➖"},
    "closed":    {"label": "SOLD OUT", "color": "bearish", "icon": "⛔"},
    "new_put":   {"label": "NEW PUT",  "color": "bearish", "icon": "🔻"},
    "added_put": {"label": "PUT ↑",    "color": "bearish", "icon": "⬇️"},
}


async def _get(client: httpx.AsyncClient, url: str, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            r = await client.get(url, headers=_HEADERS, timeout=15.0)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning("EDGAR rate-limited (429) for %s — waiting %ds", url, wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("EDGAR non-200 %s → %d", url, r.status_code)
        except Exception as exc:
            logger.warning("EDGAR fetch error %s: %s", url, exc)
        break
    return None


async def _get_recent_13f_pairs(client: httpx.AsyncClient, cik: str, n: int = 2) -> List[Tuple[str, str]]:
    """Return last n (accession_number, filing_date) tuples for 13F-HR filings."""
    url = f"{_BASE}/submissions/CIK{cik}.json"
    data = await _get(client, url)
    if not data:
        return []
    filings = data.get("filings", {}).get("recent", {})
    forms   = filings.get("form", [])
    accnums = filings.get("accessionNumber", [])
    dates   = filings.get("filingDate", [])
    found = []
    for form, acc, date in zip(forms, accnums, dates):
        if form == "13F-HR":
            found.append((acc, date))
        if len(found) == n:
            break
    return found  # most recent first


def _diff_holdings(old_list: List[Dict], new_list: List[Dict]) -> List[Dict]:
    def _key(h):
        return (h["name"], (h.get("put_call") or "").upper())

    old_map = {_key(h): h for h in old_list}
    new_map = {_key(h): h for h in new_list}
    changes = []

    for k, nw in new_map.items():
        old = old_map.get(k)
        ns = nw.get("shares", 0)
        nv = nw.get("value_usd", 0)
        is_put = k[1] == "PUT"
        if not ns:
            continue
        if old is None or old.get("shares", 0) == 0:
            changes.append(_make_change(nw, None, "new_put" if is_put else "new_buy"))
        else:
            os_ = old["shares"]
            if ns > os_:
                changes.append(_make_change(nw, old, "added_put" if is_put else "added"))
            elif ns < os_:
                changes.append(_make_change(nw, old, "reduced"))

    for k, old in old_map.items():
        if k not in new_map and old.get("shares", 0) > 0:
            fake = {"name": old["name"], "ticker": old.get("ticker",""), "put_call": old.get("put_call",""), "shares": 0, "value_usd": 0}
            changes.append(_make_change(fake, old, "closed"))

    changes.sort(key=lambda x: -(x.get("new_value") or x.get("old_value") or 0))
    return changes


def _make_change(nw: Dict, old: Optional[Dict], action_key: str) -> Dict:
    ns = nw.get("shares", 0)
    nv = nw.get("value_usd", 0)
    os_ = old["shares"] if old else 0
    ov  = old["value_usd"] if old else 0
    pct = round((ns - os_) / os_ * 100, 1) if os_ else None
    return {
        "issuer":     nw["name"],
        "ticker":     nw.get("ticker", ""),
        "put_call":   (nw.get("put_call") or "").upper(),
        "action_key": action_key,
        "old_shares": os_,
        "new_shares": ns,
        "old_value":  ov,
        "new_value":  nv,
        "pct_change": pct,
    }


def _quarter_label(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        q = (dt.month - 1) // 3 + 1
        return f"Q{q} {dt.year}"
    except Exception:
        return date_str


async def fetch_whale_portfolio_changes() -> Dict:
    """
    For every tracked whale, diff last 2 × 13F-HR filings.
    Returns dict: fund_name → { date_new, date_old, quarter, changes[], total_positions }
    """
    result = {}
    async with httpx.AsyncClient() as client:
        for cik, fund_name in TRACKED_WHALES.items():
            try:
                pairs = await _get_recent_13f_pairs(client, cik, 2)
                if not pairs:
                    logger.debug("No 13F filings found for %s", fund_name)
                    continue

                new_acc, new_date = pairs[0]
                old_acc = pairs[1][0] if len(pairs) > 1 else None
                old_date = pairs[1][1] if len(pairs) > 1 else None

                new_holdings = await get_13f_holdings(client, cik, new_acc)
                await asyncio.sleep(0.6)
                old_holdings = await get_13f_holdings(client, cik, old_acc) if old_acc else []
                if old_acc:
                    await asyncio.sleep(0.6)

                if not new_holdings:
                    continue

                changes = _diff_holdings(old_holdings, new_holdings)
                result[fund_name] = {
                    "cik":             cik,
                    "date_new":        new_date,
                    "date_old":        old_date,
                    "quarter":         _quarter_label(new_date),
                    "changes":         changes[:25],
                    "total_positions": len(new_holdings),
                    "total_changes":   len(changes),
                }
                logger.info("13F diff: %s → %d changes (%s)", fund_name, len(changes), new_date)

            except Exception as exc:
                logger.warning("Whale diff error for %s: %s", fund_name, exc)

    return result
