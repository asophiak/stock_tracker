"""
Alert deduplication and dispatch service.

Tracks which alerts have been sent (per symbol, per session) and
enforces cooldown windows to prevent spam.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from app.config import settings
from app.schemas.signals import SignalScore, TradeLabel
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)

# (symbol, session_date) → (last_sent_at, last_label)
_alert_state: Dict[Tuple[str, str], Tuple[datetime, str]] = {}


def _should_send(symbol: str, label: str) -> bool:
    """Return True if alert should fire (not in cooldown, or label upgraded)."""
    key = (symbol, session_date_str())
    now = datetime.now(timezone.utc)

    if key not in _alert_state:
        return True

    last_sent, last_label = _alert_state[key]
    elapsed = (now - last_sent).total_seconds()

    # Always alert on label upgrade (e.g. WATCH → IMMEDIATE_TRADE)
    label_rank = {
        "NO_TRADE": 0, "WATCH": 1, "POSSIBLE_TRADE": 2, "IMMEDIATE_TRADE": 3
    }
    if label_rank.get(label, 0) > label_rank.get(last_label, 0):
        return True

    # Cooldown
    return elapsed >= settings.ALERT_COOLDOWN_SECONDS


def _record_sent(symbol: str, label: str) -> None:
    key = (symbol, session_date_str())
    _alert_state[key] = (datetime.now(timezone.utc), label)


def format_alert_message(sig: SignalScore) -> str:
    direction_str = "LONG" if sig.direction == 1 else "SHORT"
    lines = [
        f"{'🟢' if sig.direction == 1 else '🔴'} {sig.color.value} — {sig.symbol}",
        f"Direction: {direction_str}  |  Score: {sig.total_score:.0f}/100  |  Label: {sig.label.value}",
        f"Price: {sig.price:.2f}" if sig.price else "Price: N/A",
    ]

    if sig.thesis:
        th = sig.thesis
        lines.append(f"Why now: {th.why_now}")
        if th.technical_evidence:
            lines.append("Technical: " + " | ".join(th.technical_evidence[:2]))
        if th.news_evidence:
            lines.append("News: " + " | ".join(th.news_evidence[:1]))
        if th.invalidation:
            lines.append(f"Invalidation: {th.invalidation}")
        if th.suggested_stop:
            lines.append(f"Stop: {th.suggested_stop:.2f}  Target: {th.suggested_target:.2f}" if th.suggested_target else f"Stop: {th.suggested_stop:.2f}")
        if th.risk_note:
            lines.append(f"Risk: {th.risk_note}")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"Time: {now_str}")
    return "\n".join(lines)


async def maybe_send_alert(
    sig: SignalScore,
    alert_dispatcher,
) -> None:
    """Check if an alert should be sent and dispatch it."""
    if sig.label not in (TradeLabel.POSSIBLE_TRADE, TradeLabel.IMMEDIATE_TRADE):
        return

    if not _should_send(sig.symbol, sig.label.value):
        return

    message = format_alert_message(sig)
    _record_sent(sig.symbol, sig.label.value)

    await alert_dispatcher.dispatch(
        symbol=sig.symbol,
        message=message,
        alert_type=sig.color.value.lower(),
        score=sig.total_score,
        direction="long" if sig.direction == 1 else "short",
    )
