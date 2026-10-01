"""
Structured logging configuration for the application.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from app.config import settings


class ColorFormatter(logging.Formatter):
    """Console formatter with ANSI color codes by log level."""

    COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


def setup_logging() -> None:
    """Configure root logger and suppress noisy third-party loggers."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    date_fmt = "%H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter(fmt, datefmt=date_fmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet down chatty libraries
    for noisy in (
        "uvicorn.access",
        "websockets.client",
        "websockets.server",
        "httpx",
        "httpcore",
        "aiohttp",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
