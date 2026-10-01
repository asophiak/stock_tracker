"""
Options Flow Analyzer
─────────────────────
Uses yfinance to inspect the options chain for a symbol and detect unusual
institutional activity:

  • Put/Call ratio (PCR) — < 0.7 = bullish, > 1.3 = bearish
  • OI sweep: large call or put blocks (volume >> open interest average)
  • Skew: ITM/OTM call vs put dollar premium imbalance

Returns an OptionsFlowResult that the whale scorer converts to a signal.

yfinance calls are synchronous; we run them in an asyncio executor.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OptionsFlowResult:
    symbol: str
    put_call_ratio: float = 1.0        # < 0.7 bullish, > 1.3 bearish
    call_oi: int = 0
    put_oi: int = 0
    call_volume: int = 0
    put_volume: int = 0
    unusual_calls: List[dict] = field(default_factory=list)  # large call sweeps
    unusual_puts:  List[dict] = field(default_factory=list)  # large put sweeps
    sentiment: float = 0.0             # -1..+1
    summary: str = ""
    error: Optional[str] = None


def _fetch_options_sync(symbol: str) -> OptionsFlowResult:
    """
    Synchronous yfinance call — run this in an executor thread.
    """
    try:
        import yfinance as yf
    except ImportError:
        return OptionsFlowResult(symbol=symbol, error="yfinance not installed")

    result = OptionsFlowResult(symbol=symbol)

    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            result.error = "no_options_chain"
            result.summary = "No options data available."
            return result

        # Use nearest 1-2 expiries for freshest institutional sentiment
        dates_to_check = expirations[:2]

        total_call_oi  = 0
        total_put_oi   = 0
        total_call_vol = 0
        total_put_vol  = 0
        unusual_calls  = []
        unusual_puts   = []

        for expiry in dates_to_check:
            chain = ticker.option_chain(expiry)
            calls = chain.calls
            puts  = chain.puts

            if calls.empty or puts.empty:
                continue

            # Fill NaN
            for col in ("volume", "openInterest", "lastPrice", "strike"):
                if col in calls.columns:
                    calls[col] = calls[col].fillna(0)
                if col in puts.columns:
                    puts[col] = puts[col].fillna(0)

            c_oi  = int(calls["openInterest"].sum())
            p_oi  = int(puts["openInterest"].sum())
            c_vol = int(calls["volume"].sum())
            p_vol = int(puts["volume"].sum())

            total_call_oi  += c_oi
            total_put_oi   += p_oi
            total_call_vol += c_vol
            total_put_vol  += p_vol

            # Detect unusual sweeps: volume > 2× OI on individual strikes
            avg_call_oi = c_oi / max(len(calls), 1)
            avg_put_oi  = p_oi / max(len(puts),  1)

            for _, row in calls.iterrows():
                vol = row.get("volume", 0) or 0
                oi  = row.get("openInterest", 0) or 0
                if vol > max(avg_call_oi * 2, 100) and vol > oi * 0.5:
                    unusual_calls.append({
                        "expiry":    expiry,
                        "strike":    float(row.get("strike", 0)),
                        "volume":    int(vol),
                        "oi":        int(oi),
                        "last":      float(row.get("lastPrice", 0)),
                    })

            for _, row in puts.iterrows():
                vol = row.get("volume", 0) or 0
                oi  = row.get("openInterest", 0) or 0
                if vol > max(avg_put_oi * 2, 100) and vol > oi * 0.5:
                    unusual_puts.append({
                        "expiry":    expiry,
                        "strike":    float(row.get("strike", 0)),
                        "volume":    int(vol),
                        "oi":        int(oi),
                        "last":      float(row.get("lastPrice", 0)),
                    })

        result.call_oi     = total_call_oi
        result.put_oi      = total_put_oi
        result.call_volume = total_call_vol
        result.put_volume  = total_put_vol
        result.unusual_calls = sorted(unusual_calls, key=lambda x: -x["volume"])[:5]
        result.unusual_puts  = sorted(unusual_puts,  key=lambda x: -x["volume"])[:5]

        denom = total_put_vol + total_call_vol
        if denom > 0:
            result.put_call_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else 99.0
        else:
            result.put_call_ratio = 1.0

        # Compute sentiment from PCR + unusual activity
        pcr = result.put_call_ratio
        if pcr < 0.5:
            pcr_sentiment = 0.8
        elif pcr < 0.7:
            pcr_sentiment = 0.5
        elif pcr < 1.0:
            pcr_sentiment = 0.2
        elif pcr < 1.3:
            pcr_sentiment = -0.2
        elif pcr < 2.0:
            pcr_sentiment = -0.5
        else:
            pcr_sentiment = -0.8

        sweep_sentiment = 0.0
        if unusual_calls:
            sweep_sentiment += min(0.3, len(unusual_calls) * 0.1)
        if unusual_puts:
            sweep_sentiment -= min(0.3, len(unusual_puts) * 0.1)

        result.sentiment = max(-1.0, min(1.0, pcr_sentiment + sweep_sentiment))

        # Human-readable summary
        parts = []
        if pcr < 0.7:
            parts.append(f"Bullish options flow (P/C={pcr:.2f})")
        elif pcr > 1.3:
            parts.append(f"Bearish options flow (P/C={pcr:.2f})")
        else:
            parts.append(f"Neutral options flow (P/C={pcr:.2f})")
        if unusual_calls:
            parts.append(f"{len(unusual_calls)} unusual call sweep(s)")
        if unusual_puts:
            parts.append(f"{len(unusual_puts)} unusual put sweep(s)")
        result.summary = ". ".join(parts) + "."

    except Exception as exc:
        logger.warning("Options flow error for %s: %s", symbol, exc)
        result.error   = str(exc)
        result.summary = "Options data temporarily unavailable."

    return result


async def fetch_options_flow(symbol: str) -> OptionsFlowResult:
    """Async wrapper — runs the synchronous yfinance call in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_options_sync, symbol)
