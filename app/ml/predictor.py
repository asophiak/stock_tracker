"""
Live scoring for the ML trade filter.

Trade filter — two interchangeable models (settings.ML_MODEL):
  nn  — GRU over the last 60 bars + MLP over features (models/ml/trade_filter_nn.pt)
  gbm — gradient-boosted trees over features        (models/ml/trade_filter.joblib)

Autonomous bot — models/ml/auto_trader_nn.pt, scored by predict_auto().

The model file is loaded lazily and reloaded automatically when a training
script writes a new one, so retraining does not need a server restart.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Deque, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from app.config import settings
from app.ml.auto import AUTO_FEATURES, build_auto_inputs
from app.ml.features import FEATURE_NAMES, build_features, to_row
from app.ml.sequence import build_sequence
from app.schemas.market_data import SymbolState
from app.schemas.signals import SignalScore
from app.utils.time_utils import market_open_dt  # noqa: F401  (tests build bars relative to it)

logger = logging.getLogger(__name__)
_ET = ZoneInfo(settings.SESSION_TIMEZONE)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "ml"
MODEL_PATHS = {
    "nn": MODEL_DIR / "trade_filter_nn.pt",
    "gbm": MODEL_DIR / "trade_filter.joblib",
    "auto": MODEL_DIR / "auto_trader_nn.pt",
}

# kind → (mtime, meta, score_fn(features_row, sequence) -> prob)
_cache: Dict[str, Tuple[float, dict, Callable]] = {}

# Most recent scored candidates, for the /api/ml/status endpoint
recent_predictions: Deque[dict] = deque(maxlen=200)


def _minutes_since_open(bar_ts: datetime) -> float:
    """
    Minutes from that session's open to the completion of the bar stamped
    bar_ts. Measured against the bar's own trading day (not "today"), so
    readings stay correct after midnight and on replayed history.
    """
    et = bar_ts.astimezone(_ET)
    h, m = map(int, settings.MARKET_OPEN_TIME.split(":"))
    session_open = et.replace(hour=h, minute=m, second=0, microsecond=0)
    return (et + timedelta(minutes=1) - session_open).total_seconds() / 60.0


def _load_gbm(path: Path):
    import joblib
    bundle = joblib.load(path)
    model = bundle["model"]
    return bundle["meta"], lambda row, seq: float(model.predict_proba(np.array([row]))[0, 1])


def _load_nn(path: Path):
    import torch
    from app.ml.nn_model import NetConfig, TradeNet, standardize

    bundle = torch.load(path, map_location="cpu", weights_only=False)
    net = TradeNet(NetConfig(**bundle["config"]))
    net.load_state_dict(bundle["state_dict"])
    net.eval()
    mean, std = bundle["mean"], bundle["std"]

    def score(row, seq):
        if seq is None:
            return None
        with torch.no_grad():
            tab = standardize(torch.tensor([row], dtype=torch.float32), mean, std)
            out = torch.sigmoid(net(torch.from_numpy(seq[None]), tab))[0]
            return float(out) if out.ndim == 0 else out.tolist()

    def batch(rows, seqs) -> np.ndarray:
        """Score many inputs in one pass (used by explain_auto)."""
        with torch.no_grad():
            tab = standardize(torch.tensor(np.asarray(rows), dtype=torch.float32), mean, std)
            return torch.sigmoid(net(torch.from_numpy(np.asarray(seqs, dtype=np.float32)), tab)).numpy()

    score.batch = batch
    score.feature_mean = mean.numpy()
    return bundle["meta"], score


def _load(kind: Optional[str] = None) -> Optional[Tuple[dict, Callable]]:
    if kind is None:
        kind = settings.ML_MODEL if settings.ML_MODEL in ("nn", "gbm") else "nn"
    path = MODEL_PATHS[kind]
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    cached = _cache.get(kind)
    if cached is None or cached[0] != mtime:
        try:
            meta, fn = (_load_gbm if kind == "gbm" else _load_nn)(path)
            expected = AUTO_FEATURES if kind == "auto" else FEATURE_NAMES
            if meta["features"] != expected:
                logger.error("ML %s model feature list does not match the code — retrain.", kind)
                return None
            _cache[kind] = (mtime, meta, fn)
            logger.info("ML model loaded: %s (trained %s, threshold %.3f).",
                        kind, meta["trained_at"][:10], meta["threshold"])
        except Exception as exc:
            logger.error("Failed to load ML %s model: %s", kind, exc)
            return None
    _, meta, fn = _cache[kind]
    return meta, fn


def model_meta() -> Optional[dict]:
    loaded = _load()
    return loaded[0] if loaded else None


def threshold() -> Optional[float]:
    if settings.ML_MIN_PROB is not None:
        return settings.ML_MIN_PROB
    meta = model_meta()
    return meta["threshold"] if meta else None


def predict(state: SymbolState, sig: SignalScore, engine_scalp: bool) -> Optional[float]:
    """P(trade is profitable at exit) for this candidate, or None if unavailable."""
    if settings.ML_FILTER_MODE == "off":
        return None
    loaded = _load()
    if loaded is None:
        return None
    _, score_fn = loaded
    bars = state.bars_1m
    if len(bars) < 15 or not sig.price:
        return None

    o = np.fromiter((b.open for b in bars), float)
    h = np.fromiter((b.high for b in bars), float)
    l = np.fromiter((b.low for b in bars), float)
    c = np.fromiter((b.close for b in bars), float)
    v = np.fromiter((b.volume for b in bars), float)
    minutes_open = _minutes_since_open(bars[-1].timestamp)

    feats = build_features(
        o, h, l, c, v,
        direction=sig.direction,
        engine_score=sig.total_score,
        engine_scalp=engine_scalp,
        minutes_since_open=minutes_open,
        vwap=state.vwap,
        session_high=state.session_high if state.session_high is not None else float(h.max()),
        session_low=state.session_low if state.session_low is not None else float(l.min()),
        stop_price=sig.thesis.suggested_stop if sig.thesis else None,
    )
    if feats is None:
        return None
    prob = score_fn(to_row(feats), build_sequence(o, h, l, c, v, sig.direction))
    if prob is None:
        return None
    recent_predictions.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "symbol": sig.symbol,
        "engine": "scalp" if engine_scalp else "swing",
        "direction": sig.direction,
        "score": sig.total_score,
        "prob": round(prob, 4),
    })
    return prob


def allows(prob: Optional[float]) -> Tuple[bool, str]:
    """Whether the filter lets a candidate through. Never blocks in shadow/off mode."""
    thr = threshold()
    if prob is None or thr is None:
        return True, "ml_unavailable"
    if prob >= thr:
        return True, f"ml_pass p={prob:.2f}"
    if settings.ML_FILTER_MODE == "gate":
        return False, f"ml_below_threshold p={prob:.2f}<{thr:.2f}"
    return True, f"ml_shadow_would_block p={prob:.2f}<{thr:.2f}"


# ── Autonomous bot ────────────────────────────────────────────────────────────

# symbol → latest autonomous reading, refreshed once per completed bar
auto_readings: Dict[str, dict] = {}


def auto_meta() -> Optional[dict]:
    loaded = _load("auto")
    return loaded[0] if loaded else None


def _auto_inputs(state: SymbolState):
    """(feature_row, sequence, atr, closes) for the autonomous model, or None."""
    bars = state.bars_1m
    if len(bars) < 15:
        return None
    o = np.fromiter((b.open for b in bars), float)
    h = np.fromiter((b.high for b in bars), float)
    l = np.fromiter((b.low for b in bars), float)
    c = np.fromiter((b.close for b in bars), float)
    v = np.fromiter((b.volume for b in bars), float)
    minutes_open = _minutes_since_open(bars[-1].timestamp)
    inp = build_auto_inputs(
        o, h, l, c, v,
        minutes_since_open=minutes_open,
        vwap=state.vwap,
        session_high=state.session_high if state.session_high is not None else float(h.max()),
        session_low=state.session_low if state.session_low is not None else float(l.min()),
    )
    if inp is None:
        return None
    row, seq, atr = inp
    return row, seq, atr, c


def explain_auto(state: SymbolState) -> Optional[dict]:
    """
    Why the autonomous network reads this symbol the way it does.

    Occlusion attribution: each input is replaced by a neutral value (a feature
    by its training mean, a 10-bar window of the sequence by flat, zero-volume
    bars) and the change in P(long) / P(short) is measured. Positive "lean"
    means the input pushes the network toward a long.
    """
    loaded = _load("auto")
    if loaded is None:
        return None
    meta, score_fn = loaded
    if not hasattr(score_fn, "batch"):
        return None
    inp = _auto_inputs(state)
    if inp is None:
        return None
    row, seq, atr, closes = inp
    row = np.asarray(row, dtype=np.float32)
    mean = score_fn.feature_mean

    rows, seqs = [row], [seq]
    for i in range(len(row)):                       # each feature → its training mean
        r = row.copy(); r[i] = mean[i]; rows.append(r); seqs.append(seq)
    windows = [(i, i + 10) for i in range(0, 60, 10)]
    for a, b in windows:                            # each 10-bar window → flat bars
        sq = seq.copy(); sq[a:b, :-1] = 0.0; rows.append(row); seqs.append(sq)
    sq = seq.copy(); sq[:, :-1] = 0.0; rows.append(row); seqs.append(sq)          # whole sequence branch
    rows.append(mean.astype(np.float32)); seqs.append(seq)                         # whole feature branch

    p = score_fn.batch(rows, seqs)
    base = p[0]
    n_f = len(row)

    def lean(k):   # how much this input pushes toward long, in probability points
        d = base - p[k]
        return float(d[0] - d[1])

    from app.ml.auto import FEATURE_HELP
    features = [{
        "name": name,
        "label": FEATURE_HELP.get(name, (name, ""))[0],
        "help": FEATURE_HELP.get(name, (name, ""))[1],
        "value": None if not np.isfinite(row[i]) else round(float(row[i]), 4),
        "d_long": round(float(base[0] - p[1 + i][0]), 4),
        "d_short": round(float(base[1] - p[1 + i][1]), 4),
        "lean": round(lean(1 + i), 4),
    } for i, name in enumerate(AUTO_FEATURES)]
    seq_windows = [{
        "bars_ago": f"{60 - a}–{60 - b + 1}",
        "lean": round(lean(1 + n_f + j), 4),
    } for j, (a, b) in enumerate(windows)]
    k_seq, k_tab = 1 + n_f + len(windows), 2 + n_f + len(windows)
    return {
        "symbol": state.symbol,
        "p_long": round(float(base[0]), 4),
        "p_short": round(float(base[1]), 4),
        "threshold": meta["threshold"],
        "price": float(closes[-1]),
        "closes": [round(float(x), 4) for x in closes[-60:]],
        "branch_lean": {"sequence": round(lean(k_seq), 4), "features": round(lean(k_tab), 4)},
        "features": sorted(features, key=lambda f: -abs(f["lean"])),
        "sequence_windows": seq_windows,
        "model_trained_at": meta["trained_at"],
    }


def predict_auto(state: SymbolState) -> Optional[dict]:
    """
    {"p_long", "p_short", "atr", "price", "bar_time"} for one symbol, computed
    from raw bars only. Cached per completed bar, so calling every scoring
    cycle costs one forward pass per symbol per minute.
    """
    bars = state.bars_1m
    if len(bars) < 15:
        return None
    last = auto_readings.get(state.symbol)
    if last and last["bar_time"] == bars[-1].timestamp:
        return last
    loaded = _load("auto")
    if loaded is None:
        return None
    meta, score_fn = loaded
    inp = _auto_inputs(state)
    if inp is None:
        return None
    row, seq, atr, c = inp
    probs = score_fn(row, seq)
    if probs is None:
        return None
    reading = {
        "symbol": state.symbol,
        "p_long": round(probs[0], 4),
        "p_short": round(probs[1], 4),
        "atr": atr,
        "price": float(c[-1]),
        "bar_time": bars[-1].timestamp,
        "threshold": meta["threshold"],
    }
    auto_readings[state.symbol] = reading
    return reading


def status() -> Dict:
    meta = model_meta()
    return {
        "mode": settings.ML_FILTER_MODE,
        "model": settings.ML_MODEL,
        "model_loaded": meta is not None,
        "threshold": threshold(),
        "meta": meta,
        "recent": list(recent_predictions)[-50:][::-1],
        "auto": {
            "meta": auto_meta(),
            "readings": sorted(
                ({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items() if k != "atr"}
                 for r in auto_readings.values()),
                key=lambda r: -max(r["p_long"], r["p_short"]),
            ),
        },
    }
