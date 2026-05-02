# intelligence-brief.md

## Цель

Единый сводный отчёт для аналитика и трейдера — объединяет market context, алерты, прогнозы, риски, журнал ИИ и топ-паттерны.

## CLI

```bash
python scripts/intelligence_brief.py --mode hourly --symbols BTCUSDT,ETHUSDT --format markdown
```

## Поля вывода

| Поле | Описание |
|------|----------|
| `market_context` | RSI, SMA20/SMA50, MACD, объём, волатильность, тренд |
| `alerts` | Открытые алерты с типом, символом, severity и метаданными |
| `forecasts` | Прогнозы с точностью, ошибкой, моделью и статусом |
| `risks` | Активные риски (например, позиция > бюджет) |
| `journal_summary` | Краткое резюме журнала ИИ за период |
| `top_patterns` | Самые частые рыночные паттерны с win rate и avg move |
| `secrets_printed` | Всегда `false` — ни один секрет не раскрывается |

## Безопасность

- Не пишет в БД
- Не требует collector
- Не раскрывает API ключи, пароли, токены
- При ошибках — JSON с `error`, без traceback
- Работает даже без `asyncpg`/`httpx` — только Python stdlib