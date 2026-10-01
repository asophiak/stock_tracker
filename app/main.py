"""
FastAPI application factory and startup/shutdown lifecycle.

Startup sequence:
 1. Initialise logging
 2. Create DB tables
 3. Sync watchlist from config
 4. Select and initialise provider (Alpaca or mock)
 5. Load today's bars and average volume for each symbol
 6. Start real-time streaming
 7. Start background scoring loop
 8. Start EOD monitoring task
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.alerts.dispatcher import AlertDispatcher
from app.config import settings
from app.db.init_db import init_db
from app.db.session import async_session_factory
from app.logging_config import setup_logging
from app.services.alert_service import maybe_send_alert
from app.services.bar_service import load_avg_daily_volume, load_today_bars
from app.services.market_session import should_flatten_positions, should_score_signals
from app.services.news_service import fetch_and_cache_news, handle_incoming_news
from app.services.signal_service import run_scoring_cycle
from app.services import bot_state_service
from app.services.symbol_service import sync_watchlist
from app.utils.cache import StateManager
from app.utils.time_utils import session_date_str

setup_logging()
logger = logging.getLogger(__name__)

# Global singletons — referenced by dependency injection
state_manager = StateManager()
alert_dispatcher: AlertDispatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and graceful shutdown."""
    global alert_dispatcher

    logger.info("=" * 60)
    logger.info("Stock Tracker starting up...")
    logger.info("=" * 60)

    # ── DB init ───────────────────────────────────────────────────────────────
    await init_db()
    _validate_config()

    # ── Restore persisted bot wallet state ────────────────────────────────────
    async with async_session_factory() as db:
        await bot_state_service.record_restart_time(db)
        saved_cash, saved_pnl = await bot_state_service.load_bot_state(db)
    if saved_cash is not None:
        state_manager.bot_cash = saved_cash
        state_manager.bot_realized_pnl = saved_pnl
        logger.info(
            "Bot wallet restored from DB: cash=$%.2f  realized_pnl=$%.2f",
            saved_cash, saved_pnl,
        )
    else:
        logger.info(
            "Bot wallet: no saved state found — starting fresh at $%.2f",
            state_manager.bot_cash,
        )

    # ── Alpaca position sync on startup ──────────────────────────────────────
    if state_manager.execution_mode == "paper_alpaca":
        async with async_session_factory() as db:
            from app.services.paper_trading_service import sync_alpaca_positions
            await sync_alpaca_positions(db, state_manager)
        logger.info("Startup Alpaca sync complete.")

    # ── Alert dispatcher ──────────────────────────────────────────────────────
    alert_dispatcher = AlertDispatcher(async_session_factory)

    # ── Sync watchlist ────────────────────────────────────────────────────────
    async with async_session_factory() as db:
        symbols = await sync_watchlist(db)
    state_manager.init_symbols(symbols)
    state_manager.session_date = session_date_str()
    state_manager.execution_mode = settings.EXECUTION_MODE
    state_manager.kill_switch = settings.KILL_SWITCH_DEFAULT

    logger.info("Watchlist: %s", symbols)

    # ── Provider selection ────────────────────────────────────────────────────
    market_provider, news_provider = await _init_providers(symbols)

    # ── Load historical data for today ────────────────────────────────────────
    if market_provider:
        await _load_history(symbols, market_provider)
        await _load_news(symbols, news_provider)

    # ── Start background tasks ────────────────────────────────────────────────
    scoring_task = asyncio.create_task(_scoring_loop(symbols))
    eod_task     = asyncio.create_task(_eod_monitor(market_provider))

    # ── Whale data: kick off initial fetch then schedule periodic refresh ─────
    whale_task = asyncio.create_task(_whale_startup(symbols))

    # ── ML: nightly self-retraining after the close ───────────────────────────
    retrain_task = asyncio.create_task(_ml_retrain_scheduler())

    # ── Macro / market data: warm caches on startup ───────────────────────────
    from app.services import macro_service as _macro_svc
    asyncio.create_task(_macro_svc.refresh_macro_data())

    state_manager.startup_complete = True
    logger.info("Startup complete. Dashboard at http://%s:%d/", settings.APP_HOST, settings.APP_PORT)

    # ── Backfill any session summaries the restart script interrupted ─────────
    asyncio.create_task(_backfill_missing_summaries())

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    scoring_task.cancel()
    eod_task.cancel()
    whale_task.cancel()
    retrain_task.cancel()
    if market_provider:
        await market_provider.disconnect()
    if news_provider:
        await news_provider.disconnect()
    logger.info("Shutdown complete.")


async def _init_providers(symbols: list[str]):
    """Initialise market data and news providers."""
    if settings.alpaca_credentials_present:
        from app.providers.alpaca.market_data import AlpacaMarketDataProvider
        from app.providers.alpaca.news import AlpacaNewsProvider

        market_provider = AlpacaMarketDataProvider()
        news_provider = AlpacaNewsProvider()

        await market_provider.connect()
        await news_provider.connect()

        # Register bar callback → feeds state manager
        def _on_bar(symbol: str, bar) -> None:
            state_manager.add_bar(symbol, bar)
            # Trigger mark-to-market on open positions
            asyncio.create_task(_update_position_price(symbol, bar.close))
            state_manager.broadcast_sse("price_update", {"symbol": symbol, "price": bar.close})

        market_provider.on_bar(_on_bar)

        # Register news callback
        def _on_news(item) -> None:
            handle_incoming_news(item)

        news_provider.on_news(_on_news)

        await market_provider.subscribe(symbols)
        await news_provider.subscribe(symbols)

        state_manager.provider_connected = True
        state_manager.provider_name = "alpaca"
        state_manager.stream_connected = True
        logger.info("Alpaca providers connected.")
        return market_provider, news_provider

    else:
        logger.warning(
            "No Alpaca credentials — using yfinance for real market data (polling mode)."
        )
        from app.providers.yfinance.market_data import YFinanceMarketDataProvider

        market_provider = YFinanceMarketDataProvider(poll_interval_seconds=60.0)
        await market_provider.connect()

        def _on_bar(symbol: str, bar) -> None:
            from app.utils.time_utils import is_market_open
            if not is_market_open():
                # Stale bar from yfinance outside market hours — update price display only
                state_manager.get_state(symbol).last_price = bar.close
                return
            state_manager.add_bar(symbol, bar)
            asyncio.create_task(_update_position_price(symbol, bar.close))
            state_manager.broadcast_sse("price_update", {"symbol": symbol, "price": bar.close})

        market_provider.on_bar(_on_bar)
        await market_provider.subscribe(symbols)

        # Seed latest prices immediately so the dashboard isn't blank on first load
        asyncio.create_task(_seed_yfinance_prices(symbols, market_provider))

        state_manager.provider_connected = True
        state_manager.provider_name = "yfinance"
        state_manager.stream_connected = True
        return market_provider, None


async def _seed_yfinance_prices(symbols: list[str], market_provider) -> None:
    """
    Fetch current quotes for all symbols so last_price is populated for header display.
    Only injects a synthetic chart bar when the market is currently open — on weekends
    or pre-market the historical bar loader already has the correct last session bars.
    """
    from app.utils.time_utils import is_market_open
    market_open = is_market_open()

    for sym in symbols:
        try:
            quote = await market_provider.fetch_latest_quote(sym)
            if quote:
                mid = (quote.ask_price + quote.bid_price) / 2
                # Always update the last_price for header/cards
                state = state_manager.get_state(sym)
                state.last_price = mid
                state_manager.broadcast_sse("price_update", {"symbol": sym, "price": mid})

                # Only add a synthetic bar when market is live — otherwise the
                # historical loader already has correct last-session bars
                if market_open:
                    from app.schemas.market_data import Bar
                    from datetime import datetime, timezone
                    synthetic_bar = Bar(
                        timestamp=datetime.now(timezone.utc),
                        open=mid, high=mid, low=mid, close=mid,
                        volume=0, timeframe="1Min",
                    )
                    state_manager.add_bar(sym, synthetic_bar)
        except Exception as exc:
            logger.debug("Price seed failed for %s: %s", sym, exc)


async def _load_history(symbols: list[str], market_provider) -> None:
    """Load today's bars and avg volume for all symbols in parallel."""
    tasks = []
    for sym in symbols:
        tasks.append(load_today_bars(sym, market_provider, state_manager))
        tasks.append(load_avg_daily_volume(sym, market_provider, state_manager))
    await asyncio.gather(*tasks, return_exceptions=True)


async def _load_news(symbols: list[str], news_provider) -> None:
    if news_provider is None:
        return
    async with async_session_factory() as db:
        for sym in symbols:
            await fetch_and_cache_news(sym, news_provider, db)


