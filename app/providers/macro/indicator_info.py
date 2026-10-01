"""
Static descriptions + market-impact notes for major economic indicators.
Used by the /api/macro/indicator endpoint to power the dashboard pop-up.
"""
from __future__ import annotations

from typing import Optional

# Map of indicator title keywords → info dict
# Keys are lowercase substrings; first match wins.
_INDICATOR_DB: list[dict] = [
    # ── US Employment ──────────────────────────────────────────────────────────
    {
        "keys": ["non-farm payroll", "nfp", "nonfarm payroll"],
        "full_name": "Non-Farm Payrolls (NFP)",
        "frequency": "Monthly (first Friday)",
        "source": "U.S. Bureau of Labor Statistics",
        "what_it_measures": (
            "The net change in the number of paid U.S. workers excluding farm workers, "
            "government employees, private household employees, and non-profit organization "
            "employees."
        ),
        "why_it_matters": (
            "NFP is the single most market-moving economic release. A strong print signals "
            "a healthy labor market, giving the Fed room to keep rates higher; a weak print "
            "raises recession fears and rate-cut expectations. Equity volatility typically "
            "spikes ±1–2% on the S&P 500 at release."
        ),
        "market_reaction": {
            "beat": "USD ↑, Bonds sell off (yields ↑), Growth stocks ↓ short-term",
            "miss": "USD ↓, Bond rally (yields ↓), Rate-sensitive sectors ↑",
        },
        "key_thresholds": "Consensus ~150–200k/month; <100k raises alarm; >300k is very hot",
        "related_releases": "ADP Private Payrolls (Wednesday before), Unemployment Rate",
    },
    {
        "keys": ["unemployment claims", "initial claims", "jobless claims"],
        "full_name": "Initial / Continuing Jobless Claims",
        "frequency": "Weekly (every Thursday)",
        "source": "U.S. Department of Labor",
        "what_it_measures": (
            "Initial claims = first-time filers for unemployment insurance. "
            "Continuing claims = people still collecting benefits. "
            "Both are real-time leading indicators of the labor market."
        ),
        "why_it_matters": (
            "Arguably the most timely labor-market signal available — released every Thursday. "
            "A sustained rise above ~260k initial claims historically precedes recessions. "
            "Markets watch the 4-week moving average to smooth noise."
        ),
        "market_reaction": {
            "beat": "Lower claims = stronger jobs market; mild USD ↑, equities steady",
            "miss": "Higher claims = labor softening; rate-cut odds rise, growth stocks ↑",
        },
        "key_thresholds": "Normal range: 200–240k initial claims. >300k = concern.",
        "related_releases": "Non-Farm Payrolls (monthly confirmation)",
    },
    {
        "keys": ["unemployment rate"],
        "full_name": "U.S. Unemployment Rate",
        "frequency": "Monthly (alongside NFP)",
        "source": "U.S. Bureau of Labor Statistics",
        "what_it_measures": "Percentage of the labor force actively seeking employment.",
        "why_it_matters": (
            "Released simultaneously with NFP. The Fed's dual mandate targets 'maximum employment' — "
            "currently interpreted as ~4%. A rising rate triggers dovish pivot speculation."
        ),
        "market_reaction": {
            "beat": "Lower rate → tight labor market; Fed stays hawkish",
            "miss": "Higher rate → Fed may cut; bonds rally, rate-sensitive sectors ↑",
        },
        "key_thresholds": "Fed 'natural rate' ~4.0–4.2%. >4.5% raises red flags.",
        "related_releases": "NFP, Labor Force Participation Rate",
    },
    # ── Inflation ──────────────────────────────────────────────────────────────
    {
        "keys": ["cpi", "consumer price index"],
        "full_name": "Consumer Price Index (CPI)",
        "frequency": "Monthly (~2nd week of month)",
        "source": "U.S. Bureau of Labor Statistics",
        "what_it_measures": (
            "Measures the average change in prices paid by urban consumers for a basket of "
            "goods and services. Core CPI excludes volatile food and energy prices."
        ),
        "why_it_matters": (
            "The Fed's primary inflation gauge (alongside PCE). "
            "Hot CPI → rate hike fears → growth stocks sell off, USD strengthens. "
            "Cool CPI → rate cut hopes → risk assets rally. "
            "The 2% target is the benchmark."
        ),
        "market_reaction": {
            "beat": "Lower inflation = Fed can cut; equities ↑, USD ↓, gold ↑",
            "miss": "Higher inflation = Fed stays hawkish; tech sells off, USD ↑",
        },
        "key_thresholds": "Fed target: 2% YoY. >4% is hot. Core CPI watched more closely.",
        "related_releases": "PPI (leading indicator), PCE (Fed preferred measure)",
    },
    {
        "keys": ["ppi", "producer price index"],
        "full_name": "Producer Price Index (PPI)",
        "frequency": "Monthly (~1 week before CPI)",
        "source": "U.S. Bureau of Labor Statistics",
        "what_it_measures": (
            "Tracks price changes from the perspective of domestic producers — "
            "essentially wholesale inflation. Core PPI excludes food and energy."
        ),
        "why_it_matters": (
            "PPI is a leading indicator for CPI because producer costs eventually pass "
            "through to consumers. A hot PPI foreshadows sticky inflation ahead."
        ),
        "market_reaction": {
            "beat": "Lower PPI = inflation pipeline cooling; mild risk-on",
            "miss": "Higher PPI = inflation pressure building; bonds sell off",
        },
        "key_thresholds": "Watch Core PPI YoY vs CPI trend. Divergences signal turning points.",
        "related_releases": "CPI (releases ~1 week later), Import Price Index",
    },
    {
        "keys": ["pce", "personal consumption expenditures"],
        "full_name": "PCE Price Index (Fed's Preferred Inflation Gauge)",
        "frequency": "Monthly (last week of month)",
        "source": "U.S. Bureau of Economic Analysis",
        "what_it_measures": (
            "Tracks inflation across all goods and services consumed by households. "
            "Broader than CPI as it adjusts for consumer substitution behavior. "
            "Core PCE (ex-food & energy) is the Fed's official target measure."
        ),
        "why_it_matters": (
            "The Fed explicitly targets 2% Core PCE. Even small deviations from this "
            "guide dot-plot rate projections. Markets often see less volatility on PCE "
            "vs CPI since CPI precedes it by ~2 weeks."
        ),
        "market_reaction": {
            "beat": "Cool PCE = rate cut timing moves earlier; growth stocks ↑",
            "miss": "Hot PCE = Fed delays cuts; yields rise, high-multiple stocks ↓",
        },
        "key_thresholds": "Fed target: 2.0% Core PCE YoY. Currently watch progress toward 2%.",
        "related_releases": "CPI (precedes PCE), FOMC meetings (use PCE in projections)",
    },
    # ── GDP / Growth ───────────────────────────────────────────────────────────
    {
        "keys": ["gdp", "gross domestic product"],
        "full_name": "Gross Domestic Product (GDP)",
        "frequency": "Quarterly (advance, second, third estimates)",
        "source": "U.S. Bureau of Economic Analysis",
        "what_it_measures": (
            "The total monetary value of all goods and services produced within the U.S. "
            "in a given quarter. The advance estimate lands ~4 weeks after quarter-end."
        ),
        "why_it_matters": (
            "Two consecutive negative quarters = technical recession. "
            "GDP sets the macro backdrop for all other indicators. "
            "Markets react most strongly to the advance estimate — revisions are usually minor."
        ),
        "market_reaction": {
            "beat": "Strong GDP = economy resilient; cyclicals ↑, but rate-cut hopes may fade",
            "miss": "Weak/negative GDP = recession risk; defensive sectors ↑, USD ↓",
        },
        "key_thresholds": "Trend growth ~2.0–2.5% annualized. <1% = soft landing concern. <0% = contraction.",
        "related_releases": "Personal Consumption (largest GDP component), ISM data",
    },
    # ── Manufacturing / Services ───────────────────────────────────────────────
    {
        "keys": ["ism manufacturing", "manufacturing pmi"],
        "full_name": "ISM Manufacturing PMI",
        "frequency": "Monthly (first business day of month)",
        "source": "Institute for Supply Management",
        "what_it_measures": (
            "A diffusion index based on surveys of purchasing managers at manufacturing firms. "
            "Above 50 = expansion; below 50 = contraction."
        ),
        "why_it_matters": (
            "One of the best leading indicators of the economy. Leads GDP by ~3–6 months. "
            "The 50-line is psychologically critical — a sustained break below signals "
            "industrial recession. New Orders sub-index is the most forward-looking component."
        ),
        "market_reaction": {
            "beat": "Above 50 or beat = industrials ↑, cyclicals ↑",
            "miss": "Below 50 or miss = risk-off, defensive sectors ↑",
        },
        "key_thresholds": "50 = neutral. >55 = strong expansion. <45 = contraction territory.",
        "related_releases": "ISM Services PMI, Markit/S&P Global PMI (released earlier same day)",
    },
    {
        "keys": ["ism services", "services pmi", "non-manufacturing"],
        "full_name": "ISM Services PMI (Non-Manufacturing)",
        "frequency": "Monthly (third business day of month)",
        "source": "Institute for Supply Management",
        "what_it_measures": (
            "Surveys purchasing managers at service-sector firms (~80% of U.S. economy). "
            "Above 50 = expansion; below 50 = contraction."
        ),
        "why_it_matters": (
            "Since services dominate the U.S. economy, this PMI often matters more than "
            "manufacturing. The Prices Paid sub-index is a key inflation signal."
        ),
        "market_reaction": {
            "beat": "Strong services = consumer healthy; mild risk-on",
            "miss": "Weak services = demand slowdown; rate-cut expectations ↑",
        },
        "key_thresholds": "50 = neutral. >55 = robust. <50 for 2+ months = broad slowdown.",
        "related_releases": "ISM Manufacturing PMI, Consumer Spending (PCE)",
    },
    # ── Housing ────────────────────────────────────────────────────────────────
    {
        "keys": ["housing starts", "building permits"],
        "full_name": "Housing Starts & Building Permits",
        "frequency": "Monthly (~3rd week of month)",
        "source": "U.S. Census Bureau",
        "what_it_measures": (
            "Housing starts = new residential construction begun. "
            "Building permits = authorized future construction (leading indicator)."
        ),
        "why_it_matters": (
            "Housing is highly rate-sensitive — the first sector to slow when the Fed hikes. "
            "A recovery signals rate cuts are working through the economy."
        ),
        "market_reaction": {
            "beat": "Strong housing = rate cut impact showing; homebuilders ↑ (DHI, LEN, TOL)",
            "miss": "Weak housing = rates still restrictive; homebuilders ↓",
        },
        "key_thresholds": "~1.4–1.6M annualized starts = healthy. <1.2M = soft.",
        "related_releases": "Existing Home Sales, NAHB Housing Market Index",
    },
    {
        "keys": ["existing home sales"],
        "full_name": "Existing Home Sales",
        "frequency": "Monthly (~4th week of month)",
        "source": "National Association of Realtors",
        "what_it_measures": "Completed transactions on previously owned single-family homes.",
        "why_it_matters": (
            "The largest segment of housing activity. Strong sales signal consumer confidence "
            "and wealth effects. Very rate-sensitive — tracks 30-year mortgage rates closely."
        ),
        "market_reaction": {
            "beat": "More sales = consumer strong; mild positive for financials/homebuilders",
            "miss": "Fewer sales = rate headwinds persist",
        },
        "key_thresholds": "Pre-2022 pace: ~5.5–6M/yr. Post-hike levels: ~3.8–4.3M/yr.",
        "related_releases": "New Home Sales, Housing Starts",
    },
    # ── Consumer ───────────────────────────────────────────────────────────────
    {
        "keys": ["retail sales"],
        "full_name": "Retail Sales",
        "frequency": "Monthly (~2nd week of month)",
        "source": "U.S. Census Bureau",
        "what_it_measures": (
            "Total receipts at retail stores, including online. "
            "Core retail sales (ex-autos and gas) best reflects consumer spending trends."
        ),
        "why_it_matters": (
            "Consumer spending drives ~70% of U.S. GDP. "
            "Retail sales beat = economy resilient; miss = consumer fatigue. "
            "A key input to GDP forecasts."
        ),
        "market_reaction": {
            "beat": "Strong sales = economy healthy; consumer discretionary ↑",
            "miss": "Weak sales = growth fears; defensive/staples ↑",
        },
        "key_thresholds": "MoM change: >0.5% = strong. Consecutive monthly declines = concern.",
        "related_releases": "PCE Personal Spending, Consumer Confidence",
    },
    {
        "keys": ["consumer confidence", "consumer sentiment", "university of michigan"],
        "full_name": "Consumer Confidence / Sentiment",
        "frequency": "Monthly",
        "source": "Conference Board (Confidence) / University of Michigan (Sentiment)",
        "what_it_measures": (
            "Surveys of households on current economic conditions and 6-month outlook. "
            "UMich also includes 1-year and 5-year inflation expectations — highly Fed-relevant."
        ),
        "why_it_matters": (
            "Forward-looking signal for consumer spending. "
            "UMich inflation expectations are explicitly watched by the Fed. "
            "Confidence leads spending by 2–3 months."
        ),
        "market_reaction": {
            "beat": "High confidence = spending likely to stay strong; risk-on",
            "miss": "Low confidence = spending risk; defensives ↑",
        },
        "key_thresholds": "Conference Board: 100 = neutral. UMich: 70 = healthy baseline.",
        "related_releases": "Retail Sales (coincident), PCE (lagged confirmation)",
    },
    # ── Fed / FOMC ────────────────────────────────────────────────────────────
    {
        "keys": ["fomc meeting", "fomc statement", "fomc minutes", "federal open market", "fed rate decision", "fed decision", "interest rate decision"],
        "full_name": "FOMC Meeting / Fed Rate Decision",
        "frequency": "8 times per year (roughly every 6 weeks)",
        "source": "Federal Reserve",
        "what_it_measures": (
            "The Federal Open Market Committee sets the federal funds rate target range. "
            "Each meeting includes a statement, projections (quarterly), and a press conference."
        ),
        "why_it_matters": (
            "The single most impactful recurring event for all asset classes. "
            "Rate changes directly affect borrowing costs, valuations (discount rate), "
            "and USD strength. The dot plot (released quarterly) guides rate path expectations."
        ),
        "market_reaction": {
            "beat": "Rate cut or dovish surprise: equities ↑↑, bonds ↑, USD ↓",
            "miss": "Rate hike or hawkish surprise: growth stocks ↓↓, bonds ↓, USD ↑",
        },
        "key_thresholds": "Watch CME FedWatch tool for real-time rate odds. >90% priced = low surprise risk.",
        "related_releases": "FOMC Minutes (3 weeks later), PCE (used in projections)",
    },
    {
        "keys": ["president trump speaks", "trump speaks", "president speaks",
                 "white house", "executive order", "tariff announcement"],
        "full_name": "Presidential / Executive Policy Statement",
        "frequency": "Irregular",
        "source": "White House / Executive Branch",
        "what_it_measures": (
            "Official statements, speeches, or policy announcements from the sitting "
            "President, including tariff decisions, executive orders, and economic policy directives."
        ),
        "why_it_matters": (
            "Presidential statements can immediately move markets — especially on trade policy "
            "(tariffs), regulatory changes, or fiscal spending. In 2025–2026, tariff "
            "announcements have been one of the biggest single-day volatility drivers. "
            "Unlike Fed speeches, these events have no formal forecast to beat or miss — "
            "reaction is purely driven by surprise factor and policy direction."
        ),
        "market_reaction": {
            "beat": "Pro-growth or trade-deal announcement: risk-on, equities ↑, USD ↑",
            "miss": "Tariff escalation or policy shock: equities ↓, safe havens ↑ (gold, bonds)",
        },
        "key_thresholds": (
            "Watch for: new tariff rates >10%, trade deal signings, major deregulation. "
            "Sectors most affected: industrials, tech (supply chains), consumer goods, energy."
        ),
        "related_releases": "Trade Balance (next month), ISM Manufacturing Prices Paid",
    },
    {
        "keys": ["fed chair", "fomc member", "powell speaks", "waller speaks", "williams speaks",
                 "lagarde speaks", "bailey speaks", "breman speaks", "schlegel speaks",
                 "fed governor", "fed president",
                 "central bank governor"],
        "full_name": "Central Bank Official / Policy Maker Speech",
        "frequency": "Irregular",
        "source": "Federal Reserve / ECB / BOE / Other central banks",
        "what_it_measures": (
            "Public remarks from central bank officials, often at conferences or congressional "
            "hearings. These can signal shifts in policy thinking between formal meetings."
        ),
        "why_it_matters": (
            "Central bank communication is a policy tool itself. "
            "A single comment from the Fed Chair can move markets 1–2%. "
            "Watch for: changes in inflation language, employment assessment, "
            "or hints about the next meeting's direction."
        ),
        "market_reaction": {
            "beat": "Dovish tone: equities ↑, yields ↓, USD ↓",
            "miss": "Hawkish tone: yields ↑, high-growth stocks ↓, USD ↑",
        },
        "key_thresholds": "Listen for key phrases: 'data dependent', 'restrictive enough', 'premature to cut'.",
        "related_releases": "Next FOMC meeting, CPI/PCE data that will inform Fed views",
    },
    # ── Trade / International ──────────────────────────────────────────────────
    {
        "keys": ["trade balance", "current account", "trade deficit"],
        "full_name": "Trade Balance / Current Account",
        "frequency": "Monthly",
        "source": "U.S. Census Bureau / Bureau of Economic Analysis",
        "what_it_measures": "The difference between exports and imports of goods and services.",
        "why_it_matters": (
            "A widening deficit can weigh on GDP and USD. "
            "In 2025–2026, trade data is especially watched given tariff policy changes. "
            "Sudden shifts can reflect front-loading (businesses stockpiling before tariffs)."
        ),
        "market_reaction": {
            "beat": "Narrowing deficit = positive for USD, GDP upgrade potential",
            "miss": "Widening deficit = potential GDP headwind",
        },
        "key_thresholds": "U.S. typically runs ~$60–90B monthly goods deficit.",
        "related_releases": "GDP (trade is a direct component), USD Index",
    },
    # ── ECB ────────────────────────────────────────────────────────────────────
    {
        "keys": ["ecb", "european central bank", "ecb rate", "ecb decision"],
        "full_name": "European Central Bank (ECB) Policy Decision",
        "frequency": "~8 times per year",
        "source": "European Central Bank",
        "what_it_measures": "ECB deposit facility rate and forward guidance for the Eurozone.",
        "why_it_matters": (
            "Directly impacts EUR/USD, European equities, and global bond markets. "
            "ECB divergence from the Fed creates FX opportunities. "
            "As of 2026, the ECB has been cutting rates faster than the Fed."
        ),
        "market_reaction": {
            "beat": "Dovish ECB: EUR ↓, European bonds ↑",
            "miss": "Hawkish ECB: EUR ↑, European equities may sell off",
        },
        "key_thresholds": "Watch ECB deposit rate vs Fed funds rate spread for EUR/USD direction.",
        "related_releases": "Eurozone CPI, German Ifo Business Climate",
    },
    # ── Philly Fed / NY Empire ────────────────────────────────────────────────
    {
        "keys": ["philly fed", "philadelphia fed", "empire state", "ny empire"],
        "full_name": "Philly Fed / Empire State Manufacturing Survey",
        "frequency": "Monthly (mid-month)",
        "source": "Philadelphia Fed / New York Fed",
        "what_it_measures": (
            "Regional manufacturing surveys covering business activity, new orders, "
            "employment, and prices paid/received."
        ),
        "why_it_matters": (
            "Early indicators for the national ISM Manufacturing PMI (released ~2 weeks later). "
            "Experienced traders use them to calibrate ISM expectations."
        ),
        "market_reaction": {
            "beat": "Better regional outlook → ISM beat expected → mild industrials ↑",
            "miss": "Worse regional outlook → ISM miss risk → mild risk-off",
        },
        "key_thresholds": "0 = neutral. Consistent negative readings precede ISM <50.",
        "related_releases": "ISM Manufacturing PMI, Chicago PMI",
    },
    # ── Durable Goods ──────────────────────────────────────────────────────────
    {
        "keys": ["durable goods", "factory orders", "core capital goods"],
        "full_name": "Durable Goods Orders / Core Capital Goods",
        "frequency": "Monthly (~4th week of month)",
        "source": "U.S. Census Bureau",
        "what_it_measures": (
            "New orders placed with manufacturers for long-lasting goods (>3 years). "
            "Core capital goods (ex-defense, ex-aircraft) measures business investment."
        ),
        "why_it_matters": (
            "A leading indicator for business investment and GDP. "
            "Core capex orders signal whether CFOs are willing to invest in growth."
        ),
        "market_reaction": {
            "beat": "Strong business investment → industrials, tech equipment sectors ↑",
            "miss": "Weak orders → business caution; slight risk-off",
        },
        "key_thresholds": "Core capex: >1% MoM = strong. Consecutive declines = capex recession risk.",
        "related_releases": "ISM Manufacturing, GDP (investment component)",
    },
    # ── SNB ────────────────────────────────────────────────────────────────────
    {
        "keys": ["snb", "swiss national bank"],
        "full_name": "Swiss National Bank (SNB) Policy Decision",
        "frequency": "Quarterly",
        "source": "Swiss National Bank",
        "what_it_measures": "SNB policy rate and FX intervention stance.",
        "why_it_matters": (
            "CHF is a safe-haven currency. SNB policy affects EUR/CHF and global risk sentiment. "
            "The SNB has historically intervened to weaken CHF to protect Swiss exporters."
        ),
        "market_reaction": {
            "beat": "Rate cut or FX cap: CHF ↓, Swiss equities ↑",
            "miss": "Hawkish SNB or strong CHF signal: safe-haven flows ↑",
        },
        "key_thresholds": "Watch EUR/CHF 1.05 as a key level SNB has historically defended.",
        "related_releases": "Swiss CPI, EUR/CHF exchange rate",
    },
    # ── RBNZ ──────────────────────────────────────────────────────────────────
    {
        "keys": ["rbnz", "reserve bank of new zealand"],
        "full_name": "Reserve Bank of New Zealand (RBNZ) Policy Decision",
        "frequency": "~7 times per year",
        "source": "Reserve Bank of New Zealand",
        "what_it_measures": "RBNZ official cash rate (OCR) and monetary policy outlook.",
        "why_it_matters": (
            "RBNZ is often one of the first major central banks to move rates, "
            "making it a leading indicator for global rate cycles. "
            "NZD is a risk-on commodity currency — tracks global growth sentiment."
        ),
        "market_reaction": {
            "beat": "Dovish RBNZ: NZD ↓, AUD/NZD moves",
            "miss": "Hawkish RBNZ: NZD ↑",
        },
        "key_thresholds": "Watch NZD/USD reaction to guidance on OCR path.",
        "related_releases": "Australian RBA, global commodity prices",
    },
    # ── BOE ────────────────────────────────────────────────────────────────────
    {
        "keys": ["boe", "bank of england", "bailey"],
        "full_name": "Bank of England (BOE) Policy Decision / Governor Speech",
        "frequency": "8 times per year (MPC meetings)",
        "source": "Bank of England",
        "what_it_measures": "BOE bank rate and Monetary Policy Committee vote.",
        "why_it_matters": (
            "Directly impacts GBP and UK gilts. BOE often navigates between "
            "sticky UK services inflation and sluggish growth — 'stagflation lite' dynamics."
        ),
        "market_reaction": {
            "beat": "Dovish BOE: GBP ↓, UK gilts ↑",
            "miss": "Hawkish BOE: GBP ↑, UK homebuilders / REITs ↓",
        },
        "key_thresholds": "Watch split MPC votes — 5-4 vs 7-2 signals policy path conviction.",
        "related_releases": "UK CPI, UK GDP, GBP/USD",
    },
]


def _default_info(title: str) -> dict:
    return {
        "full_name": title,
        "frequency": "Varies",
        "source": "Economic authority",
        "what_it_measures": "See official release for details.",
        "why_it_matters": (
            "Economic data releases can move markets depending on how the reading "
            "compares to consensus expectations. A beat or miss vs forecast "
            "is usually more important than the absolute level."
        ),
        "market_reaction": {
            "beat": "Better-than-expected reading → risk-on tone",
            "miss": "Worse-than-expected reading → risk-off / rate-cut speculation",
        },
        "key_thresholds": "Compare actual release to Bloomberg/FactSet consensus.",
        "related_releases": "—",
    }


def lookup_indicator(title: str) -> dict:
    """
    Return the best-matching indicator info dict for a given event title.
    Falls back to a generic template if no match found.
    """
    title_lower = title.lower()
    for entry in _INDICATOR_DB:
        for keyword in entry["keys"]:
            if keyword in title_lower:
                return {k: v for k, v in entry.items() if k != "keys"}
    return _default_info(title)
