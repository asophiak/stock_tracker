"""
Neural trade filter: GRU over the raw bar sequence + MLP over engineered
features, fused into a single P(profitable) output.

    bars (60×7) ──► GRU ──► last hidden ─┐
                                         ├─► MLP head ──► logit
    features (21) ──► MLP ───────────────┘

The same network serves two roles: the trade filter (n_out=1, scores a
candidate the rule engines proposed) and the autonomous bot (n_out=2, reads
raw market state and outputs P(long profitable), P(short profitable)).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn

from app.ml.features import FEATURE_NAMES
from app.ml.sequence import N_CHANNELS


@dataclass
class NetConfig:
    n_tab: int = len(FEATURE_NAMES)
    n_seq: int = N_CHANNELS
    gru_hidden: int = 48
    gru_layers: int = 1
    tab_hidden: int = 64
    head_hidden: int = 64
    dropout: float = 0.25
    n_out: int = 1          # 1 = filter (P profitable); 2 = autonomous (P long, P short)

    def to_dict(self) -> dict:
        return asdict(self)


class TradeNet(nn.Module):
    def __init__(self, cfg: NetConfig):
        super().__init__()
        self.cfg = cfg
        self.gru = nn.GRU(cfg.n_seq, cfg.gru_hidden, num_layers=cfg.gru_layers, batch_first=True)
        self.seq_norm = nn.LayerNorm(cfg.gru_hidden)
        self.tab = nn.Sequential(
            nn.Linear(cfg.n_tab, cfg.tab_hidden),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(cfg.tab_hidden, cfg.tab_hidden),
            nn.GELU(),
        )
        self.head = nn.Sequential(
            nn.Dropout(cfg.dropout),
            nn.Linear(cfg.gru_hidden + cfg.tab_hidden, cfg.head_hidden),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(cfg.head_hidden, cfg.n_out),
        )

    def forward(self, seq: torch.Tensor, tab: torch.Tensor) -> torch.Tensor:
        _, h = self.gru(seq)                 # h: (layers, batch, hidden)
        s = self.seq_norm(h[-1])
        t = self.tab(tab)
        out = self.head(torch.cat([s, t], dim=1))
        return out.squeeze(1) if self.cfg.n_out == 1 else out


def standardize(x, mean, std):
    """Feature scaling used in training and live; NaNs become the mean (0)."""
    z = (x - mean) / std
    z[~torch.isfinite(z)] = 0.0
    return z