_dynamic_wl_last_refresh: float = 0.0   # epoch seconds


async def _scoring_loop(symbols: list[str]) -> None:
    """Background task: score all symbols every N seconds."""
    global _dynamic_wl_last_refresh

    while True:
        try:
            await asyncio.sleep(settings.SCORE_INTERVAL_SECONDS)
            if not should_score_signals():
                # Outside market hours: score once from the loaded bars so the
                # dashboard shows the last session instead of empty cards.
                # No persistence, alerts or trading.
                from app.services.signal_service import get_all_latest_signals
                if not get_all_latest_signals():
                    async with async_session_factory() as db:
                        await run_scoring_cycle(state_manager, db, symbols, offline=True)
                    logger.info("Offline scoring: dashboard populated from last session's bars.")
                continue

            # ── Dynamic watchlist refresh (periodic) ──────────────────────────
            import time
            if settings.ENABLE_DYNAMIC_WATCHLIST:
                now_ts = time.monotonic()
                refresh_interval = settings.DYNAMIC_WATCHLIST_REFRESH_MINUTES * 60
                if now_ts - _dynamic_wl_last_refresh >= refresh_interval:
                    _dynamic_wl_last_refresh = now_ts
                    from app.services.watchlist_scanner import refresh_dynamic_watchlist, get_full_watchlist
                    added = await refresh_dynamic_watchlist()
                    if added:
                        # Expand scoring list with newly discovered symbols
                        for sym in added:
                            if sym not in symbols:
                                symbols.append(sym)
                                state_manager.init_symbols([sym])

            async with async_session_factory() as db:
                await run_scoring_cycle(state_manager, db, symbols)

            # Dispatch alerts for any high-conviction signals
            from app.services.signal_service import get_all_latest_signals
            for sym, sig in get_all_latest_signals().items():
                if sym in ("SPY", "QQQ"):
                    continue
                await maybe_send_alert(sig, alert_dispatcher)

            # Auto-execute if enabled
            await _maybe_auto_execute(state_manager)
            await _maybe_scalp_execute(state_manager)
            await _maybe_nn_execute(state_manager)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Scoring loop error: %s", exc, exc_info=True)


# Consecutive sub-48 score counts per symbol — signal_exhausted requires 2 strikes
_exhausted_strikes: dict[str, int] = {}

# Per-symbol scalp cooldown timestamps (last scalp exit time)
_scalp_cooldowns: dict = {}

# Per-symbol last scalp ENTRY timestamps — prevents re-entry before min gap
_scalp_entry_times: dict = {}


def _validate_config() -> None:
    """
    Log all critical effective settings at startup.
    Warn loudly about values that could cause outsized losses if .env drifts
    back to config.py defaults (e.g., BOT_ALLOCATION_PCT 15% → 85%).
    """
    logger.info("─" * 60)
    logger.info("Effective configuration:")
    logger.info("  EXECUTION_MODE     = %s", settings.EXECUTION_MODE)
    logger.info("  BOT_ENTRY_SCORE    = %d", settings.BOT_ENTRY_SCORE)
    logger.info("  BOT_ALLOCATION_PCT = %.1f%%", settings.BOT_ALLOCATION_PCT)
    logger.info("  BOT_PAPER_CAPITAL  = $%.0f", settings.BOT_PAPER_CAPITAL)
    logger.info(
        "  MAX_TRADES_PER_DAY = %s",
        str(settings.MAX_TRADES_PER_DAY) if settings.MAX_TRADES_PER_DAY else "unlimited",
    )
    logger.info("  DAILY_LOSS_LIMIT   = $%.0f", settings.DAILY_LOSS_LIMIT)
    logger.info("  LIVE_TRADING       = %s", settings.LIVE_TRADING_ENABLED)
    logger.info("  SCORE_INTERVAL     = %ds", settings.SCORE_INTERVAL_SECONDS)
    logger.info("  A_TRADE_MIN_SCORE  = %d", settings.A_TRADE_MIN_SCORE)
    logger.info("  B_TRADE_MIN_SCORE  = %d", settings.B_TRADE_MIN_SCORE)
    logger.info("  SHADOW_MIN_SCORE   = %d", settings.SHADOW_TRADE_MIN_SCORE)
    logger.info("  ENABLE_B_TRADES    = %s", settings.ENABLE_B_TRADES)
    logger.info("  ENABLE_SHADOW_TRADES = %s", settings.ENABLE_SHADOW_TRADES)
    logger.info("  ENABLE_DYNAMIC_WL  = %s", settings.ENABLE_DYNAMIC_WATCHLIST)
    logger.info("  USE_RISK_SIZING    = %s", settings.USE_RISK_BASED_SIZING)
    logger.info("  MAX_OPEN_POS       = %d", settings.MAX_OPEN_POSITIONS)
    logger.info("  MAX_CONSEC_LOSSES  = %d", settings.MAX_CONSECUTIVE_LOSSES)
    logger.info("─" * 60)

    if settings.LIVE_TRADING_ENABLED:
        logger.warning("⚠  LIVE_TRADING_ENABLED=true — real money at risk!")
    if settings.BOT_ALLOCATION_PCT > 50:
        logger.warning(
            "⚠  BOT_ALLOCATION_PCT=%.0f%% is dangerously high. "
            "A single trade uses %.0f%% of the bot wallet. "
            "config.py default is 85%% — check that .env override is in place.",
            settings.BOT_ALLOCATION_PCT, settings.BOT_ALLOCATION_PCT,
        )
    if settings.MAX_TRADES_PER_DAY == 0:
        logger.warning(
            "⚠  MAX_TRADES_PER_DAY=0 (unlimited). "
            "Set MAX_TRADES_PER_DAY in .env to cap daily exposure."
        )
    if settings.KILL_SWITCH_DEFAULT:
        logger.info("Kill switch starts ENGAGED (KILL_SWITCH_DEFAULT=true).")


async def _backfill_missing_summaries() -> None:
    """
    On startup: build session summaries for any past trading days that have
    closed trades in the DB but no entry in daily_session_summaries.

    This fixes the gap caused by the restart script killing the process
    before the EOD summary job could run (observed on May 15, 18, 19, 22).
    In-memory fields (best_trade_*) will be null for backfilled rows but
    all DB-driven fields (P&L, trade counts) will be correct.
    """
    from sqlalchemy import text

    try:
        async with async_session_factory() as db:
            trade_result = await db.execute(
                text("SELECT DISTINCT session_date FROM closed_trades ORDER BY session_date")
            )
            trade_dates = {row[0] for row in trade_result.fetchall()}

            summary_result = await db.execute(
                text("SELECT DISTINCT session_date FROM daily_session_summaries")
            )
            summary_dates = {row[0] for row in summary_result.fetchall()}

        missing = sorted(trade_dates - summary_dates)
        if not missing:
            logger.info("Session summaries: all %d trading days accounted for.", len(trade_dates))
            return

        logger.info(
            "Backfilling %d missing session summaries: %s",
            len(missing), missing,
        )
        from app.services.session_summary_service import build_session_summary
        for date_str in missing:
            try:
                async with async_session_factory() as date_db:
                    await build_session_summary(date_db, state_manager, date_str)
                logger.info("Backfilled session summary for %s", date_str)
            except Exception as exc:
                logger.warning("Could not backfill summary for %s: %s", date_str, exc)

    except Exception as exc:
        logger.warning("Session summary backfill failed: %s", exc)


def _with_ml_prob(snapshot_json: str, prob: Optional[float]) -> str:
    """Add the ML filter's probability to an entry snapshot so outcomes can be audited later."""
    if prob is None:
        return snapshot_json
    import json
    try:
        snap = json.loads(snapshot_json)
    except ValueError:
        return snapshot_json
    snap["ml_prob"] = round(prob, 4)
    snap["ml_mode"] = settings.ML_FILTER_MODE
    return json.dumps(snap)


