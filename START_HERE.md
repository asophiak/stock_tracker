# Stock Tracker — getting it running

A day-trading research platform: two rule-based paper-trading bots, an autonomous
neural-net bot (GRU + MLP, PyTorch), nightly self-retraining, a walk-forward
evaluator, and a 3D dashboard. Everything trades **paper money only**.

## 1. Install (Mac, needs Python 3.10+)

```bash
cd stock-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt      # includes PyTorch — takes a few minutes
```

## 2. Configure

`.env` (API keys and settings) is **not in the repo** — it's shared separately. Put it in
the project folder. Without it, `cp .env.example .env` gives a working mock-data mode.

**Important:** Alpaca's free plan allows only **one live data connection per key**.
If this copy and the original run at the same time with the same key, they keep
disconnecting each other ("connection limit exceeded" in the log) and one loses
live prices. Either:
- get your own free paper-trading key at https://alpaca.markets (2 min) and put it
  in `ALPACA_API_KEY` / `ALPACA_API_SECRET` in `.env`, **or**
- coordinate so only one copy runs at a time.

Also note these keys control a shared paper account — the bots here will place
paper trades in it.

## 3. Run

```bash
source venv/bin/activate
python run.py
```

Open **http://127.0.0.1:8000** on the same computer.

## 4. Open it on your phone (Safari)

The server has to run on a computer; the phone just views it.

1. In `.env` change `APP_HOST=127.0.0.1` to `APP_HOST=0.0.0.0` and restart `python run.py`.
2. Find the computer's local IP: `ipconfig getifaddr en0` (prints something like `192.168.1.23`).
3. With the phone on the **same Wi-Fi**, open `http://192.168.1.23:8000` in Safari.
4. Optional: Share → **Add to Home Screen** to open it like an app.

There's no login, so only do this on a network you trust.

## 5. Where to look

| Page | What's there |
|---|---|
| `/` | Market Galaxy (3D map of every stock), stock cards, bot wallet, signals |
| `/ml` | Interactive 3D neural network — click any layer for live data and per-input attribution; walk-forward results after costs |
| `/journal` | Every trade with the bot's post-mortem |
| `/diagnostics` | Strategy edge analysis |

## 6. The ML side

Code: `app/ml/` (features, GRU+MLP model, live predictor) and `scripts/ml_*.py`.
Trained models are included in `models/ml/`, so it works immediately.

Price history isn't included (2+ GB). To rebuild it and retrain:

```bash
python scripts/ml_fetch_bars.py          # bot's own recorded sessions (IEX)
python scripts/ml_fetch_history.py       # 2 years of full-market 1-min bars (SIP), ~10 min
python scripts/ml_walkforward.py         # monthly walk-forward test, after trading costs
python scripts/ml_retrain.py --force     # champion/challenger retrain of every model
```

Honest status: across 15 monthly walk-forward folds the models show small but real
skill (AUC ≈ 0.524) that does **not** survive trading costs yet. Ideas worth trying are
in the walk-forward section of `/ml`: longer horizons with wider stops, cross-sectional
ranking, and market-context features (SPY/QQQ, gaps, prior-day levels).

Tests: `python -m pytest tests/`
