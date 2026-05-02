# Playbook: Diagnose Runtime

## Цель
Полная диагностика всех сервисов WORED и определение проблем.

## Шаги

### 1. Docker статус
```
/ps
```
Ожидание: все 5 сервисов Up.

### 2. WebUI health
```
/health
```
Ожидание: HTTP 200.

### 3. Collector логи
```
/lc
```
Искать: WS disconnect, GZIP errors, missed scheduler jobs.

### 4. Chatbot логи
```
/lb
```
Искать: TelegramUnauthorizedError, AI provider errors, Redis FSM errors.

### 5. WebUI логи
```
/lw
```
Искать: 500 errors, template errors, Redis/Postgres connection errors.

### 6. Redis tickers
```
/tickers
```
Ожидание: минимум 2 тикера (btcusdt, ethusdt).

### 7. AI Journal
```
/journal
```
Ожидание: свежая запись (TTL > 0, не -2).

### 8. PostgreSQL таблицы
```
/dbtables
```
Ожидание: 8 таблиц.

### 9. Алерты
```
/alerts
```
Проверить: есть ли новые, нет ли backlog.

### 10. Прогнозы
```
/forecasts
```
Проверить: нет ли stuck forecast_requests.

## Выходной формат

```
## Runtime Diagnosis

### Статус сервисов
- postgres: ✅/❌ [детали]
- redis: ✅/❌ [детали]
- collector: ✅/❌ [детали]
- chatbot: ✅/❌ [детали]
- webui: ✅/❌ [детали]

### Проблемы
1. [описание] → [вероятная причина] → [следующий шаг]

### Рекомендации
- [безопасное действие для исправления]
```

## Когда остановиться и спросить владельца
- Обнаружена утечка секретов в логах
- Несколько сервисов одновременно упали
- PostgreSQL данные повреждены
- Требуется `docker compose down -v`
- Требуется изменение runtime-кода

## Критерии готовности
Диагностика завершена, когда:
- Каждый сервис имеет статус ✅ или ❌
- Для каждого ❌ есть вероятная причина
- Для каждой причины есть безопасный следующий шаг
