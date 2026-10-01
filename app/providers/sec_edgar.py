"""
SEC EDGAR 13F Institutional Holdings Provider
──────────────────────────────────────────────
Fetches 13F-HR quarterly filings from the SEC's free EDGAR API to identify
which major hedge funds / institutions hold our watchlist symbols.

No API key required.  Rate limit: 10 req/sec.

Key design: holdings are cached PER FUND for 6 hours.
fetch_whale_holdings_for_symbol() reuses the cache so we make ~30 EDGAR
calls total at startup (10 funds × 3 requests each), not 37 × 10 × 3 = 1,110.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_BASE    = "https://data.sec.gov"   # submissions / search API
_ARCHIVE = "https://www.sec.gov"    # Archives live here (not data.sec.gov)
_EFTS    = "https://efts.sec.gov"

# SEC requires: "Company Name contact@domain.com"
_HEADERS = {
    "User-Agent": "StockTrackerApp contact@stocktracker.app",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json",
}

# ── Top institutional whales we track ────────────────────────────────────────
TRACKED_WHALES: Dict[str, str] = {
    "0001067983": "Berkshire Hathaway",
    "0001697748": "ARK Invest",        # was 0001579982 (ARK ETF Trust — wrong entity)
    "0001423053": "Citadel Advisors",  # was 0001423237 (unrelated fund — wrong entity)
    "0001179392": "Two Sigma",
    "0001603466": "Point72 (S. Cohen)",
    "0001273087": "Millennium Mgmt",   # was 0001273931 (MoneyGram Inc — wrong entity)
    "0002012383": "BlackRock",         # was 0001364742 (old entity, stopped filing 2024)
    "0000102909": "Vanguard",
    "0001336528": "Soros Fund Mgmt",
}

# ── Per-fund holdings cache ───────────────────────────────────────────────────
# Stores { cik: (holdings_list, fetched_at) }
# Avoids re-downloading the full 13F XML for every watchlist symbol.
_HOLDINGS_CACHE: Dict[str, Tuple[List[Dict], datetime]] = {}
_CACHE_TTL_HOURS = 6
_cache_lock = asyncio.Lock()


def _cache_fresh(cik: str) -> bool:
    if cik not in _HOLDINGS_CACHE:
        return False
    _, fetched_at = _HOLDINGS_CACHE[cik]
    age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    return age_h < _CACHE_TTL_HOURS


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def _get(client: httpx.AsyncClient, url: str, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            r = await client.get(url, headers=_HEADERS, timeout=15.0)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 12 * (attempt + 1)
                logger.warning("EDGAR rate-limited (429) — waiting %ds before retry", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("EDGAR non-200 %d for %s", r.status_code, url)
        except Exception as exc:
            logger.warning("EDGAR fetch error (%s): %s", url, exc)
        break
    return None


# ── Core EDGAR fetchers ───────────────────────────────────────────────────────

async def get_latest_13f_accession(client: httpx.AsyncClient, cik: str) -> Optional[str]:
    """Return the accession number of the most recent 13F-HR filing for `cik`."""
    url = f"{_BASE}/submissions/CIK{cik}.json"
    data = await _get(client, url)
    if not data:
        return None

    filings = data.get("filings", {}).get("recent", {})
    forms   = filings.get("form", [])
    accnums = filings.get("accessionNumber", [])
    dates   = filings.get("filingDate", [])

    best_date, best_accnum = "", None
    for form, accnum, date in zip(forms, accnums, dates):
        if form == "13F-HR" and date > best_date:
            best_date   = date
            best_accnum = accnum
    return best_accnum


async def get_13f_holdings(
    client: httpx.AsyncClient,
    cik: str,
    accession: str,
) -> List[Dict]:
    """Fetch the holdings table from a 13F-HR filing.

    EDGAR archives live at www.sec.gov (not data.sec.gov).
    The filing index is an .htm file whose name uses the *dashed* accession number,
    e.g. 0001193125-26-054580-index.htm inside the nodash directory 000119312526054580/.
    """
    import re as _re

    acc_clean  = accession.replace("-", "")
    # Reconstruct dashed form for the index filename
    acc_dashed = f"{acc_clean[:10]}-{acc_clean[10:12]}-{acc_clean[12:]}"

    index_url = (
        f"{_ARCHIVE}/Archives/edgar/data/{int(cik)}"
        f"/{acc_clean}/{acc_dashed}-index.htm"
    )

    try:
        r = await client.get(index_url, headers=_HEADERS, timeout=15.0)
        if r.status_code != 200:
            logger.warning("EDGAR index %d for %s", r.status_code, index_url)
            return []
        html = r.text
    except Exception as exc:
        logger.warning("EDGAR index fetch error: %s", exc)
        return []

    # Parse index HTML to find the infotable XML filename.
    # Prefer files with "infotable" in name; fall back to any .xml.
    xml_file = None
    for m in _re.finditer(r'href="([^"]*infotable[^"]*\.xml)"', html, _re.IGNORECASE):
        xml_file = m.group(1).rsplit("/", 1)[-1]
        break
    if not xml_file:
        for m in _re.finditer(r'href="([^"]*\.xml)"', html, _re.IGNORECASE):
            candidate = m.group(1).rsplit("/", 1)[-1]
            if candidate.lower() not in ("xbrl_doc_only.xml",):
                xml_file = candidate
                break

    if not xml_file:
        logger.warning("EDGAR: no XML found in 13F index for CIK %s accession %s", cik, accession)
        return []

    xml_url = (
        f"{_ARCHIVE}/Archives/edgar/data/{int(cik)}"
        f"/{acc_clean}/{xml_file}"
    )
    try:
        r = await client.get(xml_url, headers=_HEADERS, timeout=20.0)
        if r.status_code != 200:
            logger.warning("EDGAR XML %d for %s", r.status_code, xml_url)
            return []
        return _parse_13f_xml(r.text)
    except Exception as exc:
        logger.warning("EDGAR XML fetch error: %s", exc)
        return []


def _parse_13f_xml(xml_text: str) -> List[Dict]:
    """Minimal regex-free XML parser for 13F holdings tables."""
    import re

    holdings = []
    text = re.sub(r'<[^/][^:>]*:', '<', xml_text)
    text = re.sub(r'</[^:>]+:', '</', text)

    for block in re.findall(r'<infoTable>(.*?)</infoTable>', text, re.DOTALL | re.IGNORECASE):
        def _tag(t: str) -> str:
            m = re.search(rf'<{t}[^>]*>(.*?)</{t}>', block, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ""

        name       = _tag("nameOfIssuer")
        ticker     = _tag("titleOfClass")
        value_str  = _tag("value")
        shares_str = _tag("sshPrnamt")
        put_call   = _tag("putCall")

        try:
            value_usd = int(value_str.replace(",", "")) * 1000 if value_str else 0
        except ValueError:
            value_usd = 0
        try:
            shares = int(shares_str.replace(",", "")) if shares_str else 0
        except ValueError:
            shares = 0

        if name:
            holdings.append({
                "name":      name.upper(),
                "ticker":    ticker.upper(),
                "value_usd": value_usd,
                "shares":    shares,
                "put_call":  put_call.upper() if put_call else "",
            })
    return holdings


# ── Cached per-fund fetch ─────────────────────────────────────────────────────

async def _fetch_and_cache_fund_holdings(cik: str, fund_name: str) -> List[Dict]:
    """
    Fetch all holdings for one fund and cache them.
    Returns [] on any failure.
    """
    async with httpx.AsyncClient() as client:
        try:
            accnum = await get_latest_13f_accession(client, cik)
            if not accnum:
                logger.warning("EDGAR: no 13F-HR found for %s (CIK %s)", fund_name, cik)
                return []
            await asyncio.sleep(0.4)
            holdings = await get_13f_holdings(client, cik, accnum)
            logger.info("EDGAR: loaded %d holdings for %s", len(holdings), fund_name)
            _HOLDINGS_CACHE[cik] = (holdings, datetime.now(timezone.utc))
            return holdings
        except Exception as exc:
            logger.warning("EDGAR: fund fetch error for %s: %s", fund_name, exc)
            return []


async def warm_holdings_cache() -> None:
    """
    Fetch 13F holdings for all tracked funds and populate the cache.
    Fetches one fund at a time with a 1-second gap to stay under EDGAR's rate limit.
    """
    logger.info("EDGAR: warming holdings cache for %d funds…", len(TRACKED_WHALES))
    for cik, fund_name in TRACKED_WHALES.items():
        if _cache_fresh(cik):
            logger.debug("EDGAR: cache hit for %s", fund_name)
            continue
        await _fetch_and_cache_fund_holdings(cik, fund_name)
        await asyncio.sleep(1.0)   # stay well under 10 req/sec
    logger.info("EDGAR: holdings cache warm — %d funds cached", len(_HOLDINGS_CACHE))


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_whale_holdings_for_symbol(symbol: str) -> List[Dict]:
    """
    Return a list of fund dicts that hold `symbol`, using the per-fund cache.
    If cache is cold for a fund, fetches it on demand.
    """
    results = []
    sym_upper = symbol.upper()

    for cik, fund_name in TRACKED_WHALES.items():
        # Use cache if fresh, otherwise skip (warm_holdings_cache handles bulk loading)
        if not _cache_fresh(cik):
            continue
        holdings, _ = _HOLDINGS_CACHE[cik]
        for h in holdings:
            if sym_upper in h["name"] or h["ticker"] == sym_upper:
                pc = h["put_call"]
                sentiment = -1 if pc == "PUT" else 1
                results.append({
                    "fund_name": fund_name,
                    "value_usd": h["value_usd"],
                    "shares":    h["shares"],
                    "put_call":  pc or "LONG",
                    "sentiment": sentiment,
                })
                break
    return results
