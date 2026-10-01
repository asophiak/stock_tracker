# Developer notes

Orientation for working on the code: how it fits together, how to test changes,
and the known issues worth fixing. Setup is in `START_HERE.md`.

## Architecture

FastAPI app, single process, asyncio background tasks, SQLite via async SQLAlchemy.
Entry point `run.py` → `app/main.py` (lifespan starts every background task).

```
Alpaca websocket (1m bars) ──► StateManager (app/utils/cache.py, in-memory per-symbol state)
                                   │
           every 30 s  ┌───────────┴──────────────────────────────────────────┐
           _scoring_loop (app/main.py)                                        │
             ├─ run_scoring_cycle  → swing engine  (app/signal_engine/engine.py)
             │                     → scalp engine  (app/signal_engine/scalp_engine.py)
             ├─ _maybe_auto_execute   swing bot   (tiers A/B/shadow, ML filter in shadow)
             ├─ _maybe_scalp_execute  scalp bot   (ML filter in shadow)
             └─ _maybe_nn_execute     autonomous neural-net bot (app/ml)
                                   │
           orders ──► app/services/paper_trading_service.py (+ Alpaca paper adapter)
           ticks  ──► _update_position_price → stop / target exits for every bot
```

Other background tasks in `app/main.py`: `_eod_monitor` (flatten before close,
session summaries), whale/13F refresh, macro data, `_ml_retrain_scheduler`
(nightly at `ML_RETRAIN_TIME`).

| Area | Where |
|---|---|
| Settings (all via `.env`) | `app/config.py` |
| Signal scoring | `app/signal_engine/` |
| Bots / entry & exit logic | `app/main.py` (`_maybe_*_execute`) |
| Risk checks | `app/execution/risk_manager.py` |
| Post-trade grading & lessons | `app/services/learning_service.py` |
| ML: features, GRU+MLP, live scoring, explanations | `app/ml/` |
| ML: data download, training, walk-forward, retrain | `scripts/ml_*.py` |
| JSON API | `app/web/api/` (`ml.py` = `/api/ml/*`) |
| Pages (Jinja) | `app/templates/` + `app/web/router.py` |
| Front end | `app/static/js/` — `ui.js` (formatting + icons), `fx3d.js` (shared 3D), `galaxy.js`, `nn3d.js`, `ml.js`, `dashboard.js` |
| Styles | `app/static/css/main.css` (original) + `theme.css` (current theme, overrides only) |

Bots are told apart by `strategy_version` on positions/trades: `A` / `B` / `4b`
(swing), `scalp`, `nn` (neural net), `alpaca_import` (synced from the broker).

## The ML side

- **Autonomous bot model** (`models/ml/auto_trader_nn.pt`): GRU over the last 60
  one-minute bars + MLP over 18 features → P(long profitable), P(short profitable).
  Labels: triple barrier (stop 1.5×ATR, target 2× stop, 60-min horizon), after costs.
- **Trade filter** (`trade_filter_nn.pt` / `trade_filter.joblib`): scores the rule
  bots' candidates; runs in **shadow mode** (logs only) — `ML_FILTER_MODE`.
- **Explanations** (`/api/ml/explain`): occlusion attribution — each input is
  neutralised and the change in output measured. Drives the clickable 3D view on `/ml`.
- **Evaluation**: `scripts/ml_walkforward.py` — monthly expanding-window folds on
  two years of SIP data, 3 seeds, GBM baseline, 3 bp/side costs.
  Result (`models/ml/walkforward.json`): AUC ≈ 0.524, edge does not survive costs.
- **Live/offline parity** is enforced by tests (`tests/test_ml.py`): the live
  predictor must build exactly the same inputs as the training pipeline.

## Testing changes

```bash
python -m pytest tests/            # 61 tests, < 1 s
```

The server does **not** auto-reload — restart `python run.py` after code changes.
Static files are cache-busted only on restart (`?v=` is the server start time).
Outside market hours the dashboard scores once from the last session's bars,
so pages have data in the evening; trading logic only runs while the market is open.

## Known issues / good things to fix

1. **Log file never rotates.** The launchd job appends stdout to
   `logs/stock-tracker.log` (it reached 4.2 GB). Log lines also only carry
   `HH:MM:SS`, no date. → `RotatingFileHandler` in `app/logging_config.py` and a
   date in the format.
2. **Startup history burst hits Alpaca's rate limit** ("too many requests" for
   several symbols in `logs/stock-tracker.recent.log`) — `_load_history` fetches every
   symbol at once; those symbols start without intraday history. → throttle / batch
   (one multi-symbol request), retry with backoff.
3. **One websocket per API key.** Two running copies on the same key knock each
   other off ("connection limit exceeded"). Any second process with the key will
   cause it — use separate keys per machine.
4. **No authentication.** Anyone who can reach the port can trade (paper), change
   settings, flip the kill switch. Fine on localhost; needs auth (or a read-only
   mode) before exposing it on a network.
5. **NAAIM / AAII sentiment panels have no data source loaded** (the panels hide
   themselves). `app/providers/macro/sentiment.py` expects a table that's never filled.
6. **The trading strategies lose money after costs.** Scalp candidates average about
   −0.1R per trade once spread/slippage are counted; tight 0.6% stops make costs a
   large fraction of risk. Most promising directions: longer horizons with wider
   stops, cross-sectional ranking (long top / short bottom), market-context features
   (SPY/QQQ momentum, overnight gap, prior-day levels). The walk-forward script
   tests any of these properly.
7. **Settings, Whales and Diagnostics pages** only got the theme, not a layout
   redesign like the dashboard and `/ml`.
8. **3D views need internet** (Three.js from cdnjs) and WebGL; without them those
   sections hide themselves.

## Data that isn't in the repo

`.env`, the database, logs and price history are git-ignored (secrets / too large).

- `stock_tracker.db` — shared separately as a trimmed copy: every trade, session summary,
  journal entry and the setups the ML trains on; the last two weeks of everything else.
  Without it the app creates an empty database on first run.
- Recent logs (`stock-tracker.recent.log`, ML run logs) — shared separately.
- Price history (2+ GB) — rebuild with `scripts/ml_fetch_bars.py` and
  `scripts/ml_fetch_history.py`.
