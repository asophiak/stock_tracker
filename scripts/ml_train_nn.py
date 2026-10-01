#!/usr/bin/env python3
"""
Train the neural trade filter (GRU over raw bars + MLP over features).

Usage (from project root, after scripts/ml_train.py has built data/ml/dataset.pkl):
    venv/bin/python scripts/ml_train_nn.py [--epochs 30] [--device auto|cpu|mps]

Uses the same chronological fit / validation / test split as the gradient-boosted
model and trains a GBM baseline on the identical split, so the two are compared
like-for-like on sessions neither model has seen.

  1. Train on fit sessions, early-stop on validation loss, pick the entry
     threshold on validation.
  2. Report on the held-out test sessions (NN vs GBM baseline).
  3. Retrain on all sessions for the best epoch count and save to
     models/ml/trade_filter_nn.pt.
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
import pandas as pd
import torch
from torch import nn

from app.ml.features import FEATURE_NAMES
from app.ml.nn_model import NetConfig, TradeNet, standardize
from app.ml.sequence import SEQ_LEN, N_CHANNELS, build_sequence
from scripts.ml_train import (
    BARS_DIR, DATASET, MODEL_DIR, _make_model, _pick_threshold, _print_block, _summary,
    _symbol_arrays, load_dataset, split_sessions,
)

SEQ_CACHE = ROOT / "data" / "ml" / "sequences.npy"
SEED = 7


# ── Sequences ─────────────────────────────────────────────────────────────────

def build_sequences(data: pd.DataFrame) -> np.ndarray:
    """(len(data), SEQ_LEN, N_CHANNELS) aligned with data's row order; cached on disk."""
    if SEQ_CACHE.exists() and SEQ_CACHE.stat().st_mtime >= DATASET.stat().st_mtime:
        seqs = np.load(SEQ_CACHE, mmap_mode="r")
        if len(seqs) == len(data):
            return np.asarray(seqs)

    print("Building bar sequences …")
    seqs = np.zeros((len(data), SEQ_LEN, N_CHANNELS), dtype=np.float32)
    pos = pd.Series(np.arange(len(data)), index=data.index)
    for session, rows in data.groupby("session"):
        bars = pd.read_pickle(BARS_DIR / f"{session}.pkl")
        arrays = {a[0]: a for a in _symbol_arrays(bars)}
        for sym, srows in rows.groupby("symbol"):
            sym_, ts, o, h, l, c, v = arrays[sym][:7]
            idx = ts.get_indexer(pd.DatetimeIndex(srows.ts))
            for row_i, i, d in zip(pos[srows.index], idx, srows.direction):
                s = build_sequence(o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1], v[: i + 1], int(d))
                if s is not None:
                    seqs[row_i] = s
    np.save(SEQ_CACHE, seqs)
    return seqs


# ── Training ──────────────────────────────────────────────────────────────────

def _tensors(df, seqs, mean, std, device):
    tab = torch.tensor(df[FEATURE_NAMES].to_numpy(np.float32))
    return (
        torch.from_numpy(np.ascontiguousarray(seqs)).to(device),
        standardize(tab, mean, std).to(device),
        torch.tensor(df.win.to_numpy(np.float32)).to(device),
    )


def _predict(model, seq, tab, batch=8192) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(seq), batch):
            out.append(torch.sigmoid(model(seq[i:i + batch], tab[i:i + batch])).cpu())
    return torch.cat(out).numpy()


