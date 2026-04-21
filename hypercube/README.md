# Hypercube — Telegram AI Gateway для HTX

Telegram AI-шлюз для аналитики криптотрейдинга по бирже HTX. Поддерживает мульти-провайдерную маршрутизацию, учет токенов, сохранение контекста и устойчивость к отказам.

**Статус:** 🟡 Beta (hardening в процессе)

**Последнее обновление:** 2026-04-21

---

## 🚀 Особенности

- 🤖 **Multi-provider**: DashScope (Qwen), NVIDIA nvapi, Zhipu AI (GLM-5), Baidu AI Studio
- 📊 **HTX интеграция**: Чтение рыночных данных (цены, объемы, стакан, свечи)
- ⚙️ **Config-driven маршрутизация**: YAML-конфигурация цепочек кандидатов
- 🔄 **Fallback + Resilience**: Circuit breaker, retry, timeout для каждого провайдера
- 🧠 **Context handoff**: Версионирование схемы и валидация при передаче контекста
- 💰 **Учет токенов**: Подсчет токенов, квот, стоимости
- 🛡️ **Безопасность**: Read-only HTX API, секреты в `.gitignore`, политики ротации ключей

## 📚 Документация

Полная документация доступна в директории [`docs/`](docs/).

**Ключевые документы:**
- [📋 INDEX.md](docs/INDEX.md) — Полный индекс документации
- [🔐 Security](docs/14-security.md) — Политики безопасности
- [🎯 Provider Registry](docs/18-provider-validation-and-registry.md) — Конфигурация провайдеров
- [🛡️ Resilience](docs/23-provider-reliability.md) — Circuit breaker, retry, timeout
- [🧠 Context Handoff](docs/22-context-handoff-hardening.md) — Версионирование схемы
- [⚠️ Known Limits](docs/KNOWN_LIMITS.md) — Известные ограничения
- [📊 Provider Comparison](docs/PROVIDER_DIFF.md) — Сравнение провайдеров
- [🗑️ Deprecations](docs/DEPRECATIONS.md) — Устаревшие функции

---

1. Установите Python 3.12+
2. Установите Docker и Docker Compose

3. Скопируйте `.env.example` в `.env` и укажите свои ключи:
```bash
cp .env.example .env
# отредактируйте .env и добавьте свои API-ключи
```

4. Установите зависимости:
```bash
pip install -r requirements.txt
```

## Запуск

### Через Docker (рекомендуется):
```bash
make up
# или
docker compose -f docker/docker-compose.yml up --build -d
```

Сервис будет доступен на `http://localhost:8000`

### Локально:
```bash
# Инициализация БД
python scripts/db_init.py

# Запуск сервера
uvicorn app.main:app --reload
```

## Использование

1. Найдите бота в Telegram по токену из `.env`
2. Отправьте `/start` для начала
3. Используйте `/ask <ваш вопрос>` для аналитики HTX
4. Управляйте режимами через `/mode`

### Команды бота

- `/start` - Приветствие
- `/help` - Список команд
- `/ask <вопрос>` - Анализ рынка
- `/mode [free_only|balanced|premium]` - Режим маршрутизации
- `/models` - Доступные модели
- `/providers` - Состояние провайдеров
- `/usage` - Использование токенов
- `/quota` - Состояние квот
- `/context` - Состояние контекста
- `/switch_model <model_id>` - Ручная смена модели
- `/health` - Проверка системы
- `/admin_stats` - Статистика (только админу)

## Конфигурация

См. `.env.example` для полного списка настроек:

- `TELEGRAM_BOT_TOKEN` - Токен Telegram бота
- `HTX_API_KEY/SECRET` - Ключи для HTX (read-only)
- `DASHSCOPE_API_KEY` - Ключ для Qwen/DashScope
- `NVAPI_API_KEY` - Ключ для NVIDIA nvapi
- `GLM5_API_KEY` - Ключ для Zhipu AI GLM-5
- `AI_STUDIO_API_KEY` - Ключ для Baidu AI Studio
- `QUOTA_*_THRESHOLD_PCT` - Пороги предупреждений о квоте

## Архитектура

```
bot/          - Telegram handlers (aiogram)
app/          - FastAPI gateway
core/         - Общие схемы, конфиг, enums
providers/    - Адаптеры провайдеров AI и HTX
routing/      - Маршрутизация моделей
accounting/   - Учет токенов
quotas/       - Политики квот
context/      - Управление контекстом
storage/      - ORM, репозитории, миграции
```

## 🧪 Тестирование

```bash
# Запуск всех тестов
pytest -v

# Запуск unit тестов
pytest tests/unit/ -v

# Запуск integration тестов
pytest tests/integration/ -v

# Запуск smoke тестов
pytest tests/smoke/ -v

# Линтинг
flake8 .
```

**Статус тестов:** ✅ 36 тестов проходят (unit: 26, integration: 6, smoke: 4)

**Оставшиеся задачи:**
- ❌ Unit тесты для `providers/openai_compatible.py`
- ❌ Unit тесты для `context/service.py`
- ❌ Unit тесты для `bot/handlers.py`
- ❌ Integration тесты для HTX adapter

## Миграции БД

```bash
# Применить миграции
make migrate
# или
alembic upgrade head
```

## 📈 Статус релиза

**Текущий спринт:** WP-7 (Documentation Normalization)

**Завершено:**
- ✅ WP-1: Repository Audit And Gap Report
- ✅ WP-2: Security Cleanup And Secret Hygiene
- ✅ WP-3: Config-Driven Routing And Provider Registry
- ✅ WP-4: Context Handoff Hardening
- ✅ WP-5: Provider Reliability, Timeouts, Retries, Circuit Breakers
- ✅ WP-6: Quality Gates Closure
- 🔄 WP-7: Documentation Normalization And Encoding Repair

**Остается:**
- ⏳ WP-8: Weekly Refresh Activation
- ⏳ Release checklist and go/no-go report

См. [CHANGELOG_WEEKLY.md](CHANGELOG_WEEKLY.md) для полной информации.

---

## Лицензия

Apache 2.0