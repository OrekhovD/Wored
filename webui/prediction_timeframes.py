"""
Shared constants for the WORED prediction system.

Centralizes timeframe normalization so every service (webui, collector,
pattern matcher) uses the same canonical periods and step-minute mapping.
"""

CANONICAL_PERIODS = {"1min", "5min", "15min", "30min", "60min", "4hour", "1day"}

# Mapping from canonical period name to candle step length in minutes.
STEP_MINUTES_MAP: dict[str, int] = {
    "1min": 1,
    "5min": 5,
    "15min": 15,
    "30min": 30,
    "60min": 60,
    "4hour": 240,
    "1day": 1440,
}

# Aliases users / models may send. Everything normalizes to canonical keys.
PERIOD_ALIASES: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    "1h": "60min",
    "1hour": "60min",
    "hour": "60min",
    "4h": "4hour",
    "4hour": "4hour",
    "12h": "4hour",  # HTX doesn't have 12h; downsample to 4h for context
    "1d": "1day",
    "daily": "1day",
}


def normalize_period(raw: str) -> str:
    """Return canonical period name. Raises ValueError for unsupported input."""
    key = str(raw).strip().lower()
    canonical = PERIOD_ALIASES.get(key, key)
    if canonical not in CANONICAL_PERIODS:
        raise ValueError(f"Unsupported period: {raw!r}")
    return canonical


def period_to_minutes(period: str) -> int:
    """Return step length in minutes for a canonical (or aliased) period."""
    canonical = normalize_period(period)
    return STEP_MINUTES_MAP[canonical]


def horizon_steps_to_hours(steps: int, period: str) -> int:
    """Convert a step count on a given period to a whole-hour horizon."""
    minutes = steps * period_to_minutes(period)
    return max(1, int(minutes / 60))


def steps_for_hours(hours: int, period: str) -> int:
    """Convert a whole-hour horizon to a step count for a period."""
    minutes = period_to_minutes(period)
    if minutes <= 0:
        raise ValueError(f"Invalid period minutes for {period}")
    return max(1, int((hours * 60) / minutes))