def train(train_t, val_t, epochs: int, device, fixed_epochs: bool = False, lr: float = 1e-3,
          cfg: NetConfig | None = None):
    """Returns (model, best_epoch). With fixed_epochs, trains exactly `epochs` with no early stop."""
    torch.manual_seed(SEED)
    model = TradeNet(cfg or NetConfig()).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.BCEWithLogitsLoss()
    seq, tab, y = train_t
    n = len(y)
    best_loss, best_epoch, best_state, patience = np.inf, 0, None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        t0 = time.time()
        perm = torch.randperm(n, device=device)
        total = 0.0
        for i in range(0, n, 1024):
            b = perm[i:i + 1024]
            opt.zero_grad()
            loss = loss_fn(model(seq[b], tab[b]), y[b])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(b)
        sched.step()
        msg = f"  epoch {epoch:>2}  train loss {total / n:.4f}"

        if val_t is not None and not fixed_epochs:
            p = _predict(model, val_t[0], val_t[1])
            yv = val_t[2].cpu().numpy()
            vloss = float(-np.mean(yv * np.log(p + 1e-7) + (1 - yv) * np.log(1 - p + 1e-7)))
            msg += f"  val loss {vloss:.4f}"
            if vloss < best_loss - 1e-4:
                best_loss, best_epoch, patience = vloss, epoch, 0
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            else:
                patience += 1
        print(msg + f"  ({time.time() - t0:.0f}s)")
        if not fixed_epochs and patience >= 4:
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, (best_epoch or epochs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps"])
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--out-dir", type=Path, default=MODEL_DIR)
    args = ap.parse_args()

    device = torch.device("mps" if args.device == "auto" and torch.backends.mps.is_available()
                          else ("cpu" if args.device == "auto" else args.device))
    torch.manual_seed(SEED)
    np.random.seed(SEED)

    if not DATASET.exists():
        sys.exit("No dataset — run scripts/ml_train.py first.")
    data = load_dataset().reset_index(drop=True)
    seqs = build_sequences(data)
    print(f"Dataset: {len(data)} candidates, sequences {seqs.shape}, device {device}")

    fit, val, test = split_sessions(data, args.test_frac)
    mean = torch.tensor(np.nanmean(fit[FEATURE_NAMES].to_numpy(np.float32), axis=0))
    std = torch.tensor(np.nanstd(fit[FEATURE_NAMES].to_numpy(np.float32), axis=0)) + 1e-6

    fit_t = _tensors(fit, seqs[fit.index], mean, std, device)
    val_t = _tensors(val, seqs[val.index], mean, std, device)
    test_t = _tensors(test, seqs[test.index], mean, std, device)

    # 1) fit with early stopping on validation, pick threshold on validation
    print("\nTraining (fit sessions, early-stopped on validation) …")
    model, best_epoch = train(fit_t, val_t, args.epochs, device, lr=args.lr)
    thr = _pick_threshold(val.r.to_numpy(), _predict(model, val_t[0], val_t[1]))
    print(f"Best epoch {best_epoch}, threshold (validation) {thr:.2f}")

    # 2) held-out test: NN vs GBM trained on the identical split
    p_nn = _predict(model, test_t[0], test_t[1])
    gbm = _make_model().fit(pd.concat([fit, val])[FEATURE_NAMES], pd.concat([fit, val]).win)
    p_gbm = gbm.predict_proba(test[FEATURE_NAMES])[:, 1]
    gbm_thr = _pick_threshold(val.r.to_numpy(), gbm.predict_proba(val[FEATURE_NAMES])[:, 1])

    test_metrics = {"all": _summary(test, p_nn, thr)}
    _print_block("TEST — neural net", test_metrics["all"])
    _print_block("TEST — GBM baseline (same split)", _summary(test, p_gbm, gbm_thr))
    for name, mask in (("scalp", test.engine_scalp == 1), ("swing", test.engine_scalp == 0)):
        m = mask.to_numpy()
        if m.sum() > 50:
            test_metrics[name] = _summary(test[mask], p_nn[m], thr)
            _print_block(f"TEST — neural net, {name}", test_metrics[name])

    dec = pd.qcut(p_nn, 10, duplicates="drop")
    calib = test.assign(p=p_nn, bucket=dec).groupby("bucket", observed=True).agg(
        n=("win", "size"), predicted=("p", "mean"), actual=("win", "mean"), avg_r=("r", "mean"))
    print("\nCalibration (test, neural net):")
    print(calib.round(3).to_string())

    # 3) final model on all sessions, same number of epochs
    print(f"\nRetraining on all sessions for {best_epoch} epochs …")
    mean = torch.tensor(np.nanmean(data[FEATURE_NAMES].to_numpy(np.float32), axis=0))
    std = torch.tensor(np.nanstd(data[FEATURE_NAMES].to_numpy(np.float32), axis=0)) + 1e-6
    all_t = _tensors(data, seqs, mean, std, device)
    final, _ = train(all_t, None, best_epoch, device, fixed_epochs=True, lr=args.lr)

    sessions = sorted(data.session.unique())
    meta = {
        "kind": "nn",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": FEATURE_NAMES,
        "seq_len": SEQ_LEN,
        "threshold": thr,
        "epochs": best_epoch,
        "n_samples": int(len(data)),
        "sessions": [sessions[0], sessions[-1]],
        "test_metrics": test_metrics,
        "gbm_baseline_test": _summary(test, p_gbm, gbm_thr),
    }
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": {k: v.cpu() for k, v in final.state_dict().items()},
        "config": final.cfg.to_dict(),
        "mean": mean, "std": std,
        "meta": meta,
    }, out_dir / "trade_filter_nn.pt")
    (out_dir / "trade_filter_nn.json").write_text(json.dumps(meta, indent=2))
    print(f"Saved model → {out_dir / 'trade_filter_nn.pt'}")


if __name__ == "__main__":
    main()
