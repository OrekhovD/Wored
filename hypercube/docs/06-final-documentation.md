# Phase 6: Final Documentation

## Сервисы

### 1. Telegram Bot
- **Функция**: UI и контрольная плоскость
- **Технология**: aiogram 3.x
- **Команды**: /start, /ask, /mode, /models, /usage и др.
- **Типичный сценарий**: Пользователь отправляет /ask, бот нормализует запрос → вызывает HTX → выбирает модель → отправляет → сохраняет контекст

### 2. AI Gateway (FastAPI)
- **Функция**: Внутренний API, health checks, внутренние вызовы
- **Порты**: 8000
- **Эндпоинты**:
  - `GET /health`, `GET /health/deep`
  - `POST /internal/ask`, `POST /internal/switch-model`
  - `GET /internal/providers`, `GET /internal/models`

### 3. HTX Data Adapter
- **Функция**: Чтение рыночных данных (цены, объемы, стакан, свечи)
- **Типичный вызов**: `get_ticker(symbol)`, `get_klines(symbol, interval)`
- **Ограничения**: Только read-only, без торговых операций

### 4. Routing Engine
- **Функция**: Выбор модели по политике, fallback при ошибках
- **Политики**: free_only, balanced, premium
- **Алгоритм**:
  1. Получает цепочку кандидатов по политике
  2. Проверяет квоту и здоровье провайдера
  3. Выполняет вызов первой доступной модели
  4. При ошибке переходит к следующей

### 5. Context Service
- **Функция**: Управление состоянием сессии, сохранение/восстановление контекста
- **Компоненты**:
  - `ContextService`: история сообщений, сессии
  - `HandoffBuilder`: создание пакета передачи при смене модели

### 6. Accounting Service
- **Функция**: Запись использования токенов, подсчет стоимости
- **События**: Каждый вызов AI → запись в usage_records

### 7. Quota Engine
- **Функция**: Проверка лимитов, предупреждения, hard stop
- **Пороги**:
  - Warning: 20%
  - Critical: 10%
  - Hard Stop: 3%

## Конфигурация

### .env переменные

| Переменная | Описание | Пример |
|------------|----------|---------|
| TELEGRAM_BOT_TOKEN | Токен бота | 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 |
| HTX_API_KEY | HTX API ключ (read-only) | 16117211-... |
| DASHSCOPE_API_KEY | Qwen Cloud ключ | sk-... |
| NVAPI_API_KEY | NVIDIA nvapi ключ | nvapi-... |
| GLM5_API_KEY | GLM-5 ключ | ... |
| DEFAULT_ROUTING_MODE | Режим по умолчанию | free_only |
| QUOTA_WARNING_THRESHOLD_PCT | Порог предупреждения | 20.0 |

## Тестирование

### Unit-тесты
- Модули тестируются изолированно
- В `tests/unit/`

### Integration-тесты
- Проверка взаимодействия между модулями
- В `tests/integration/`

### Smoke-тесты
- Проверка минимального вертикального слайса
- Запуск: `make test-smoke`

## Запуск

### Через Docker (рекомендуется)
```bash
make up
# или
docker compose -f docker/docker-compose.yml up --build -d
```

### Локально
```bash
# Установка зависимостей
pip install -r requirements.txt

# Инициализация БД
python scripts/db_init.py

# Запуск
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Мониторинг и диагностика

### Логи
- Уровень: определяется через LOG_LEVEL
- Формат: structured JSON (планируется)

### Health Checks
- `/health` - базовая проверка
- `/health/deep` - проверка всех зависимостей

### Аудит
- Все вызовы AI логируются в `usage_records`
- События маршрутизации в `route_decisions`
- События передачи контекста в `context_handoffs`

## Безопасность

- API-ключи только в `.env`, не в коде
- Админ-команды только для allowlist'а Telegram ID
- Нет логирования токенов или чувствительных данных
- HTX интеграция только read-only

## Ограничения

- Только биржа HTX
- Только аналитика, без торговли
- SQLite для локального запуска (подготовлен для миграции в Postgres)
- Один пользователь (расширяемо)