def _time_of_day_extra_points() -> int:
    """
    Returns how many extra points above BOT_ENTRY_SCORE are required based on
    time of day (US Eastern). Calibrated from live trade outcomes:

      9:30–10:30  (open)      → +8   0% historical win rate — near-block
      10:30–12:00 (morning)   → +6   27% historical win rate — high bar
      12:00–14:30 (midday)    → +2   50% win rate, best avg PnL — low bar
      14:30–15:30 (afternoon) → +2   47% win rate — leave open
      15:30–16:00 (close)     → +5   directional but EOD noise; entries blocked
                                      at 30 min before close by risk_manager anyway
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = datetime.now(ZoneInfo("America/New_York"))
    mins_after_open = et.hour * 60 + et.minute - 570  # 9:30 ET = 570 min

    if mins_after_open < 0:
        return 999  # pre-market — block
    elif mins_after_open < 30:
        return 999  # 9:30–10:00 — first 30 min blocked (highest fakeout risk)
    elif mins_after_open < 60:
        return 5    # 10:00–10:30 — still elevated risk, +5 bar
    elif mins_after_open < 150:
        return 3    # 10:30–12:00 — moderate bar
    elif mins_after_open < 270:
        return 0    # 12:00–14:30 — best window, no extra bar
    elif mins_after_open < 360:
        return 0    # 14:30–15:30 — still good
    else:
        return 999  # 15:30–16:00 — EOD noise, block


async def _maybe_auto_execute(sm: StateManager) -> None:
    """
    Predictor Bot (swing) — runs after every scoring cycle (every 30 s).

    Phase 1 — AUTO-EXIT: scan every open bot position and close it if:
      • Tier 1: RED/FLASH_RED + direction == -1 (confirmed high-conviction reversal)
      • Tier 2: direction flipped against position AND score < 55 (low-score reversal)
      • Tier 3: score < 48 (signal completely dead in any direction)
      Stop-price and take-profit exits are handled separately by update_position_prices
      on every incoming price tick — this layer catches *signal-based* reversals.
      A cooldown is applied after every exit to prevent same-symbol churn.

    Phase 2 — AUTO-ENTRY with tiered thresholds:
      A-TRADE (score >= A_TRADE_MIN_SCORE + tod_extra): full risk-based size, live
      B-TRADE (score >= B_TRADE_MIN_SCORE + tod_extra, ENABLE_B_TRADES=True): reduced size, live
      SHADOW  (score >= SHADOW_TRADE_MIN_SCORE): logged + tracked hypothetically
      REJECTED: counted in diagnostics only

    Every evaluated setup (including A-trades and B-trades that execute) is logged
    to the rejected_setups table for diagnostic reporting.
    """
    if not sm.auto_paper_execution:
        return
    if sm.execution_mode == "disabled" or sm.kill_switch:
        return

    from datetime import datetime, timezone

    from app.execution.risk_manager import (
        check_risk,
        calculate_position_size,
        get_trades_today,
        increment_trade_count,
        is_loss_paused,
        record_trade_result,
        set_symbol_cooldown,
    )
    from app.schemas.signals import SignalColor, TradeTier, TradeLabel
    from app.schemas.trading import CreateOrderRequest, OrderSide, OrderType
    from app.services.paper_trading_service import (
        _close_position,
        attach_entry_signal,
        get_daily_pnl,
        get_open_positions,
        place_paper_order,
    )
    from app.services.learning_service import build_entry_snapshot
    from app.services.signal_service import get_all_latest_signals
    from app.services import bot_state_service
    from app.services.shadow_trade_service import log_rejected_setup, open_shadow_trade

    signals = get_all_latest_signals()

    async with async_session_factory() as db:

        # ── ALPACA SYNC: pull real account positions into local DB ────────────
        if sm.execution_mode == "paper_alpaca":
            from app.services.paper_trading_service import sync_alpaca_positions
            cash_before = sm.bot_cash
            await sync_alpaca_positions(db, sm)
            if sm.bot_cash != cash_before:
                async with async_session_factory() as _save_db:
                    await bot_state_service.save_bot_state(_save_db, sm.bot_cash, sm.bot_realized_pnl)

        # ── PHASE 1: AUTO-EXIT ────────────────────────────────────────────────
        positions = await get_open_positions(db)
        for pos in positions:
            # Swing bot only manages its own positions — scalp bot manages scalp ones
            if getattr(pos, "strategy_version", None) in ("scalp", "nn"):
                continue
            sig = signals.get(pos.symbol)
            if not sig:
                continue

            color = sig.color
            should_exit = False
            exit_reason = "signal_exit"
            current_price = sig.price or sm.get_state(pos.symbol).last_price

            if pos.side == "long":
                if color in (SignalColor.FLASH_RED, SignalColor.RED) and sig.direction == -1:
                    should_exit, exit_reason = True, "signal_reversed_bearish"
                    _exhausted_strikes.pop(pos.symbol, None)
                elif sig.direction == -1 and sig.total_score < 55:
                    should_exit, exit_reason = True, "signal_reversed_bearish"
                    _exhausted_strikes.pop(pos.symbol, None)
                elif sig.total_score < 48:
                    in_profit = bool(current_price and current_price > pos.avg_entry_price)
                    # In-profit: allow 2 strikes before exiting (don't shake out too early).
                    # In-loss: exit after just 1 strike — cut losses faster.
                    threshold = 38 if in_profit else 48
                    needed    = 2  if in_profit else 1
                    if sig.total_score >= threshold:
                        _exhausted_strikes.pop(pos.symbol, None)
                    else:
                        _exhausted_strikes[pos.symbol] = _exhausted_strikes.get(pos.symbol, 0) + 1
                        if _exhausted_strikes[pos.symbol] >= needed:
                            should_exit, exit_reason = True, "signal_exhausted"
                else:
                    _exhausted_strikes.pop(pos.symbol, None)

            elif pos.side == "short":
                if color in (SignalColor.FLASH_GREEN, SignalColor.GREEN) and sig.direction == 1:
                    should_exit, exit_reason = True, "signal_reversed_bullish"
                    _exhausted_strikes.pop(pos.symbol, None)
                elif sig.direction == 1 and sig.total_score < 55:
                    should_exit, exit_reason = True, "signal_reversed_bullish"
                    _exhausted_strikes.pop(pos.symbol, None)
                elif sig.total_score < 48:
                    in_profit = bool(current_price and current_price < pos.avg_entry_price)
                    threshold = 38 if in_profit else 48
                    needed    = 2  if in_profit else 1
                    if sig.total_score >= threshold:
                        _exhausted_strikes.pop(pos.symbol, None)
                    else:
                        _exhausted_strikes[pos.symbol] = _exhausted_strikes.get(pos.symbol, 0) + 1
                        if _exhausted_strikes[pos.symbol] >= needed:
                            should_exit, exit_reason = True, "signal_exhausted"
                else:
                    _exhausted_strikes.pop(pos.symbol, None)

            if should_exit:
                exit_price = current_price
                if not exit_price:
                    continue

                if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
                    try:
                        from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                        await AlpacaPaperExecutionAdapter().close_position(pos.symbol, exit_price)
                        logger.info("ALPACA PAPER CLOSE submitted: %s", pos.symbol)
                    except Exception as exc:
                        logger.error("Alpaca paper close failed for %s: %s", pos.symbol, exc)

                if sm.execution_mode == "live" and settings.LIVE_TRADING_ENABLED:
                    try:
                        from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
                        await AlpacaLiveExecutionAdapter().close_position(pos.symbol, exit_price)
                        logger.warning("LIVE CLOSE submitted: %s", pos.symbol)
                    except Exception as exc:
                        logger.error("Live close failed for %s: %s", pos.symbol, exc)

                trade = await _close_position(db, pos.symbol, exit_price, exit_reason)
                if trade:
                    await db.commit()
                    record_trade_result(bool(trade.is_winner))
                    position_cost = pos.avg_entry_price * pos.qty
                    sm.bot_cash += position_cost + trade.pnl
                    sm.bot_realized_pnl += trade.pnl
                    await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
                    logger.info(
                        "BOT EXIT: %s @ $%.2f | PnL=$%.2f (%s) | wallet=$%.2f",
                        pos.symbol, exit_price, trade.pnl, exit_reason, sm.bot_cash,
                    )
                    sm.broadcast_sse("bot_exit", {
                        "symbol": pos.symbol, "exit_price": exit_price,
                        "pnl": trade.pnl, "reason": exit_reason,
                        "bot_cash": sm.bot_cash,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    })
                    set_symbol_cooldown(pos.symbol)
                    _exhausted_strikes.pop(pos.symbol, None)

        # Refresh positions after exits
        positions = await get_open_positions(db)

        # ── DAILY LOSS LIMIT: flatten all positions if breached ──────────────
        daily_pnl_check = await get_daily_pnl(db)
        if daily_pnl_check <= -abs(settings.DAILY_LOSS_LIMIT):
            positions_to_close = await get_open_positions(db)
            if positions_to_close:
                logger.warning(
                    "RISK: daily loss limit ($%.0f) breached (PnL=$%.0f) — "
                    "flattening all %d open positions.",
                    settings.DAILY_LOSS_LIMIT, daily_pnl_check, len(positions_to_close),
                )
                from app.services.paper_trading_service import flatten_all_positions
                prices = {
                    sym: state_manager.get_state(sym).last_price
                    for sym in state_manager.all_symbols()
                    if state_manager.get_state(sym).last_price
                }
                if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
                    try:
                        from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                        adapter = AlpacaPaperExecutionAdapter()
                        for pos in positions_to_close:
                            try:
                                await adapter.close_position(pos.symbol)
                            except Exception as _exc:
                                logger.error("Alpaca close on loss-limit for %s: %s", pos.symbol, _exc)
                    except Exception as _exc:
                        logger.error("Alpaca loss-limit flatten failed: %s", _exc)
                await flatten_all_positions(db, prices)
            return

        # ── PHASE 2: TIERED AUTO-ENTRY ────────────────────────────────────────
        from app.execution.risk_manager import maybe_reset_daily_counters
        maybe_reset_daily_counters()

        if sm.max_trades_per_day > 0 and get_trades_today() >= sm.max_trades_per_day:
            return
        if sm.bot_cash < 5.0:
            logger.debug("Bot wallet too low ($%.2f) — skipping entry.", sm.bot_cash)
            return

        swing_positions = [p for p in positions if getattr(p, "strategy_version", None) not in ("scalp", "nn")]
        max_swing = settings.MAX_SWING_POSITIONS

        tod_extra    = _time_of_day_extra_points()
        a_min_score  = settings.A_TRADE_MIN_SCORE + tod_extra
        b_min_score  = settings.B_TRADE_MIN_SCORE + tod_extra
        sh_min_score = settings.SHADOW_TRADE_MIN_SCORE  # no tod_extra for shadow

        # ── Score every candidate and classify into a tier ────────────────────
        for sym, sig in signals.items():
            if sym in ("SPY", "QQQ"):
                continue
            if not sig.price or sig.direction == 0:
                continue

            already_in_position = any(p.symbol == sym for p in positions)
            vwap_misaligned = (
                (sig.direction == -1 and sig.vwap and sig.price > sig.vwap) or
                (sig.direction ==  1 and sig.vwap and sig.price < sig.vwap)
            )
            has_thesis = bool(sig.thesis and sig.thesis.suggested_stop and sig.thesis.suggested_target)
            score = sig.total_score

            # ── Classify tier ─────────────────────────────────────────────────
            rejection_reasons = []

            if score < sh_min_score:
                tier = TradeTier.REJECTED
                rejection_reasons.append("score_below_shadow_threshold")
                await log_rejected_setup(db, sig, tier, rejection_reasons)
                continue   # not worth continuing — truly below threshold

            # Shadow and above: check hard-filter reasons
            if already_in_position:
                rejection_reasons.append("already_in_position")
            if vwap_misaligned:
                rejection_reasons.append("vwap_misaligned")
            if not has_thesis:
                rejection_reasons.append("missing_stop_or_tp")
            if is_loss_paused():
                rejection_reasons.append("consecutive_loss_pause")

            if score >= a_min_score and not rejection_reasons:
                tier = TradeTier.A_TRADE
            elif score >= b_min_score and not rejection_reasons:
                tier = TradeTier.B_TRADE
            else:
                # Shadow range OR hard-blocked
                if score >= sh_min_score:
                    tier = TradeTier.SHADOW
                    if not rejection_reasons:
                        rejection_reasons.append("score_below_b_threshold")
                else:
                    tier = TradeTier.REJECTED
                    if not rejection_reasons:
                        rejection_reasons.append("score_below_shadow_threshold")

            sig.trade_tier = tier

            # ── Shadow trade: log and open hypothetical position ──────────────
            if tier == TradeTier.SHADOW:
                await log_rejected_setup(db, sig, tier, rejection_reasons)
                if settings.ENABLE_SHADOW_TRADES and not already_in_position:
                    await open_shadow_trade(db, sig, strategy="predictor")
                continue

            if tier == TradeTier.REJECTED:
                await log_rejected_setup(db, sig, tier, rejection_reasons)
                continue

            # ── B-trade: check flag ───────────────────────────────────────────
            if tier == TradeTier.B_TRADE and not settings.ENABLE_B_TRADES:
                await log_rejected_setup(db, sig, tier, ["b_trades_disabled"])
                continue

            # ── A or B: check swing position cap ─────────────────────────────
            if len(swing_positions) >= max_swing:
                await log_rejected_setup(db, sig, tier, ["max_swing_positions"])
                continue

            # ── A or B: log the setup (no rejection reasons = executed) ──────
            await log_rejected_setup(db, sig, tier, [])

        # ── Find the best live candidate this cycle ───────────────────────────
        # Re-evaluate: pick the A-trade with highest score, then B-trade if none.
        live_candidates = [
            sig for sym, sig in signals.items()
            if sym not in ("SPY", "QQQ")
            and sig.trade_tier in (TradeTier.A_TRADE, TradeTier.B_TRADE)
            and sig.price
            and not any(p.symbol == sym for p in positions)
            and len([p for p in positions if getattr(p, "strategy_version", None) not in ("scalp", "nn")]) < max_swing
        ]

        if not live_candidates:
            return

        # ML trade filter — score every candidate; in gate mode drop the weak ones
        ml_probs: dict[str, Optional[float]] = {}
        if settings.ML_FILTER_MODE != "off":
            from app.ml import predictor as ml
            kept = []
            for sig in live_candidates:
                prob = ml.predict(sm.get_state(sig.symbol), sig, engine_scalp=False)
                ml_probs[sig.symbol] = prob
                allowed, note = ml.allows(prob)
                if prob is not None:
                    logger.info("ML swing %s score=%.0f %s", sig.symbol, sig.total_score, note)
                if allowed:
                    kept.append(sig)
                else:
                    await log_rejected_setup(db, sig, sig.trade_tier, [note.split(" ")[0]])
            live_candidates = kept
            if not live_candidates:
                return

        # Prefer A-trades over B-trades; within same tier prefer higher score
        live_candidates.sort(key=lambda s: (0 if s.trade_tier == TradeTier.A_TRADE else 1, -s.total_score))
        best = live_candidates[0]
        is_b_trade = (best.trade_tier == TradeTier.B_TRADE)

        price  = best.price
        side   = OrderSide.BUY if best.direction == 1 else OrderSide.SELL
        stop   = best.thesis.suggested_stop   if best.thesis else None
        target = best.thesis.suggested_target if best.thesis else None

        # Thesis gate — must have stop + target (already checked in tier classification
        # but being explicit here for safety)
        if not stop or not target:
            logger.warning(
                "BOT ENTRY BLOCKED — %s: no stop/TP (source=%s score=%.0f).",
                best.symbol,
                getattr(getattr(best, "thesis", None), "thesis_source", "no_thesis"),
                best.total_score,
            )
            sm.thesis_rejections += 1
            sm.log_thesis_rejection(best.symbol, best.total_score, "missing_stop_or_tp")
            return

        # ── Position sizing ───────────────────────────────────────────────────
        if settings.USE_RISK_BASED_SIZING:
            # Scale risk% by conviction for A-trades — higher score = bigger size.
            # B-trades keep their reduced fraction regardless of score.
            _score = best.total_score
            if is_b_trade:
                conviction_mult = 1.0
            elif _score >= 95:  conviction_mult = 2.0
            elif _score >= 90:  conviction_mult = 1.5
            elif _score >= 87:  conviction_mult = 1.25
            else:               conviction_mult = 1.0
            effective_risk_pct = settings.PER_TRADE_RISK_PCT * conviction_mult
            qty = calculate_position_size(
                equity=sm.bot_cash,
                price=price,
                stop_price=stop,
                risk_pct=effective_risk_pct,
                max_position_pct=settings.MAX_POSITION_SIZE_PCT,
                is_b_trade=is_b_trade,
            )
            sizing_note = f"risk_based riskPct={effective_risk_pct:.2f}% (×{conviction_mult}) isB={is_b_trade}"
        else:
            # Legacy conviction-scaled allocation-% model
            score = best.total_score
            if score >= 100:   mult = 2.5
            elif score >= 95:  mult = 2.0
            elif score >= 90:  mult = 1.5
            else:              mult = 1.0
            raw_pct = settings.BOT_ALLOCATION_PCT * mult
            if is_b_trade:
                raw_pct *= settings.B_TRADE_SIZE_FRACTION
            alloc_pct = min(raw_pct, settings.MAX_POSITION_SIZE_PCT)
            alloc_dollars = sm.bot_cash * (alloc_pct / 100.0)
            qty = round(alloc_dollars / price, 4)
            sizing_note = f"alloc_pct={alloc_pct:.1f}%"

        if side == OrderSide.SELL:
            qty = max(1, int(qty))      # Alpaca paper: no fractional shorts
        else:
            qty = max(0.01, qty)

        proposed_dollars = qty * price
        logger.info(
            "Sizing %s (%s): score=%.0f %s qty=%.4f ($%.0f)",
            best.symbol, "B" if is_b_trade else "A",
            best.total_score, sizing_note, qty, proposed_dollars,
        )

        thesis_note = (best.thesis.why_now[:150] if best.thesis and best.thesis.why_now else None)
        strategy_ver = "B" if is_b_trade else "A"

        req = CreateOrderRequest(
            symbol=best.symbol,
            side=side,
            qty=qty,
            order_type=OrderType.MARKET,
            stop_price=stop,
            take_profit_price=target,
            thesis_summary=thesis_note,
            strategy_version=strategy_ver,
        )

        daily_pnl = await get_daily_pnl(db)
        rejection = check_risk(
            req, sm.kill_switch, daily_pnl, len(positions),
            equity=sm.bot_cash,
            open_positions=positions,
            proposed_trade_dollars=proposed_dollars,
        )
        if rejection:
            logger.info("Bot entry blocked for %s: %s", best.symbol, rejection)
            return

        # ── Broker submission ─────────────────────────────────────────────────
        if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
            try:
                from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                paper_order = await AlpacaPaperExecutionAdapter().submit_order(req, price)
                if paper_order.filled_price:
                    price = paper_order.filled_price
                logger.info(
                    "ALPACA PAPER ORDER: %s %s %.4f @ $%.2f (score=%.0f tier=%s)",
                    side.value.upper(), best.symbol, qty, price, best.total_score,
                    "B" if is_b_trade else "A",
                )
            except Exception as exc:
                logger.error("Alpaca paper order failed for %s: %s — recording locally only", best.symbol, exc)

        if sm.execution_mode == "live" and settings.LIVE_TRADING_ENABLED:
            try:
                from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
                live_order = await AlpacaLiveExecutionAdapter().submit_order(req, price)
                if live_order.filled_price:
                    price = live_order.filled_price
                logger.warning(
                    "LIVE ORDER: %s %s %.4f @ $%.2f (score=%.0f tier=%s)",
                    side.value.upper(), best.symbol, qty, price, best.total_score,
                    "B" if is_b_trade else "A",
                )
            except Exception as exc:
                logger.error("Live order failed for %s: %s — aborting entry", best.symbol, exc)
                return

        order = await place_paper_order(req, db, sm.execution_mode, price)
        if order.status == "filled":
            increment_trade_count()
            snapshot_json = _with_ml_prob(build_entry_snapshot(best), ml_probs.get(best.symbol))
            await attach_entry_signal(db, best.symbol, snapshot_json)
            await db.commit()
            trade_value = qty * price
            sm.bot_cash -= trade_value
            await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
            logger.info(
                "BOT ENTRY (%s): %s %s %.4f @ $%.2f | score=%.0f | "
                "stop=$%s | target=$%s | wallet=$%.2f",
                "B-TRADE" if is_b_trade else "A-TRADE",
                side.value.upper(), best.symbol, qty, price, best.total_score,
                f"{stop:.2f}" if stop else "—",
                f"{target:.2f}" if target else "—",
                sm.bot_cash,
            )
            sm.broadcast_sse("bot_trade", {
                "symbol": best.symbol, "side": side.value, "qty": qty,
                "price": price, "score": best.total_score,
                "tier": "B" if is_b_trade else "A",
                "stop": stop, "target": target, "thesis": thesis_note,
                "bot_cash": sm.bot_cash,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })


async def _maybe_scalp_execute(sm: StateManager) -> None:
    """
    Scalp Bot — runs after every scoring cycle alongside the swing bot.

    Phase 1 — SCALP EXIT: close positions opened by the scalp bot if the
      scalp signal reverses direction. Scalp positions are identified by
      thesis_summary starting with "Scalp".

    Phase 2 — SCALP ENTRY: enter the best symbol whose scalp score is
      >= SCALP_ENTRY_SCORE. Uses a smaller allocation (SCALP_ALLOCATION_PCT)
      and tight ATR-based stop/target from the scalp thesis.
    """
    if not sm.auto_paper_execution:
        return
    if not settings.SCALP_MODE_ENABLED:
        return
    if sm.execution_mode == "disabled" or sm.kill_switch:
        return

    from datetime import datetime, timedelta, timezone

    from app.execution.risk_manager import (
        check_risk,
        get_trades_today,
        increment_trade_count,
    )
    from app.schemas.signals import SignalColor, TradeLabel
    from app.schemas.trading import CreateOrderRequest, OrderSide, OrderType
    from app.services.paper_trading_service import (
        _close_position,
        attach_entry_signal,
        get_daily_pnl,
        get_open_positions,
        place_paper_order,
    )
    from app.services.learning_service import build_entry_snapshot
    from app.services.signal_service import get_all_latest_scalp_signals

    scalp_signals = get_all_latest_scalp_signals()

    async with async_session_factory() as db:

        # ── PHASE 1: SCALP EXIT ───────────────────────────────────────────────
        positions = await get_open_positions(db)
        scalp_positions = [p for p in positions
                           if getattr(p, "strategy_version", None) == "scalp"]

        for pos in scalp_positions:
            sig = scalp_signals.get(pos.symbol)
            if not sig:
                continue

            current_price = sig.price or sm.get_state(pos.symbol).last_price
            should_exit = False
            exit_reason = "scalp_signal_exit"

            if pos.side == "long":
                if sig.direction == -1 and sig.total_score >= settings.SCALP_ENTRY_SCORE:
                    should_exit = True
                    exit_reason = "scalp_reversed_bearish"
                elif sig.total_score < 40:
                    should_exit = True
                    exit_reason = "scalp_exhausted"
            elif pos.side == "short":
                if sig.direction == 1 and sig.total_score >= settings.SCALP_ENTRY_SCORE:
                    should_exit = True
                    exit_reason = "scalp_reversed_bullish"
                elif sig.total_score < 40:
                    should_exit = True
                    exit_reason = "scalp_exhausted"

            if should_exit and current_price:
                # Close on Alpaca FIRST so sync_alpaca_positions doesn't
                # re-import this as a zombie position without a stop.
                if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
                    try:
                        from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                        await AlpacaPaperExecutionAdapter().close_position(pos.symbol, current_price)
                    except Exception as exc:
                        logger.warning("Alpaca scalp close failed for %s: %s", pos.symbol, exc)

                trade = await _close_position(db, pos.symbol, current_price, exit_reason)
                if trade:
                    await db.commit()
                    from app.execution.risk_manager import record_trade_result
                    record_trade_result(bool(trade.is_winner))
                    position_cost = pos.avg_entry_price * pos.qty
                    sm.bot_cash += position_cost + trade.pnl
                    sm.bot_realized_pnl += trade.pnl
                    from app.services import bot_state_service
                    await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
                    _scalp_cooldowns[pos.symbol] = datetime.now(timezone.utc)
                    logger.info(
                        "SCALP EXIT: %s @ $%.2f | PnL=$%.2f (%s) | wallet=$%.2f",
                        pos.symbol, current_price, trade.pnl, exit_reason, sm.bot_cash,
                    )
                    sm.broadcast_sse("scalp_exit", {
                        "symbol": pos.symbol,
                        "exit_price": current_price,
                        "pnl": trade.pnl,
                        "reason": exit_reason,
                        "bot_cash": sm.bot_cash,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    })

        # Refresh positions after exits
        positions = await get_open_positions(db)

        # ── PHASE 2: SCALP ENTRY ──────────────────────────────────────────────
        from app.execution.risk_manager import maybe_reset_daily_counters
        maybe_reset_daily_counters()

        if sm.max_trades_per_day > 0 and get_trades_today() >= sm.max_trades_per_day:
            return

        if sm.bot_cash < 5.0:
            return

        now = datetime.now(timezone.utc)
        cooldown_delta = timedelta(minutes=settings.SCALP_COOLDOWN_MINUTES)

        # Enforce scalp position cap before evaluating candidates
        open_scalp_positions = [p for p in positions if getattr(p, "strategy_version", None) == "scalp"]
        if len(open_scalp_positions) >= settings.MAX_SCALP_POSITIONS:
            return

        _SCALP_MIN_PRICE = 15.0   # never scalp stocks below $15 — tiny ATR → $0 profit

        candidates = [
            sig
            for sym, sig in scalp_signals.items()
            if sym not in ("SPY", "QQQ")
            and sig.total_score >= settings.SCALP_ENTRY_SCORE
            and sig.direction != 0
            and sig.price
            and sig.price >= _SCALP_MIN_PRICE          # skip penny / low-priced stocks
            and sig.thesis
            and sig.thesis.suggested_stop
            and sig.thesis.suggested_target
            # No open position in this symbol (either swing or scalp)
            and not any(p.symbol == sym for p in positions)
            # Scalp-specific cooldown (shorter than swing cooldown)
            and (sym not in _scalp_cooldowns or
                 (now - _scalp_cooldowns[sym]) > cooldown_delta)
            # Minimum re-entry gap: don't re-enter a symbol within cooldown window
            # even if the position was closed by a stop hit (not caught by _scalp_cooldowns)
            and (sym not in _scalp_entry_times or
                 (now - _scalp_entry_times[sym]) > cooldown_delta)
            # VWAP alignment: don't short above VWAP or long below VWAP
            and not (sig.direction == -1 and sig.vwap and sig.price > sig.vwap)
            and not (sig.direction == 1 and sig.vwap and sig.price < sig.vwap)
        ]

        if not candidates:
            return

        # ML trade filter — score every candidate; in gate mode drop the weak ones
        ml_probs: dict[str, Optional[float]] = {}
        if settings.ML_FILTER_MODE != "off":
            from app.ml import predictor as ml
            kept = []
            for sig in candidates:
                prob = ml.predict(sm.get_state(sig.symbol), sig, engine_scalp=True)
                ml_probs[sig.symbol] = prob
                allowed, note = ml.allows(prob)
                if prob is not None:
                    logger.debug("ML scalp %s score=%.0f %s", sig.symbol, sig.total_score, note)
                if allowed:
                    kept.append(sig)
            candidates = kept
            if not candidates:
                return

        best = max(candidates, key=lambda s: s.total_score)
        price = best.price
        side  = OrderSide.BUY if best.direction == 1 else OrderSide.SELL

        # Cap scalp allocation at $25k per trade regardless of wallet size —
        # prevents huge share counts on cheap stocks as the wallet grows.
        _SCALP_MAX_DOLLARS = 25_000.0
        alloc_dollars = min(sm.bot_cash * (settings.SCALP_ALLOCATION_PCT / 100.0), _SCALP_MAX_DOLLARS)
        qty = round(alloc_dollars / price, 4)
        # Alpaca paper doesn't allow fractional short sales — round down to whole shares
        if side == OrderSide.SELL:
            qty = max(1, int(qty))
        else:
            qty = max(0.01, qty)

        thesis_note = f"Scalp — {best.thesis.why_now[:120]}" if best.thesis else "Scalp entry"

        req = CreateOrderRequest(
            symbol=best.symbol,
            side=side,
            qty=qty,
            order_type=OrderType.MARKET,
            stop_price=best.thesis.suggested_stop,
            take_profit_price=best.thesis.suggested_target,
            thesis_summary=thesis_note,
            strategy_version="scalp",
        )

        daily_pnl = await get_daily_pnl(db)
        rejection = check_risk(req, sm.kill_switch, daily_pnl, len(positions))
        if rejection:
            logger.debug("Scalp entry blocked for %s: %s", best.symbol, rejection)
            return

        # Paper Alpaca: submit to Alpaca paper broker before local recording
        if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
            try:
                from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                paper = AlpacaPaperExecutionAdapter()
                paper_order = await paper.submit_order(req, price)
                if paper_order.filled_price:
                    price = paper_order.filled_price
            except Exception as exc:
                logger.error("Alpaca paper scalp order failed for %s: %s — recording locally only", best.symbol, exc)

        order = await place_paper_order(req, db, sm.execution_mode, price)
        if order.status == "filled":
            increment_trade_count()
            _scalp_entry_times[best.symbol] = now   # block re-entry for cooldown window
            snapshot_json = _with_ml_prob(build_entry_snapshot(best), ml_probs.get(best.symbol))
            await attach_entry_signal(db, best.symbol, snapshot_json)
            await db.commit()
            sm.bot_cash -= qty * price
            from app.services import bot_state_service
            await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
            logger.info(
                "SCALP ENTRY: %s %s %.4f @ $%.2f | score=%.0f | stop=$%.2f | "
                "target=$%.2f | wallet=$%.2f",
                side.value.upper(), best.symbol, qty, price, best.total_score,
                best.thesis.suggested_stop, best.thesis.suggested_target, sm.bot_cash,
            )
            sm.broadcast_sse("scalp_trade", {
                "symbol":   best.symbol,
                "side":     side.value,
                "qty":      qty,
                "price":    price,
                "score":    best.total_score,
                "stop":     best.thesis.suggested_stop,
                "target":   best.thesis.suggested_target,
                "bot_cash": sm.bot_cash,
                "sent_at":  datetime.now(timezone.utc).isoformat(),
            })


# NN bot: symbol → last entry time (per-symbol re-entry gap)
_nn_entry_times: dict = {}


async def _maybe_nn_execute(sm: StateManager) -> None:
    """
    Autonomous neural-net bot — runs after every scoring cycle alongside the
    swing and scalp bots, but ignores their signals entirely.

    Phase 1 — EXIT: close NN positions held longer than ML_NN_MAX_HOLD_MINUTES
      (the model was trained on a 60-minute horizon). Stops and targets are
      handled by the shared tick monitor in _update_position_price.

    Phase 2 — ENTRY: score every symbol with the autonomous model (P long,
      P short from raw bars) and enter the most confident side that clears
      the model's validated threshold.

    Paper only: never runs in live execution mode.
    """
    if not settings.ML_NN_BOT_ENABLED or not sm.auto_paper_execution:
        return
    if sm.execution_mode in ("disabled", "live") or sm.kill_switch:
        return

    from datetime import datetime, timedelta, timezone

    from app.execution.risk_manager import check_risk, get_trades_today, increment_trade_count
    from app.ml import predictor as ml
    from app.ml.auto import trade_levels
    from app.schemas.trading import CreateOrderRequest, OrderSide, OrderType
    from app.services.paper_trading_service import (
        _close_position,
        attach_entry_signal,
        get_daily_pnl,
        get_open_positions,
        place_paper_order,
    )

    meta = ml.auto_meta()
    if meta is None:
        return
    threshold = meta["threshold"]
    now = datetime.now(timezone.utc)
    use_alpaca = sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present

    async with async_session_factory() as db:

        # ── PHASE 1: TIME EXIT ────────────────────────────────────────────────
        positions = await get_open_positions(db)
        max_hold = timedelta(minutes=settings.ML_NN_MAX_HOLD_MINUTES)
        for pos in [p for p in positions if getattr(p, "strategy_version", None) == "nn"]:
            opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=timezone.utc)
            if now - opened < max_hold:
                continue
            price = sm.get_state(pos.symbol).last_price
            if not price:
                continue
            if use_alpaca:
                try:
                    from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                    await AlpacaPaperExecutionAdapter().close_position(pos.symbol, price)
                except Exception as exc:
                    logger.warning("Alpaca NN close failed for %s: %s", pos.symbol, exc)
            trade = await _close_position(db, pos.symbol, price, "nn_time_exit")
            if trade:
                await db.commit()
                from app.execution.risk_manager import record_trade_result
                record_trade_result(bool(trade.is_winner))
                sm.bot_cash += pos.avg_entry_price * pos.qty + trade.pnl
                sm.bot_realized_pnl += trade.pnl
                await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
                logger.info("NN EXIT: %s @ $%.2f | PnL=$%.2f (time exit) | wallet=$%.2f",
                            pos.symbol, price, trade.pnl, sm.bot_cash)
                sm.broadcast_sse("bot_exit", {
                    "symbol": pos.symbol, "exit_price": price, "pnl": trade.pnl,
                    "reason": "nn_time_exit", "bot_cash": sm.bot_cash, "sent_at": now.isoformat(),
                })

        # ── PHASE 2: ENTRY ────────────────────────────────────────────────────
        positions = await get_open_positions(db)
        if len([p for p in positions if getattr(p, "strategy_version", None) == "nn"]) >= settings.ML_NN_MAX_POSITIONS:
            return
        if sm.max_trades_per_day > 0 and get_trades_today() >= sm.max_trades_per_day:
            return
        if sm.bot_cash < 5.0:
            return

        held = {p.symbol for p in positions}
        gap = timedelta(minutes=settings.ML_NN_COOLDOWN_MINUTES)
        best = None
        for sym in sm.all_symbols():
            if sym in ("SPY", "QQQ") or sym in held:
                continue
            if sym in _nn_entry_times and now - _nn_entry_times[sym] < gap:
                continue
            state = sm.get_state(sym)
            if not state.last_price or state.last_price < 15.0:
                continue
            try:
                r = ml.predict_auto(state)
            except Exception as exc:
                logger.debug("NN scoring failed for %s: %s", sym, exc)
                continue
            if r is None:
                continue
            direction, prob = (1, r["p_long"]) if r["p_long"] >= r["p_short"] else (-1, r["p_short"])
            if prob >= threshold and (best is None or prob > best[2]):
                best = (sym, direction, prob, r)

        if best is None:
            return

        sym, direction, prob, reading = best
        price = sm.get_state(sym).last_price
        side = OrderSide.BUY if direction == 1 else OrderSide.SELL
        stop, target = trade_levels(price, reading["atr"], direction)

        alloc = min(sm.bot_cash * settings.ML_NN_ALLOCATION_PCT / 100.0, 25_000.0)
        qty = round(alloc / price, 4)
        qty = max(1, int(qty)) if side == OrderSide.SELL else max(0.01, qty)

        req = CreateOrderRequest(
            symbol=sym,
            side=side,
            qty=qty,
            order_type=OrderType.MARKET,
            stop_price=round(stop, 4),
            take_profit_price=round(target, 4),
            thesis_summary=(f"NN — P(long)={reading['p_long']:.2f} P(short)={reading['p_short']:.2f} "
                            f"threshold={threshold:.2f}"),
            strategy_version="nn",
        )
        daily_pnl = await get_daily_pnl(db)
        rejection = check_risk(req, sm.kill_switch, daily_pnl, len(positions))
        if rejection:
            logger.debug("NN entry blocked for %s: %s", sym, rejection)
            return

        if use_alpaca:
            try:
                from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                paper_order = await AlpacaPaperExecutionAdapter().submit_order(req, price)
                if paper_order.filled_price:
                    price = paper_order.filled_price
            except Exception as exc:
                logger.error("Alpaca paper NN order failed for %s: %s — recording locally only", sym, exc)

        order = await place_paper_order(req, db, sm.execution_mode, price)
        if order.status != "filled":
            return
        increment_trade_count()
        _nn_entry_times[sym] = now
        import json
        await attach_entry_signal(db, sym, json.dumps({
            "engine": "nn",
            "direction": direction,
            "ml_prob": round(prob, 4),
            "p_long": reading["p_long"],
            "p_short": reading["p_short"],
            "threshold": threshold,
            "model_trained_at": meta["trained_at"],
            "price": price,
            "suggested_stop": req.stop_price,
            "suggested_target": req.take_profit_price,
        }))
        await db.commit()
        sm.bot_cash -= qty * price
        await bot_state_service.save_bot_state(db, sm.bot_cash, sm.bot_realized_pnl)
        logger.info(
            "NN ENTRY: %s %s %.4f @ $%.2f | p=%.3f (thr %.3f) | stop=$%.2f target=$%.2f | wallet=$%.2f",
            side.value.upper(), sym, qty, price, prob, threshold, stop, target, sm.bot_cash,
        )
        sm.broadcast_sse("nn_trade", {
            "symbol": sym, "side": side.value, "qty": qty, "price": price, "prob": prob,
            "stop": stop, "target": target, "bot_cash": sm.bot_cash, "sent_at": now.isoformat(),
        })


async def _ml_retrain_scheduler() -> None:
    """
    Once per weekday after ML_RETRAIN_TIME (ET), run scripts/ml_retrain.py in a
    subprocess: fetch the day's bars, retrain every model, promote challengers
    that beat the champion. The predictor hot-reloads promoted models.
    Output goes to logs/ml_retrain.log; results to models/ml/history.json.
    """
    import sys
    from datetime import time as dtime

    from app.utils.time_utils import now_et

    root = Path(__file__).resolve().parent.parent
    h, m = map(int, settings.ML_RETRAIN_TIME.split(":"))
    last_run_date = None
    while True:
        try:
            await asyncio.sleep(300)
            if not settings.ML_RETRAIN_ENABLED:
                continue
            now = now_et()
            if now.weekday() >= 5 or now.time() < dtime(h, m) or last_run_date == now.date():
                continue
            last_run_date = now.date()
            logger.info("ML retrain: starting nightly run …")
            (root / "logs").mkdir(exist_ok=True)
            with open(root / "logs" / "ml_retrain.log", "ab") as log:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(root / "scripts" / "ml_retrain.py"),
                    cwd=str(root), stdout=log, stderr=asyncio.subprocess.STDOUT,
                )
                code = await proc.wait()
            logger.info("ML retrain finished (exit %s) — see models/ml/history.json", code)
            state_manager.broadcast_sse("ml_retrain", {"exit_code": code, "at": now.isoformat()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ML retrain scheduler error: %s", exc)


async def _whale_startup(symbols: list[str]) -> None:
    """
    Kick off whale data on startup (non-blocking — runs in background).
    Runs two independent fetches concurrently:
      • refresh_all_whales  — per-symbol 13F + options (feeds dashboard cards)
      • get_whale_portfolio_changes — per-fund 13F diffs (feeds /whales page)
    """
    from app.services.whale_service import refresh_all_whales, whale_refresh_loop
    from app.services.whale_wallet_service import get_whale_portfolio_changes

    # Launch both fetches at the same time — they're independent
    logger.info("Whale: starting per-symbol fetch + portfolio-change diff in parallel…")
    await asyncio.gather(
        _run_whale_symbols(symbols, refresh_all_whales),
        _run_whale_portfolio(get_whale_portfolio_changes),
        return_exceptions=True,
    )
    logger.info("Whale: initial fetches complete.")
    # Periodic 13F refresh loop (every 6 hours)
    await whale_refresh_loop(symbols, interval_hours=6)


async def _run_whale_symbols(symbols, refresh_fn) -> None:
    try:
        from app.providers.sec_edgar import warm_holdings_cache
        logger.info("Whale: warming per-fund holdings cache (~30 EDGAR calls)…")
        await warm_holdings_cache()          # fetch each fund's 13F ONCE
        await refresh_fn(symbols)            # reads from cache — no extra EDGAR calls
        logger.info("Whale: per-symbol fetch complete.")
    except Exception as exc:
        logger.warning("Whale per-symbol fetch failed: %s", exc)


async def _run_whale_portfolio(portfolio_fn) -> None:
    # Give warm_holdings_cache() time to finish before portfolio diff starts,
    # so both tasks aren't hitting EDGAR simultaneously.
    logger.info("Whale: portfolio-change diff will start in 120s (waiting for cache warm)…")
    await asyncio.sleep(120)
    try:
        await portfolio_fn()
        logger.info("Whale: portfolio-change diff complete.")
    except Exception as exc:
        logger.warning("Whale portfolio-change fetch failed: %s", exc)


async def _log_daily_summary(sm: StateManager) -> None:
    """
    Print a clean end-of-day P&L summary to the log and broadcast via SSE.
    Pulls real equity figures from Alpaca when in paper_alpaca mode.
    """
    from app.utils.time_utils import session_date_str
    from app.services.paper_trading_service import get_closed_trades, get_open_positions

    date_str = session_date_str()

    async with async_session_factory() as db:
        trades   = await get_closed_trades(db, date_str)
        open_pos = await get_open_positions(db)

    winners  = [t for t in trades if t.is_winner]
    losers   = [t for t in trades if not t.is_winner]
    closed_pnl = round(sum(t.pnl for t in trades), 2)
    win_rate   = round(len(winners) / len(trades) * 100, 1) if trades else 0

    # Pull live Alpaca figures
    alpaca_equity = None
    alpaca_day_pnl = None
    if sm.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
        try:
            from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
            adapter = AlpacaPaperExecutionAdapter()
            acct = await adapter._provider.get_account()
            if acct:
                alpaca_equity  = acct.get("equity")
                alpaca_cash    = acct.get("cash")
        except Exception:
            pass

    sep = "=" * 62
    lines = [
        sep,
        f"  📊  DAILY SUMMARY — {date_str}",
        sep,
        f"  Bot trades today : {len(trades)}  ({len(winners)}W / {len(losers)}L)  win rate {win_rate}%",
        f"  Closed P&L       : ${closed_pnl:+,.2f}",
    ]
    if alpaca_equity is not None:
        lines += [
            f"  Alpaca equity    : ${alpaca_equity:,.2f}",
            f"  Alpaca cash      : ${alpaca_cash:,.2f}",
        ]
    if open_pos:
        lines.append(f"  Open positions   : {', '.join(p.symbol for p in open_pos)} (carrying overnight)")
    else:
        lines.append("  Open positions   : none (flat)")
    if trades:
        best  = max(trades, key=lambda t: t.pnl)
        worst = min(trades, key=lambda t: t.pnl)
        lines += [
            f"  Best trade       : {best.symbol} ${best.pnl:+,.2f}",
            f"  Worst trade      : {worst.symbol} ${worst.pnl:+,.2f}",
        ]
    lines.append(sep)

    for line in lines:
        logger.info(line)

    sm.broadcast_sse("daily_summary", {
        "date": date_str,
        "trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "closed_pnl": closed_pnl,
        "alpaca_equity": alpaca_equity,
        "open_positions": [p.symbol for p in open_pos],
    })


async def _eod_monitor(market_provider=None) -> None:
    """
    Monitor approach of market close.
    Flatten all paper positions EOD_FLATTEN_MINUTES before close.
    Build session summary after close.
    Reload today's bar history at market open so signals aren't blind
    after an overnight run.
    """
    eod_done_today = False
    summary_done_today = False
    open_reset_done_today = False
    last_date = session_date_str()

    while True:
        try:
            await asyncio.sleep(60)
            current_date = session_date_str()

            # Reset flags on new trading day (weekdays only — skip Sat/Sun rollovers)
            from app.utils.time_utils import today_et
            if current_date != last_date and today_et().weekday() < 5:
                eod_done_today = False
                summary_done_today = False
                open_reset_done_today = False
                last_date = current_date
                state_manager.session_date = current_date
                state_manager.best_trade_of_day = None
                for sym in state_manager.all_symbols():
                    state_manager.reset_session(sym)
                logger.info("New trading day: %s", current_date)

            # At market open, reload today's bar history from the provider so
            # the signal engine has full intraday context.  This replaces the
            # old manual VWAP-accumulator reset: load_today_bars() calls
            # reset_session() internally and then rebuilds VWAP bar-by-bar.
            from app.utils.time_utils import is_market_open
            if is_market_open() and not open_reset_done_today:
                open_reset_done_today = True   # set first to prevent re-entry
                if market_provider:
                    logger.info("Market open: reloading today's bars for all symbols…")
                    _reload_tasks = [
                        load_today_bars(sym, market_provider, state_manager)
                        for sym in state_manager.all_symbols()
                    ]
                    await asyncio.gather(*_reload_tasks, return_exceptions=True)
                    loaded = sum(
                        len(state_manager.get_state(sym).bars_1m)
                        for sym in state_manager.all_symbols()
                    )
                    logger.info(
                        "Market open: bar reload complete — %d total bars across %d symbols.",
                        loaded, len(_reload_tasks),
                    )
                else:
                    # No provider (mock/test mode) — manual VWAP reset only
                    for sym in state_manager.all_symbols():
                        state = state_manager.get_state(sym)
                        state.vwap = None
                        state.cumulative_tp_vol = 0.0
                        state.cumulative_vol = 0
                        state.session_volume = 0
                        state.session_high = None
                        state.session_low = None
                    logger.info("Market open: VWAP accumulators reset (no provider).")

            from app.services.market_session import should_flatten_positions
            from app.utils.time_utils import is_market_open, minutes_until_close

            if should_flatten_positions() and not eod_done_today:
                if settings.BOT_HOLD_OVERNIGHT:
                    # Overnight holds enabled — skip the EOD flatten and let
                    # stop/target and signal-reversal exits manage the position.
                    logger.info("EOD flatten skipped — BOT_HOLD_OVERNIGHT is enabled.")
                else:
                    prices = {
                        sym: state_manager.get_state(sym).last_price
                        for sym in state_manager.all_symbols()
                        if state_manager.get_state(sym).last_price
                    }
                    async with async_session_factory() as db:
                        from app.services.paper_trading_service import flatten_all_positions, get_open_positions
                        # Close positions at the broker before updating local DB
                        if state_manager.execution_mode == "live" and settings.LIVE_TRADING_ENABLED:
                            try:
                                from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
                                adapter = AlpacaLiveExecutionAdapter()
                                for pos in await get_open_positions(db):
                                    try:
                                        await adapter.close_position(pos.symbol)
                                    except Exception as exc:
                                        logger.error("Live EOD close failed for %s: %s", pos.symbol, exc)
                            except Exception as exc:
                                logger.error("Live EOD flatten (Alpaca) failed: %s", exc)
                        elif state_manager.execution_mode == "paper_alpaca" and settings.alpaca_credentials_present:
                            try:
                                from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                                adapter = AlpacaPaperExecutionAdapter()
                                for pos in await get_open_positions(db):
                                    try:
                                        await adapter.close_position(pos.symbol)
                                        logger.info("Alpaca paper EOD close submitted: %s", pos.symbol)
                                    except Exception as exc:
                                        logger.error("Alpaca paper EOD close failed for %s: %s", pos.symbol, exc)
                            except Exception as exc:
                                logger.error("Alpaca paper EOD flatten failed: %s", exc)
                        closed = await flatten_all_positions(db, prices)
                        if closed:
                            logger.info("EOD flatten: %d positions closed.", len(closed))
                eod_done_today = True

            if not is_market_open() and not summary_done_today and eod_done_today:
                async with async_session_factory() as db:
                    from app.services.session_summary_service import build_session_summary
                    await build_session_summary(db, state_manager)

                    # Close any open shadow trades at EOD prices
                    if settings.ENABLE_SHADOW_TRADES:
                        from app.services.shadow_trade_service import eod_close_shadows
                        prices = {
                            sym: state_manager.get_state(sym).last_price
                            for sym in state_manager.all_symbols()
                            if state_manager.get_state(sym).last_price
                        }
                        await eod_close_shadows(db, prices)

                    # Generate EOD diagnostic report
                    if settings.DIAGNOSTIC_REPORT_EOD:
                        try:
                            from app.services.diagnostic_report_service import generate_daily_report
                            report = await generate_daily_report(db, save_to_disk=True)
                            findings = report.get("frequency_diagnosis", {}).get("findings", [])
                            for finding in findings:
                                logger.info("DIAGNOSTIC: %s", finding)
                        except Exception as _rpt_exc:
                            logger.warning("Diagnostic report failed: %s", _rpt_exc)

                await _log_daily_summary(state_manager)
                summary_done_today = True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("EOD monitor error: %s", exc)


async def _update_position_price(symbol: str, price: float) -> None:
    """Update mark-to-market for open positions on every tick.
    Also updates the bot wallet when a stop or take-profit closes a position.
    Also updates open shadow trades for excursion tracking.
    """
    try:
        # Update shadow trade excursion tracking
        try:
            async with async_session_factory() as db:
                from app.services.shadow_trade_service import update_shadow_prices
                await update_shadow_prices(db, symbol, price)
        except Exception as _se:
            logger.debug("Shadow price update failed for %s: %s", symbol, _se)

        async with async_session_factory() as db:
            from app.services.paper_trading_service import update_position_prices
            trade = await update_position_prices(db, symbol, price)

        # If a stop/target closed the position, return capital + P&L to bot wallet
        if trade:
            # Record result for consecutive-loss tracking
            from app.execution.risk_manager import record_trade_result
            record_trade_result(bool(trade.is_winner))

            # Apply cooldown so the bot doesn't immediately re-enter the same symbol
            # after a stop or take-profit — mirrors the cooldown set on signal exits.
            from app.execution.risk_manager import set_symbol_cooldown
            set_symbol_cooldown(symbol)

            # In live mode the local stop/target check must also close the real Alpaca position
            if state_manager.execution_mode == "live" and settings.LIVE_TRADING_ENABLED:
                try:
                    from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
                    await AlpacaLiveExecutionAdapter().close_position(symbol)
                except Exception as exc:
                    logger.error("Live close (stop/target) failed for %s: %s", symbol, exc)
            position_cost = trade.entry_price * trade.qty
            state_manager.bot_cash += position_cost + trade.pnl
            state_manager.bot_realized_pnl += trade.pnl
            try:
                async with async_session_factory() as _sdb:
                    await bot_state_service.save_bot_state(
                        _sdb, state_manager.bot_cash, state_manager.bot_realized_pnl
                    )
            except Exception as _se:
                logger.warning("Could not persist bot state after stop/target: %s", _se)
            logger.info(
                "BOT STOP/TARGET HIT: %s PnL=$%.2f (%s) | wallet=$%.2f",
                symbol, trade.pnl, trade.close_reason, state_manager.bot_cash,
            )
            state_manager.broadcast_sse("bot_exit", {
                "symbol":   symbol,
                "exit_price": trade.exit_price,
                "pnl":      trade.pnl,
                "reason":   trade.close_reason,
                "bot_cash": state_manager.bot_cash,
            })
    except Exception:
        pass


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Stock Tracker — Day Trading Research Platform",
        description="Real-time signal engine and paper trading dashboard",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Static files
    static_path = Path(__file__).parent / "static"
    static_path.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Routers
    from app.web.api.router import api_router
    from app.web.router import router as page_router

    app.include_router(api_router)
    app.include_router(page_router)

    return app


app = create_app()
