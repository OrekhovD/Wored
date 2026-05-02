# market-context.md

## Цель
Собрать для Hermes компактный аналитический market context по символу/периоду — без записи в БД.

## CLI
```bash
python scripts/market_context.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 14 \
  --format markdown
```

## Источники
1. Локальный Postgres (если есть нужные candles)
2. Redis cache (если подходит)
3. HTX REST через `fetch_history.py`
4. Пересчёт индикаторов локально

## Что считается
- SMA20 / SMA50
- RSI14
- MACD 12/26/9
- Volume average & spike ratio
- Volatility
- Max drawdown
- Trend direction
- High/low range

## Паттерны
| Тип | Условие |
|-----|---------|
| `trend_up` | close > open × 1.02 |
| `trend_down` | close < open × 0.98 |
| `sideways` | иначе |
| `volume_spike` | volume > avg_volume × 2.0 |
| `rsi_overbought` | RSI > 70 |
| `rsi_oversold` | RSI < 30 |
| `sma_bull_cross` | SMA20 пересекает SMA50 снизу |
| `sma_bear_cross` | SMA20 пересекает SMA50 сверху |
| `macd_bull_cross` | MACD пересекает сигнал снизу |
| `macd_bear_cross` | MACD пересекает сигнал сверху |
| `volatility_expansion` | std dev > 2× среднего |
| `drawdown` | падение от пика > 5% |
| `rebound` | рост после drawdown > 3% |

## Форматы вывода
### JSON
Возвращает полный объект `MarketContext`.

### Markdown
Человеко-читаемый формат для Hermes и WebUI.

## Безопасность
- Не требует запуска collector.
- Не пишет в БД по умолчанию.
- При недостатке данных → `insufficient_data`.
- Все HTTP-запросы имеют timeout=30s.

## Definition of Done
- ✅ Работает без записи в БД по умолчанию.
- ✅ Может вернуть JSON и Markdown.
- ✅ При недостатке данных пишет `insufficient_data`.
- ✅ Не требует запуска collector.