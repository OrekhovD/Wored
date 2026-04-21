# Security Guide

## 1. Secret Management

### 1.1 Где хранить секреты
Все чувствительные данные хранятся **только** в `.env` файле в корне проекта. Этот файл **никогда** не должен быть закоммичен в git.

### 1.2 .env.example
Файл `.env.example` содержит безопасные placeholder-значения и является шаблоном для создания `.env`:

```bash
cp .env.example .env
# Отредактируйте .env и вставьте свои ключи
```

### 1.3 Переменные окружения с секретами
| Переменная | Описание | Где получить |
|-----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | @BotFather |
| `HTX_API_KEY` | HTX API ключ (read-only) | HTX Account → API Management |
| `HTX_API_SECRET` | HTX API секрет | HTX Account → API Management |
| `DASHSCOPE_API_KEY` | Qwen Cloud ключ | DashScope Console |
| `NVAPI_API_KEY` | NVIDIA nvapi ключ | NVIDIA NGC |
| `GLM5_API_KEY` | GLM-5 ключ | Zhipu AI Console |
| `AI_STUDIO_API_KEY` | Baidu AI Studio ключ | Baidu AI Studio Console |

### 1.4 Переменные БЕЗ секретов
Эти переменные безопасны и могут быть в примерах:
- `HTX_BASE_URL`
- `SQLITE_DB_URL`
- `GATEWAY_HOST`
- `GATEWAY_PORT`
- `DEFAULT_ROUTING_MODE`
- `LOG_LEVEL`
- `CONTEXT_MAX_TOKENS`
- `QUOTA_*_THRESHOLD_PCT`
- `REQUEST_TIMEOUT_SECONDS`
- `FALLBACK_RETRY_COUNT`
- `CACHING_ENABLED`
- `CACHE_TTL_SECONDS`
- `TELEGRAM_POLL_TIMEOUT`

## 2. Key Rotation Procedure

### 2.1 Когда ротировать ключи
- При подозрении на утечку
- Регулярно (каждые 90 дней)
- При смене персонала
- После публичного коммита с секретами

### 2.2 Порядок ротации
1. Создать новый ключ в панели провайдера
2. Обновить значение в `.env`
3. Перезапустить сервис: `docker compose -f docker/docker-compose.yml down && docker compose -f docker/docker-compose.yml up --build -d`
4. Проверить работоспособность: `/health`
5. Удалить старый ключ в панели провайдера

### 2.3 Экстренная ротация
При утечке ключей:
1. Немедленно отозвать скомпрометированные ключи в панели провайдера
2. Создать новые ключи
3. Обновить `.env`
4. Перезапустить сервис
5. Проверить логи на предмет несанкционированного использования

## 3. Логирование и безопасность

### 3.1 Что НЕ логируется
- API ключи и токены
- Bearer tokens
- Полные тексты запросов к AI (только метаданные)
- Чувствительные данные пользователей

### 3.2 Что логируется
- Request ID
- Provider ID
- Model ID
- Token counts (без содержимого)
- Latency
- Status (success/failure)
- Error codes (без деталей аутентификации)

## 4. Контроль доступа

### 4.1 Telegram Admin Access
Админ-доступ контролируется через `ADMIN_TELEGRAM_IDS` в `.env`. Только пользователи из этого списка могут использовать команды:
- `/reload`
- `/admin_stats`

### 4.2 Принцип наименьших привилегий
- HTX API ключи имеют только read-only доступ
- Нет разрешения на торговые операции
- Бот не хранит данные кошельков или ключи от биржи

## 5. .gitignore правила

Файл `.gitignore` исключает из git:
- `.env` и все варианты `.env.*`
- `data/` (база данных)
- `__pycache__/`
- `.venv/`
- `settings.json` (конфигурация Qwen Code)
- `docs/kluch.txt` (файл с ключами)

## 6. Проверка безопасности

### 6.1 Регулярная проверка
```bash
# Проверка на наличие ключей в репозитории
grep -r "sk-" --include="*.py" --include="*.md" --include="*.txt" --include="*.yaml" --include="*.json" . | grep -v ".gitignore" | grep -v ".env.example"

# Проверка .env не в git
git status .env
```

### 6.2 Аудит
Раз в месяц проверять:
- Нет ли секретов в git history
- Все ли ключи активны и нужны
- Не истекли ли сроки действия ключей