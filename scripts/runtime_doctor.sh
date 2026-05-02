#!/bin/bash

set -euo pipefail

echo "=== DOCKER ==="
docker compose ps

echo ""
echo "=== REDIS ==="
docker compose exec -T redis redis-cli ping || echo "Redis недоступен"
echo "ticker count:"
docker compose exec -T redis redis-cli --scan --pattern 'ticker:*' | wc -l || echo "0"
echo "sample ticker (btcusdt):"
docker compose exec -T redis redis-cli get ticker:btcusdt | head -n 5 || echo "ticker:btcusdt пуст или отсутствует"
echo "journal ttl:"
docker compose exec -T redis redis-cli ttl ai:journal:latest || echo "ai:journal:latest не существует"

echo ""
echo "=== POSTGRES ==="
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"' || echo "Postgres недоступен"
echo "latest alerts (count):"
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) FROM alerts WHERE status = \'open\';"' || echo "alerts таблица недоступна"
echo "latest ai_journal row:"
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, symbol, timestamp, market_context FROM ai_journal ORDER BY timestamp DESC LIMIT 1;"' || echo "ai_journal таблица недоступна"

echo ""
echo "=== COLLECTOR LOGS ==="
docker compose logs collector --tail=50 | grep -Ei 'error|exception|traceback|failed|timeout|refused' || echo "Нет ошибок в логах collector за последние 50 строк"

echo ""
echo "=== HEALTH SNAPSHOT ==="
curl -fsS http://localhost:8080/api/health | python3 -m json.tool 2>/dev/null || echo "/api/health недоступен"
