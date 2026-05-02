#!/usr/bin/env bash
# scripts/runtime_snapshot.sh
# Gathers project state (Docker, Git, WebUI, Redis, Postgres, Logs) safely.
# No secrets will be leaked.

set -uo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SNAPSHOT_DIR="runtime_snapshot/$TIMESTAMP"

mkdir -p "$SNAPSHOT_DIR"
echo "Creating snapshot in $SNAPSHOT_DIR..."

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# 1. Compose PS
echo "Gathering docker compose ps..."
docker compose ps > "$SNAPSHOT_DIR/compose_ps.txt" 2>&1

# 2. Git Status
echo "Gathering git status..."
{
    echo "Branch:"
    git branch --show-current
    echo -e "\nStatus:"
    git status --short
    echo -e "\nDiff Stat:"
    git diff --stat
} > "$SNAPSHOT_DIR/git_status.txt" 2>&1

# 3. WebUI Routes
echo "Checking WebUI routes..."
{
    curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK" || echo "/ FAIL"
    curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK" || echo "/alerts FAIL"
    curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK" || echo "/predictions FAIL"
    curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK" || echo "/journal FAIL"
} > "$SNAPSHOT_DIR/webui_routes.txt" 2>&1

# 4. Redis Summary
echo "Gathering Redis summary..."
{
    echo "Ping:"
    docker compose exec -T redis redis-cli ping || echo "Redis unreachable"
    echo -e "\nTicker Count:"
    docker compose exec -T redis redis-cli keys "ticker:*" | wc -l || echo "0"
    echo -e "\nai:journal:latest TTL:"
    docker compose exec -T redis redis-cli ttl "ai:journal:latest" || echo "Not found"
    echo -e "\nSample Keys:"
    docker compose exec -T redis redis-cli keys "*" | head -n 10 || echo "Empty"
} > "$SNAPSHOT_DIR/redis_summary.txt" 2>&1

# 5. Postgres Summary
echo "Gathering Postgres summary..."
POSTGRES_USER=${POSTGRES_USER:-bot}
POSTGRES_DB=${POSTGRES_DB:-trading}
docker compose exec -T postgres sh -lc "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c '\dt'" > "$SNAPSHOT_DIR/postgres_summary.txt" 2>&1 || echo "Postgres \dt failed"
docker compose exec -T postgres sh -lc "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c 'SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;'" >> "$SNAPSHOT_DIR/postgres_summary.txt" 2>&1 || echo "Postgres stats failed"

echo "Gathering Postgres latest alerts..."
docker compose exec -T postgres sh -lc "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c 'SELECT id, symbol, alert_type, severity, status, created_at FROM alerts ORDER BY created_at DESC LIMIT 20;'" > "$SNAPSHOT_DIR/latest_alerts.txt" 2>&1 || echo "alerts unavailable" > "$SNAPSHOT_DIR/latest_alerts.txt"

echo "Gathering Postgres latest forecasts..."
docker compose exec -T postgres sh -lc "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c 'SELECT id, symbol, horizon_hours, status, created_at FROM forecast_requests ORDER BY created_at DESC LIMIT 20;'" > "$SNAPSHOT_DIR/latest_forecasts.txt" 2>&1 || echo "forecast_requests unavailable" > "$SNAPSHOT_DIR/latest_forecasts.txt"

# 6. Recent Errors
echo "Gathering recent errors from logs..."
docker compose logs --tail=400 2>&1 | grep -Ei 'error|exception|traceback|failed|timeout|refused' > "$SNAPSHOT_DIR/recent_errors.txt" || true

# 7. Metadata & README
echo "Writing metadata..."
cat <<EOF > "$SNAPSHOT_DIR/metadata.json"
{
  "timestamp": "$TIMESTAMP",
  "project": "WORED",
  "snapshot_version": "1.0"
}
EOF

cat <<EOF > "$SNAPSHOT_DIR/README.txt"
Runtime Snapshot Bundle
Created: $TIMESTAMP

Contains safe, read-only diagnostic information about the WORED runtime environment.
EOF

# 8. Masking secrets across all txt files just in case
echo "Masking any potential secrets..."
for file in "$SNAPSHOT_DIR"/*.txt; do
    sed -i -E 's/(API_KEY|TOKEN|SECRET|PASSWORD)=([^[:space:]]+)/\1=***MASKED***/g' "$file"
done

echo "Snapshot Bundle successfully created at $SNAPSHOT_DIR"
exit 0
