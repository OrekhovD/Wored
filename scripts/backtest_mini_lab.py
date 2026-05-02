#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# --- CONFIG ---
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_PERIOD = "60min"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_STRATEGY = "rsi_oversold_rebound"
DEFAULT_INITIAL_BALANCE = 10000.0
DEFAULT_RISK_PCT = 1.0
DEFAULT_FEE_PCT = 0.001
DEFAULT_SLIPPAGE_PCT = 0.0005
DEFAULT_EXIT_AFTER_HOURS = 4

# --- UTILS ---
def parse_period(period: str) -> str:
    mapping = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "60min": "60min",
        "4hour": "4hour",
        "1day": "1day",
    }
    if period not in mapping:
        raise ValueError(f"Unsupported period '{period}'")
    return mapping[period]

def parse_iso_date(date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid ISO date: {date_str}")

def get_time_range(
    lookback_days: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if from_date and to_date:
        return from_date, to_date
    if from_date:
        return from_date, now
    if to_date:
        return to_date - timedelta(days=lookback_days or DEFAULT_LOOKBACK_DAYS), to_date
    if lookback_days is None:
        lookback_days = DEFAULT_LOOKBACK_DAYS
    from_dt = now - timedelta(days=lookback_days)
    return from_dt, now

# --- INDICATORS (copied & simplified) ---
def compute_rsi(candles, period=14):
    if len(candles) < period + 1:
        return [None] * len(candles)
    rsi = [None] * len(candles)
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = candles[i]["close"] - candles[i-1]["close"]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1 + rs))
    for i in range(period + 1, len(candles)):
        change = candles[i]["close"] - candles[i-1]["close"]
        gain = max(0, change)
        loss = max(0, -change)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1 + rs))
    return rsi

def compute_sma(candles, period):
    if len(candles) < period:
        return [None] * len(candles)
    sma = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        window = [c["close"] for c in candles[i - period + 1:i + 1]]
        sma[i] = sum(window) / len(window)
    return sma

def compute_macd(candles, fast=12, slow=26, signal=9):
    if len(candles) < slow:
        return {"macd": [None]*len(candles), "signal": [None]*len(candles), "hist": [None]*len(candles)}
    fast_sma = compute_sma(candles, fast)
    slow_sma = compute_sma(candles, slow)
    macd_line = [None] * len(candles)
    for i in range(len(candles)):
        if fast_sma[i] is not None and slow_sma[i] is not None:
            macd_line[i] = fast_sma[i] - slow_sma[i]
    signal_line = compute_sma([{"close": v} for v in macd_line if v is not None], signal)
    padded_signal = [None] * len(candles)
    if len(signal_line) > 0:
        padded_signal[-len(signal_line):] = signal_line
    hist = [None] * len(candles)
    for i in range(len(candles)):
        if macd_line[i] is not None and padded_signal[i] is not None:
            hist[i] = macd_line[i] - padded_signal[i]
    return {"macd": macd_line, "signal": padded_signal, "hist": hist}

