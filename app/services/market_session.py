"""
Market session state service.

Tracks the current session phase and triggers session-boundary events
(opening-range set, EOD flatten warning, post-close summary).
"""
from __future__ import annotations

import logging
from enum import Enum

from app.utils.time_utils import (
    is_afterhours,
    is_market_open,
    is_opening_range_active,
    is_premarket,
    minutes_until_close,
    now_et,
)

logger = logging.getLogger(__name__)


class SessionPhase(str, Enum):
    CLOSED = "closed"
    PREMARKET = "premarket"
    OPENING_RANGE = "opening_range"     # first N minutes of regular session
    REGULAR = "regular"
    POWER_HOUR = "power_hour"           # last 60 minutes
    EOD_APPROACH = "eod_approach"       # last 15 minutes — flatten soon
    AFTERHOURS = "afterhours"


def get_session_phase() -> SessionPhase:
    if is_premarket():
        return SessionPhase.PREMARKET
    if not is_market_open():
        if is_afterhours():
            return SessionPhase.AFTERHOURS
        return SessionPhase.CLOSED
    if is_opening_range_active():
        return SessionPhase.OPENING_RANGE
    mins_left = minutes_until_close()
    if mins_left <= 10:
        return SessionPhase.EOD_APPROACH
    if mins_left <= 60:
        return SessionPhase.POWER_HOUR
    return SessionPhase.REGULAR


def should_flatten_positions() -> bool:
    """True during EOD approach — paper engine should close all positions."""
    return get_session_phase() == SessionPhase.EOD_APPROACH


def should_score_signals() -> bool:
    """True during phases where signals are meaningful."""
    phase = get_session_phase()
    return phase in (
        SessionPhase.OPENING_RANGE,
        SessionPhase.REGULAR,
        SessionPhase.POWER_HOUR,
        SessionPhase.EOD_APPROACH,
    )


def session_status_dict() -> dict:
    phase = get_session_phase()
    now = now_et()
    return {
        "phase": phase.value,
        "is_open": is_market_open(),
        "is_premarket": is_premarket(),
        "is_afterhours": is_afterhours(),
        "minutes_until_close": round(minutes_until_close(), 1),
        "current_time_et": now.strftime("%H:%M:%S"),
        "should_score": should_score_signals(),
        "should_flatten": should_flatten_positions(),
    }
