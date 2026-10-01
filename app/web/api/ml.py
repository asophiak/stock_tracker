"""ML endpoints: trade-filter status and the /ml dashboard payload."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_state_manager
from app.ml import predictor
from app.utils.cache import StateManager

router = APIRouter()
logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "models" / "ml"
HISTORY = MODEL_DIR / "history.json"
WALKFORWARD = MODEL_DIR / "walkforward.json"

# strategy_version → bot shown on the dashboard
_BOT_SQL = """
    CASE
        WHEN strategy_version = 'nn'    THEN 'Neural net'
        WHEN strategy_version = 'scalp' THEN 'Scalp bot'
        ELSE 'Swing bot'
    END
"""


@router.get("/api/ml/status")
async def ml_status():
    """Filter mode, model metadata (incl. held-out test metrics) and recent predictions."""
    return predictor.status()


def _param_count(meta: dict | None) -> int | None:
    if not meta:
        return None
    try:
        from app.ml.nn_model import NetConfig, TradeNet
        cfg = NetConfig(n_tab=len(meta["features"]), n_out=2 if meta.get("kind") == "auto" else 1)
        return sum(p.numel() for p in TradeNet(cfg).parameters())
    except Exception:
        return None


@router.get("/api/ml/explain")
async def ml_explain(symbol: str | None = None, sm: StateManager = Depends(get_state_manager)):
    """Live inputs + per-input attribution of the autonomous network for one symbol."""
    from fastapi import HTTPException
    if not symbol:
        if not predictor.auto_readings:       # e.g. right after a restart — score everything once
            for sym in sm.all_symbols():
                if sym not in ("SPY", "QQQ"):
                    try:
                        predictor.predict_auto(sm.get_state(sym))
                    except Exception as exc:
                        logger.warning("NN reading failed for %s: %s", sym, exc)
        readings = predictor.auto_readings.values()
        if not readings:
            raise HTTPException(404, "No readings yet")
        symbol = max(readings, key=lambda r: max(r["p_long"], r["p_short"]))["symbol"]
    out = predictor.explain_auto(sm.get_state(symbol.upper()))
    if out is None:
        raise HTTPException(404, f"Not enough data to explain {symbol.upper()}")
    out["symbols"] = sorted(s for s in sm.all_symbols() if s not in ("SPY", "QQQ"))
    return out


@router.get("/api/ml/dashboard")
async def ml_dashboard(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    # Refresh the network's view of every symbol (cached per completed bar, so
    # this is cheap) — keeps the live readings populated outside the bot loop,
    # e.g. after the close.
    for sym in sm.all_symbols():
        if sym in ("SPY", "QQQ"):
            continue
        try:
            predictor.predict_auto(sm.get_state(sym))
        except Exception as exc:
            logger.warning("NN reading failed for %s: %s", sym, exc)

    since = (date.today() - timedelta(days=days)).isoformat()

    by_bot = (await db.execute(text(f"""
        SELECT {_BOT_SQL} AS bot,
               COUNT(*)                          AS trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(pnl), 2)                AS pnl,
               ROUND(AVG(pnl), 2)                AS avg_pnl
        FROM closed_trades
        WHERE session_date >= :since AND COALESCE(strategy_version, '') != 'alpaca_import'
        GROUP BY bot
    """), {"since": since})).mappings().all()

    daily = (await db.execute(text(f"""
        SELECT session_date, {_BOT_SQL} AS bot, ROUND(SUM(pnl), 2) AS pnl
        FROM closed_trades
        WHERE session_date >= :since AND COALESCE(strategy_version, '') != 'alpaca_import'
        GROUP BY session_date, bot
        ORDER BY session_date
    """), {"since": since})).mappings().all()

    nn_trades = (await db.execute(text("""
        SELECT symbol, side, entry_price, exit_price, pnl, pnl_pct, opened_at, closed_at, close_reason,
               json_extract(entry_signal_json, '$.ml_prob') AS prob
        FROM closed_trades WHERE strategy_version = 'nn'
        ORDER BY closed_at DESC LIMIT 25
    """))).mappings().all()

    nn_open = (await db.execute(text("""
        SELECT symbol, side, qty, avg_entry_price, current_price, unrealized_pnl, stop_price,
               take_profit_price, opened_at, json_extract(entry_signal_json, '$.ml_prob') AS prob
        FROM paper_positions WHERE strategy_version = 'nn'
    """))).mappings().all()

    try:
        history = json.loads(HISTORY.read_text())[-30:]
    except (FileNotFoundError, ValueError):
        history = []

    try:
        wf = json.loads(WALKFORWARD.read_text())
        walkforward = {k: wf[k] for k in ("generated_at", "source", "cost_bps", "samples", "sessions",
                                          "n_sessions", "summary")}
        walkforward["folds"] = [
            {"test": f["test"], "random": f["random_avg_r"],
             "nn_top10": f["nn"]["top_k"]["10"]["avg_r"], "gbm_top10": f["gbm"]["top_k"]["10"]["avg_r"],
             "nn_auc": (f["nn"]["auc_long"] + f["nn"]["auc_short"]) / 2}
            for f in wf["folds"]
        ]
    except (FileNotFoundError, ValueError, KeyError):
        walkforward = None

    status = predictor.status()
    auto_meta = status["auto"]["meta"]
    return {
        "settings": {
            "nn_bot_enabled": settings.ML_NN_BOT_ENABLED,
            "filter_mode": settings.ML_FILTER_MODE,
            "filter_model": settings.ML_MODEL,
            "retrain_enabled": settings.ML_RETRAIN_ENABLED,
            "retrain_time_et": settings.ML_RETRAIN_TIME,
            "max_positions": settings.ML_NN_MAX_POSITIONS,
            "max_hold_minutes": settings.ML_NN_MAX_HOLD_MINUTES,
        },
        "auto": {**status["auto"], "params": _param_count(auto_meta)},
        "filter": {"meta": status["meta"], "threshold": status["threshold"], "recent": status["recent"][:20],
                   "params": _param_count(status["meta"]) if settings.ML_MODEL == "nn" else None},
        "bots": [dict(r) for r in by_bot],
        "daily": [dict(r) for r in daily],
        "nn_trades": [dict(r) for r in nn_trades],
        "nn_open": [dict(r) for r in nn_open],
        "history": history,
        "walkforward": walkforward,
    }
