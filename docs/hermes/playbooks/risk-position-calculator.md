# risk-position-calculator.md

## Цель

Расчёт размера позиции, максимального убытка и соотношения риск/прибыль — для WebUI и Telegram.

## CLI

```bash
python scripts/risk_position.py \
  --balance 1000 \
  --risk-pct 1 \
  --entry 62000 \
  --stop 61000 \
  --take 65000
```

## Входные параметры

| Параметр | Описание |
|----------|----------|
| `--balance` | Общий баланс (USD) |
| `--risk-pct` | Процент риска от баланса |
| `--entry` | Цена входа |
| `--stop` | Стоп-лосс |
| `--take` | Тейк-профит |

## Выходные поля

| Поле | Описание |
|------|----------|
| `position_size` | Количество единиц актива (например, BTC), которое можно купить/продать |
| `max_loss` | Максимальный убыток в USD при срабатывании стопа |
| `risk_reward` | Отношение потенциальной прибыли к убытку (`(take - entry) / (entry - stop)`) |
| `warnings` | Список предупреждений (`["invalid_stop"]`) |
| `secrets_printed` | `false` — ни один секрет не выведен |

## Примеры вывода

### Корректный расчёт

```json
{
  "position_size": 1.0,
  "max_loss": 10.0,
  "risk_reward": 4.0,
  "warnings": [],
  "secrets_printed": false
}
```

### Ошибка ввода

```json
{
  "error": "stop_loss must be < entry",
  "secrets_printed": false
}
```

## Безопасность

- Не пишет в БД
- Не требует collector
- Не раскрывает секреты
- При ошибке — JSON с `error`, без traceback
- Работает даже без внешних зависимостей