# Playbook: Release Check

## Цель
Проверка готовности проекта к релизу. Выполняется перед каждым деплоем.

## Pre-release чеклист

### 1. Docker конфигурация
```bash
docker compose config
```
Ожидание: без ошибок, все сервисы определены.

### 2. Пересборка
```bash
docker compose up -d --build
```
Ожидание: все образы собраны без ошибок.

### 3. Все сервисы подняты
```bash
docker compose ps
```
Ожидание: 5/5 сервисов Up, postgres и webui — healthy.

### 4. WebUI роуты
```bash
curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK" || echo "/ FAIL"
curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK" || echo "/alerts FAIL"
curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK" || echo "/predictions FAIL"
curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK" || echo "/journal FAIL"
```
Ожидание: все 4 OK.

### 5. Redis
```bash
docker compose exec -T redis redis-cli ping
docker compose exec -T redis redis-cli --scan --pattern 'ticker:*' | wc -l
docker compose exec -T redis redis-cli ttl ai:journal:latest
```
Ожидание: PONG, ≥ 2 тикера, TTL > 0.

### 6. PostgreSQL
```bash
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
```
Ожидание: 8 таблиц.

### 7. Collector
```bash
docker compose logs collector --tail=30
```
Искать: WS connected, scheduler running, нет критичных ошибок.

### 8. Chatbot
```bash
docker compose logs chatbot --tail=30
```
Искать: polling started, bot identity получен, нет Unauthorized.

### 9. Ошибки
```bash
docker compose logs --tail=300 | grep -Ei 'error|exception|traceback|failed|timeout|refused' || echo "No errors found"
```
Ожидание: нет критичных ошибок.

### 10. Git
```bash
git status
git log --oneline -5
```
Проверить: нет незакоммиченных изменений (или они осознанные).

### 11. Диск
```bash
df -h /mnt/d
docker system df
```
Проверить: достаточно места.

## Результат

```
## Release Check: YYYY-MM-DD HH:MM

| Проверка | Статус |
|----------|--------|
| Docker config | ✅/❌ |
| Build | ✅/❌ |
| Services (5/5) | ✅/❌ |
| WebUI routes | ✅/❌ |
| Redis | ✅/❌ |
| PostgreSQL | ✅/❌ |
| Collector | ✅/❌ |
| Chatbot | ✅/❌ |
| No errors | ✅/❌ |
| Git clean | ✅/❌ |
| Disk space | ✅/❌ |

**Вердикт**: READY / NOT READY
**Блокеры**: [список]
```

## Когда остановиться и спросить владельца
- Любой сервис не поднимается
- Есть критичные ошибки
- Незакоммиченные изменения
- Недостаточно дискового пространства

## Критерии готовности
- Все 11 проверок пройдены
- Нет блокеров
- Вердикт: READY
