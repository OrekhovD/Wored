# WORED Wiki

WORED — это local-first Telegram-бот для мониторинга крипторынка и AI-аналитики по данным HTX. Проект объединяет сбор рыночных данных, логирование событий, Telegram UI и веб-панель управления.

## Что находится в проекте

- `collector` — ingestion и обработка данных HTX, алерты, таймеры, market feed
- `chatbot` — Telegram-бот на `aiogram 3`
- `webui` — FastAPI + браузерная панель управления
- `db` — схема Postgres и bootstrap
- `docs` — документация по проекту и доп. материалам
- `hypercube` — отдельный AI-gateway подпроект
- `paper_trading` — симуляционный/пaper trading и автоматические сценарии
- `tests` — регрессионные и функциональные проверки

## Основные сценарии

- Мониторинг ��ынка и сигналов по HTX
- Telegram-команды и AI-аналитика
- WebUI для просмотра свечей и внутреннего состояния
- Локальный runtime через Docker Compose
- Поддержка нескольких AI-провайдеров и fallback-цепочек

## Ключевые ссылки

- [Быстрый старт](Quick-Start.md)
- [Архитектура](Architecture.md)
- [AI стек](AI-Stack.md)
- [README проекта](../../README.md)

## Репозиторий

- GitHub: https://github.com/OrekhovD/Wored
- Основная ветка: `main`

## Статус

Проект находится в активной разработке, часть логики уже работает в локальном runtime, часть функциональности остаётся в стадии hardening, legacy и experimental модулей.
