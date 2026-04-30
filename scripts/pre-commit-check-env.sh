#!/bin/bash
# Pre-commit hook to prevent committing .env files

BLOCKED_FILES=(".env" ".env.postgres")

for file in "${BLOCKED_FILES[@]}"; do
    if git diff --cached --name-only | grep -q "^${file}$"; then
        echo "❌ [BLOCKED] Попытка закоммитить файл ${file}!"
        echo "⚠️  Секреты нельзя хранить в репозитории."
        echo "💡 Если это сделано случайно, удалите файл из индекса: git reset HEAD ${file}"
        exit 1
    fi
done

exit 0
