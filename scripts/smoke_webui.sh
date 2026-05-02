#!/bin/bash

set -euo pipefail

echo "=== DOCKER CONFIG ==="
docker compose config

echo ""
echo "=== WEBUI ROUTES ==="

# / route
if curl -fsS http://localhost:8080/ >/dev/null; then
  echo "/ OK"
else
  echo "/ FAIL"
  exit 1
fi

# /alerts
if curl -fsS http://localhost:8080/alerts >/dev/null; then
  echo "/alerts OK"
else
  echo "/alerts FAIL"
  exit 1
fi

# /predictions
if curl -fsS http://localhost:8080/predictions >/dev/null; then
  echo "/predictions OK"
else
  echo "/predictions FAIL"
  exit 1
fi

# /journal
if curl -fsS http://localhost:8080/journal >/dev/null; then
  echo "/journal OK"
else
  echo "/journal FAIL"
  exit 1
fi

echo ""
echo "=== WEBUI LOGS ==="
docker compose logs webui --tail=50 | head -n 20

echo ""
echo "=== CHECK FOR 500 ==="
if docker compose logs webui --tail=100 | grep -q "500"; then
  echo "ERROR: Found 500 in webui logs"
  exit 1
else
  echo "No 500 errors found"
fi

echo ""
echo "✅ Smoke test passed."
