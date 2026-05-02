#!/usr/bin/env bash
# scripts/doctor_full.sh — Full system diagnostic for Hermes
set -euo pipefail
cd /mnt/d/WORED

echo "=== DOCKER ==="
docker compose ps

echo ""
echo "=== WEBUI ROUTES ==="
curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK" || echo "/ FAIL"
curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK" || echo "/alerts FAIL"
curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK" || echo "/predictions FAIL"
curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK" || echo "/journal FAIL"

echo ""
echo "=== REDIS ==="
docker compose exec -T redis redis-cli ping || true
echo "ticker count:"
docker compose exec -T redis redis-cli --scan --pattern 'ticker:*' | wc -l || true
echo "journal ttl:"
docker compose exec -T redis redis-cli ttl ai:journal:latest || true

echo ""
echo "=== POSTGRES TABLES ==="
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"' || true

echo ""
echo "=== RECENT ERRORS ==="
docker compose logs --tail=300 | grep -Ei 'error|exception|traceback|failed|timeout|refused' || echo "No errors found"
