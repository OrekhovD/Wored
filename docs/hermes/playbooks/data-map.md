# data-map.md

## Цель
Дать Hermes и WebUI карту доступности данных: что есть в Redis/Postgres, что stale, что нужно получать on-demand.

## Endpoint
GET `/api/runtime/data-map`

## Ответ
```json
{
  "redis": {
    "status": "ok",
    "ticker_count": 2,
    "journal_ttl": 84,
    "sample_ticker_keys": ["ticker:btcusdt", "ticker:ethusdt"]
  },
  "postgres": {
    "status": "ok",
    "tables": {
      "alerts": {"rows": 120, "latest": "2026-05-01T05:00:00Z"},
      "ai_journal": {"rows": 48, "latest": "2026-05-01T04:00:00Z"},
      "forecast_requests": {"rows": 18, "latest": "2026-05-01T04:30:00Z"},
      "candles": {"rows": 1000, "ranges": [...]}
    }
  },
  "external": {
    "htx_rest": "not_checked",
    "mode": "on_demand"
  },
  "policy": {
    "long_market_history": "on_demand",
    "durable_product_history": "postgres"
  }
}
```

## Требования
- Endpoint не должен падать при недоступности Redis/Postgres.
- При ошибке — возвращает `status: "error"`, а не 500.
- Не выводит секреты, API ключи, bearer tokens.
- Имеет timeout < 2s.

## Команды проверки
```bash
curl -fsS http://localhost:8080/api/runtime/data-map | python3 -m json.tool
docker compose exec -T redis redis-cli ping || echo "Redis down"
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dt"' || echo "Postgres down"
```

## Ошибки
- Если `redis_client` или `pool` == `None` — возвращаем `status: "error"`.
- Если таблица отсутствует — пишем `rows: 0`.
- Если `serialize_dt()` получает `None` — возвращает `null`.