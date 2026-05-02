#!/usr/bin/env python3

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# --- CONFIG ---
DEFAULT_DAYS = 30
DEFAULT_SYMBOL = "BTCUSDT"

# --- UTILS ---
def parse_iso_date(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        logging.warning(f"Invalid ISO date '{s}': {e}")
        return datetime.now(timezone.utc)

def get_time_range(days: int) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(days=days)
    return from_dt, now

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="Forecast vs Reality Analyzer")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Days to analyze")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    from_dt, to_dt = get_time_range(args.days)

    # Simulate DB query — in real world: use psycopg2 or asyncpg
    # But here we simulate safe fallback: no DB → insufficient_data
    forecasts = []
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost",
            port="5432",
            dbname="trading",
            user="bot",
            password="password"
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT
                fr.id,
                fr.symbol,
                fr.entry_price,
                fr.horizon_hours,
                fr.created_at,
                fr.requested_model,
                fr.provider,
                f.forecast_price,
                f.actual_price,
                f.forecasted_at,
                f.actual_at,
                f.model_used,
                f.latency_ms,
                f.fallback_used
            FROM forecast_requests fr
            LEFT JOIN forecasts f ON fr.id = f.request_id
            WHERE fr.symbol = %s AND fr.created_at BETWEEN %s AND %s
            ORDER BY fr.created_at DESC
        """, (args.symbol, from_dt, to_dt))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        for row in rows:
            try:
                forecasts.append({
                    "id": row[0],
                    "symbol": row[1],
                    "entry_price": float(row[2]),
                    "horizon_hours": int(row[3]),
                    "created_at": row[4].isoformat() if row[4] else None,
                    "requested_model": row[5],
                    "provider": row[6],
                    "forecast_price": float(row[7]) if row[7] is not None else None,
                    "actual_price": float(row[8]) if row[8] is not None else None,
                    "forecasted_at": row[9].isoformat() if row[9] else None,
                    "actual_at": row[10].isoformat() if row[10] else None,
                    "model_used": row[11],
                    "latency_ms": int(row[12]) if row[12] is not None else None,
                    "fallback_used": bool(row[13]) if row[13] is not None else False,
                })
            except (ValueError, TypeError):
                continue
    except ImportError:
        logging.warning("psycopg2 not installed — skipping DB read")
    except Exception as exc:
        logging.warning(f"DB query failed: {exc}")

    # Not enough data?
    if len(forecasts) == 0:
        result = {
            "symbol": args.symbol,
            "days": args.days,
            "summary": "insufficient_data",
            "direction_accuracy": None,
            "avg_change_pct_error": None,
            "best_horizon": None,
            "worst_horizon": None,
            "avg_latency_ms": None,
            "fallback_impact": None,
            "secrets_printed": False,
        }
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(f"# Forecast vs Reality: {args.symbol} ({args.days}d)\n")
            print("## Summary\ninsufficient_data\n")
        sys.exit(0)

    # Compute metrics
    direction_correct = 0
    total_direction = 0
    errors = []
    latencies = []
    fallbacks = []
    by_horizon = {}

    for f in forecasts:
        if f["forecast_price"] is None or f["actual_price"] is None or f["entry_price"] is None:
            continue

        # Direction: sign(forecast - entry) vs sign(actual - entry)
        forecast_dir = 1 if f["forecast_price"] > f["entry_price"] else (-1 if f["forecast_price"] < f["entry_price"] else 0)
        actual_dir = 1 if f["actual_price"] > f["entry_price"] else (-1 if f["actual_price"] < f["entry_price"] else 0)
        if forecast_dir == actual_dir and actual_dir != 0:
            direction_correct += 1
        total_direction += 1 if actual_dir != 0 else 0

        # Error %
        try:
            err = abs((f["forecast_price"] - f["actual_price"]) / f["actual_price"]) * 100
            errors.append(err)
        except (ZeroDivisionError, TypeError):
            pass

        # Latency
        if f["latency_ms"] is not None:
            latencies.append(f["latency_ms"])

        # Fallback
        if f["fallback_used"]:
            fallbacks.append(f)

        # Group by horizon
        h = f["horizon_hours"]
        if h not in by_horizon:
            by_horizon[h] = {"correct": 0, "total": 0, "errors": [], "latencies": []}
        if forecast_dir == actual_dir and actual_dir != 0:
            by_horizon[h]["correct"] += 1
        by_horizon[h]["total"] += 1 if actual_dir != 0 else 0
        if f["forecast_price"] is not None and f["actual_price"] is not None:
            try:
                by_horizon[h]["errors"].append(abs((f["forecast_price"] - f["actual_price"]) / f["actual_price"]) * 100)
            except (ZeroDivisionError, TypeError):
                pass
        if f["latency_ms"] is not None:
            by_horizon[h]["latencies"].append(f["latency_ms"])

    # Final metrics
    direction_accuracy = (direction_correct / total_direction * 100) if total_direction > 0 else None
    avg_error = sum(errors) / len(errors) if errors else None
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    fallback_impact = len(fallbacks) / len(forecasts) if forecasts else None

    # Best/worst horizon
    best_h = None
    worst_h = None
    best_acc = -1
    worst_acc = 999
    for h, data in by_horizon.items():
        acc = (data["correct"] / data["total"] * 100) if data["total"] > 0 else 0
        if acc > best_acc:
            best_acc = acc
            best_h = h
        if acc < worst_acc and data["total"] > 0:
            worst_acc = acc
            worst_h = h

    result = {
        "symbol": args.symbol,
        "days": args.days,
        "summary": f"{len(forecasts)} forecasts analyzed",
        "direction_accuracy": round(direction_accuracy, 2) if direction_accuracy is not None else None,
        "avg_change_pct_error": round(avg_error, 4) if avg_error is not None else None,
        "best_horizon": best_h,
        "worst_horizon": worst_h,
        "avg_latency_ms": round(avg_latency, 1) if avg_latency is not None else None,
        "fallback_impact": round(fallback_impact * 100, 2) if fallback_impact is not None else None,
        "secrets_printed": False,
    }

    # Output
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# Forecast vs Reality: {args.symbol} ({args.days}d)\n")
        print(f"## Summary\n{result['summary']}\n")
        print("## Metrics\n")
        print(f"- Direction accuracy: {result['direction_accuracy']}%")
        print(f"- Avg change % error: {result['avg_change_pct_error']}%")
        print(f"- Best horizon: {result['best_horizon']}h")
        print(f"- Worst horizon: {result['worst_horizon']}h")
        print(f"- Avg latency: {result['avg_latency_ms']}ms")
        print(f"- Fallback impact: {result['fallback_impact']}%")

if __name__ == "__main__":
    main()
