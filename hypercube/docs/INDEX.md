# Documentation Index

Централизованный индекс всей документации проекта Hypercube (Telegram AI Gateway для HTX).

## 📋 Задания (Task Specifications)

| Документ | Описание | Статус |
|----------|----------|--------|
| [TZ-QWENCODE-HTX-TELEGRAM-AI-GATEWAY.md](TZ-QWENCODE-HTX-TELEGRAM-AI-GATEWAY.md) | Оригинальное задание на разработку шлюза | ✅ Завершено |
| [TZ-QWENCODE-02-RELEASE-HARDENING-AND-VALIDATION.md](TZ-QWENCODE-02-RELEASE-HARDENING-AND-VALIDATION.md) | Задание на hardening и валидацию релиза | 🔄 В работе |

## 🏗️ Архитектура и дизайн

| Документ | Описание |
|----------|----------|
| [06-final-documentation.md](06-final-documentation.md) | Финальная документация по архитектуре системы |
| [03-phase3-plan.md](03-phase3-plan.md) | План Phase 3 разработки |

## 🔐 Безопасность

| Документ | Описание |
|----------|----------|
| [14-security.md](14-security.md) | Политики безопасности, управление секретами, ротация ключей |
| [16-gap-audit.md](16-gap-audit.md) | Аудит проблем и разрывов в архитектуре и безопасности |
| [17-security-remediation.md](17-security-remediation.md) | Исправление проблем безопасности (SEC-001 — SEC-005) |

## 🎯 Маршрутизация и провайдеры

| Документ | Описание |
|----------|----------|
| [18-provider-validation-and-registry.md](18-provider-validation-and-registry.md) | Валидация провайдеров и YAML-реестр конфигурации |
| [19-chain-builder.md](19-chain-builder.md) | ChainBuilder для построения цепочек кандидатов |
| [20-provider-manager.md](20-provider-manager.md) | ProviderManager для управления провайдерами |
| [21-model-registry.md](21-model-registry.md) | ModelRegistry для config-driven загрузки моделей |
| [23-provider-reliability.md](23-provider-reliability.md) | Circuit Breaker, Retry, Timeout для устойчивости провайдеров |

## 🧠 Контекст и сессии

| Документ | Описание |
|----------|----------|
| [22-context-handoff-hardening.md](22-context-handoff-hardening.md) | Версионирование схемы и валидация для передачи контекста |

## ✅ Quality Gates

| Документ | Описание |
|----------|----------|
| [24-quality-gates-closure.md](24-quality-gates-closure.md) | Отчет о закрытии quality gates и тестировании |

## 📁 Структура проекта

```
D:\WORED\hypercube\
├── docs/                    # Документация
│   ├── INDEX.md            # Этот файл
│   ├── TZ-*.md             # Задания
│   ├── 03-*.md             # Планы
│   ├── 06-*.md             # Архитектура
│   ├── 14-security.md      # Безопасность
│   ├── 16-gap-audit.md     # Аудит
│   ├── 17-security-remediation.md
│   ├── 18-24-*.md          # Реализация
│   └── kluch.txt           # Секретные ключи (в .gitignore!)
├── app/                     # FastAPI gateway
├── bot/                     # Telegram handlers (aiogram)
├── core/                    # Общие схемы, конфиг, enums
├── providers/               # Адаптеры провайдеров AI и HTX
├── routing/                 # Маршрутизация моделей
├── accounting/              # Учет токенов
├── quotas/                  # Политики квот
├── context/                 # Управление контекстом
├── storage/                 # ORM, репозитории, миграции
├── admin/                   # Админские инструменты
├── tests/                   # Тесты (unit, integration, smoke)
├── scripts/                 # Скрипты (db_init.py, fix_encoding.py)
├── alembic/                 # Миграции БД
├── docker/                  # Dockerfile и docker-compose.yml
├── examples/                # Примеры конфигурации
└── .env                     # Переменные окружения (в .gitignore!)
```

## 🔗 Быстрые ссылки

- [README.md](../README.md) — Главная страница проекта
- [CHANGELOG_WEEKLY.md](../CHANGELOG_WEEKLY.md) — Еженедельные изменения
- [Makefile](../Makefile) — Команды для разработки
- [requirements.txt](../requirements.txt) — Python зависимости
- [pyproject.toml](../pyproject.toml) — Конфигурация проекта

## 📊 Статус документации

| Категория | Файлов | Статус |
|-----------|--------|--------|
| Задания | 2 | ✅ Актуально |
| Архитектура | 2 | ✅ Актуально |
| Безопасность | 3 | ✅ Актуально |
| Маршрутизация | 5 | ✅ Актуально |
| Контекст | 1 | ✅ Актуально |
| Quality Gates | 1 | ✅ Актуально |
| **Всего** | **14** | **✅ 100%** |

## 🔄 Еженедельное обновление

Согласно [AGENTS.md](../AGENTS.md), документация обновляется еженедельно по понедельникам в 09:00 Asia/Bangkok.

Требуемые документы еженедельного обновления:
- `CHANGELOG_WEEKLY.md`
- `docs/KNOWN_LIMITS.md`
- `docs/PROVIDER_DIFF.md`
- `docs/DEPRECATIONS.md`

## 📝 Вклад в документацию

При добавлении новой документации:
1. Создайте файл с номером в формате `NN-description.md`
2. Добавьте запись в этот INDEX.md
3. Обновите статус в таблице выше
4. Убедитесь, что кодировка UTF-8

## 🛠️ Утилиты

- [`scripts/fix_encoding.py`](../scripts/fix_encoding.py) — Исправление кодировки файлов на UTF-8
