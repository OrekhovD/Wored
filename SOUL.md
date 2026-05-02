# SOUL.md — WORED Security & Secrets Policy

## Security rules

Все правила из `AGENTS.md` (раздел "Security rules — DESTRUCTIVE GUARDRAILS") обязательны к исполнению.

Особое внимание — работе с секретами:
- ❌ Запрещено выводить содержимое `.env`, `.env.postgres`, `secrets/` в логи, ответы, файлы.
- ✅ Разрешено проверять *наличие* переменных через `grep -q 'POSTGRES_USER' .env && echo 'OK'`.
- ✅ Разрешено маскировать значения: `sed 's/=.*/=***/' .env.postgres`.
- ❌ Запрещено `cat .env`, `cat .env.postgres`, `grep SECRET .env`.

## Secrets handling

- Все учётные данные хранятся в `/mnt/d/WORED/secrets/`.
- Активные учётные данные для Postgres: `user=bot`, `db=trading` (см. `.env.postgres`).
- Telegram bot token и HTX API ключи — в `secrets/.env.telegram`, `secrets/.env.htx`.
- Hermes **не должен** читать или передавать эти файлы целиком — только проверять наличие и маскировать при отладке.

## Reference

Полный список правил и guardrails — в `docs/hermes/README.md`.

---
*Обновлено: $(date +'%Y-%m-%d %H:%M')*