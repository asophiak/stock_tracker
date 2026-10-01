#!/usr/bin/env python3
"""
Walk-forward evaluation of the autonomous trader, after trading costs.

Usage (from project root, after scripts/ml_fetch_history.py):
    venv/bin/python scripts/ml_walkforward.py [--source sip] [--cost-bps 3] [--seeds 2]

A single train/test split can flatter or punish a model by luck. This instead
rolls through history one month at a time:

    fold 1:  train [day 0 ……… day 200]  → test [201 … 221]
    fold 2:  train [day 0 ………… day 221] → test [222 … 242]
    …

For every fold the network is trained from scratch (several seeds), early-
stopped and thresholded on the last VAL_DAYS of its training window, then
scored on the next month it has never seen. Every R figure is net of a
round-trip cost of 2 × cost-bps. Baselines on the same folds:

  • random side      — expected R of a coin-flip long/short (after costs)
  • GBM              — gradient-boosted trees on the same 18 features
  • network          — GRU + MLP (the production architecture)

The report gives per-fold results plus the mean, a 95% confidence interval
across folds and the share of profitable folds, and is saved to
models/ml/walkforward.json (shown on the /ml page).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import torch
from sklearn.ensemble import HistGradientBoostingClassifier

from app.ml.auto import AUTO_FEATURES
from app.ml.nn_model import NetConfig
from scripts.ml_train import MODEL_DIR
from scripts.ml_train_auto import (
    DEFAULT_COST_BPS, _tensors, apply_costs, evaluate, load_dataset, pick_threshold, top_k_table,
)
from scripts.ml_train_nn import _predict, train

VAL_DAYS = 30
OUT = MODEL_DIR / "walkforward.json"


def _ci(xs: list) -> dict:
    xs = np.asarray([x for x in xs if x is not None], float)
    if len(xs) == 0:
        return {"mean": None, "lo": None, "hi": None, "positive_share": None, "n": 0}
    half = 1.96 * xs.std(ddof=1) / np.sqrt(len(xs)) if len(xs) > 1 else float("nan")
    return {"mean": float(xs.mean()), "lo": float(xs.mean() - half), "hi": float(xs.mean() + half),
            "positive_share": float((xs > 0).mean()), "n": int(len(xs))}


def _gbm_probs(d, fit_i, pred_i) -> np.ndarray:
    out = []
    for key in ("r_long", "r_short"):
        m = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, max_depth=4, min_samples_leaf=200,
                                           l2_regularization=1.0, early_stopping=True, random_state=7)
        m.fit(d["tab"][fit_i], (d[key][fit_i] > 0).astype(int))
        out.append(m.predict_proba(d["tab"][pred_i])[:, 1])
    return np.stack(out, axis=1)


def _model_report(d, val_i, test_i, p_val, p_test) -> dict:
    thr = pick_threshold(d, val_i, p_val)
    m = evaluate(d, test_i, p_test, thr)
    m["threshold"] = thr
    m["top_k"] = top_k_table(d, test_i, p_test, ks=(5, 10, 25))
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="sip", choices=["sip", "iex"])
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--min-train-days", type=int, default=200)
    ap.add_argument("--test-days", type=int, default=21)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--max-folds", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    d = apply_costs(load_dataset(source=args.source), args.cost_bps)
    sessions = sorted(np.unique(d["session"]))
    print(f"{len(d['r_long']):,} samples over {len(sessions)} sessions "
          f"({sessions[0]} → {sessions[-1]}), cost {args.cost_bps} bp/side, device {device}", flush=True)

    starts = list(range(args.min_train_days, len(sessions), args.test_days))
    if args.max_folds:
        starts = starts[-args.max_folds:]
    sess_arr = d["session"]
    folds = []
    t_start = time.time()

    for fi, start in enumerate(starts, 1):
        train_s = sessions[:start]
        test_s = sessions[start:start + args.test_days]
        fit_s, val_s = train_s[:-VAL_DAYS], train_s[-VAL_DAYS:]
        fit_i, val_i, test_i = (np.flatnonzero(np.isin(sess_arr, s)) for s in (fit_s, val_s, test_s))
        print(f"\nFold {fi}/{len(starts)}: train {train_s[0]} → {train_s[-1]} ({len(fit_i):,} + {len(val_i):,} val) | "
              f"test {test_s[0]} → {test_s[-1]} ({len(test_i):,})", flush=True)

        side_r = (d["r_long"][test_i] + d["r_short"][test_i]) / 2
        fold = {"test": [test_s[0], test_s[-1]], "train_days": len(train_s),
                "random_avg_r": float(side_r.mean()),
                "always_long_avg_r": float(d["r_long"][test_i].mean())}

        mean = torch.from_numpy(np.nanmean(d["tab"][fit_i], axis=0))
        std = torch.from_numpy(np.nanstd(d["tab"][fit_i], axis=0)) + 1e-6
        fit_t, val_t, test_t = (_tensors(d, i, mean, std, device) for i in (fit_i, val_i, test_i))
        cfg = NetConfig(n_tab=len(AUTO_FEATURES), n_out=2)

        seed_reports, p_tests = [], []
        for seed in range(args.seeds):
            torch.manual_seed(seed)
            import scripts.ml_train_nn as nn_mod
            nn_mod.SEED = seed
            model, ep = train(fit_t, val_t, args.epochs, device, lr=args.lr, cfg=cfg)
            p_val, p_test = _predict(model, val_t[0], val_t[1]), _predict(model, test_t[0], test_t[1])
            rep = _model_report(d, val_i, test_i, p_val, p_test)
            rep["epochs"] = ep
            seed_reports.append(rep)
            p_tests.append((p_val, p_test))
        # seed ensemble — average the probabilities of all seeds
        p_val_e = np.mean([p[0] for p in p_tests], axis=0)
        p_test_e = np.mean([p[1] for p in p_tests], axis=0)
        fold["nn_seeds"] = seed_reports
        fold["nn"] = _model_report(d, val_i, test_i, p_val_e, p_test_e)
        fold["gbm"] = _model_report(d, val_i, test_i, _gbm_probs(d, fit_i, val_i), _gbm_probs(d, fit_i, test_i))
        del fit_t, val_t, test_t
        if device.type == "mps":
            torch.mps.empty_cache()

        n, g = fold["nn"], fold["gbm"]
        print(f"  random {fold['random_avg_r']:+.3f}R | NN thr {n['threshold']:.3f}: {n['avg_r'] if n['avg_r'] is not None else float('nan'):+.3f}R "
              f"({n['trades_per_day']:.1f}/day) top10 {n['top_k']['10']['avg_r']:+.3f} AUC {(n['auc_long'] + n['auc_short']) / 2:.3f} | "
              f"GBM {g['avg_r'] if g['avg_r'] is not None else float('nan'):+.3f}R top10 {g['top_k']['10']['avg_r']:+.3f} "
              f"AUC {(g['auc_long'] + g['auc_short']) / 2:.3f}  [{(time.time() - t_start) / 60:.0f} min]", flush=True)
        folds.append(fold)

    def summarize(key):
        return {
            "auc": _ci([(f[key]["auc_long"] + f[key]["auc_short"]) / 2 for f in folds]),
            "avg_r": _ci([f[key]["avg_r"] for f in folds]),
            "top10_avg_r": _ci([f[key]["top_k"]["10"]["avg_r"] for f in folds]),
            "top5_avg_r": _ci([f[key]["top_k"]["5"]["avg_r"] for f in folds]),
            "trades_per_day": _ci([f[key]["trades_per_day"] for f in folds]),
        }

    seed_spread = [np.std([s["top_k"]["10"]["avg_r"] for s in f["nn_seeds"]]) for f in folds]
    summary = {
        "random_avg_r": _ci([f["random_avg_r"] for f in folds]),
        "always_long_avg_r": _ci([f["always_long_avg_r"] for f in folds]),
        "nn": summarize("nn"),
        "gbm": summarize("gbm"),
        "nn_seed_spread_top10": float(np.mean(seed_spread)) if seed_spread else None,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": args.source, "cost_bps": args.cost_bps, "samples": int(len(d["r_long"])),
        "sessions": [sessions[0], sessions[-1]], "n_sessions": len(sessions),
        "config": vars(args), "summary": summary, "folds": folds,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=float))

    print("\n══ Walk-forward summary (after costs, mean [95% CI] across folds, % folds > 0) ══")
    def line(name, c):
        if c["mean"] is None:
            return f"  {name:<26} —"
        return f"  {name:<26} {c['mean']:+.3f}  [{c['lo']:+.3f}, {c['hi']:+.3f}]  {c['positive_share']:.0%} of {c['n']} folds"
    print(line("random side avg R", summary["random_avg_r"]))
    print(line("always long avg R", summary["always_long_avg_r"]))
    for key in ("nn", "gbm"):
        s = summary[key]
        print(f" {key.upper()}  AUC {s['auc']['mean']:.3f} [{s['auc']['lo']:.3f}, {s['auc']['hi']:.3f}]")
        print(line(f"{key} at threshold avg R", s["avg_r"]))
        print(line(f"{key} top-10/day avg R", s["top10_avg_r"]))
        print(line(f"{key} top-5/day avg R", s["top5_avg_r"]))
    print(f"  NN seed-to-seed spread (top-10 avg R): ±{summary['nn_seed_spread_top10']:.3f}")
    print(f"\nSaved → {OUT}")


if __name__ == "__main__":
    main()
