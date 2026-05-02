# rollback.md

## Цель
Безопасный откат WORED до предыдущей стабильной версии: код, конфиги, база данных, Redis-состояние.

## Команды диагностики
- `git log --oneline -n 10` — последние коммиты
- `git status` — текущее состояние рабочей директории
- `docker compose ps` — статус контейнеров
- `docker compose exec -T redis redis-cli --scan --pattern 'ai:journal:*' | wc -l` — количество журналов ИИ
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) FROM predictions LIMIT 1;"'` — есть ли данные в `predictions`

## Файлы, которые можно трогать
- `git checkout <commit-hash>` — только для `webui/`, `collector/`, `chatbot/`
- `docker-compose.yml` — откат на предыдущую версию сервисов
- `scripts/rollback_to.sh` — если существует (автоматизированный скрипт)

## Файлы, которые нельзя трогать
- `secrets/.env.*` — запрет любых изменений без ручного подтверждения
- `redis` — полный `FLUSHALL` запрещён. Только selective delete по ключам.
- `postgres` — `DROP DATABASE` или `TRUNCATE TABLE` запрещены без `hermes rollback --dry-run` и подтверждения.

## Критерии готовности
- Все сервисы запущены (`docker compose ps | grep Up`)
- `/health` возвращает `200 OK`
- `/journal` возвращает не пустой JSON
- `/forecasts` содержит > 5 записей
- `curl -fsS http://localhost:8080/` отдаёт HTML без 500

## Команды проверки
- `git checkout <prev-commit>`
- `docker compose down && docker compose up -d`
- `curl -fsS http://localhost:8080/ >/dev/null && echo OK || echo FAIL`
- `docker compose logs webui --tail=20 | grep 'Application startup complete'`

## Когда остановиться и спросить владельца
- При обнаружении расхождения в схеме БД между текущей и целевой версией
- При наличии незакоммиченных изменений в `webui/` или `collector/`
- При попытке `FLUSHALL` в Redis
- При любом `DROP` или `TRUNCATE` в Postgres