#!/usr/bin/env bash
# WORED Codex review launcher — запускает Codex CLI с Docker-доступом.
#
# Почему так: Codex Desktop (Windows sandbox) блокирует named pipe
# \\.\pipe\docker_engine независимо от config.toml (GitHub issue #41415).
# CLI с --sandbox danger-full-access Docker-доступ даёт (issue #25076).
#
# Использование:
#   bash codex_review.sh "промпт для ревью"
#   bash codex_review.sh "промпт" --model gpt-5.5
set -euo pipefail

CODEX_BIN="${CODEX_BIN:-codex}"
WORKDIR="D:/WORED"

if [ $# -eq 0 ]; then
  echo "Usage: bash codex_review.sh \"<prompt>\" [--model <model>] [extra codex flags...]" >&2
  exit 1
fi

PROMPT="$1"
shift

cd "$WORKDIR"

# danger-full-access = без sandbox-ограничений (нужно для Docker pipe)
# --skip-git-repo-check = не требовать чистый git (ревью идёт по diff)
exec "$CODEX_BIN" exec \
  --sandbox danger-full-access \
  --skip-git-repo-check \
  "$@" \
  "$PROMPT"
