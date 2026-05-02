# Prediction Lab: Quality View

## Цель
Улучшить отображение качества прогнозов в `/predictions`: не только «что предсказали», а «кто был точнее».

## Команды диагностики
- `redis-cli lrange forecasts:latest 0 9` — последние прогнозы
- `redis-cli lrange actuals:latest 0 9` — последние фактические значения
- `docker compose logs collector --tail=50 | grep -i 'forecast.*sent'` — логи отправки
- `psql -U bot -d trading -c "SELECT * FROM predictions ORDER BY created_at DESC LIMIT 5;"` — данные из БД

## Файлы, которые можно трогать
- `webui/templates/predictions.html` — добавление колонок `direction_hit`, `change_pct_error`, `latency`
- `webui/app.py` — расширение `/predictions` endpoint для возврата `provider`, `fallback`, `actual_value`, `error`
- `webui/static/styles.css` — стили для новых колонок (цвета: green/red для hit/miss)

## Файлы, которые нельзя трогать
- `webui/static/app.js` — без явного подтверждения адмирала
- `collector/forecast.py` — логика генерации, не отображения
- `models/` — ML-модели

## Критерии готовности
- Вывод `/predictions` содержит:
  - `direction_hit: true/false`
  - `change_pct_error: ±X.X%`
  - `forecast vs actual` рядом
  - `provider: qwen / glm / minimax`
  - `latency_ms: N`
- Все существующие функции работают (без 500/JS ошибок)

## Команды проверки
```bash
curl -fsS http://localhost:8080/predictions | grep -E '(direction_hit|change_pct_error|provider|latency)' || echo "MISSING FIELDS"
docker compose logs webui --tail=20 | grep -i error || echo "NO WEBUI ERRORS"
```

## Когда остановиться и спросить владельца
- Если `actuals:latest` пуст или устарел >5 мин — запросить ручной запуск `collector --force-actuals`.
- Если `psql` возвращает `permission denied` — запросить обновление `POSTGRES_USER=bot` в `docker-compose.yml`.
- Если в `predictions.html` требуется изменение `app.js` — **остановиться и спросить**.