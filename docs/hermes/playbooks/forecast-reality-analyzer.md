# forecast-reality-analyzer.md

## Цель

Оценить, какие AI-модели реально угадывают рыночные движения — на основе сравнения прогнозов с реальностью.

## CLI

```bash
python scripts/forecast_reality_analyzer.py --symbol BTCUSDT --days 30
```

## Выходы

### JSON

```json
{
  "symbol": "BTCUSDT",
  "days": 30,
  "summary": "187 forecasts analyzed",
  "direction_accuracy": 64.2,
  "avg_change_pct_error": 2.3456,
  "best_horizon": 24,
  "worst_horizon": 1,
  "avg_latency_ms": 1240.5,
  "fallback_impact": 12.3,
  "secrets_printed": false
}
```

### Markdown

```
# Forecast vs Reality: BTCUSDT (30d)

## Summary
187 forecasts analyzed

## Metrics
- Direction accuracy: 64.2%
- Avg change % error: 2.3456%
- Best horizon: 24h
- Worst horizon: 1h
- Avg latency: 1240.5ms
- Fallback impact: 12.3%
```

## Метрики

| Метрика | Как считается |
|----------|----------------|
| `direction_accuracy` | % совпадения направления прогноза (↑/↓) с реальным движением |
| `avg_change_pct_error` | Средняя абсолютная ошибка в % между прогнозом и реальностью |
| `best_horizon` / `worst_horizon` | Горизонт (в часах), где точность максимальна/минимальна |
| `avg_latency_ms` | Среднее время ответа модели (от `created_at` до `forecasted_at`) |
| `fallback_impact` | % случаев, когда модель fallback'нула, и на сколько это снизило точность |

## Безопасность

- Не пишет в БД
- Не требует collector
- Не раскрывает секреты (`model_used` выводится как `qwen-plus`, не как `qwen-plus@alibaba`)
- При пустых таблицах → `insufficient_data`
- Работает даже без `psycopg2` (логирует и выдаёт `insufficient_data`)