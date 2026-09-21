# AI Stack

## Обзор

WORED использует мульти-провайдерный AI стек с primary-provider, utility-tier, fallback-tier и router logic. Цель — не зависеть от одного конкретного провайдера и иметь возможность переключаться между провайдерами в зависимости от нагрузки, стоимости и доступности.

## Основной стек

| Роль | Провайдер | Пример модели |
|---|---|---|
| Premium / сложные задачи | Ollama Cloud | `glm-5.2` |
| Analyst / reasoning | Ollama Cloud | `deepseek-v4-pro` |
| Worker / быстрые задачи | Ollama Cloud | `deepseek-v4-flash` |
| Reviewer / second opinion | Ollama Cloud | `minimax-m3` |
| Oracle / fallback | Ollama Cloud, NVIDIA NIM, OpenRouter | `kimi-k2.6`, `kimi-k2:1t` |

## Utility / cheap tier

- TokenRouter
- модель: `moonshotai/kimi-k3-free`

## Fallback и resilience

- NVIDIA NIM
- OpenRouter
- резервные маршруты на уровне конфигурации и runtime-проверок

## Важное замечание

В активном стеке проект явно исключил Qwen/DashScope из основного рабочего контура, хотя этот и другие провайдеры могут встречаться в кодовой базе и документации как legacy/experimental.

## Переменные окружения

Ключевые переменные, которые обычно используются для управления мо��елями:

```bash
OLLAMA_WORKER_MODEL=deepseek-v4-flash
OLLAMA_ANALYST_MODEL=deepseek-v4-pro
OLLAMA_PREMIUM_MODEL=glm-5.2
```

## Применение в проекте

AI-стек используется:

- для анализа рынка
- для AI-journey / журналирования
- для Telegram-подсказок и AI-операций
- для маршрутизации между провайдерами и fallback-цепочками

## Источники

- [README проекта](../../README.md)
- [Architecture](Architecture.md)
- [Quick Start](Quick-Start.md)
