"""
Application configuration via Pydantic settings.
All values can be overridden via environment variables or .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    SECRET_KEY: str = "change-me-in-production-please"
    LOG_LEVEL: str = "INFO"

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./stock_tracker.db"

    # ── Watchlist ─────────────────────────────────────────────────────────────
    # Comma-separated list of symbols. SPY and QQQ are always added for regime.
    # SPY/QQQ = regime filter only (never traded)
    # High-beta large-caps: NVDA, TSLA, META, AAPL, MSFT, AMZN, GOOGL
    # Mid-range / momentum: PLTR, HOOD, SOFI, AMD, COIN, NFLX
    # Macro-sensitive: GS, JPM, BAC
    # Defence/industrial: GE, LMT
    WATCHLIST: str = (
        "SPY,QQQ,"
        # Mega-cap tech
        "NVDA,TSLA,META,AAPL,MSFT,AMZN,GOOGL,NFLX,"
        # High-beta / momentum
        "PLTR,HOOD,SOFI,AMD,COIN,MSTR,RKLB,"
        # Semiconductors
        "AVGO,MU,INTC,QCOM,"
        # Financials
        "GS,JPM,BAC,V,MA,"
        # Industrials / Defense
        "GE,LMT,RTX,CAT,"
        # Healthcare
        "UNH,LLY,"
        # Energy
        "XOM,CVX,"
        # Consumer / Retail
        "COST,NKE,WMT"
    )

    @property
    def watchlist_symbols(self) -> List[str]:
        raw = [s.strip().upper() for s in self.WATCHLIST.split(",") if s.strip()]
        # Ensure SPY/QQQ present for market-regime filter
        for regime_sym in ("SPY", "QQQ"):
            if regime_sym not in raw:
                raw.insert(0, regime_sym)
        return raw

    @property
    def tradeable_symbols(self) -> List[str]:
        """Symbols excluding pure regime-filter instruments."""
        return [s for s in self.watchlist_symbols if s not in ("SPY", "QQQ")]

    # ── Signal thresholds ─────────────────────────────────────────────────────
    SIGNAL_SCORE_FLASH_THRESHOLD: int = 85   # FLASH GREEN / FLASH RED → IMMEDIATE TRADE
    SIGNAL_SCORE_TRADE_THRESHOLD: int = 70   # GREEN / RED → POSSIBLE TRADE
    SIGNAL_SCORE_WATCH_THRESHOLD: int = 55   # directional bias → WATCH

    # ── Trade tiers ───────────────────────────────────────────────────────────
    # A-trade: highest confidence → normal size live execution
    # B-trade: medium confidence → reduced size live execution (if enabled)
    # Shadow: near-tradeable → logged + tracked hypothetically, never executed
    #
    # All three thresholds are intentionally separate from the signal label
    # thresholds above so they can be tuned independently.
    #
    # How to read these:
    #   score >= A_TRADE_MIN_SCORE  → A-trade (full size, live)
    #   score >= B_TRADE_MIN_SCORE  → B-trade (reduced size, live if ENABLE_B_TRADES)
    #   score >= SHADOW_TRADE_MIN_SCORE → shadow (hypothetical tracking only)
    #   score <  SHADOW_TRADE_MIN_SCORE → rejected (only counted in diagnostics)
    A_TRADE_MIN_SCORE: int = 82           # was BOT_ENTRY_SCORE; kept separate so both coexist
    B_TRADE_MIN_SCORE: int = 72           # qualifies for live execution at smaller size
    SHADOW_TRADE_MIN_SCORE: int = 62      # tracked but NOT executed

    # B-trade position size as a fraction of A-trade size (0.5 = 50% of normal)
    B_TRADE_SIZE_FRACTION: float = 0.40   # 40% of normal risk for B-trades

    # ── Safety / feature flags ────────────────────────────────────────────────
    # Default: safe / analysis mode. Flip each flag explicitly to enable.
    ENABLE_B_TRADES: bool = False         # allow live execution of B-trades
    ENABLE_SCALP_BOT: bool = True         # SCALP_MODE_ENABLED alias (new canonical flag)
    ENABLE_SHADOW_TRADES: bool = True     # log shadow setups for analysis
    ENABLE_DYNAMIC_WATCHLIST: bool = True # append daily movers to fixed watchlist
    # LIVE_TRADING_ENABLED already exists below; surfaced here for clarity in docs

    # ── Scoring weights ────────────────────────────────────────────────────────
    # Engine normalises by sum(weights), so adding WEIGHT_WHALE doesn't break
    # the 0-100 output scale — it just adds a new competitive component.
    WEIGHT_TECHNICAL_TREND: int = 25
    WEIGHT_CANDLESTICK: int = 20
    WEIGHT_VOLUME: int = 15
    WEIGHT_VWAP: int = 15
    WEIGHT_MARKET_REGIME: int = 10
    WEIGHT_NEWS: int = 15
    WEIGHT_WHALE: int = 10   # institutional 13F holdings + live options flow

    # ── Risk management ───────────────────────────────────────────────────────
    MAX_TRADES_PER_DAY: int = 0            # 0 = unlimited
    DAILY_LOSS_LIMIT: float = 500.0        # USD — bot pauses after hitting this
    PER_TRADE_RISK_PCT: float = 1.0        # % of account equity risked per A-trade
    DEFAULT_CAPITAL: float = 10_000.0      # manual paper capital USD
    SYMBOL_COOLDOWN_MINUTES: int = 15      # after stop-out, ignore symbol
    EOD_FLATTEN_MINUTES: int = 10          # flatten N mins before close

    # ── Extended risk rules ───────────────────────────────────────────────────
    # These apply to BOTH swing bot and scalp bot through the central risk manager.
    MAX_CONSECUTIVE_LOSSES: int = 3        # pause bot after N losses in a row
    MAX_OPEN_POSITIONS: int = 4            # absolute cap across both bots
    MAX_SWING_POSITIONS: int = 2           # swing bot cap (unchanged)
    MAX_SCALP_POSITIONS: int = 2           # scalp bot concurrent cap
    MAX_EXPOSURE_PER_SYMBOL_PCT: float = 20.0   # max % of equity in one symbol
    MAX_TOTAL_EXPOSURE_PCT: float = 80.0   # max % of equity deployed at once
    SPREAD_KILL_THRESHOLD_PCT: float = 0.15     # % spread above which no new entries
    CONSECUTIVE_LOSS_PAUSE_MINUTES: int = 30    # how long to pause after N losses

    # ── Risk-based position sizing ────────────────────────────────────────────
    # Position size = (equity * PER_TRADE_RISK_PCT/100) / stop_distance_per_share
    # This replaces the old allocation-% model for A-trades and B-trades.
    # Scalp trades still use SCALP_ALLOCATION_PCT (fast fills, tight stops).
    USE_RISK_BASED_SIZING: bool = True     # False = revert to old allocation-% model
    MAX_POSITION_SIZE_PCT: float = 40.0    # hard cap: no position > X% of equity

    # ── Bot auto-trader ───────────────────────────────────────────────────────
    # The bot has its own paper wallet, separate from manual trading capital.
    BOT_PAPER_CAPITAL: float = 10_000.0    # bot starting wallet ($)
    # Base % of capital to allocate per trade — used only when USE_RISK_BASED_SIZING=False.
    # When risk-based sizing is on, size = (equity * PER_TRADE_RISK_PCT) / stop_distance.
    BOT_ALLOCATION_PCT: float = 40.0
    # When True, bot positions are NOT force-closed at EOD — they carry overnight
    BOT_HOLD_OVERNIGHT: bool = False
    # Minimum signal score to trigger bot entry — kept for legacy; A_TRADE_MIN_SCORE is canonical.
    BOT_ENTRY_SCORE: int = 82

    # ── Execution ─────────────────────────────────────────────────────────────
    # Options: disabled | paper_local | paper_alpaca | live
    EXECUTION_MODE: str = "paper_local"
    AUTO_PAPER_EXECUTION: bool = True      # bot trades automatically by default
    KILL_SWITCH_DEFAULT: bool = False      # start with kill-switch engaged?
    LIVE_TRADING_ENABLED: bool = False     # hard gate for live mode

    # ── Session / Market hours ────────────────────────────────────────────────
    SESSION_TIMEZONE: str = "America/New_York"
    MARKET_OPEN_TIME: str = "09:30"
    MARKET_CLOSE_TIME: str = "16:00"
    PREMARKET_START_TIME: str = "04:00"
    AFTERHOURS_END_TIME: str = "20:00"
    PREMARKET_ENABLED: bool = True
    OPENING_RANGE_MINUTES: int = 15        # first-N-minute opening range

    # ── Alpaca ────────────────────────────────────────────────────────────────
    ALPACA_API_KEY: str = ""
    ALPACA_API_SECRET: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    ALPACA_DATA_FEED: str = "iex"          # iex (free) or sip (paid)
    ALPACA_DATA_URL: str = "https://data.alpaca.markets"

    # ── Alerts ────────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    DISCORD_WEBHOOK_URL: str = ""
    ALERT_COOLDOWN_SECONDS: int = 300      # per-symbol alert dedupe window

    # ── News / LLM (optional enrichment) ─────────────────────────────────────
    USE_LLM_NEWS_SUMMARY: bool = False
    LLM_PROVIDER: str = "anthropic"
    LLM_API_KEY: str = ""
    NEWS_MAX_AGE_HOURS: int = 8            # ignore news older than this

    # ── Bar history ───────────────────────────────────────────────────────────
    BAR_HISTORY_DAYS: int = 5             # days of 1m bars to load at startup
    MAX_BARS_1M: int = 390               # one full session of 1m bars
    MAX_BARS_5M: int = 150
    MAX_BARS_15M: int = 60

    # ── Dynamic watchlist scanner ─────────────────────────────────────────────
    # How many movers to add from each source (0 = disabled for that source).
    DYNAMIC_WATCHLIST_TOP_GAINERS: int = 5
    DYNAMIC_WATCHLIST_TOP_LOSERS: int = 5
    DYNAMIC_WATCHLIST_HIGH_RVOL: int = 10    # high relative-volume stocks
    DYNAMIC_WATCHLIST_REFRESH_MINUTES: int = 30   # how often to re-scan
    # Liquidity filters for dynamically added symbols
    DYNAMIC_MIN_PRICE: float = 5.0           # ignore penny stocks
    DYNAMIC_MIN_VOLUME: int = 500_000        # min daily volume
    DYNAMIC_MAX_SPREAD_PCT: float = 0.20     # reject wide-spread stocks

    # ── Diagnostic / reporting ────────────────────────────────────────────────
    DIAGNOSTIC_REPORT_EOD: bool = True       # generate report at end of each session
    DIAGNOSTIC_REPORT_WEEKLY: bool = True    # generate report at end of each week

    # ── Scalp engine (parallel, 1m bars only) ────────────────────────────────
    SCALP_MODE_ENABLED: bool = True
    SCALP_ENTRY_SCORE: int = 55            # bot auto-entry threshold
    SCALP_FLASH_THRESHOLD: int = 75        # FLASH label
    SCALP_TRADE_THRESHOLD: int = 62        # POSSIBLE_TRADE label
    SCALP_WATCH_THRESHOLD: int = 50        # WATCH label (below = NO_TRADE)
    SCALP_COOLDOWN_MINUTES: int = 2        # per-symbol cooldown after scalp exit
    SCALP_ALLOCATION_PCT: float = 15.0    # % of bot_cash per scalp trade (smaller = more trades)

    # ── ML trade filter (app/ml, trained by scripts/ml_train.py) ─────────────
    # off    — model not consulted
    # shadow — every candidate is scored and logged, entries are unchanged
    # gate   — candidates below ML_MIN_PROB are skipped
    ML_FILTER_MODE: str = "shadow"
    ML_MODEL: str = "nn"                   # nn (GRU + MLP, trade_filter_nn.pt) | gbm (trade_filter.joblib)
    ML_MIN_PROB: Optional[float] = None    # None → threshold chosen at training time

    # Autonomous neural-net bot (paper only — never routed to a live broker)
    ML_NN_BOT_ENABLED: bool = True
    ML_NN_ALLOCATION_PCT: float = 10.0     # % of bot_cash per NN trade (capped at $25k like scalp)
    ML_NN_MAX_POSITIONS: int = 2
    ML_NN_MAX_HOLD_MINUTES: int = 60       # matches the training label horizon
    ML_NN_COOLDOWN_MINUTES: int = 10       # per-symbol gap between NN entries

    # Nightly self-retraining (scripts/ml_retrain.py) — runs once per weekday after close
    ML_RETRAIN_ENABLED: bool = True
    ML_RETRAIN_TIME: str = "16:45"         # ET

    # ── Scoring interval ─────────────────────────────────────────────────────
    SCORE_INTERVAL_SECONDS: int = 30       # re-score watchlist every N seconds

    @property
    def alpaca_credentials_present(self) -> bool:
        return bool(self.ALPACA_API_KEY and self.ALPACA_API_SECRET)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
