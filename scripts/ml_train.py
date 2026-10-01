#!/usr/bin/env python3
"""
Build the ML trade-filter dataset and train the model.

Usage (from project root, after scripts/ml_fetch_bars.py):
    venv/bin/python scripts/ml_train.py [--rebuild] [--test-frac 0.25]

Dataset (cached to data/ml/dataset.pkl; --rebuild regenerates it):
  • scalp candidates — the scalp engine is replayed minute-by-minute over the
    cached 1m bars, applying the same entry filters the live scalp bot uses
    (score ≥ SCALP_ENTRY_SCORE, price ≥ $15, VWAP-aligned, thesis present).
  • swing candidates — rows from rejected_setups with score ≥ SHADOW_TRADE_MIN_SCORE
    and a stop/target, de-duplicated to one per symbol per minute.
  Every candidate is labelled with the triple-barrier outcome of its own stop
  and target over the next 60 minutes (timeouts exit at the last close). The
  model is trained to predict "R > 0 at exit".

Training:
  Sessions are split chronologically — the model never sees the test period.
  The entry threshold is chosen on a validation slice taken from the end of
  the training period, then reported on the untouched test sessions.
  The final model is refit on all sessions and saved to models/ml/trade_filter.joblib.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.config import settings
from app.ml.features import FEATURE_NAMES, build_features
from app.ml.labeling import triple_barrier

DB_PATH = ROOT / "stock_tracker.db"
BARS_DIR = ROOT / "data" / "ml" / "bars"
DATASET = ROOT / "data" / "ml" / "dataset.pkl"
MODEL_DIR = ROOT / "models" / "ml"
ET = ZoneInfo("America/New_York")
SCALP_MIN_PRICE = 15.0   # mirrors _SCALP_MIN_PRICE in app/main.py


# ── Per-session dataset construction ──────────────────────────────────────────

def _symbol_arrays(df: pd.DataFrame):
    """Yield (symbol, timestamps_utc, o, h, l, c, v, vwap, sess_hi, sess_lo, mins_open)."""
    for sym, g in df.groupby(level="symbol"):
        g = g.droplevel("symbol").sort_index()
        if len(g) < 30:
            continue
        ts = g.index.tz_convert("UTC")
        o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        v = g["volume"].to_numpy(float)
        tp = (h + l + c) / 3.0
        cumv = np.cumsum(v)
        vwap = np.where(cumv > 0, np.cumsum(tp * v) / np.maximum(cumv, 1), np.nan)
        et = ts.tz_convert(ET)
        mins_open = np.asarray((et.hour - 9) * 60 + et.minute - 30 + 1, dtype=float)  # bar completes 1m after its stamp
        yield sym, ts, o, h, l, c, v, vwap, np.maximum.accumulate(h), np.minimum.accumulate(l), mins_open


def _scalp_rows(session: str, df: pd.DataFrame) -> list[dict]:
    from app.schemas.market_data import Bar, SymbolState
    from app.signal_engine.scalp_engine import score_scalp

    rows = []
    for sym, ts, o, h, l, c, v, vwap, shi, slo, mins in _symbol_arrays(df):
        bars = [
            Bar(timestamp=ts[i].to_pydatetime(), open=o[i], high=h[i], low=l[i], close=c[i], volume=int(v[i]))
            for i in range(len(c))
        ]
        state = SymbolState(symbol=sym)
        for i in range(15, len(c) - 1):
            price = c[i]
            if price < SCALP_MIN_PRICE:
                continue
            state.bars_1m = bars[max(0, i - settings.MAX_BARS_1M + 1): i + 1]
            state.last_price = price
            state.vwap = vwap[i]
            sig = score_scalp(state)
            if (
                sig.direction == 0
                or sig.total_score < settings.SCALP_ENTRY_SCORE
                or not sig.thesis
                or not sig.thesis.suggested_stop
                or not sig.thesis.suggested_target
                or (sig.direction == -1 and price > vwap[i])
                or (sig.direction == 1 and price < vwap[i])
            ):
                continue
            stop, tgt = sig.thesis.suggested_stop, sig.thesis.suggested_target
            feats = build_features(
                o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1], v[: i + 1],
                direction=sig.direction, engine_score=sig.total_score, engine_scalp=True,
                minutes_since_open=mins[i], vwap=vwap[i],
                session_high=shi[i], session_low=slo[i], stop_price=stop,
            )
            if feats is None:
                continue
            win, r, outcome = triple_barrier(h, l, c, i, sig.direction, price, stop, tgt)
            rows.append({**feats, "session": session, "symbol": sym, "ts": ts[i],
                         "win": win, "r": r, "outcome": outcome})
    return rows


def _swing_rows(session: str, df: pd.DataFrame, setups: pd.DataFrame) -> list[dict]:
    rows = []
    if setups.empty:
        return rows
    by_sym = {s: g for s, g in setups.groupby("symbol")}
    for sym, ts, o, h, l, c, v, vwap, shi, slo, mins in _symbol_arrays(df):
        g = by_sym.get(sym)
        if g is None:
            continue
        done_at = ts + pd.Timedelta(minutes=1)   # a bar is complete one minute after its stamp
        idx = np.searchsorted(done_at.values, g["evaluated_at"].values, side="right") - 1
        g = g.assign(bar_idx=idx)
        g = g[(g.bar_idx >= 15) & (g.bar_idx < len(c) - 1)]
        g = g.drop_duplicates(["bar_idx", "direction"])
        for r in g.itertuples():
            i = int(r.bar_idx)
            price = c[i]
            d = int(r.direction)
            stop, tgt = float(r.suggested_stop), float(r.suggested_target)
            # Discard rows whose levels sit on the wrong side of the bar price (stale quote)
            if (d == 1 and not (stop < price < tgt)) or (d == -1 and not (tgt < price < stop)):
                continue
            feats = build_features(
                o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1], v[: i + 1],
                direction=d, engine_score=float(r.score), engine_scalp=False,
                minutes_since_open=mins[i], vwap=vwap[i],
                session_high=shi[i], session_low=slo[i], stop_price=stop,
            )
            if feats is None:
                continue
            win, rr, outcome = triple_barrier(h, l, c, i, d, price, stop, tgt)
            rows.append({**feats, "session": session, "symbol": sym, "ts": ts[i],
                         "win": win, "r": rr, "outcome": outcome})
    return rows


def _build_session(args) -> list[dict]:
    session, setups = args
    df = pd.read_pickle(BARS_DIR / f"{session}.pkl")
    if df.empty:
        return []
    return _scalp_rows(session, df) + _swing_rows(session, df, setups)


def build_dataset() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    setups = pd.read_sql_query(
        """
        SELECT session_date, symbol, evaluated_at, direction, score, suggested_stop, suggested_target
        FROM rejected_setups
        WHERE direction != 0 AND score >= ? AND suggested_stop IS NOT NULL AND suggested_target IS NOT NULL
        """,
        conn,
        params=(settings.SHADOW_TRADE_MIN_SCORE,),
    )
    conn.close()
    setups["evaluated_at"] = pd.to_datetime(setups["evaluated_at"], format="ISO8601", utc=True)

    sessions = sorted(p.stem for p in BARS_DIR.glob("*.pkl"))
    jobs = [(s, setups[setups.session_date == s]) for s in sessions]
    rows: list[dict] = []
    with ProcessPoolExecutor() as pool:
        for s, out in zip(sessions, pool.map(_build_session, jobs)):
            rows.extend(out)
            print(f"  {s}: {len(out):>5} candidates")
    return pd.DataFrame(rows)


# ── Evaluation helpers ────────────────────────────────────────────────────────

def load_dataset() -> pd.DataFrame:
    data = pd.read_pickle(DATASET)
    # Most candidates hit neither barrier within the horizon, so "target hit" is
    # too rare to learn from. Train on "profitable at exit" instead; threshold
    # selection still optimises total R.
    data["win"] = (data.r > 0).astype(int)
    return data


def split_sessions(data: pd.DataFrame, test_frac: float = 0.25):
    """Chronological fit / validation / test split by session (no overlap in time)."""
    sessions = sorted(data.session.unique())
    n_test = max(1, int(len(sessions) * test_frac))
    n_val = max(1, int(len(sessions) * 0.15))
    test_s = set(sessions[-n_test:])
    val_s = set(sessions[-(n_test + n_val):-n_test])
    fit_s = set(sessions[: -(n_test + n_val)])
    print(f"Split: fit {len(fit_s)} sessions / val {len(val_s)} / test {len(test_s)} "
          f"(test = {min(test_s)} → {max(test_s)})")
    return tuple(data[data.session.isin(s)] for s in (fit_s, val_s, test_s))


def _make_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=400,
        max_depth=4,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=30,
        random_state=7,
    )


def _summary(df: pd.DataFrame, prob: np.ndarray, thr: float) -> dict:
    take = prob >= thr
    out = {
        "n": int(len(df)),
        "base_win_rate": float(df.win.mean()),
        "base_avg_r": float(df.r.mean()),
        "auc": float(roc_auc_score(df.win, prob)) if df.win.nunique() > 1 else None,
        "auc_engine_score": float(roc_auc_score(df.win, df.engine_score)) if df.win.nunique() > 1 else None,
        "brier": float(brier_score_loss(df.win, prob)),
        "kept_frac": float(take.mean()),
        "kept_win_rate": float(df.win[take].mean()) if take.any() else None,
        "kept_avg_r": float(df.r[take].mean()) if take.any() else None,
        "skipped_avg_r": float(df.r[~take].mean()) if (~take).any() else None,
    }
    return out


def _pick_threshold(y_r: np.ndarray, prob: np.ndarray) -> float:
    """Highest total R on the validation slice, requiring ≥15% of candidates kept."""
    best_thr, best_total = 0.0, -np.inf
    for thr in np.arange(0.20, 0.71, 0.01):
        take = prob >= thr
        if take.mean() < 0.15:
            break
        total = y_r[take].sum()
        if total > best_total:
            best_thr, best_total = float(thr), total
    return round(best_thr, 2)


def _print_block(title: str, s: dict) -> None:
    print(f"\n{title}")
    print(f"  candidates          {s['n']}")
    print(f"  base win rate       {s['base_win_rate']:.1%}   avg R {s['base_avg_r']:+.3f}")
    if s["auc"] is not None:
        print(f"  AUC model / score   {s['auc']:.3f} / {s['auc_engine_score']:.3f}")
    print(f"  brier               {s['brier']:.4f}")
    if s["kept_win_rate"] is not None:
        print(f"  kept {s['kept_frac']:.0%}: win {s['kept_win_rate']:.1%}   avg R {s['kept_avg_r']:+.3f}"
              f"   | skipped avg R {s['skipped_avg_r'] if s['skipped_avg_r'] is not None else float('nan'):+.3f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="regenerate the dataset from cached bars")
    ap.add_argument("--test-frac", type=float, default=0.25, help="fraction of most recent sessions held out")
    ap.add_argument("--out-dir", type=Path, default=MODEL_DIR, help="where to write the model")
    args = ap.parse_args()

    if args.rebuild or not DATASET.exists():
        print("Building dataset …")
        data = build_dataset()
        DATASET.parent.mkdir(parents=True, exist_ok=True)
        data.to_pickle(DATASET)
    data = load_dataset()

    if data.empty:
        sys.exit("Dataset is empty — run scripts/ml_fetch_bars.py first.")

    print(f"\nDataset: {len(data)} candidates over {data.session.nunique()} sessions "
          f"(scalp {int(data.engine_scalp.sum())}, swing {int((1 - data.engine_scalp).sum())})")
    print(f"Outcomes: {data.outcome.value_counts().to_dict()}")

    sessions = sorted(data.session.unique())
    fit, val, test = split_sessions(data, args.test_frac)

    # 1) fit on early sessions, choose threshold on validation
    model = _make_model().fit(fit[FEATURE_NAMES], fit.win)
    thr = _pick_threshold(val.r.to_numpy(), model.predict_proba(val[FEATURE_NAMES])[:, 1])
    print(f"Chosen threshold (validation): {thr:.2f}")

    # 2) refit on fit+val, evaluate once on the untouched test sessions
    trainval = pd.concat([fit, val])
    model = _make_model().fit(trainval[FEATURE_NAMES], trainval.win)
    p_test = model.predict_proba(test[FEATURE_NAMES])[:, 1]
    test_metrics = {"all": _summary(test, p_test, thr)}
    _print_block("TEST — all candidates", test_metrics["all"])
    for name, mask in (("scalp", test.engine_scalp == 1), ("swing", test.engine_scalp == 0)):
        if mask.sum() > 50:
            test_metrics[name] = _summary(test[mask], p_test[mask.to_numpy()], thr)
            _print_block(f"TEST — {name}", test_metrics[name])

    # Calibration by decile on test
    dec = pd.qcut(p_test, 10, duplicates="drop")
    calib = test.assign(p=p_test, bucket=dec).groupby("bucket", observed=True).agg(
        n=("win", "size"), predicted=("p", "mean"), actual=("win", "mean"), avg_r=("r", "mean"))
    print("\nCalibration (test, by predicted-probability decile):")
    print(calib.round(3).to_string())

    # 3) final model on all sessions
    final = _make_model().fit(data[FEATURE_NAMES], data.win)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": "gbm",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": FEATURE_NAMES,
        "threshold": thr,
        "n_samples": int(len(data)),
        "sessions": [sessions[0], sessions[-1]],
        "test_metrics": test_metrics,
    }
    joblib.dump({"model": final, "meta": meta}, args.out_dir / "trade_filter.joblib")
    (args.out_dir / "trade_filter.json").write_text(json.dumps(meta, indent=2))
    print(f"\nSaved model → {args.out_dir / 'trade_filter.joblib'}")


if __name__ == "__main__":
    main()
