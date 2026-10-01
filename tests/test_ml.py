"""
Tests for the ML trade filter: labelling and feature extraction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.ml.features import FEATURE_NAMES, build_features
from app.ml.labeling import triple_barrier
from app.ml.sequence import CHANNELS, N_CHANNELS, SEQ_LEN, build_sequence


def _arrays(closes):
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.05
    l = np.minimum(o, c) - 0.05
    v = np.full(len(c), 1000.0)
    return o, h, l, c, v


# ── Triple barrier ────────────────────────────────────────────────────────────

def test_long_hits_target():
    _, h, l, c, _ = _arrays([100, 100.5, 101, 102.5])
    win, r, outcome = triple_barrier(h, l, c, 0, 1, 100.0, stop=99.0, target=102.0)
    assert (win, outcome) == (1, "target")
    assert r == 2.0


def test_short_hits_stop():
    _, h, l, c, _ = _arrays([100, 100.5, 101.2])
    win, r, outcome = triple_barrier(h, l, c, 0, -1, 100.0, stop=101.0, target=98.0)
    assert (win, r, outcome) == (0, -1.0, "stop")


def test_same_bar_counts_as_stop():
    h = np.array([100.0, 103.0])
    l = np.array([100.0, 98.0])
    c = np.array([100.0, 100.0])
    assert triple_barrier(h, l, c, 0, 1, 100.0, stop=99.0, target=102.0)[2] == "stop"


def test_timeout_uses_last_close():
    _, h, l, c, _ = _arrays([100, 100.2, 100.4, 100.5])
    win, r, outcome = triple_barrier(h, l, c, 0, 1, 100.0, stop=99.0, target=102.0, horizon=3)
    assert outcome == "timeout"
    assert abs(r - 0.5) < 1e-9


def test_entry_bar_is_not_future():
    # Entry bar itself spikes through the target — must not count
    h = np.array([105.0, 100.1])
    l = np.array([99.9, 99.9])
    c = np.array([100.0, 100.0])
    assert triple_barrier(h, l, c, 0, 1, 100.0, stop=99.0, target=102.0)[2] == "timeout"


# ── Features ──────────────────────────────────────────────────────────────────

def _feats(closes, direction):
    o, h, l, c, v = _arrays(closes)
    return build_features(
        o, h, l, c, v, direction=direction, engine_score=60, engine_scalp=True,
        minutes_since_open=60, vwap=float(c.mean()),
        session_high=float(h.max()), session_low=float(l.min()), stop_price=c[-1] - 0.5,
    )


def test_feature_names_complete():
    f = _feats(np.linspace(100, 101, 40), 1)
    assert list(f) == FEATURE_NAMES


def test_too_little_history_returns_none():
    assert _feats([100] * 10, 1) is None


def test_directional_features_flip_sign():
    up = np.linspace(100, 102, 40)
    long_f, short_f = _feats(up, 1), _feats(up, -1)
    for k in ("ret_5_atr", "ret_15_atr", "vwap_dist_atr", "ema9_21_atr", "rsi14_signed"):
        assert long_f[k] > 0
        assert abs(long_f[k] + short_f[k]) < 1e-9
    assert long_f["range_pos"] + short_f["range_pos"] == 1.0


def test_live_predictor_matches_offline_features():
    """predictor.predict() must build the same row as the training pipeline."""
    from app.ml import predictor
    from app.schemas.market_data import Bar, SymbolState
    from app.schemas.signals import SignalColor, SignalScore, TradeLabel, TradingThesis

    o, h, l, c, v = _arrays(np.linspace(100, 101, 40) + np.sin(np.arange(40)) * 0.2)
    t0 = predictor.market_open_dt().astimezone(timezone.utc)
    state = SymbolState(symbol="TEST")
    state.bars_1m = [
        Bar(timestamp=t0 + timedelta(minutes=i), open=o[i], high=h[i], low=l[i], close=c[i], volume=int(v[i]))
        for i in range(40)
    ]
    state.vwap, state.session_high, state.session_low = 100.4, float(h.max()), float(l.min())
    sig = SignalScore(
        symbol="TEST", scored_at=datetime.now(timezone.utc), total_score=63.0, direction=1,
        color=SignalColor.GREEN, label=TradeLabel.POSSIBLE_TRADE, price=c[-1], vwap=state.vwap,
        thesis=TradingThesis(direction="long", confidence=63, why_now="", suggested_stop=c[-1] - 0.6),
    )
    expected = build_features(
        o, h, l, c, v, direction=1, engine_score=63.0, engine_scalp=True, minutes_since_open=40,
        vwap=100.4, session_high=float(h.max()), session_low=float(l.min()), stop_price=c[-1] - 0.6,
    )

    captured = {}

    def _score(row, seq):
        captured["row"], captured["seq"] = row, seq
        return 0.6

    meta = {"features": FEATURE_NAMES, "threshold": 0.5, "trained_at": "test"}
    original = predictor._load
    predictor._load = lambda: (meta, _score)  # type: ignore[assignment]
    try:
        assert predictor.predict(state, sig, engine_scalp=True) == 0.6
    finally:
        predictor._load = original
    np.testing.assert_allclose(captured["row"], [expected[k] for k in FEATURE_NAMES])
    np.testing.assert_allclose(captured["seq"], build_sequence(o, h, l, c, v, 1))


# ── Sequences / neural net ────────────────────────────────────────────────────

def test_sequence_shape_and_padding():
    o, h, l, c, v = _arrays(np.linspace(100, 101, 20))
    s = build_sequence(o, h, l, c, v, 1)
    assert s.shape == (SEQ_LEN, N_CHANNELS) and s.dtype == np.float32
    mask = s[:, CHANNELS.index("mask")]
    assert mask.sum() == 20 and mask[-1] == 1 and mask[0] == 0


def test_sequence_mirrors_for_shorts():
    o, h, l, c, v = _arrays(np.linspace(100, 102, 70))
    long_s, short_s = build_sequence(o, h, l, c, v, 1), build_sequence(o, h, l, c, v, -1)
    ret, body = CHANNELS.index("ret"), CHANNELS.index("body")
    np.testing.assert_allclose(long_s[:, ret], -short_s[:, ret], atol=1e-6)
    np.testing.assert_allclose(long_s[:, body], -short_s[:, body], atol=1e-6)
    up, dn = CHANNELS.index("upper_wick"), CHANNELS.index("lower_wick")
    np.testing.assert_allclose(long_s[:, up], short_s[:, dn], atol=1e-6)


def test_nn_save_load_round_trip(tmp_path):
    import torch
    from app.ml import predictor
    from app.ml.nn_model import NetConfig, TradeNet

    torch.manual_seed(0)
    net = TradeNet(NetConfig())
    net.eval()
    mean, std = torch.zeros(len(FEATURE_NAMES)), torch.ones(len(FEATURE_NAMES))
    path = tmp_path / "nn.pt"
    torch.save({"state_dict": net.state_dict(), "config": net.cfg.to_dict(), "mean": mean, "std": std,
                "meta": {"features": FEATURE_NAMES, "threshold": 0.5, "trained_at": "test"}}, path)

    _, score = predictor._load_nn(path)
    o, h, l, c, v = _arrays(np.linspace(100, 101, 40))
    f = _feats(np.linspace(100, 101, 40), 1)
    seq = build_sequence(o, h, l, c, v, 1)
    p = score([f[k] for k in FEATURE_NAMES], seq)
    with torch.no_grad():
        tab = torch.nan_to_num(torch.tensor([[f[k] for k in FEATURE_NAMES]], dtype=torch.float32))
        expected = float(torch.sigmoid(net(torch.from_numpy(seq[None]), tab))[0])
    assert 0.0 < p < 1.0
    assert abs(p - expected) < 1e-6


# ── Autonomous bot ────────────────────────────────────────────────────────────

def test_trade_levels_use_min_stop_and_2r_target():
    from app.ml.auto import trade_levels
    stop, tgt = trade_levels(100.0, atr=0.1, direction=1)      # 1.5×ATR = 0.15 < 0.6% floor
    assert (stop, tgt) == (99.4, 101.2)
    stop, tgt = trade_levels(100.0, atr=1.0, direction=-1)     # 1.5×ATR = 1.5
    assert (stop, tgt) == (101.5, 97.0)


def test_auto_inputs_exclude_engine_features():
    from app.ml.auto import AUTO_FEATURES, build_auto_inputs
    assert not {"engine_score", "engine_scalp", "direction"} & set(AUTO_FEATURES)
    o, h, l, c, v = _arrays(np.linspace(100, 101, 40))
    row, seq, atr = build_auto_inputs(o, h, l, c, v, minutes_since_open=40, vwap=100.5,
                                      session_high=float(h.max()), session_low=float(l.min()))
    assert len(row) == len(AUTO_FEATURES) and seq.shape == (SEQ_LEN, N_CHANNELS) and atr > 0


def test_predict_auto_two_outputs_and_caches_per_bar(tmp_path, monkeypatch):
    import torch
    from app.ml import predictor
    from app.ml.auto import AUTO_FEATURES
    from app.ml.nn_model import NetConfig, TradeNet
    from app.schemas.market_data import Bar, SymbolState

    torch.manual_seed(0)
    cfg = NetConfig(n_tab=len(AUTO_FEATURES), n_out=2)
    net = TradeNet(cfg)
    path = tmp_path / "auto.pt"
    torch.save({"state_dict": net.state_dict(), "config": cfg.to_dict(),
                "mean": torch.zeros(cfg.n_tab), "std": torch.ones(cfg.n_tab),
                "meta": {"features": AUTO_FEATURES, "threshold": 0.55, "trained_at": "test"}}, path)
    monkeypatch.setitem(predictor.MODEL_PATHS, "auto", path)
    monkeypatch.setattr(predictor, "_cache", {})
    monkeypatch.setattr(predictor, "auto_readings", {})

    o, h, l, c, v = _arrays(np.linspace(100, 101, 40))
    t0 = predictor.market_open_dt().astimezone(timezone.utc)
    state = SymbolState(symbol="TEST")
    state.bars_1m = [Bar(timestamp=t0 + timedelta(minutes=i), open=o[i], high=h[i], low=l[i], close=c[i],
                         volume=int(v[i])) for i in range(40)]
    r = predictor.predict_auto(state)
    assert 0 < r["p_long"] < 1 and 0 < r["p_short"] < 1 and r["threshold"] == 0.55
    assert predictor.predict_auto(state) is r            # same bar → cached, no new forward pass


def test_minutes_since_open_uses_the_bars_own_session():
    """After midnight ET the bars still belong to yesterday's session (regression)."""
    from zoneinfo import ZoneInfo
    from app.ml.predictor import _minutes_since_open
    et = ZoneInfo("America/New_York")
    assert _minutes_since_open(datetime(2026, 9, 30, 9, 30, tzinfo=et)) == 1.0      # first bar completes 9:31
    assert _minutes_since_open(datetime(2026, 9, 30, 15, 59, tzinfo=et)) == 390.0   # last bar of the day
    assert _minutes_since_open(datetime(2026, 9, 30, 19, 59, tzinfo=timezone.utc)) == 390.0  # UTC stamps too
