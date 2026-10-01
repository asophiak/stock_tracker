#!/usr/bin/env python3
"""
Train the autonomous neural-net trader.

Usage (from project root, after scripts/ml_fetch_bars.py):
    venv/bin/python scripts/ml_train_auto.py [--epochs 30] [--out-dir models/ml]

Dataset: every STRIDE minutes, for every symbol with price ≥ $15, the raw
market state (bar sequence + features, long-side view) is labelled twice with
the triple-barrier outcome of a long AND a short using the bot's own stop /
target / 60-minute rule. The network (GRU + MLP, two outputs) learns
P(long profitable) and P(short profitable). No rule-engine input is used.

Per-session samples are cached in data/ml/auto/ so nightly retrains only
process new sessions.

Evaluation mirrors the bot's decision: at each sample take the side with the
higher probability and trade only if it clears the threshold. The threshold
is chosen on validation sessions; results are reported on held-out test
sessions the model never saw.
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import roc_auc_score

from app.ml.auto import AUTO_FEATURES, build_auto_inputs, trade_levels
from app.ml.labeling import triple_barrier
from app.ml.nn_model import NetConfig, standardize
from scripts.ml_train import BARS_DIR, MODEL_DIR, _symbol_arrays
from scripts.ml_train_nn import SEED, _predict, train

AUTO_DIR = ROOT / "data" / "ml" / "auto"
STRIDE = 3          # minutes between samples per symbol
MIN_PRICE = 15.0    # mirrors the scalp bot's minimum price
NO_ENTRY_LAST_MIN = 30   # the risk manager blocks entries in the last 30 minutes
MIN_TRADES_PER_DAY = 5
DEFAULT_COST_BPS = 3.0   # per side: ~1–2 bp half-spread + ~1–2 bp slippage on liquid large caps


# ── Dataset ───────────────────────────────────────────────────────────────────

SIP_BARS_DIR = ROOT / "data" / "ml" / "bars_sip"
SIP_AUTO_DIR = ROOT / "data" / "ml" / "auto_sip"
CONTEXT_ONLY = {"SPY", "QQQ"}   # market context, never traded
DATA_SOURCES = {
    # name: (bars dir, sample cache dir, stride in minutes)
    "iex": (BARS_DIR, AUTO_DIR, STRIDE),
    "sip": (SIP_BARS_DIR, SIP_AUTO_DIR, 10),
}
_KEYS = ("tab", "seq", "r_long", "r_short", "risk_pct", "symbol", "minute")


def _session_samples(bars_path: Path, stride: int) -> dict:
    df = pd.read_pickle(bars_path)
    rows = {k: [] for k in _KEYS}
    if not df.empty:
        for sym, ts, o, h, l, c, v, vwap, shi, slo, mopen in _symbol_arrays(df):
            if sym in CONTEXT_ONLY:
                continue
            for i in range(15 + zlib.crc32(sym.encode()) % stride, len(c) - 1, stride):
                if c[i] < MIN_PRICE or mopen[i] > 390 - NO_ENTRY_LAST_MIN:
                    continue
                inp = build_auto_inputs(
                    o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1], v[: i + 1],
                    minutes_since_open=mopen[i], vwap=vwap[i], session_high=shi[i], session_low=slo[i],
                )
                if inp is None:
                    continue
                row, seq, atr = inp
                outcomes = []
                for d in (1, -1):
                    stop, tgt = trade_levels(c[i], atr, d)
                    outcomes.append(triple_barrier(h, l, c, i, d, c[i], stop, tgt)[1])
                rows["tab"].append(row)
                rows["seq"].append(seq.astype(np.float16))
                rows["r_long"].append(outcomes[0])
                rows["r_short"].append(outcomes[1])
                rows["risk_pct"].append(abs(c[i] - trade_levels(c[i], atr, 1)[0]) / c[i])
                rows["symbol"].append(sym)
                rows["minute"].append(mopen[i])
    return {
        "tab": np.asarray(rows["tab"], np.float32).reshape(-1, len(AUTO_FEATURES)),
        "seq": np.asarray(rows["seq"], np.float16).reshape(-1, 60, 7),
        "r_long": np.asarray(rows["r_long"], np.float32),
        "r_short": np.asarray(rows["r_short"], np.float32),
        "risk_pct": np.asarray(rows["risk_pct"], np.float32),
        "symbol": np.asarray(rows["symbol"]),
        "minute": np.asarray(rows["minute"], np.float32),
    }


def _build_and_cache(job) -> str:
    bars_path, cache_path, stride = job
    np.savez(cache_path, **_session_samples(bars_path, stride))
    return cache_path.stem


def load_dataset(rebuild: bool = False, source: str = "iex") -> dict:
    bars_dir, cache_dir, stride = DATA_SOURCES[source]
    cache_dir.mkdir(parents=True, exist_ok=True)
    sessions = sorted(p.stem for p in bars_dir.glob("*.pkl"))

    def stale(s: str) -> bool:
        f = cache_dir / f"{s}.npz"
        if rebuild or not f.exists():
            return True
        with np.load(f) as z:
            return "risk_pct" not in z.files   # cache from before costs were modelled

    todo = [s for s in sessions if stale(s)]
    if todo:
        print(f"Building samples for {len(todo)} {source} sessions …", flush=True)
        jobs = [(bars_dir / f"{s}.pkl", cache_dir / f"{s}.npz", stride) for s in todo]
        with ProcessPoolExecutor() as pool:
            for _ in pool.map(_build_and_cache, jobs, chunksize=4):
                pass

    parts = {k: [] for k in (*_KEYS, "session")}
    for s in sessions:
        with np.load(cache_dir / f"{s}.npz") as z:
            n = len(z["r_long"])
            if n == 0:
                continue
            for k in _KEYS:
                parts[k].append(z[k])
        parts["session"].append(np.full(n, s))
    return {k: np.concatenate(v) for k, v in parts.items()}


def net_r(r: np.ndarray, risk_pct: np.ndarray, cost_bps: float) -> np.ndarray:
    """R after round-trip costs: cost_bps per side, expressed in units of the stop distance."""
    return r - (2.0 * cost_bps / 1e4) / risk_pct


def apply_costs(d: dict, cost_bps: float) -> dict:
    """
    Replace r_long / r_short with after-cost R (gross kept as *_gross), so every
    metric, threshold and training label downstream is net of costs.
    """
    if "r_long_gross" not in d:
        d["r_long_gross"], d["r_short_gross"] = d["r_long"], d["r_short"]
    d["r_long"] = net_r(d["r_long_gross"], d["risk_pct"], cost_bps)
    d["r_short"] = net_r(d["r_short_gross"], d["risk_pct"], cost_bps)
    d["cost_bps"] = cost_bps
    return d


# ── Evaluation ────────────────────────────────────────────────────────────────

def decide(p: np.ndarray, thr: float):
    """Bot decision per sample: side with higher probability, if it clears thr."""
    side = np.where(p[:, 0] >= p[:, 1], 1, -1)
    best = p.max(axis=1)
    return side, best >= thr


def evaluate(d: dict, idx: np.ndarray, p: np.ndarray, thr: float, with_auc: bool = True) -> dict:
    r_long, r_short = d["r_long"][idx], d["r_short"][idx]
    side, take = decide(p, thr)
    r = np.where(side == 1, r_long, r_short)
    sessions = d["session"][idx]
    n_days = len(np.unique(sessions))
    daily = pd.Series(r[take]).groupby(sessions[take]).sum() if take.any() else pd.Series(dtype=float)
    yl, ys = (r_long > 0).astype(int), (r_short > 0).astype(int)
    return {
        "n": int(len(idx)),
        "days": n_days,
        "auc_long": float(roc_auc_score(yl, p[:, 0])) if with_auc else None,
        "auc_short": float(roc_auc_score(ys, p[:, 1])) if with_auc else None,
        "base_avg_r": float((r_long.mean() + r_short.mean()) / 2),
        "trades": int(take.sum()),
        "trades_per_day": float(take.sum() / max(n_days, 1)),
        "win_rate": float((r[take] > 0).mean()) if take.any() else None,
        "avg_r": float(r[take].mean()) if take.any() else None,
        "total_r": float(r[take].sum()),
        "positive_days": float((daily > 0).mean()) if len(daily) else None,
        "long_share": float((side[take] == 1).mean()) if take.any() else None,
    }


def pick_threshold(d: dict, idx: np.ndarray, p: np.ndarray) -> float:
    """
    Best average R on validation among thresholds that still give at least
    MIN_TRADES_PER_DAY signals — the bot holds only a few positions, so what
    matters is the quality of its most confident picks, not total volume.
    """
    best_thr, best_avg = 0.99, -np.inf
    for thr in np.arange(0.40, 0.80, 0.0025):
        m = evaluate(d, idx, p, thr, with_auc=False)
        if m["trades_per_day"] < MIN_TRADES_PER_DAY:
            break
        if m["avg_r"] is not None and m["avg_r"] > best_avg:
            best_thr, best_avg = float(thr), m["avg_r"]
    return round(best_thr, 4)


def top_k_table(d: dict, idx: np.ndarray, p: np.ndarray, ks=(5, 10, 25, 100)) -> dict:
    """Avg R if the bot took only its k most confident signals each day."""
    side, _ = decide(p, 0.0)
    r = np.where(side == 1, d["r_long"][idx], d["r_short"][idx])
    df = pd.DataFrame({"s": d["session"][idx], "conf": p.max(axis=1), "r": r})
    df = df.sort_values("conf", ascending=False)
    out = {}
    for k in ks:
        top = df.groupby("s").head(k)
        out[str(k)] = {"avg_r": float(top.r.mean()), "win_rate": float((top.r > 0).mean()),
                       "positive_days": float((top.groupby("s").r.sum() > 0).mean())}
    return out


def _print(title: str, m: dict) -> None:
    print(f"\n{title}")
    print(f"  samples {m['n']} over {m['days']} days   AUC long {m['auc_long']:.3f}  short {m['auc_short']:.3f}")
    print(f"  random-side avg R   {m['base_avg_r']:+.3f}")
    if m["trades"]:
        print(f"  model trades        {m['trades']} ({m['trades_per_day']:.1f}/day, {m['long_share']:.0%} long)")
        print(f"  win rate {m['win_rate']:.1%}   avg R {m['avg_r']:+.3f}   total R {m['total_r']:+.1f}   "
              f"positive days {m['positive_days']:.0%}")


def score_model_file(path: Path, d: dict, idx: np.ndarray) -> np.ndarray:
    """(len(idx), 2) probabilities from a saved autonomous model."""
    from app.ml.nn_model import TradeNet

    b = torch.load(path, map_location="cpu", weights_only=False)
    net = TradeNet(NetConfig(**b["config"]))
    net.load_state_dict(b["state_dict"])
    seq, tab, _ = _tensors(d, idx, b["mean"], b["std"], torch.device("cpu"))
    return _predict(net, seq, tab)


# ── Main ──────────────────────────────────────────────────────────────────────

def _tensors(d, idx, mean, std, device):
    tab = standardize(torch.from_numpy(d["tab"][idx]), mean, std)
    seq = torch.from_numpy(d["seq"][idx].astype(np.float32))
    y = torch.from_numpy(np.stack([d["r_long"][idx] > 0, d["r_short"][idx] > 0], axis=1).astype(np.float32))
    return seq.to(device), tab.to(device), y.to(device)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps"])
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--out-dir", type=Path, default=MODEL_DIR)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS,
                    help="per-side cost (spread + slippage) in basis points")
    ap.add_argument("--source", default="iex", choices=list(DATA_SOURCES))
    args = ap.parse_args()

    device = torch.device("mps" if args.device == "auto" and torch.backends.mps.is_available()
                          else ("cpu" if args.device == "auto" else args.device))
    torch.manual_seed(SEED)

    d = apply_costs(load_dataset(args.rebuild, args.source), args.cost_bps)
    sessions = sorted(np.unique(d["session"]))
    n_test = max(1, int(len(sessions) * args.test_frac))
    n_val = max(1, int(len(sessions) * 0.15))
    test_s, val_s, fit_s = sessions[-n_test:], sessions[-(n_test + n_val):-n_test], sessions[: -(n_test + n_val)]
    fit_i, val_i, test_i = (np.flatnonzero(np.isin(d["session"], s)) for s in (fit_s, val_s, test_s))
    print(f"Dataset: {len(d['r_long'])} samples over {len(sessions)} sessions, device {device}")
    print(f"Split: fit {len(fit_s)} / val {len(val_s)} / test {len(test_s)} (test = {test_s[0]} → {test_s[-1]})")

    cfg = NetConfig(n_tab=len(AUTO_FEATURES), n_out=2)
    fit_mean = torch.from_numpy(np.nanmean(d["tab"][fit_i], axis=0))
    fit_std = torch.from_numpy(np.nanstd(d["tab"][fit_i], axis=0)) + 1e-6
    fit_t, val_t, test_t = (_tensors(d, i, fit_mean, fit_std, device) for i in (fit_i, val_i, test_i))

    print("\nTraining (fit sessions, early-stopped on validation) …")
    model, best_epoch = train(fit_t, val_t, args.epochs, device, lr=args.lr, cfg=cfg)
    thr = pick_threshold(d, val_i, _predict(model, val_t[0], val_t[1]))
    print(f"Best epoch {best_epoch}, threshold (validation) {thr:.3f}")

    p_test = _predict(model, test_t[0], test_t[1])
    test_m = evaluate(d, test_i, p_test, thr)
    _print("TEST — autonomous neural net (unseen sessions)", test_m)
    test_m["top_k"] = top_k_table(d, test_i, p_test)
    print("\n  If it only took its k most confident signals per day:")
    for k, m in test_m["top_k"].items():
        print(f"    top {k:>3}/day   avg R {m['avg_r']:+.3f}   win {m['win_rate']:.1%}   "
              f"positive days {m['positive_days']:.0%}")

    # Keep the fit-only model: it has never seen the test sessions, so the nightly
    # retrain can compare it fairly against the current champion on fresh days.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
        "config": cfg.to_dict(),
        "mean": fit_mean, "std": fit_std,
        "meta": {"kind": "auto_holdout", "threshold": thr, "features": AUTO_FEATURES,
                 "trained_through": fit_s[-1], "val_sessions": [val_s[0], val_s[-1]]},
    }, args.out_dir / "auto_trader_nn_holdout.pt")

    print(f"\nRetraining on all sessions for {best_epoch} epochs …")
    all_i = np.arange(len(d["r_long"]))
    mean = torch.from_numpy(np.nanmean(d["tab"], axis=0))
    std = torch.from_numpy(np.nanstd(d["tab"], axis=0)) + 1e-6
    final, _ = train(_tensors(d, all_i, mean, std, device), None, best_epoch, device,
                     fixed_epochs=True, lr=args.lr, cfg=cfg)

    meta = {
        "kind": "auto",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": AUTO_FEATURES,
        "seq_len": 60,
        "threshold": thr,
        "epochs": best_epoch,
        "n_samples": int(len(all_i)),
        "sessions": [sessions[0], sessions[-1]],
        "test_sessions": [test_s[0], test_s[-1]],
        "test_metrics": {"all": test_m},
        "cost_bps": args.cost_bps,
        "data_source": args.source,
    }
    torch.save({
        "state_dict": {k: v.cpu() for k, v in final.state_dict().items()},
        "config": cfg.to_dict(),
        "mean": mean, "std": std,
        "meta": meta,
    }, args.out_dir / "auto_trader_nn.pt")
    (args.out_dir / "auto_trader_nn.json").write_text(json.dumps(meta, indent=2))
    print(f"Saved model → {args.out_dir / 'auto_trader_nn.pt'}")


if __name__ == "__main__":
    main()
