# Telegram Gateway Hardening

## Цель
Обеспечить безопасное использование Hermes через Telegram: только администраторы, без утечки секретов, с подтверждением destructive-команд.

## Требования
- Отдельный Telegram bot token (`TELEGRAM_ADMIN_BOT_TOKEN`) — не совпадает с `TELEGRAM_BOT_TOKEN` чатбота.
- Allowlist Telegram ID (`TELEGRAM_ADMIN_IDS`) — список `user_id`, разделённых запятыми.
- Запрещено использовать `.env` или `docker-compose.yml` в ответах.
- `/down`, `/rebuild`, `/restart` требуют подтверждения (`/down --yes`).
- Все admin-действия логируются в `logs/telegram-admin-actions.log`.
- Gateway отключён по умолчанию.

## Настройка

### 1. Создайте отдельного бота
- Через [@BotFather](https://t.me/BotFather) создайте нового бота `@wored_hermes_admin_bot`.
- Получите токен — сохраните его в `secrets/.env.telegram.admin`:
  ```env
  TELEGRAM_ADMIN_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqRSTuvwXYZ123456789
  ```

### 2. Добавьте allowlist
В `secrets/.env.telegram.admin` добавьте:
```env
TELEGRAM_ADMIN_IDS=123456789,987654321
```

### 3. Включите gateway (только после проверки)
В `~/.hermes/config.yaml` добавьте:
```yaml
telegram:
  enabled: true
  token_env_var: TELEGRAM_ADMIN_BOT_TOKEN
  allowlist_env_var: TELEGRAM_ADMIN_IDS
  log_path: /mnt/d/WORED/logs/telegram-admin-actions.log
  destructive_commands_require_confirmation: true
```

### 4. Проверьте правила безопасности
Убедитесь, что в `AGENTS.md` и `SOUL.md` прописано:
- ❌ Запрещено печатать `.env`, `docker-compose.yml`, `secrets/`
- ❌ Запрещено выполнять destructive-команды без подтверждения
- ✅ Все команды показывают план перед применением

## Подтверждение destructive-команд
Команды `/down`, `/rebuild`, `/restart` **не выполняются** без флага `--yes`:
```text
/down --yes
/restart --yes
/rebuild --yes
```

Если флаг не указан — Hermes вернёт:
> ⚠️ Опасная команда. Используйте `/down --yes` для подтверждения.

## Логирование
Все действия администраторов записываются в `logs/telegram-admin-actions.log` в формате:
```log
[2026-05-01 07:22:34] @admiral (123456789) → /doctor-full
[2026-05-01 07:23:11] @admiral (123456789) → /down --yes
```

## Отключение
Чтобы отключить gateway — установите `enabled: false` в `config.yaml` или удалите блок `telegram`.

---
⚠️ **ВНИМАНИЕ**: Telegram gateway НЕ заменяет чатбота. Это отдельный канал управления для администраторов.