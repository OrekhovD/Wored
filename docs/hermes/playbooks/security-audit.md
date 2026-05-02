# Playbook: Security Audit

## Цель
Проверка безопасности проекта WORED — утечки секретов, неправильный доступ, уязвимости.

## Чеклист

### 1. .env не в git
```bash
git ls-files | grep -E '\.env$|\.env\.'
```
Ожидание: пустой вывод (ни один .env не отслеживается).

```bash
cat .gitignore | grep -E '\.env'
```
Ожидание: `.env` и `.env.postgres` в .gitignore.

### 2. Нет ключей в git истории
```bash
git log --all --diff-filter=A -- '*.env' '*.env.*' --oneline
git log -p --all -S 'api_key' -- '*.py' '*.yml' '*.yaml' | head -100
```
Ожидание: нет коммитов с реальными ключами.

### 3. Docker compose не раздаёт секреты лишним контейнерам
```bash
grep -A5 'env_file' docker-compose.yml
```
Проверить:
- collector и chatbot получают `.env` — OK (нужны API ключи)
- webui получает `.env` — проверить, нужны ли все ключи
- postgres получает `.env.postgres` — OK (только DB creds)
- redis не получает env_file — OK

### 4. WebUI auth
```bash
curl -fsS http://localhost:8080/ -o /dev/null -w '%{http_code}'
```
Проверить: требуется ли авторизация для sensitive роутов.

Проверить `.env` на `WEBUI_ADMIN_PASSWORD` (presence only):
```bash
grep WEBUI_ADMIN_PASSWORD /mnt/d/WORED/.env | sed 's/=.*/=***/'
```

### 5. PostgreSQL least privilege
```bash
cat .env.postgres
```
Проверить:
- POSTGRES_USER не `postgres` (суперпользователь)
- POSTGRES_DB отдельная база
- Пароль не дефолтный

### 6. Hermes secrets permissions
```bash
ls -la ~/.hermes/.env 2>/dev/null
stat -c '%a' ~/.hermes/.env 2>/dev/null
```
Ожидание: 600 или 400 (только владелец читает).

### 7. Quick commands без секретных утечек
Проверить каждую quick_command в `~/.hermes/config.yaml`:
- Нет команд типа `cat .env`
- Нет команд с `echo $SECRET`
- Нет команд, печатающих полные ключи

### 8. Telegram gateway
- Проверить: включён ли Telegram gateway в Hermes
- Если да — проверить allowlist, запрет destructive команд
- По умолчанию должен быть отключён

### 9. Pre-commit hook
```bash
ls -la .git/hooks/pre-commit
cat .git/hooks/pre-commit | head -20
```
Ожидание: hook блокирует коммит .env файлов.

### 10. Docker образы
```bash
docker images | grep wored
```
Проверить: нет ли `latest` тегов вместо фиксированных версий (для postgres:16 — OK).

## Типовые проблемы

### .env в git истории
**Решение**: `git filter-branch` или BFG Repo-Cleaner. Требует подтверждения!

### Слабый пароль PostgreSQL
**Решение**: обновить в `.env.postgres`, пересоздать контейнер.

### WebUI без авторизации
**Решение**: добавить session-based auth (уже реализовано? проверить).

### Hermes .env доступен всем
**Решение**: `chmod 600 ~/.hermes/.env`

## Когда остановиться и спросить владельца
- Обнаружены секреты в git истории (нужен force-push)
- Требуется ротация API ключей
- Требуется изменение Docker конфигурации
- Обнаружена критичная уязвимость

## Критерии готовности
- .env не в git
- Нет ключей в истории
- Docker compose раздаёт секреты минимально
- WebUI авторизация работает
- PostgreSQL не использует дефолтные креды
- Hermes secrets с правильными правами
- Quick commands не выводят секреты
- Pre-commit hook на месте
