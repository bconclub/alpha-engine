"""Helpers and logging setup."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Create a logger with console + standardised format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)-20s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """ISO-8601 timestamp string."""
    return utcnow().isoformat()


def round_price(price: float, precision: int = 2) -> float:
    """Round a price to given decimal places."""
    return round(price, precision)


def pct_change(old: float, new: float) -> float:
    """Percentage change from old to new."""
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def format_usd(amount: float) -> str:
    """Format a dollar amount."""
    return f"${amount:,.2f}"


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """Divide without ZeroDivisionError."""
    return a / b if b != 0 else default


log = setup_logger("alpha")
