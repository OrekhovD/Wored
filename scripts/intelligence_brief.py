#!/usr/bin/env python3

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

# --- CONFIG ---
DEFAULT_MODE = "hourly"
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_DAYS = 7

# --- UTILS ---
def get_time_range(mode: str, days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if mode == "hourly":
        from_dt = now - timedelta(hours=1)
        to_dt = now
    else:  # daily
        from_dt = now - timedelta(days=days)
        to_dt = now
    return from_dt, to_dt

def load_market_context(symbol: str, period: str = "60min") -> Dict[str, Any]:
    """Mock: in real world, calls fetch_history + pattern_lab."""
    return {
        "symbol": symbol,
        "period": period,
        "rsi": 67.3,
        "sma20": 61800.0,
        "sma50": 61200.0,
        "macd": {"line": 230.1, "signal": 210.4},
        "volume_24h_avg": 125000000.0,
        "volatility_7d_pct": 2.1,
        "trend": "bullish",
        "summary": "Strong bullish momentum, RSI rising but not overbought, volume above average."
    }

def load_alerts(symbols: List[str], from_dt: datetime, to_dt: datetime) -> List[Dict]:
    return [
        {
            "id": "ALERT-1001",
            "symbol": "BTCUSDT",
            "alert_type": "volume_spike",
            "severity": "high",
            "status": "open",
            "created_at": (from_dt + timedelta(minutes=15)).isoformat(),
            "metadata": {"multiplier": 2.3}
        },
        {
            "id": "ALERT-1002",
            "symbol": "ETHUSDT",
            "alert_type": "rsi_overbought",
            "severity": "medium",
            "status": "open",
            "created_at": (from_dt + timedelta(minutes=22)).isoformat(),
            "metadata": {"rsi": 72.1}
        }
    ]

def load_forecasts(symbols: List[str], from_dt: datetime, to_dt: datetime) -> List[Dict]:
    return [
        {
            "id": "FC-2001",
            "symbol": "BTCUSDT",
            "forecast_type": "price",
            "horizon_hours": 4,
            "predicted_price": 62500.0,
            "confidence": 0.72,
            "model": "qwen-plus",
            "status": "executed",
            "created_at": (from_dt + timedelta(minutes=5)).isoformat(),
            "scorecard": {"direction_accuracy": 64.2, "error_pct": 2.34}
        }
    ]

def load_risks() -> List[Dict]:
    return [
        {
            "id": "RISK-3001",
            "type": "position_size_mismatch",
            "description": "Position size exceeds risk budget on ETHUSDT",
            "impact": "medium",
            "status": "active"
        }
    ]

def load_journal_summary(from_dt: datetime, to_dt: datetime) -> str:
    return f"AI Journal ({from_dt.strftime('%H:%M')} -> {to_dt.strftime('%H:%M')}):\n- 3 new forecasts generated\n- 2 volume spike alerts triggered\n- 1 RSI overbought alert\n- No errors"

def load_top_patterns(symbols: List[str]) -> List[Dict]:
    return [
        {
            "symbol": "BTCUSDT",
            "pattern": "volume_spike",
            "occurrences": 4,
            "avg_forward_change_pct": 0.62,
            "win_rate_up": 58.3,
            "best_group": "hour_of_day=14"
        }
    ]

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="WORED Intelligence Brief")
    parser.add_argument("--mode", choices=["hourly", "daily"], default=DEFAULT_MODE, help="Brief mode")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Days for daily mode")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    symbols = [s.strip() for s in args.symbols.split(",")]

    from_dt, to_dt = get_time_range(args.mode, args.days)

    # Load all data
    market_context = {s: load_market_context(s) for s in symbols}
    alerts = load_alerts(symbols, from_dt, to_dt)
    forecasts = load_forecasts(symbols, from_dt, to_dt)
    risks = load_risks()
    journal = load_journal_summary(from_dt, to_dt)
    patterns = load_top_patterns(symbols)

    # Build brief
    brief = {
        "mode": args.mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "time_range": {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
        },
        "symbols": symbols,
        "market_context": market_context,
        "alerts": {
            "total": len(alerts),
            "by_type": {},
            "open": [a for a in alerts if a["status"] == "open"],
        },
        "forecasts": {
            "total": len(forecasts),
            "by_model": {},
            "accuracy_score": sum(f.get("scorecard", {}).get("direction_accuracy", 0) for f in forecasts) / len(forecasts) if forecasts else 0.0,
        },
        "risks": risks,
        "journal_summary": journal,
        "top_patterns": patterns,
        "secrets_printed": False,
    }

    # Count by type
    for a in alerts:
        atype = a["alert_type"]
        brief["alerts"]["by_type"][atype] = brief["alerts"]["by_type"].get(atype, 0) + 1

    for f in forecasts:
        model = f.get("model", "unknown")
        brief["forecasts"]["by_model"][model] = brief["forecasts"]["by_model"].get(model, 0) + 1

    # Output
    if args.format == "json":
        print(json.dumps(brief, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# WORED Intelligence Brief - {args.mode.title()} ({from_dt.strftime('%H:%M')} -> {to_dt.strftime('%H:%M')})\n")
        print(f"## Market Context\n")
        for s, ctx in market_context.items():
            print(f"- `{s}`: {ctx['trend']} | RSI {ctx['rsi']:.1f} | Volatility {ctx['volatility_7d_pct']:.1f}%")
        print()
        print(f"## Alerts ({len(alerts)} total)")
        for a in brief["alerts"]["open"]:
            marker = a["metadata"].get("rsi", a["metadata"].get("multiplier", "?"))
            print(f"- [{a['alert_type']}] {a['symbol']} - {a['severity']} ({marker})")
        print()
        print(f"## Forecasts ({len(forecasts)} total)")
        print(f"- Accuracy score: {brief['forecasts']['accuracy_score']:.1f}%")
        for model, count in brief["forecasts"]["by_model"].items():
            print(f"- `{model}`: {count}")
        print()
        print(f"## Open Risks ({len(risks)} total)")
        for r in risks:
            print(f"- `{r['type']}`: {r['description']}")
        print()
        print(f"## AI Journal Summary\n```\n{journal}\n```")
        print()
        print(f"## Top Patterns\n")
        for p in patterns:
            print(f"- `{p['symbol']}` {p['pattern']}: {p['occurrences']}x, win rate up {p['win_rate_up']}%")

if __name__ == "__main__":
    main()
