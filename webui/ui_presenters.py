"""WORED UI Presenters — UI-01
Pure functions: raw backend data → UI-ready dicts.
No side effects, no I/O — safe for templates, JSON API, and tests.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Union


# ── Number formatting ──────────────────────────────────────────────────────────

def fmt_usdt(value: Optional[float], min_decimals: int = 2, max_decimals: int = 8) -> str:
    """Format a USDT value. None/NaN → '—'. Near-zero → '<0,00000001 USDT'."""
    if value is None or not math.isfinite(value):
        return '—'
    if value == 0:
        return '0,00 USDT'
    abs_v = abs(value)
    if 0 < abs_v < 0.01:
        formatted = _ru_format(value, min_decimals, max_decimals)
        if formatted in ('0,00', '-0,00', '0,00000000'):
            return ('-' if value < 0 else '') + '<0,00000001 USDT'
        return formatted + ' USDT'
    if abs_v >= 1:
        return _ru_format(value, min_decimals, max_decimals) + ' USDT'
    return _ru_format(value, 2, 8) + ' USDT'


def fmt_price(value: Optional[float]) -> str:
    """Format price without suffix. None/NaN → '—'."""
    if value is None or not math.isfinite(value):
        return '—'
    if value == 0:
        return '0'
    abs_v = abs(value)
    if abs_v >= 1:
        return _ru_format(value, 2, 8)
    return _ru_format(value, 2, 8)


def fmt_pct(value: Optional[float]) -> str:
    """Format percentage points (input already in %). E.g. 0.5 → '0,50 %'."""
    if value is None or not math.isfinite(value):
        return '—'
    return _ru_format(value, 2, 2) + ' %'


def fmt_fraction_as_pct(value: Optional[float]) -> str:
    """Format fraction as percentage. E.g. 0.005 → '0,50 %'."""
    if value is None or not math.isfinite(value):
        return '—'
    return _ru_format(value * 100, 2, 2) + ' %'


def fmt_pnl(value: Optional[float]) -> str:
    """Format PnL with sign for positive. E.g. +1,25 USDT / -1,25 USDT."""
    if value is None or not math.isfinite(value):
        return '—'
    if value == 0:
        return '0,00 USDT'
    formatted = _ru_format(value, 2, 2) + ' USDT'
    return ('+' + formatted) if value > 0 else formatted


def fmt_qty(value: Optional[float], asset: str = 'BTC') -> str:
    """Format quantity with asset suffix."""
    if value is None or not math.isfinite(value):
        return '—'
    return _ru_format(value, 2, 8) + ' ' + asset


def fmt_time(iso: Optional[str]) -> str:
    """Format ISO UTC → 'DD.MM HH:MM UTC'. None → '—'."""
    if not iso:
        return '—'
    try:
        d = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        return d.strftime('%d.%m %H:%M') + ' UTC'
    except (ValueError, AttributeError):
        return '—'


def fmt_time_full(iso: Optional[str]) -> str:
    """Format ISO UTC → 'DD.MM.YYYY HH:MM:SS UTC'. None → '—'."""
    if not iso:
        return '—'
    try:
        d = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        return d.strftime('%d.%m.%Y %H:%M:%S') + ' UTC'
    except (ValueError, AttributeError):
        return '—'


def fmt_time_ago(iso: Optional[str]) -> str:
    """Format ISO UTC as relative time in Russian."""
    if not iso:
        return '—'
    try:
        d = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        diff = (datetime.now(timezone.utc) - d).total_seconds()
        if diff < 60:
            return 'только что'
        if diff < 3600:
            return f'{int(diff // 60)} мин назад'
        if diff < 86400:
            return f'{int(diff // 3600)} ч назад'
        return f'{int(diff // 86400)} д назад'
    except (ValueError, AttributeError):
        return '—'


# ── Status labels ───────────────────────────────────────────────────────────────

EXECUTION_STATES = {
    'queued':    {'label': 'В очереди',         'css_class': 'ui-status-queued'},
    'running':   {'label': 'Рассчитывается',     'css_class': 'ui-status-running'},
    'partial':   {'label': 'Частичный результат', 'css_class': 'ui-status-partial'},
    'completed': {'label': 'Расчёт завершён',    'css_class': 'ui-status-completed'},
    'failed':    {'label': 'Ошибка расчёта',     'css_class': 'ui-status-failed'},
    'expired':   {'label': 'Истёк',             'css_class': 'ui-status-expired'},
}

# Legacy mapping
_LEGACY_STATE_MAP = {
    'pending': 'queued',
    'active': None,  # ambiguous — needs real result
}


def state_label(execution_state: Optional[str] = None, legacy_status: Optional[str] = None) -> Dict[str, str]:
    """Return {label, css_class} for execution state."""
    key = execution_state or _LEGACY_STATE_MAP.get(legacy_status)  # type: ignore[arg-type]
    if key and key in EXECUTION_STATES:
        return EXECUTION_STATES[key]
    return {'label': 'Состояние уточняется', 'css_class': 'ui-status-queued'}


# Terminal states — job is done (success, failure, or expired)
TERMINAL_STATES = frozenset({'completed', 'failed', 'expired'})

# States that indicate the job is still in progress
ACTIVE_STATES = frozenset({'queued', 'running', 'partial'})


def is_terminal(execution_state: Optional[str], legacy_status: Optional[str] = None) -> bool:
    """Check if a job has reached terminal state."""
    es = execution_state
    if es and es in TERMINAL_STATES:
        return True
    # Legacy: 'active' with real result is terminal, 'failed' is terminal
    if not es and legacy_status:
        return legacy_status in ('failed',)
    return False


def is_valid_forecast(valid_until: Optional[str], now_iso: Optional[str] = None) -> bool:
    """Check if a forecast is still valid by its valid_until timestamp."""
    if not valid_until:
        return False  # 'Срок действия не указан'
    try:
        from datetime import datetime, timezone
        v = datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
        n = datetime.fromisoformat((now_iso or '').replace('Z', '+00:00')) if now_iso else datetime.now(timezone.utc)
        return v > n
    except (ValueError, AttributeError):
        return False


# ── Forecast presenter ──────────────────────────────────────────────────────────

def present_forecast_summary(forecast: Dict[str, Any]) -> Dict[str, Any]:
    """Present a single forecast run for UI card/table display."""
    es = forecast.get('execution_state') or forecast.get('status')
    sl = state_label(forecast.get('execution_state'), forecast.get('status'))

    target_price = forecast.get('target_price')
    actual_price = forecast.get('actual_price')
    predicted_price = forecast.get('predicted_price')

    # Compute PnL if possible (long position, $1000 notional)
    pnl_usdt = None
    if target_price and predicted_price and actual_price:
        direction = 1 if predicted_price > actual_price else -1
        pnl_usdt = direction * (target_price - actual_price) / actual_price * 1000

    return {
        'run_id': forecast.get('run_id', '—'),
        'symbol': forecast.get('symbol', 'BTC/USDT'),
        'execution_state': es,
        'state_label': sl['label'],
        'state_css': sl['css_class'],
        'predicted_price_fmt': fmt_price(predicted_price),
        'target_price_fmt': fmt_price(target_price),
        'actual_price_fmt': fmt_price(actual_price) if actual_price else '—',
        'target_time_fmt': fmt_time(forecast.get('target_time')),
        'target_time_full': fmt_time_full(forecast.get('target_time')),
        'target_time_ago': fmt_time_ago(forecast.get('target_time')),
        'pnl_fmt': fmt_pnl(pnl_usdt),
        'pnl_raw': pnl_usdt,
        'models_completed': forecast.get('models_completed', 0),
        'models_total': forecast.get('models_total', 0),
    }


def present_forecasts_list(forecasts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Present a list of forecasts for UI."""
    return [present_forecast_summary(f) for f in forecasts]


