# backtest-mini-lab.md

## Цель

Простой бэктест стратегий без тяжёлых фреймворков — RSI, MACD, SMA, volume spike, alert-follow.

## CLI

```bash
python scripts/backtest_mini_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --strategy rsi_oversold_rebound \
  --lookback-days 90 \
  --initial-balance 10000 \
  --risk-pct 1.0 \
  --fee-pct 0.001 \
  --format markdown
```

## Поддерживаемые стратегии

- `rsi_oversold_rebound`: вход при RSI < 30 и цене ниже SMA20, выход через 4 часа или на SMA20
- `macd_bull_cross`: вход при пересечении MACD вверх, выход через 4 часа
- `sma20_sma50_cross`: вход при пересечении SMA20 вверх, выход через 4 часа
- `volume_spike_continuation`: вход при объёме > 2× среднего за 24ч и цене выше SMA20, выход через 4 часа
- `alert_follow`: вход при срабатывании алерта (например, `volume_spike`) и цене выше SMA20, выход через 4 часа

## Выходы

### JSON

```json
{
  "symbol": "BTCUSDT",
  "period": "60min",
  "strategy": "rsi_oversold_rebound",
  "summary": "17 trades executed",
  "trades": [
    {
      "type": "long",
      "entry_time": "2026-04-28T12:00:00+00:00",
      "entry_price": 61200.5,
      "exit_time": "2026-04-28T16:00:00+00:00",
      "exit_price": 61500.2,
      "pnl": 299.7,
      "pnl_pct": 0.489,
      "duration_hours": 4.0
    }
  ],
  "metrics": {
    "total_trades": 17,
    "win_rate": 58.82,
    "total_pnl": 1240.5,
    "profit_factor": 2.1,
    "max_drawdown_pct": 3.21,
    "sharpe_ratio": 1.42
  },
  "secrets_printed": false
}
```

### Markdown

```
# Strategy Backtest: BTCUSDT 60min (rsi_oversold_rebound)

## Summary
17 trades executed

## Metrics
- `total_trades`: 17
- `win_rate`: 58.82
- `total_pnl`: 1240.5
- `profit_factor`: 2.1
- `max_drawdown_pct`: 3.21
- `sharpe_ratio`: 1.42

## Trades

### Trade #1
- Type: long
- Entry: 2026-04-28T12:00:00+00:00 @ $61200.5
- Exit: 2026-04-28T16:00:00+00:00 @ $61500.2
- PnL: $299.7 (0.489%)
- Duration: 4.0h

... and 16 more trades
```

## Безопасность

- Не пишет в БД
- Не требует collector
- Не раскрывает секреты
- При недостатке данных → `insufficient_data`
- Работает даже без `asyncpg`/`httpx` — fallback на HTX или `insufficient_data`