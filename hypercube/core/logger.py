"""Structured logging utilities used across the gateway."""

from __future__ import annotations

import logging
import sys
from typing import Optional

_DEFAULT_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO", fmt: Optional[str] = None) -> logging.Logger:
    """Configure the root logger once and return it.

    Safe to call multiple times — subsequent calls become no-ops.
    """
    if hasattr(setup_logging, "_configured"):  # type: ignore[attr-defined]
        return logging.getLogger("hytergram")

    level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(fmt or _DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    for quiet in ("asyncio", "urllib3", "httpx", "aiohttp"):
        logging.getLogger(quiet).setLevel(logging.WARNING)

    setup_logging._configured = True  # type: ignore[attr-defined]
    return logging.getLogger("hytergram")


def module_logger(name: str) -> logging.Logger:
    """Return a named child logger matching ``hytergram.<name>``."""
    return logging.getLogger(f"hytergram.{name}")