# ── Alert presenter ────────────────────────────────────────────────────────────

_SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}

def present_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Present a single alert for UI card."""
    return {
        'id': alert.get('id', '—'),
        'severity': alert.get('severity', 'info'),
        'severity_label': alert.get('severity', 'info').upper(),
        'title': alert.get('title', '—'),
        'message': alert.get('message', ''),
        'created_at_fmt': fmt_time(alert.get('created_at')),
        'created_at_ago': fmt_time_ago(alert.get('created_at')),
        'acknowledged': alert.get('acknowledged', False),
    }


def present_alerts_list(alerts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [present_alert(a) for a in alerts],
        key=lambda x: _SEVERITY_ORDER.get(x.get('severity', 'info'), 5)
    )


# ── Health presenter ────────────────────────────────────────────────────────────

def present_health(health: Dict[str, Any]) -> Dict[str, Any]:
    """Present /api/health data for status indicators."""
    return {
        'postgres': bool(health.get('postgres', False)),
        'redis': bool(health.get('redis', False)),
        'collector_feed': bool(health.get('collector_feed', False)),
        'forecast_worker': bool(health.get('forecast_worker', False)),
        'all_ok': all([
            health.get('postgres', False),
            health.get('redis', False),
            health.get('collector_feed', False),
        ]),
    }


# ── Internal helpers ────────────────────────────────────────────────────────────

def _ru_format(value: float, min_decimals: int = 2, max_decimals: int = 2) -> str:
    """Format number using ru-RU locale (comma decimal separator, space thousands)."""
    import locale
    # We don't actually set locale — just use manual formatting for consistency
    neg = value < 0
    abs_v = abs(value)
    # Round to max_decimals
    rounded = round(abs_v, max_decimals)
    # Split integer and decimal
    int_part = int(rounded)
    dec_part = rounded - int_part
    # Format integer part with spaces
    int_str = _format_int_with_spaces(int_part)
    # Format decimal part
    dec_str = f'{dec_part:.{max_decimals}f}'[2:]  # remove '0.'
    # Trim trailing zeros but keep min_decimals
    dec_str = dec_str.rstrip('0')
    if len(dec_str) < min_decimals:
        dec_str = dec_str + '0' * (min_decimals - len(dec_str))
    result = int_str + ',' + dec_str
    return '-' + result if neg else result


def _format_int_with_spaces(n: int) -> str:
    """Format integer with space as thousands separator (Russian convention)."""
    s = str(n)
    groups = []
    while s:
        groups.append(s[-3:])
        s = s[:-3]
    return ' '.join(reversed(groups))