#!/bin/bash
# =============================================
# ⚓ HERMES — WORED Launch Script
# =============================================

HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes"
WORED_DIR="/mnt/d/WORED"

cd "$WORED_DIR" || exit 1
echo "🤖 Запуск Hermes CLI для WORED (Ollama Cloud)..."
exec "$HERMES_BIN" chat --cli
