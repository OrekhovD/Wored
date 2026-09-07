#!/usr/bin/env bash
# WORED tunnel fixer — находит актуальный quick-tunnel URL из логов cloudflared,
# обновляет .env / .env.wored и перезапускает ботов.
# Запуск: bash fix_tunnel.sh
set -euo pipefail

cd /d/WORED

echo "=== WORED tunnel fixer ==="

# 1. Достаём актуальный URL из логов cloudflared
URL=$(docker logs htx_trading_bot_tunnel 2>&1 | grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)

if [ -z "$URL" ]; then
  echo "❌ Не нашёл URL в логах tunnel. Перезапускаю tunnel..."
  docker restart htx_trading_bot_tunnel >/dev/null 2>&1
  sleep 12
  URL=$(docker logs htx_trading_bot_tunnel 2>&1 | grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
fi

if [ -z "$URL" ]; then
  echo "❌ Всё ещё нет URL. Проверь tunnel вручную: docker logs htx_trading_bot_tunnel"
  exit 1
fi

echo "✅ Актуальный URL: $URL"

# 2. Проверяем что tunnel отвечает
if ! curl -fsS "$URL/api/health" --max-time 10 >/dev/null 2>&1; then
  echo "⚠️ Tunnel не отвечает на $URL/api/health"
fi

# 3. Обновляем .env и .env.wored
NEW_MINIAPP="$URL/command-deck"
for ENVFILE in .env .env.wored; do
  if [ -f "$ENVFILE" ]; then
    # Заменяем только строку TG_MINIAPP_URL
    if grep -q "^TG_MINIAPP_URL=" "$ENVFILE"; then
      sed -i "s|^TG_MINIAPP_URL=.*|TG_MINIAPP_URL=$NEW_MINIAPP|" "$ENVFILE"
      echo "✅ $ENVFILE обновлён"
    else
      echo "TG_MINIAPP_URL=$NEW_MINIAPP" >> "$ENVFILE"
      echo "✅ $ENVFILE — добавлена строка"
    fi
  fi
done

# 4. Перезапускаем ботов чтобы подхватили новый URL
echo "=== Перезапуск ботов ==="
cmd.exe /c "docker compose -f D:\\WORED\\docker-compose.yml up -d chatbot chatbot_wored --no-build" 2>&1 | tail -3

echo ""
echo "=== Готово ==="
echo "Mini App URL: $NEW_MINIAPP"
echo "Открой бота в Telegram → ⚡ Command Deck"