# --- HTX CLIENT ---
async def fetch_htx_candles(
    client,
    symbol: str,
    period: str,
    from_ts: int,
    to_ts: int,
    limit: int = 2000,
) -> List[Dict]:
    params = {
        "symbol": symbol.lower(),
        "period": period,
        "from": str(from_ts),
        "to": str(to_ts),
        "size": str(limit),
    }
    try:
        response = await client.get("https://api.huobi.pro/market/history/kline", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok":
            raise RuntimeError(f"HTX error: {payload.get('status')} {payload.get('msg', '')}")
        raw_klines = payload.get("data", [])
        candles = []
        for item in raw_klines:
            ts_ms = item[0]
            open_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            candles.append({
                "symbol": symbol.upper(),
                "period": period,
                "open_time": open_time,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "source": "htx_rest",
            })
        return candles
    except Exception as exc:
        raise RuntimeError(f"HTX fetch failed: {exc}")

# --- STRATEGIES ---
def strategy_rsi_oversold_rebound(candles, rsi_vals, sma20) -> List[Dict]:
    """Buy when RSI < 30 and price < SMA20, sell after 4h or at SMA20."""
    trades = []
    for i in range(20, len(candles) - 1):
        if rsi_vals[i] is not None and sma20[i] is not None and rsi_vals[i] < 30 and candles[i]["close"] < sma20[i]:
            # Entry
            entry_price = candles[i]["close"] * (1 + DEFAULT_SLIPPAGE_PCT)
            # Exit: 4h later or at SMA20
            exit_idx = min(i + 4, len(candles) - 1)
            exit_price = candles[exit_idx]["close"] * (1 - DEFAULT_SLIPPAGE_PCT)
            if sma20[exit_idx] is not None:
                exit_price = min(exit_price, sma20[exit_idx] * (1 - DEFAULT_SLIPPAGE_PCT))
            fee = entry_price * DEFAULT_FEE_PCT + exit_price * DEFAULT_FEE_PCT
            pnl = (exit_price - entry_price) - fee
            trades.append({
                "type": "long",
                "entry_time": candles[i]["open_time"],
                "entry_price": round(entry_price, 4),
                "exit_time": candles[exit_idx]["open_time"],
                "exit_price": round(exit_price, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round((pnl / entry_price) * 100, 4),
                "duration_hours": (exit_idx - i) * 1.0,
            })
    return trades

def strategy_macd_bull_cross(candles, macd_data) -> List[Dict]:
    trades = []
    macd_vals = macd_data["macd"]
    signal_vals = macd_data["signal"]
    for i in range(1, len(candles)):
        if (macd_vals[i-1] is not None and signal_vals[i-1] is not None and
            macd_vals[i] is not None and signal_vals[i] is not None and
            macd_vals[i-1] <= signal_vals[i-1] and macd_vals[i] > signal_vals[i]):
            entry_price = candles[i]["close"] * (1 + DEFAULT_SLIPPAGE_PCT)
            exit_idx = min(i + 4, len(candles) - 1)
            exit_price = candles[exit_idx]["close"] * (1 - DEFAULT_SLIPPAGE_PCT)
            fee = entry_price * DEFAULT_FEE_PCT + exit_price * DEFAULT_FEE_PCT
            pnl = (exit_price - entry_price) - fee
            trades.append({
                "type": "long",
                "entry_time": candles[i]["open_time"],
                "entry_price": round(entry_price, 4),
                "exit_time": candles[exit_idx]["open_time"],
                "exit_price": round(exit_price, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round((pnl / entry_price) * 100, 4),
                "duration_hours": (exit_idx - i) * 1.0,
            })
    return trades

def strategy_sma20_sma50_cross(candles, sma20, sma50) -> List[Dict]:
    trades = []
    for i in range(50, len(candles)):
        if (sma20[i-1] is not None and sma50[i-1] is not None and
            sma20[i] is not None and sma50[i] is not None and
            sma20[i-1] <= sma50[i-1] and sma20[i] > sma50[i]):
            entry_price = candles[i]["close"] * (1 + DEFAULT_SLIPPAGE_PCT)
            exit_idx = min(i + 4, len(candles) - 1)
            exit_price = candles[exit_idx]["close"] * (1 - DEFAULT_SLIPPAGE_PCT)
            fee = entry_price * DEFAULT_FEE_PCT + exit_price * DEFAULT_FEE_PCT
            pnl = (exit_price - entry_price) - fee
            trades.append({
                "type": "long",
                "entry_time": candles[i]["open_time"],
                "entry_price": round(entry_price, 4),
                "exit_time": candles[exit_idx]["open_time"],
                "exit_price": round(exit_price, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round((pnl / entry_price) * 100, 4),
                "duration_hours": (exit_idx - i) * 1.0,
            })
    return trades

def strategy_volume_spike_continuation(candles, sma20) -> List[Dict]:
    trades = []
    if len(candles) > 24:
        for i in range(24, len(candles)):
            avg_vol = sum(c["volume"] for c in candles[i-24:i]) / 24
            if candles[i]["volume"] > avg_vol * 2.0 and candles[i]["close"] > sma20[i]:
                entry_price = candles[i]["close"] * (1 + DEFAULT_SLIPPAGE_PCT)
                exit_idx = min(i + 4, len(candles) - 1)
                exit_price = candles[exit_idx]["close"] * (1 - DEFAULT_SLIPPAGE_PCT)
                fee = entry_price * DEFAULT_FEE_PCT + exit_price * DEFAULT_FEE_PCT
                pnl = (exit_price - entry_price) - fee
                trades.append({
                    "type": "long",
                    "entry_time": candles[i]["open_time"],
                    "entry_price": round(entry_price, 4),
                    "exit_time": candles[exit_idx]["open_time"],
                    "exit_price": round(exit_price, 4),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round((pnl / entry_price) * 100, 4),
                    "duration_hours": (exit_idx - i) * 1.0,
                })
    return trades

def strategy_alert_follow(candles, alerts) -> List[Dict]:
    trades = []
    for alert in alerts:
        # Find candle closest to alert.created_at
        alert_dt = datetime.fromisoformat(alert["created_at"])
        for i, c in enumerate(candles):
            c_dt = datetime.fromisoformat(c["open_time"])
            if abs((c_dt - alert_dt).total_seconds()) < 1800:  # ±30m
                entry_price = c["close"] * (1 + DEFAULT_SLIPPAGE_PCT)
                exit_idx = min(i + 4, len(candles) - 1)
                exit_price = candles[exit_idx]["close"] * (1 - DEFAULT_SLIPPAGE_PCT)
                fee = entry_price * DEFAULT_FEE_PCT + exit_price * DEFAULT_FEE_PCT
                pnl = (exit_price - entry_price) - fee
                trades.append({
                    "type": "long",
                    "entry_time": c["open_time"],
                    "entry_price": round(entry_price, 4),
                    "exit_time": candles[exit_idx]["open_time"],
                    "exit_price": round(exit_price, 4),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round((pnl / entry_price) * 100, 4),
                    "duration_hours": (exit_idx - i) * 1.0,
                })
                break
    return trades

# --- MAIN ---
async def main():
    parser = argparse.ArgumentParser(description="Strategy Backtest Mini-Lab")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Period (1min, 5min, ..., 1day)")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="Strategy name")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days to backtest")
    parser.add_argument("--initial-balance", type=float, default=DEFAULT_INITIAL_BALANCE, help="Starting balance")
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT, help="Risk per trade (%)")
    parser.add_argument("--fee-pct", type=float, default=DEFAULT_FEE_PCT, help="Fee per trade (%)")
    parser.add_argument("--slippage-pct", type=float, default=DEFAULT_SLIPPAGE_PCT, help="Slippage per trade (%)")
    parser.add_argument("--exit-after-hours", type=int, default=DEFAULT_EXIT_AFTER_HOURS, help="Hours before exit")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, help="ISO start date")
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, help="ISO end date")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Get time range
    from_dt, to_dt = get_time_range(
        lookback_days=args.lookback_days,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    # Convert to timestamps (ms)
    from_ts = int(from_dt.timestamp() * 1000)
    to_ts = int(to_dt.timestamp() * 1000)

    # Load candles
    candles = []
    try:
        import asyncpg
        pool = await asyncpg.create_pool("postgresql://bot:password@localhost:5432/trading")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM candles WHERE symbol = $1 AND period = $2 AND open_time BETWEEN $3 AND $4 ORDER BY open_time ASC",
                args.symbol,
                args.period,
                from_dt,
                to_dt,
            )
            candles = [
                {
                    "symbol": row["symbol"],
                    "period": row["period"],
                    "open_time": row["open_time"].isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "source": row["source"],
                }
                for row in rows
            ]
        await pool.close()
        logging.info(f"Loaded {len(candles)} candles from Postgres")
    except ImportError as exc:
        logging.warning(f"Postgres load skipped: asyncpg not installed ({exc})")
    except Exception as exc:
        logging.warning(f"Postgres load failed: {exc}. Falling back to HTX.")

    # If empty, fall back to HTX
    if not candles:
        try:
            import httpx
        except ImportError as exc:
            logging.warning(f"HTX fallback skipped: httpx not installed ({exc})")
            result = {
                "symbol": args.symbol,
                "period": args.period,
                "strategy": args.strategy,
                "summary": "insufficient_data",
                "trades": [],
                "metrics": {},
                "secrets_printed": False,
            }
            if args.format == "json":
                print(json.dumps(result, indent=2, ensure_ascii=False))
            elif args.format == "markdown":
                print(f"# Strategy Backtest: {args.symbol} {args.period} ({args.strategy})\n")
                print("## Summary\ninsufficient_data\n")
            sys.exit(0)
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            try:
                candles = await fetch_htx_candles(
                    http_client,
                    args.symbol,
                    parse_period(args.period),
                    from_ts,
                    to_ts,
                )
                logging.info(f"Fetched {len(candles)} candles from HTX")
            except Exception as exc:
                logging.error(f"❌ Failed to fetch from HTX: {exc}")
                sys.exit(1)

    if len(candles) < 100:
        result = {
            "symbol": args.symbol,
            "period": args.period,
            "strategy": args.strategy,
            "summary": "insufficient_data",
            "trades": [],
            "metrics": {},
            "secrets_printed": False,
        }
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(f"# Strategy Backtest: {args.symbol} {args.period} ({args.strategy})\n")
            print("## Summary\ninsufficient_data\n")
        sys.exit(0)

    # Compute indicators
    rsi_vals = compute_rsi(candles, period=14)
    sma20 = compute_sma(candles, 20)
    sma50 = compute_sma(candles, 50)
    macd_data = compute_macd(candles, fast=12, slow=26, signal=9)

    # Simulate alerts (for alert_follow only)
    alerts = []
    if args.strategy == "alert_follow":
        alerts = [
            {"created_at": candles[50]["open_time"], "symbol": args.symbol},
            {"created_at": candles[75]["open_time"], "symbol": args.symbol},
        ]

    # Run strategy
    trades = []
    if args.strategy == "rsi_oversold_rebound":
        trades = strategy_rsi_oversold_rebound(candles, rsi_vals, sma20)
    elif args.strategy == "macd_bull_cross":
        trades = strategy_macd_bull_cross(candles, macd_data)
    elif args.strategy == "sma20_sma50_cross":
        trades = strategy_sma20_sma50_cross(candles, sma20, sma50)
    elif args.strategy == "volume_spike_continuation":
        trades = strategy_volume_spike_continuation(candles, sma20)
    elif args.strategy == "alert_follow":
        trades = strategy_alert_follow(candles, alerts)
    else:
        logging.error(f"Unknown strategy '{args.strategy}'")
        sys.exit(1)

    # Compute metrics
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = sum(t["pnl"] for t in trades)
    profit_factor = sum(t["pnl"] for t in trades if t["pnl"] > 0) / abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) if any(t["pnl"] < 0 for t in trades) and any(t["pnl"] > 0 for t in trades) else 1.0
    max_dd = 0.0
    equity = [args.initial_balance]
    for t in trades:
        equity.append(equity[-1] + t["pnl"])
        dd = (max(equity[:-1]) - equity[-1]) / max(equity[:-1]) if max(equity[:-1]) > 0 else 0
        max_dd = max(max_dd, dd)
    sharpe = 0.0
    if len(trades) > 1:
        returns = [t["pnl_pct"] for t in trades]
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return)**2 for r in returns) / len(returns))**0.5 if len(returns) > 1 else 0.0
        sharpe = avg_return / std_return if std_return > 0 else 0.0

    metrics = {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
    }

    result = {
        "symbol": args.symbol,
        "period": args.period,
        "strategy": args.strategy,
        "summary": f"{total_trades} trades executed",
        "trades": trades,
        "metrics": metrics,
        "secrets_printed": False,
    }

    # Output
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# Strategy Backtest: {args.symbol} {args.period} ({args.strategy})\n")
        print(f"## Summary\n{result['summary']}\n")
        print("## Metrics\n")
        for k, v in result["metrics"].items():
            print(f"- `{k}`: {v}")
        print()
        print("## Trades\n")
        for i, t in enumerate(result["trades"][:5]):
            print(f"### Trade #{i+1}")
            print(f"- Type: {t['type']}")
            print(f"- Entry: {t['entry_time']} @ ${t['entry_price']}")
            print(f"- Exit: {t['exit_time']} @ ${t['exit_price']}")
            print(f"- PnL: ${t['pnl']} ({t['pnl_pct']}%)")
            print(f"- Duration: {t['duration_hours']}h")
            print()
        if len(result["trades"]) > 5:
            print(f"... and {len(result['trades']) - 5} more trades")

if __name__ == "__main__":
    asyncio.run(main())
