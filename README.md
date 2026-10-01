# Stock Tracker — Day Trading Research & Signal Platform

A desktop-local Python application that watches a small universe of stocks,
scores intraday trade setups continuously, and surfaces the single best
day-trade opportunity per session with full thesis, risk notes, and optional
paper-trade execution.

---

## Features

| Area | Details |
|---|---|
| **Signal Engine** | 6-component weighted scoring (0–100): trend, candlestick, volume/RVOL, VWAP/OR, market regime, news |
| **Signal States** | NEUTRAL · GREEN · RED · FLASH GREEN · FLASH RED · WATCH · POSSIBLE TRADE · IMMEDIATE TRADE |
| **Candlestick Patterns** | 11 patterns with context gating (level, volume, momentum, regime) |
| **VWAP & Opening Range** | Intraday VWAP, first-5 / first-15 minute opening range, S/R from swing points |
| **Market Regime** | SPY + QQQ used as regime filter; counter-trend signals hard-capped |
| **News Intelligence** | Rules-based keyword sentiment; headline risk hard-penalty; LLM summarisation optional |
| **Alerting** | Dashboard SSE · Console · Telegram · Discord with cooldown deduplication |
| **Paper Trading** | Local simulation (fills at market) with mark-to-market, stop/target checks, EOD flatten |
| **Alpaca Adapter** | Paper + live order routing, real-time bar streaming, news streaming |
| **Mock Mode** | Fully synthetic data mode — works with zero credentials |
| **Risk Controls** | Max trades/day · Daily loss cap · Symbol cooldown · Kill switch · Live-trade hard gate |
| **Dashboard** | 5-page dark-theme web UI: Dashboard · Symbol Detail · Journal · Settings · Session Summary |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- `pip` / `venv`

### 2. Clone and install

```bash
git clone <repo>
cd stock-tracker

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — add Alpaca credentials to enable live data
# Without credentials the app runs in mock (synthetic) mode automatically
```

### 4. Run

```bash
python run.py
# or
bash scripts/start.sh
```

Open **http://127.0.0.1:8000/** in your browser.

---

## Configuration

All settings are in `.env`. Key variables:

| Variable | Default | Description |
|---|---|---|
| `WATCHLIST` | `SPY,QQQ,NVDA,TSLA` | Comma-separated symbols to monitor |
| `EXECUTION_MODE` | `paper_local` | `disabled` \| `paper_local` \| `paper_alpaca` |
| `ALPACA_API_KEY` | *(blank)* | Alpaca key — blank = mock mode |
| `ALPACA_DATA_FEED` | `iex` | `iex` (free) or `sip` (paid) |
| `SIGNAL_SCORE_FLASH_THRESHOLD` | `85` | Score for IMMEDIATE TRADE |
| `SIGNAL_SCORE_TRADE_THRESHOLD` | `70` | Score for POSSIBLE TRADE |
| `MAX_TRADES_PER_DAY` | `1` | Max paper trades per session |
| `DAILY_LOSS_LIMIT` | `500` | USD hard stop for the day |
| `TELEGRAM_BOT_TOKEN` | *(blank)* | For Telegram alerts |
| `DISCORD_WEBHOOK_URL` | *(blank)* | For Discord alerts |

### Scoring weights (must total 100)

```
WEIGHT_TECHNICAL_TREND=25
WEIGHT_CANDLESTICK=20
WEIGHT_VOLUME=15
WEIGHT_VWAP=15
WEIGHT_MARKET_REGIME=10
WEIGHT_NEWS=15
```

---

## Project Structure

```
stock-tracker/
├── run.py                       # Entry point
├── app/
│   ├── main.py                  # FastAPI app + startup lifecycle
│   ├── config.py                # All settings (Pydantic)
│   ├── db/                      # SQLAlchemy engine + session + init
│   ├── models/                  # ORM models
│   ├── schemas/                 # Internal dataclasses (Bar, SignalScore, etc.)
│   ├── signal_engine/           # Modular scoring engine
│   │   ├── indicators.py        # VWAP, ATR, RVOL, momentum, S/R
│   │   ├── candlestick.py       # 11 pattern detectors
│   │   ├── technical.py         # Trend quality scorer
│   │   ├── volume.py            # RVOL / liquidity scorer
│   │   ├── vwap_scorer.py       # VWAP / opening range scorer
│   │   ├── market_regime.py     # SPY/QQQ regime scorer
│   │   ├── news_scorer.py       # News alignment scorer
│   │   ├── thesis.py            # Trade thesis builder
│   │   └── engine.py            # Orchestrator
│   ├── providers/
│   │   ├── base.py              # Abstract provider interfaces
│   │   ├── alpaca/              # Alpaca market data, news, trading
│   │   └── mock/                # Synthetic data (no credentials needed)
│   ├── services/                # Business logic services
│   ├── execution/               # Order adapters + risk manager
│   ├── alerts/                  # Console, Telegram, Discord dispatchers
│   ├── web/
│   │   ├── router.py            # Page routes + SSE endpoint
│   │   └── api/                 # REST API endpoints
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS + JavaScript
├── tests/                       # pytest test suite
├── scripts/                     # start.sh, reset_db.sh
└── .env.example
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Main dashboard |
| GET | `/symbol/{sym}` | Symbol detail page |
| GET | `/journal` | Trade journal |
| GET | `/settings` | Settings page |
| GET | `/session-summary` | Daily session summary |
| GET | `/health` | Health check |
| GET | `/api/status` | App + provider status |
| GET | `/api/watchlist` | List watchlist |
| POST | `/api/watchlist` | Add symbols |
| DELETE | `/api/watchlist/{sym}` | Remove symbol |
| GET | `/api/symbol/{sym}` | Symbol market data + signal + news |
| GET | `/api/signals` | Latest signal for all symbols |
| GET | `/api/best-trade` | Best trade of day |
| GET | `/api/alerts` | Recent alert history |
| POST | `/api/paper/order` | Place paper order |
| POST | `/api/paper/close` | Close paper position |
| GET | `/api/paper/positions` | Open positions |
| GET | `/api/paper/trades` | Closed trade history |
| POST | `/api/kill-switch` | Engage / disengage kill switch |
| GET | `/api/settings` | Current settings |
| POST | `/api/settings` | Update runtime settings |
| GET | `/api/session-summary/today` | Today's session summary |
| GET | `/api/events` | SSE stream for real-time dashboard updates |

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Safety Notes

- **Live trading is disabled by default.** `EXECUTION_MODE=paper_local` simulates orders locally.
- To use Alpaca paper API: set `EXECUTION_MODE=paper_alpaca` and provide credentials.
- Live trading requires `LIVE_TRADING_ENABLED=true` in `.env` — this is intentionally a separate flag.
- The kill switch (`/api/kill-switch`) immediately halts all new order attempts and signal actions.
- Paper positions are automatically flattened `EOD_FLATTEN_MINUTES` (default 10) before market close.

---

## Mock Mode

If `ALPACA_API_KEY` is blank, the app automatically starts in **mock mode**:
- Synthetic price bars are generated using Brownian motion
- News cache starts empty (no real headlines)
- All signal engine components, paper trading, UI, and alerts work normally
- Useful for UI development and testing without a live data subscription
