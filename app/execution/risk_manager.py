"""
Central risk management gate.

Every order (swing bot AND scalp bot) passes through this before execution.

Rules enforced:
  • Kill switch
  • Max trades per day
  • Daily loss cap
  • Symbol cooldowns
  • EOD entry cutoff
  • Max consecutive losses → timed pause
  • Max open positions (overall + per-strategy)
  • Max exposure per symbol
  • Max total account exposure
  • Spread too wide kill-switch

All state is in-process memory; counters reset daily.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.schemas.trading import CreateOrderRequest
from app.utils.time_utils import minutes_until_close, session_date_str

logger = logging.getLogger(__name__)

# ── In-process state ──────────────────────────────────────────────────────────
# Per-symbol cooldown after stop-out: symbol → cooldown_until datetime
_symbol_cooldowns: Dict[str, datetime] = {}

# Daily trade counter
_trades_today: int = 0
_trades_today_date: str = ""

# Consecutive loss tracking
_consecutive_losses: int = 0
_loss_pause_until: Optional[datetime] = None

# Spread cache (optional, populated externally)
_last_spread_pct: Dict[str, float] = {}


# ── Daily counter helpers ─────────────────────────────────────────────────────

def get_trades_today() -> int:
    return _trades_today


def reset_daily_counters() -> None:
    global _trades_today, _trades_today_date, _consecutive_losses, _loss_pause_until
    _trades_today = 0
    _trades_today_date = session_date_str()
    _consecutive_losses = 0
    _loss_pause_until = None
    logger.info("Risk manager daily counters reset for %s", _trades_today_date)


def maybe_reset_daily_counters() -> None:
    """Reset trade counters if the session date has rolled over."""
    if _trades_today_date != session_date_str():
        reset_daily_counters()


def increment_trade_count() -> None:
    global _trades_today, _trades_today_date
    maybe_reset_daily_counters()
    _trades_today += 1


# ── Consecutive-loss tracking ─────────────────────────────────────────────────

def record_trade_result(won: bool) -> None:
    """
    Called after every completed trade (win or loss).
    Triggers a timed pause if MAX_CONSECUTIVE_LOSSES is hit.
    """
    global _consecutive_losses, _loss_pause_until
    if won:
        _consecutive_losses = 0
    else:
        _consecutive_losses += 1
        logger.info(
            "Consecutive losses: %d / %d",
            _consecutive_losses, settings.MAX_CONSECUTIVE_LOSSES,
        )
        if (settings.MAX_CONSECUTIVE_LOSSES > 0 and
                _consecutive_losses >= settings.MAX_CONSECUTIVE_LOSSES):
            _loss_pause_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.CONSECUTIVE_LOSS_PAUSE_MINUTES
            )
            logger.warning(
                "RISK: %d consecutive losses — bot paused for %d minutes until %s",
                _consecutive_losses,
                settings.CONSECUTIVE_LOSS_PAUSE_MINUTES,
                _loss_pause_until.strftime("%H:%M:%S UTC"),
            )


def get_consecutive_losses() -> int:
    return _consecutive_losses


def is_loss_paused() -> bool:
    if _loss_pause_until is None:
        return False
    if datetime.now(timezone.utc) < _loss_pause_until:
        return True
    return False


def get_loss_pause_remaining_minutes() -> int:
    if not _loss_pause_until:
        return 0
    remaining = (_loss_pause_until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining // 60))


# ── Symbol cooldown ────────────────────────────────────────────────────────────

def set_symbol_cooldown(symbol: str, minutes: Optional[int] = None) -> None:
    mins = minutes if minutes is not None else settings.SYMBOL_COOLDOWN_MINUTES
    cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=mins)
    _symbol_cooldowns[symbol] = cooldown_until
    logger.info("Symbol %s in cooldown for %d min (until %s)", symbol, mins,
                cooldown_until.strftime("%H:%M:%S"))


def clear_symbol_cooldown(symbol: str) -> None:
    _symbol_cooldowns.pop(symbol, None)


def get_symbol_cooldowns() -> Dict[str, datetime]:
    return dict(_symbol_cooldowns)


# ── Spread tracking ────────────────────────────────────────────────────────────

def update_spread(symbol: str, spread_pct: float) -> None:
    """Called from bar/quote handler with latest bid-ask spread estimate."""
    _last_spread_pct[symbol] = spread_pct


def get_spread(symbol: str) -> Optional[float]:
    return _last_spread_pct.get(symbol)


# ── Position sizing ────────────────────────────────────────────────────────────

def calculate_position_size(
    equity: float,
    price: float,
    stop_price: float,
    risk_pct: float,
    max_position_pct: float = 40.0,
    is_b_trade: bool = False,
) -> float:
    """
    Risk-based position sizing.

    shares = (equity × risk_pct/100) / |price - stop_price|

    For B-trades the risk is scaled by B_TRADE_SIZE_FRACTION.
    Result is capped at max_position_pct % of equity.

    Returns fractional share quantity (rounds to 4 decimal places).
    """
    stop_dist = abs(price - stop_price)
    if stop_dist < 0.001:
        # Degenerate stop (set to something reasonable to avoid divide-by-zero)
        stop_dist = price * 0.005

    effective_risk_pct = risk_pct
    if is_b_trade:
        effective_risk_pct *= settings.B_TRADE_SIZE_FRACTION

    dollar_risk = equity * (effective_risk_pct / 100.0)
    shares = dollar_risk / stop_dist

    # Cap at max_position_pct of equity
    max_dollars = equity * (max_position_pct / 100.0)
    max_shares = max_dollars / price

    shares = min(shares, max_shares)
    return round(max(0.01, shares), 4)


# ── Exposure checks ────────────────────────────────────────────────────────────

def check_exposure(
    symbol: str,
    proposed_dollars: float,
    equity: float,
    open_positions: List,   # list of PaperPosition objects
) -> Optional[str]:
    """
    Returns an error string if the proposed trade would breach exposure limits.
    open_positions should include all existing positions (swing + scalp).
    """
    # Per-symbol exposure
    existing_sym = next(
        (p for p in open_positions if p.symbol == symbol), None
    )
    existing_sym_value = (
        (existing_sym.avg_entry_price * existing_sym.qty) if existing_sym else 0.0
    )
    total_sym_value = existing_sym_value + proposed_dollars
    sym_pct = (total_sym_value / equity * 100.0) if equity > 0 else 0.0
    if sym_pct > settings.MAX_EXPOSURE_PER_SYMBOL_PCT:
        return (
            f"Symbol exposure limit: {symbol} would be "
            f"{sym_pct:.1f}% of equity (max {settings.MAX_EXPOSURE_PER_SYMBOL_PCT:.0f}%)."
        )

    # Total portfolio exposure
    total_existing = sum(p.avg_entry_price * p.qty for p in open_positions)
    total_after = total_existing + proposed_dollars
    total_pct = (total_after / equity * 100.0) if equity > 0 else 0.0
    if total_pct > settings.MAX_TOTAL_EXPOSURE_PCT:
        return (
            f"Total exposure limit: portfolio would be "
            f"{total_pct:.1f}% of equity (max {settings.MAX_TOTAL_EXPOSURE_PCT:.0f}%)."
        )

    return None


# ── Main gate ─────────────────────────────────────────────────────────────────

def check_risk(
    req: CreateOrderRequest,
    kill_switch: bool,
    daily_pnl: float,
    open_position_count: int,
    equity: Optional[float] = None,
    open_positions: Optional[List] = None,
    proposed_trade_dollars: Optional[float] = None,
    is_scalp: bool = False,
) -> Optional[str]:
    """
    Returns a rejection reason string, or None if the trade is allowed.

    Parameters
    ----------
    req                    : the proposed order
    kill_switch            : manual kill switch state
    daily_pnl              : today's realized PnL (negative = losses)
    open_position_count    : total currently open positions
    equity                 : current account equity (for exposure checks)
    open_positions         : list of PaperPosition for exposure checks
    proposed_trade_dollars : total dollar value of the proposed trade
    is_scalp               : True for scalp bot trades
    """
    maybe_reset_daily_counters()

    # Kill switch
    if kill_switch:
        return "Kill switch is active — no new orders permitted."

    # Consecutive-loss pause
    if is_loss_paused():
        return (
            f"Bot paused after {_consecutive_losses} consecutive losses. "
            f"Resuming in {get_loss_pause_remaining_minutes()} min."
        )

    # Max trades per day (0 = unlimited)
    if settings.MAX_TRADES_PER_DAY > 0 and _trades_today >= settings.MAX_TRADES_PER_DAY:
        return f"Max trades per day ({settings.MAX_TRADES_PER_DAY}) reached."

    # Daily loss cap
    if daily_pnl <= -abs(settings.DAILY_LOSS_LIMIT):
        return f"Daily loss limit (${settings.DAILY_LOSS_LIMIT:.0f}) reached."

    # Max open positions (overall)
    max_total = settings.MAX_OPEN_POSITIONS
    if open_position_count >= max_total:
        return f"Max open positions ({max_total}) reached."

    # Symbol cooldown
    if req.symbol in _symbol_cooldowns:
        cooldown_until = _symbol_cooldowns[req.symbol]
        if datetime.now(timezone.utc) < cooldown_until:
            remaining = int((cooldown_until - datetime.now(timezone.utc)).total_seconds() // 60)
            return f"{req.symbol} is in cooldown for {remaining} more minutes."
        else:
            del _symbol_cooldowns[req.symbol]

    # EOD entry cutoff
    mins_left = minutes_until_close()
    eod_entry_cutoff = settings.EOD_FLATTEN_MINUTES * 3
    if mins_left < eod_entry_cutoff:
        return f"Too close to market close ({mins_left:.0f} min remaining). No new entries."

    # Spread check
    spread = _last_spread_pct.get(req.symbol)
    if spread is not None and spread > settings.SPREAD_KILL_THRESHOLD_PCT:
        return (
            f"Spread too wide for {req.symbol}: {spread:.3f}% "
            f"(max {settings.SPREAD_KILL_THRESHOLD_PCT:.2f}%)."
        )

    # Exposure checks (only when we have enough info)
    if equity and equity > 0 and open_positions is not None and proposed_trade_dollars:
        exposure_err = check_exposure(
            req.symbol, proposed_trade_dollars, equity, open_positions
        )
        if exposure_err:
            return exposure_err

    return None
