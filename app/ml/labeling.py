"""
Triple-barrier labelling: walk forward from the entry bar and record whether
the take-profit or the stop was touched first, or neither within the horizon.

If a single bar touches both, it is counted as a stop (conservative — 1m bars
don't say which came first).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

HORIZON_BARS = 60


def triple_barrier(
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    entry_idx: int,
    direction: int,
    entry_price: float,
    stop: float,
    target: float,
    horizon: int = HORIZON_BARS,
) -> Tuple[int, float, str]:
    """
    Returns (win, r_multiple, outcome) where outcome is
    "target" | "stop" | "timeout". Bars after entry_idx are the future.
    """
    risk = abs(entry_price - stop)
    if risk <= 0:
        return 0, 0.0, "invalid"
    end = min(len(c), entry_idx + 1 + horizon)
    for j in range(entry_idx + 1, end):
        if direction == 1:
            hit_stop, hit_tgt = l[j] <= stop, h[j] >= target
        else:
            hit_stop, hit_tgt = h[j] >= stop, l[j] <= target
        if hit_stop:
            return 0, -1.0, "stop"
        if hit_tgt:
            return 1, abs(target - entry_price) / risk, "target"
    last = c[end - 1] if end > entry_idx + 1 else entry_price
    return 0, (last - entry_price) * direction / risk, "timeout"
