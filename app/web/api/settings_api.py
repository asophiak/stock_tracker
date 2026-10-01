"""Settings, kill-switch, and live-trading setup endpoints."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.dependencies import get_state_manager
from app.schemas.settings import KillSwitchRequest, SettingsResponse, SettingsUpdateRequest
from app.utils.cache import StateManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_ENV_PATH = Path(".env")


# ── .env writer helpers ───────────────────────────────────────────────────────

def _read_env() -> str:
    return _ENV_PATH.read_text() if _ENV_PATH.exists() else ""


def _set_env_var(content: str, key: str, value: str) -> str:
    """
    Set or add a KEY=value line in .env content.
    Preserves comments and surrounding lines.
    """
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(content):
        return pattern.sub(line, content)
    # Not found — append
    return content.rstrip("\n") + f"\n{line}\n"


def _write_env(content: str) -> None:
    _ENV_PATH.write_text(content)


class LiveKeysRequest(BaseModel):
    api_key: str
    api_secret: str
    live: bool = True       # True = live account, False = paper account


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(sm: StateManager = Depends(get_state_manager)):
    return SettingsResponse(
        watchlist=settings.watchlist_symbols,
        max_trades_per_day=sm.max_trades_per_day,
        daily_loss_limit=settings.DAILY_LOSS_LIMIT,
        per_trade_risk_pct=settings.PER_TRADE_RISK_PCT,
        execution_mode=sm.execution_mode,
        auto_paper_execution=sm.auto_paper_execution,
        kill_switch=sm.kill_switch,
        signal_score_flash_threshold=settings.SIGNAL_SCORE_FLASH_THRESHOLD,
        signal_score_trade_threshold=settings.SIGNAL_SCORE_TRADE_THRESHOLD,
        signal_score_watch_threshold=settings.SIGNAL_SCORE_WATCH_THRESHOLD,
        opening_range_minutes=settings.OPENING_RANGE_MINUTES,
        alert_cooldown_seconds=settings.ALERT_COOLDOWN_SECONDS,
        telegram_enabled=bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        discord_enabled=bool(settings.DISCORD_WEBHOOK_URL),
        alpaca_connected=settings.alpaca_credentials_present,
        paper_capital=sm.paper_capital,
    )


@router.post("/settings")
async def update_settings(
    req: SettingsUpdateRequest,
    sm: StateManager = Depends(get_state_manager),
):
    """
    Update runtime settings. Only a subset can be changed at runtime
    (full settings require app restart via .env changes).
    """
    if req.execution_mode is not None:
        safe_modes = ("disabled", "paper_local", "paper_alpaca")
        if req.execution_mode not in safe_modes and not settings.LIVE_TRADING_ENABLED:
            raise HTTPException(
                status_code=403,
                detail="Live trading must be explicitly enabled via LIVE_TRADING_ENABLED=true in .env",
            )
        sm.execution_mode = req.execution_mode

    if req.auto_paper_execution is not None:
        sm.auto_paper_execution = req.auto_paper_execution

    if req.paper_capital is not None:
        sm.paper_capital = req.paper_capital

    if req.max_trades_per_day is not None:
        sm.max_trades_per_day = req.max_trades_per_day

    return {
        "status": "updated",
        "execution_mode": sm.execution_mode,
        "auto_paper_execution": sm.auto_paper_execution,
        "paper_capital": sm.paper_capital,
        "max_trades_per_day": sm.max_trades_per_day,
    }


@router.post("/settings/live-keys")
async def save_live_keys(
    req: LiveKeysRequest,
    sm: StateManager = Depends(get_state_manager),
):
    """
    Write Alpaca API credentials to .env and enable live trading mode.
    Requires an app restart to take effect (provider initialised at startup).
    """
    if not req.api_key or not req.api_secret:
        raise HTTPException(status_code=400, detail="api_key and api_secret are required")
    if len(req.api_key) < 10 or len(req.api_secret) < 10:
        raise HTTPException(status_code=400, detail="Keys look too short — paste the full key/secret from Alpaca")

    content = _read_env()
    content = _set_env_var(content, "ALPACA_API_KEY",    req.api_key)
    content = _set_env_var(content, "ALPACA_API_SECRET", req.api_secret)

    if req.live:
        content = _set_env_var(content, "ALPACA_BASE_URL",       "https://api.alpaca.markets")
        content = _set_env_var(content, "LIVE_TRADING_ENABLED",  "true")
        content = _set_env_var(content, "EXECUTION_MODE",        "live")
        sm.execution_mode = "live"
    else:
        content = _set_env_var(content, "ALPACA_BASE_URL",       "https://paper-api.alpaca.markets")
        content = _set_env_var(content, "LIVE_TRADING_ENABLED",  "false")
        content = _set_env_var(content, "EXECUTION_MODE",        "paper_alpaca")
        sm.execution_mode = "paper_alpaca"

    _write_env(content)

    # Clear the lru_cache so next read picks up new values
    from app.config import get_settings
    get_settings.cache_clear()

    logger.info("Alpaca credentials saved to .env (live=%s). Restart required.", req.live)
    return {
        "status":           "saved",
        "live":             req.live,
        "restart_required": True,
        "message":          "Credentials saved. Restart the app to connect to Alpaca.",
    }


@router.get("/account")
async def get_account_info(sm: StateManager = Depends(get_state_manager)):
    """
    Return live Alpaca account balance when in live mode.
    Falls back to a summary of paper capital otherwise.
    """
    from app.config import get_settings as _get_settings
    cfg = _get_settings()

    if sm.execution_mode == "live" and cfg.LIVE_TRADING_ENABLED and cfg.alpaca_credentials_present:
        try:
            from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
            adapter = AlpacaLiveExecutionAdapter()
            acct = await adapter.get_account()
            return {"mode": "live", **acct}
        except Exception as exc:
            logger.warning("Account fetch failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Alpaca account fetch failed: {exc}")

    if sm.execution_mode in ("paper_alpaca",) and cfg.alpaca_credentials_present:
        try:
            from app.providers.alpaca.trading import AlpacaTradingProvider
            provider = AlpacaTradingProvider(paper=True)
            acct = await provider.get_account()
            return {"mode": "paper_alpaca", **acct}
        except Exception as exc:
            logger.warning("Paper account fetch failed: %s", exc)

    return {
        "mode":          sm.execution_mode,
        "equity":        sm.paper_capital,
        "buying_power":  sm.bot_cash,
        "cash":          sm.bot_cash,
        "currency":      "USD",
        "status":        "simulated",
    }


@router.post("/kill-switch")
async def kill_switch(
    req: KillSwitchRequest,
    sm: StateManager = Depends(get_state_manager),
):
    sm.kill_switch = req.enabled
    status = "ENGAGED" if req.enabled else "DISENGAGED"
    reason = req.reason or "no reason given"
    import logging
    logging.getLogger(__name__).warning("KILL SWITCH %s: %s", status, reason)
    return {"kill_switch": sm.kill_switch, "status": status}
