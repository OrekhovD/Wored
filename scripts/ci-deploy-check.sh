#!/bin/bash

set -euo pipefail

echo "🚀 Starting CI/CD deploy check..."

echo ""
echo "=== STEP 1: REBUILD WEBUI ==="
docker compose up -d --build webui

echo ""
echo "=== STEP 2: WAIT FOR WEBUI HEALTHY ==="
for i in {1..60}; do
  if docker compose ps | grep webui | grep -q "healthy"; then
    echo "✅ webui is healthy"
    break
  fi
  if [ $i -eq 60 ]; then
    echo "❌ webui failed to become healthy after 60s"
    exit 1
  fi
  sleep 1
done

echo ""
echo "=== STEP 3: RUN DOCTOR-FULL ==="
/mnt/d/WORED/.hermes/bin/hermes quick doctor-full 2>/dev/null || {
  echo "❌ /doctor-full failed"
  exit 1
}

echo ""
echo "✅ CI/CD deploy check passed. Ready for production."
