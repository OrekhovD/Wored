"""Compatibility shim — the instrument registry now lives in ``trading_math``.

The implementation moved to :mod:`trading_math.instrument_registry` in P3 so the
Telegram bot can validate ``instrument_key`` through the exact same code the
dashboard uses (``./webui`` is not mounted into the bot container, while
``/opt/trading_math`` is mounted into both).  This file only re-exports it, so
every existing ``from instrument_registry import …`` call site — and the
source-level wiring assertion in ``tests/test_forecast_command_v3.py`` — keeps
working unchanged.

Do not add logic here.  Import ``trading_math.instrument_registry`` directly in
new shared code; keep using ``instrument_registry`` inside ``webui``.
"""
from __future__ import annotations

from trading_math.instrument_registry import (
    SUPPORTED_PERIODS,
    InstrumentNotFound,
    InstrumentRegistry,
    InstrumentSpec,
    RegistryError,
    default_registry_path,
    load_registry,
)

__all__ = [
    "InstrumentNotFound",
    "InstrumentRegistry",
    "InstrumentSpec",
    "RegistryError",
    "SUPPORTED_PERIODS",
    "default_registry_path",
    "load_registry",
]
