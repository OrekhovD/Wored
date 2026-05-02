# fetch-history.md

## Цель
Получать рыночную историю по запросу, не сохраняя всё постоянно.

## CLI
```bash
python scripts/fetch_history.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --mode preview
```

## Режимы
| Режим | Что делает |
|-------|-----------|
| `preview` | Показывает, что будет получено — без записи. |
| `json` | Выводит нормализованные candles в JSON. |
| `cache` | Кладёт результат в Redis с TTL (по умолчанию 1h). |
| `store` | Пишет в Postgres `candles` таблицу — **только после подтверждения**. |

## Аргументы
- `--symbol`: `BTCUSDT`, `ETHUSDT` (обязательно)
- `--period`: `1min`, `5min`, `15min`, `60min`, `4hour`, `1day`
- `--from`, `--to`: ISO даты (`2026-04-30T00:00:00Z`)
- `--lookback-days`: сколько дней назад брать данные (по умолчанию 7)
- `--limit`: максимум свечей (по умолчанию 2000)

## Безопасность
- `--mode store` требует ручного подтверждения (`y/N`).
- Скрипт не читает `.env`, не выводит секреты.
- При ошибках HTX API — выводится понятное сообщение, а не traceback.
- Все HTTP-запросы имеют timeout=30s.

## Ошибки
- Если HTX недоступен → `❌ Failed to fetch candles: ...`
- Если Redis/Postgres недоступны → `❌ Redis cache failed: ...`
- Если `--mode store` отменён → `❌ Cancelled by user.`

## Definition of Done
- ✅ `mode preview` не пишет в Redis/Postgres.
- ✅ `mode json` возвращает валидный JSON.
- ✅ `mode cache` пишет только временный Redis key с TTL.
- ✅ `mode store` не запускается случайно.
- ✅ Ошибка HTX API выводится понятно.