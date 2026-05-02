# alert-outcome-tracker.md

## Цель

Оценить полезность алертов: сколько раз они сработали, какая была реакция рынка, и насколько часто это ложные срабатывания.

## CLI

```bash
python scripts/alert_outcome_tracker.py --symbol BTCUSDT --days 60
```

## Выходы

### JSON

```json
{
  "symbol": "BTCUSDT",
  "days": 60,
  "summary": "48 alerts analyzed",
  "by_alert_type": {
    "volume_spike": {
      "samples": 48,
      "avg_move_after_4h_pct": 0.62,
      "win_rate_up": 58.33,
      "win_rate_down": 29.17,
      "false_positive_rate": 12.5
    },
    "rsi_overbought": {
      "samples": 22,
      "avg_move_after_4h_pct": -0.41,
      "win_rate_up": 9.09,
      "win_rate_down": 63.64,
      "false_positive_rate": 27.27
    }
  },
  "secrets_printed": false
}
```

### Markdown

```
# Alert Outcome Tracker: BTCUSDT (60d)

## Summary
48 alerts analyzed

## Metrics by Alert Type

### `volume_spike`
- Samples: 48
- Avg move after 4h: 0.62%
- Win rate up: 58.33%
- Win rate down: 29.17%
- False positive rate: 12.5%

### `rsi_overbought`
- Samples: 22
- Avg move after 4h: -0.41%
- Win rate up: 9.09%
- Win rate down: 63.64%
- False positive rate: 27.27%
```

## Безопасность

- Не пишет в БД
- Не требует collector
- Не раскрывает секреты
- При пустых таблицах → `insufficient_data`
- Работает даже без `psycopg2` — выдаёт `insufficient_data` и завершается чисто