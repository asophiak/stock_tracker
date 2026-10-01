"""
Thread-safe in-memory state store for the entire application.
Holds per-symbol SymbolState and application-level runtime state.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.schemas.market_data import Bar, SymbolState
from app.schemas.signals import SignalScore


class StateManager:
    """
    Central in-memory state store.

    All writes are protected by a per-symbol lock to prevent race conditions
    between the streaming ingestion task and the scoring background task.
    """

    def __init__(self) -> None:
        self._states: Dict[str, SymbolState] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

        # Application-level runtime flags
        self.kill_switch: bool = settings.KILL_SWITCH_DEFAULT
        self.execution_mode: str = settings.EXECUTION_MODE
        self.auto_paper_execution: bool = settings.AUTO_PAPER_EXECUTION
        self.paper_capital: float = settings.DEFAULT_CAPITAL
        self.max_trades_per_day: int = settings.MAX_TRADES_PER_DAY

        # Recorded at process start — used by /api/health
        self.startup_time: datetime = datetime.now(timezone.utc)

        # Bot's own $100 paper wallet (separate from manual trading capital)
        self.bot_starting_capital: float = settings.BOT_PAPER_CAPITAL
        self.bot_cash: float = settings.BOT_PAPER_CAPITAL   # available cash (updates per trade)
        self.bot_realized_pnl: float = 0.0                  # cumulative closed-trade P&L
        self.provider_connected: bool = False
        self.provider_name: str = "none"
        self.stream_connected: bool = False
        self.startup_complete: bool = False
        self.last_score_time: Optional[datetime] = None

        # Best trade of day (updated by signal service)
        self.best_trade_of_day: Optional[SignalScore] = None
        self.session_date: str = ""

        # Phase 4B/4C — entry guard: count and log thesis rejections
        self.thesis_rejections: int = 0
        self.thesis_rejection_log: list = []       # rolling last-20 entries
        self.thesis_rejection_by_symbol: dict = {} # symbol  → cumulative count
        self.thesis_rejection_by_bucket: dict = {} # score bucket → cumulative count

        # SSE subscribers (queue per client)
        self._sse_queues: List = []
        self._sse_lock = threading.Lock()

    # ── Symbol state management ───────────────────────────────────────────────

    def _get_lock(self, symbol: str) -> threading.Lock:
        with self._global_lock:
            if symbol not in self._locks:
                self._locks[symbol] = threading.Lock()
            return self._locks[symbol]

    def get_state(self, symbol: str) -> SymbolState:
        if symbol not in self._states:
            with self._get_lock(symbol):
                if symbol not in self._states:
                    self._states[symbol] = SymbolState(symbol=symbol)
        return self._states[symbol]

    def all_symbols(self) -> List[str]:
        return list(self._states.keys())

    def init_symbols(self, symbols: List[str]) -> None:
        for sym in symbols:
            self.get_state(sym)  # ensures entry exists

    # ── Bar ingestion ─────────────────────────────────────────────────────────

    def add_bar(self, symbol: str, bar: Bar) -> None:
        """Add a new bar and maintain rolling windows."""
        state = self.get_state(symbol)
        with self._get_lock(symbol):
            if bar.timeframe == "1Min":
                state.bars_1m.append(bar)
                if len(state.bars_1m) > settings.MAX_BARS_1M:
                    state.bars_1m = state.bars_1m[-settings.MAX_BARS_1M :]
                state.last_price = bar.close
                state.last_updated = bar.timestamp
                state.session_volume += bar.volume

                # Update session high/low
                if state.session_high is None or bar.high > state.session_high:
                    state.session_high = bar.high
                if state.session_low is None or bar.low < state.session_low:
                    state.session_low = bar.low

                # VWAP incremental update
                typical_price = (bar.high + bar.low + bar.close) / 3.0
                state.cumulative_tp_vol += typical_price * bar.volume
                state.cumulative_vol += bar.volume
                if state.cumulative_vol > 0:
                    state.vwap = state.cumulative_tp_vol / state.cumulative_vol

            elif bar.timeframe == "5Min":
                state.bars_5m.append(bar)
                if len(state.bars_5m) > settings.MAX_BARS_5M:
                    state.bars_5m = state.bars_5m[-settings.MAX_BARS_5M :]

            elif bar.timeframe == "15Min":
                state.bars_15m.append(bar)
                if len(state.bars_15m) > settings.MAX_BARS_15M:
                    state.bars_15m = state.bars_15m[-settings.MAX_BARS_15M :]

    def reset_session(self, symbol: str) -> None:
        """Reset intraday accumulators at market open."""
        state = self.get_state(symbol)
        with self._get_lock(symbol):
            state.bars_1m.clear()
            state.bars_5m.clear()
            state.bars_15m.clear()
            state.vwap = None
            state.cumulative_tp_vol = 0.0
            state.cumulative_vol = 0
            state.session_volume = 0
            state.session_high = None
            state.session_low = None
            state.opening_range_high = None
            state.opening_range_low = None
            state.opening_range_set = False
            state.last_price = None
            state.last_updated = None

    # ── SSE broadcast ─────────────────────────────────────────────────────────

    def register_sse_queue(self, q: "asyncio.Queue") -> None:  # type: ignore[name-defined]
        with self._sse_lock:
            self._sse_queues.append(q)

    def unregister_sse_queue(self, q: "asyncio.Queue") -> None:  # type: ignore[name-defined]
        with self._sse_lock:
            self._sse_queues = [x for x in self._sse_queues if x is not q]

    def broadcast_sse(self, event_type: str, data: dict) -> None:
        import json
        payload = json.dumps({"type": event_type, "data": data})
        with self._sse_lock:
            for q in list(self._sse_queues):
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass

    @staticmethod
    def _score_bucket(score: float) -> str:
        """Map a raw score to its display bucket label."""
        if score < 60:  return "<60"
        if score < 65:  return "60-64"
        if score < 70:  return "65-69"
        if score < 75:  return "70-74"
        if score < 80:  return "75-79"
        if score < 85:  return "80-84"
        return "85+"

    def log_thesis_rejection(self, symbol: str, score: float, reason: str) -> None:
        """
        Append a thesis-rejection event to the rolling log and update
        per-symbol and per-score-bucket counters for Phase 4C monitoring.
        """
        bucket = self._score_bucket(score)
        self.thesis_rejection_log.append({
            "symbol": symbol,
            "score":  round(score, 1),
            "bucket": bucket,
            "reason": reason,
            "ts":     datetime.now(timezone.utc).isoformat(),
        })
        if len(self.thesis_rejection_log) > 20:
            self.thesis_rejection_log = self.thesis_rejection_log[-20:]
        self.thesis_rejection_by_symbol[symbol] = (
            self.thesis_rejection_by_symbol.get(symbol, 0) + 1
        )
        self.thesis_rejection_by_bucket[bucket] = (
            self.thesis_rejection_by_bucket.get(bucket, 0) + 1
        )
