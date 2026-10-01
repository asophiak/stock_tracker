"""
Pydantic schemas for settings API.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class WatchlistUpdateRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1)


class SettingsUpdateRequest(BaseModel):
    watchlist: Optional[List[str]] = None
    max_trades_per_day: Optional[int] = Field(None, ge=0)   # 0 = unlimited
    daily_loss_limit: Optional[float] = Field(None, gt=0)
    per_trade_risk_pct: Optional[float] = Field(None, gt=0, le=10)
    execution_mode: Optional[str] = None
    auto_paper_execution: Optional[bool] = None
    paper_capital: Optional[float] = Field(None, gt=0, le=10_000_000)
    signal_score_flash_threshold: Optional[int] = Field(None, ge=50, le=100)
    signal_score_trade_threshold: Optional[int] = Field(None, ge=40, le=100)
    signal_score_watch_threshold: Optional[int] = Field(None, ge=30, le=100)
    opening_range_minutes: Optional[int] = Field(None, ge=1, le=60)
    alert_cooldown_seconds: Optional[int] = Field(None, ge=30, le=3600)
    telegram_enabled: Optional[bool] = None
    discord_enabled: Optional[bool] = None


class SettingsResponse(BaseModel):
    watchlist: List[str]
    max_trades_per_day: int
    daily_loss_limit: float
    per_trade_risk_pct: float
    execution_mode: str
    auto_paper_execution: bool
    kill_switch: bool
    signal_score_flash_threshold: int
    signal_score_trade_threshold: int
    signal_score_watch_threshold: int
    opening_range_minutes: int
    alert_cooldown_seconds: int
    telegram_enabled: bool
    discord_enabled: bool
    alpaca_connected: bool
    paper_capital: float


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: Optional[str] = None
