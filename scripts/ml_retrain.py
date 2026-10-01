#!/usr/bin/env python3
"""
Nightly self-retraining for all ML models (champion / challenger).

Usage (from project root — the server also runs this automatically after the
close, see ML_RETRAIN_TIME):
    venv/bin/python scripts/ml_retrain.py [--force]

Steps
  1. Download any new sessions of 1m bars (scripts/ml_fetch_bars.py).
  2. Train challengers into models/ml/candidates/<run>/:
       trade filter GBM, trade filter NN, autonomous NN.
  3. Promote or reject each challenger:
       • autonomous NN — the challenger's held-out copy (never trained on the
         most recent sessions) and the current champion are both scored on
         the "fresh" sessions that arrived after the champion was trained.
         Neither model has seen those days, so the comparison is fair. The
         challenger is promoted if its most-confident picks (top 10 a day)
         earn at least as much average R as the champion's.
       • filters — promoted if the held-out test AUC is ≥ 0.5 (sanity gate);
         they only run in shadow mode unless ML_FILTER_MODE=gate.
  4. Replaced champions are archived to models/ml/archive/<run>/ so any
     version can be restored by copying it back.
  5. Every run is appended to models/ml/history.json (shown on /ml).

Without --force the run is skipped when there is no new session since the
last run.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT / "models" / "ml"
HISTORY = MODEL_DIR / "history.json"
LOCK = MODEL_DIR / ".retrain.lock"
BARS_DIR = ROOT / "data" / "ml" / "bars"
PY = sys.executable

FILES = {
    "filter_gbm": ["trade_filter.joblib", "trade_filter.json"],
    "filter_nn": ["trade_filter_nn.pt", "trade_filter_nn.json"],
    "auto_nn": ["auto_trader_nn.pt", "auto_trader_nn.json"],
}


def _run(script: str, *args: str) -> None:
    print(f"\n$ {script} {' '.join(args)}", flush=True)
    subprocess.run([PY, str(ROOT / "scripts" / script), *args], cwd=ROOT, check=True)


def load_history() -> list:
    try:
        return json.loads(HISTORY.read_text())
    except (FileNotFoundError, ValueError):
        return []


def _meta(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _promote(name: str, cand_dir: Path, archive_dir: Path) -> None:
    for f in FILES[name]:
        cur = MODEL_DIR / f
        if cur.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cur, archive_dir / f)
    for f in FILES[name]:
        tmp = MODEL_DIR / f"{f}.tmp"
        shutil.copy2(cand_dir / f, tmp)
        tmp.replace(MODEL_DIR / f)   # atomic swap — the live server never sees a half-written file


def judge_auto(cand_dir: Path) -> dict:
    """Champion vs challenger on sessions neither model has trained on."""
    import numpy as np
    from scripts.ml_train_auto import (
        DEFAULT_COST_BPS, apply_costs, evaluate, load_dataset, score_model_file, top_k_table,
    )

    champ_meta = _meta(MODEL_DIR / "auto_trader_nn.json")
    chal_meta = _meta(cand_dir / "auto_trader_nn.json")
    if champ_meta is None:
        return {"promote": True, "reason": "no champion yet", "challenger_test": chal_meta["test_metrics"]["all"]}

    champ_end = champ_meta["sessions"][1]
    test_start = chal_meta["test_sessions"][0]
    d = apply_costs(load_dataset(), chal_meta.get("cost_bps", DEFAULT_COST_BPS))
    fresh = sorted(s for s in np.unique(d["session"]) if s > champ_end and s >= test_start)
    if not fresh:
        return {"promote": False, "reason": "no fresh sessions to compare on"}

    idx = np.flatnonzero(np.isin(d["session"], fresh))
    p_champ = score_model_file(MODEL_DIR / "auto_trader_nn.pt", d, idx)
    p_chal = score_model_file(cand_dir / "auto_trader_nn_holdout.pt", d, idx)
    champ = {"top_k": top_k_table(d, idx, p_champ), **evaluate(d, idx, p_champ, champ_meta["threshold"])}
    chal = {"top_k": top_k_table(d, idx, p_chal), **evaluate(d, idx, p_chal, chal_meta["threshold"])}
    c_r, h_r = champ["top_k"]["10"]["avg_r"], chal["top_k"]["10"]["avg_r"]
    return {
        "promote": h_r >= c_r,
        "reason": f"top-10/day avg R on {len(fresh)} fresh session(s): challenger {h_r:+.3f} vs champion {c_r:+.3f}",
        "fresh_sessions": [fresh[0], fresh[-1]],
        "champion": champ,
        "challenger": chal,
    }


def judge_filter(name: str, cand_dir: Path) -> dict:
    meta = _meta(cand_dir / FILES[name][1])
    auc = meta["test_metrics"]["all"]["auc"]
    return {
        "promote": auc is not None and auc >= 0.5,
        "reason": f"held-out test AUC {auc:.3f} ({'≥' if auc and auc >= 0.5 else '<'} 0.5 sanity gate)",
        "challenger_test": meta["test_metrics"]["all"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="retrain even without new sessions")
    args = ap.parse_args()

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Another retrain is already running.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history = load_history()
    entry = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(), "models": {}}

    try:
        _run("ml_fetch_bars.py")
        sessions = sorted(p.stem for p in BARS_DIR.glob("*.pkl"))
        entry["latest_session"] = sessions[-1] if sessions else None
        last = next((h for h in reversed(history) if h.get("status") == "ok"), None)
        if not args.force and last and last.get("latest_session") == entry["latest_session"]:
            print("No new sessions since the last run — nothing to do.")
            return

        cand = MODEL_DIR / "candidates" / run_id
        _run("ml_train.py", "--rebuild", "--out-dir", str(cand))
        _run("ml_train_nn.py", "--lr", "2e-4", "--out-dir", str(cand))
        _run("ml_train_auto.py", "--out-dir", str(cand))

        archive = MODEL_DIR / "archive" / run_id
        for name, judge in (("filter_gbm", lambda: judge_filter("filter_gbm", cand)),
                            ("filter_nn", lambda: judge_filter("filter_nn", cand)),
                            ("auto_nn", lambda: judge_auto(cand))):
            verdict = judge()
            if verdict["promote"]:
                _promote(name, cand, archive)
            entry["models"][name] = verdict
            print(f"{name}: {'PROMOTED' if verdict['promote'] else 'kept champion'} — {verdict['reason']}")

        shutil.rmtree(cand, ignore_errors=True)
        entry["status"] = "ok"
    except Exception as exc:
        entry["status"] = "failed"
        entry["error"] = str(exc)
        print(f"Retrain failed: {exc}", file=sys.stderr)
        raise
    finally:
        if entry.get("status"):
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
            history.append(entry)
            HISTORY.write_text(json.dumps(history[-200:], indent=2, default=str))


if __name__ == "__main__":
    main()
