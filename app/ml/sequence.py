"""
Raw bar-sequence input for the neural trade filter.

Encodes the last SEQ_LEN completed 1m bars as a (SEQ_LEN, N_CHANNELS) array the
GRU reads directly. Prices are mirrored for shorts (p → −p) so "up" always
means "in favour of the trade", and everything is scaled by the ATR at the
decision bar so the network sees shape, not price level.

Shared by training (scripts/ml_train_nn.py) and live scoring (predictor.py).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from app.ml.features import atr14

SEQ_LEN = 60
CHANNELS = ["ret", "body", "upper_wick", "lower_wick", "range", "log_vol_rel", "mask"]
N_CHANNELS = len(CHANNELS)


def build_sequence(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    direction: int,
) -> Optional[np.ndarray]:
    """Arrays end at the decision bar. Returns float32 (SEQ_LEN, N_CHANNELS) or None."""
    o, h, l, c, v = (np.asarray(a[-(SEQ_LEN + 1):], dtype=float) for a in (o, h, l, c, v))
    if len(c) < 15:
        return None
    atr = atr14(h, l, c)
    if not atr:
        return None

    if direction < 0:   # mirror so the trade direction is always "up"
        o, h, l, c = -o, -l, -h, -c

    prev_c = np.r_[o[0], c[:-1]]
    top = np.maximum(o, c)
    bot = np.minimum(o, c)
    vol_mean = v.mean() if v.mean() > 0 else 1.0

    feats = np.stack([
        (c - prev_c) / atr,
        (c - o) / atr,
        (h - top) / atr,
        (bot - l) / atr,
        (h - l) / atr,
        np.log1p(v / vol_mean),
        np.ones(len(c)),
    ], axis=1)[-SEQ_LEN:]
    feats = np.clip(feats, -10.0, 10.0)

    out = np.zeros((SEQ_LEN, N_CHANNELS), dtype=np.float32)
    out[SEQ_LEN - len(feats):] = feats   # left-pad short sessions; mask channel marks real bars
    return out
