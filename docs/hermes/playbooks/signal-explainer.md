# signal-explainer.md

## Цель

Объяснить, почему рынок выглядит bullish/bearish/neutral для заданного символа и периода — на основе технических индикаторов и паттернов.

## CLI

```bash
python scripts/signal_explainer.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 14 \
  --format markdown
```

## Выходы

### JSON

```json
{
  "symbol": "BTCUSDT",
  "period": "60min",
  "summary": "BTCUSDT 60min: moderately bullish",
  "reasons": [
    "SMA20 above SMA50",
    "RSI 62.3, not overbought",
    "MACD positive",
    "volume spike confirmed continuation"
  ],
  "risks": [
    "RSI approaching overbought",
    "latest candle has upper wick"
  ],
  "secrets_printed": false
}
```

### Markdown

```
# Signal Explainer: BTCUSDT 60min

## Summary
BTCUSDT 60min: moderately bullish

## Reasons
- SMA20 above SMA50
- RSI 62.3, not overbought
- MACD positive
- volume spike confirmed continuation

## Risks
- RSI approaching overbought
- latest candle has upper wick
```

## Поддерживаемые индикаторы

- RSI (Welles Wilder, 14)
- SMA20 / SMA50
- MACD (12,26,9)
- Volume spike (2× среднего за 24h)
- Volatility expansion (std dev > 2× базового)

## Безопасность

- Не требует запуска collector
- Не пишет в БД
- Не раскрывает секреты
- Работает с минимальным окружением (ленвые импорты)
- При недостатке данных → `insufficient_data`