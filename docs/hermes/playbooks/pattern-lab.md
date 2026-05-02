# pattern-lab.md

## Цель
Исследовать повторяющиеся паттерны поведения рынка: например, "после volume_spike через 8 часов чаще рост на 2%".

## CLI
```bash
python scripts/pattern_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --pattern volume_spike \
  --forward-hours 8 \
  --lookback-days 90 \
  --group-by hour_of_day \
  --format markdown
```

## Поддерживаемые паттерны
- `volume_spike` — объём > 2× среднего за 24 часа
- `rsi_overbought` — RSI > 70
- `rsi_oversold` — RSI < 30
- `macd_bull_cross` — MACD пересекает сигнал снизу
- `macd_bear_cross` — MACD пересекает сигнал сверху
- `sma_bull_cross` — SMA20 пересекает SMA50 снизу
- `sma_bear_cross` — SMA20 пересекает SMA50 сверху
- `large_green_candle` — зелёная свеча > 2× средней длины
- `large_red_candle` — красная свеча > 2× средней длины
- `volatility_expansion` — std dev > 2× среднего

## Поддерживаемые group-by
- `none` — без группировки
- `hour_of_day` — час дня (0–23)
- `day_of_week` — день недели (1–7)
- `session` — азиатская/европейская/американская сессия
- `month` — январь, февраль...
- `volatility_regime` — низкая/средняя/высокая волатильность

## Метрики
| Метрика | Описание |
|----------|----------|
| `samples` | Количество найденных паттернов |
| `avg_forward_change_pct` | Среднее изменение цены через N часов |
| `median_forward_change_pct` | Медианное изменение |
| `win_rate_up` | % случаев, когда цена выросла |
| `win_rate_down` | % случаев, когда цена упала |
| `max_forward_gain_pct` | Максимальный рост |
| `max_forward_drawdown_pct` | Максимальное падение |
| `stddev_forward_change` | Стандартное отклонение |
| `best_group` | Группа с лучшим avg_forward_change |
| `worst_group` | Группа с худшим avg_forward_change |

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