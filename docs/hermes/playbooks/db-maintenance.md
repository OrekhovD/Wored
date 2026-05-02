# db-maintenance.md

## Цель
Безопасное обслуживание PostgreSQL: вакуум, анализ, очистка старых записей, проверка индексов, контроль размера таблиц.

## Команды диагностики
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt+"'` — список таблиц с размерами
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT schemaname, tablename, last_vacuum, last_analyze FROM pg_stat_all_tables WHERE schemaname = \'public\' ORDER BY last_vacuum NULLS FIRST LIMIT 10;"'` — статус vacuum/analyze
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT indexrelname, idx_scan FROM pg_stat_user_indexes ORDER BY idx_scan ASC LIMIT 5;"'` — неиспользуемые индексы

## Файлы, которые можно трогать
- `docker-compose.yml` — параметры `postgres` сервиса (только `environment`, `volumes`, `healthcheck`)
- `scripts/db_maintenance.sql` — если существует (ручной скрипт для массовых операций)

## Файлы, которые нельзя трогать
- `migrations/` — любые миграции без явного подтверждения администратора
- `secrets/.env.postgres` — только чтение, запрет вывода в логи
- `webui/app.py` — никаких изменений в ORM-логике

## Критерии готовности
- Все таблицы имеют `last_vacuum` и `last_analyze` не старше 7 дней
- Нет таблиц > 500MB без индексов
- Нет индексов с `idx_scan = 0` более 3 дней

## Команды проверки
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM ANALYZE;"'`
- `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_size_pretty(pg_database_size(\'$POSTGRES_DB\'));"'`

## Когда остановиться и спросить владельца
- При обнаружении таблицы > 2GB
- При попытке удаления данных через `DELETE FROM ... WHERE ...` без `LIMIT`
- При любом изменении `pg_hba.conf` или `postgresql.conf`