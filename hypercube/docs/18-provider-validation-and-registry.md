# Provider Validation And Registry

## 1. Overview

Провайдеры и модели управляются через машиночитаемый конфигурационный файл `examples/provider_registry.yaml`. Это позволяет:

- Включать/выключать провайдеров без изменения кода
- Настраивать цепочки маршрутизации через конфигурацию
- Управлять стоимостями и лимитами моделей
- Контролировать политики fallback и health check

## 2. Provider Registry File

Файл: `examples/provider_registry.yaml`

### 2.1 Структура провайдера

```yaml
providers:
  dashscope:
    display_name: "DashScope (Qwen Cloud)"
    base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    env_key: "DASHSCOPE_API_KEY"      # Имя env переменной с ключом
    enabled: true                       # Можно отключить без изменения кода
    health_check_path: "/models"
    timeout_seconds: 30
    retry_count: 2
    retry_delay_ms: 1000
    models:
      qwen-plus:
        display_name: "Qwen Plus"
        status: "active"               # active | degraded | inactive
        is_premium: false
        supports_streaming: true
        supports_system_prompt: true
        max_tokens: 8192
        cost:
          input_per_1k: 0.0002
          output_per_1k: 0.0006
        tags: ["balanced", "analysis"]
```

### 2.2 Поля провайдера

| Поле | Описание | Обязательное |
|------|----------|-------------|
| `display_name` | Человекочитаемое название | Да |
| `base_url` | URL API провайдера | Да |
| `env_key` | Имя переменной окружения с API ключом | Да |
| `enabled` | Включён ли провайдер | Да (default: true) |
| `health_check_path` | Путь для health check | Нет (default: /models) |
| `timeout_seconds` | Таймаут запросов | Нет (default: 30) |
| `retry_count` | Количество ретраев | Нет (default: 1) |
| `retry_delay_ms` | Задержка между ретраями | Нет (default: 1000) |

### 2.3 Поля модели

| Поле | Описание | Обязательное |
|------|----------|-------------|
| `display_name` | Человекочитаемое название | Да |
| `status` | active / degraded / inactive | Да |
| `is_premium` | Требуется ли premium режим | Да |
| `supports_streaming` | Поддержка streaming | Нет (default: false) |
| `supports_system_prompt` | Поддержка system prompt | Нет (default: true) |
| `max_tokens` | Максимум токенов ответа | Нет (default: 4096) |
| `cost.input_per_1k` | Стоимость 1000 входных токенов | Нет |
| `cost.output_per_1k` | Стоимость 1000 выходных токенов | Нет |
| `tags` | Теги для фильтрации | Нет |

## 3. How Providers Are Validated

### 3.1 При запуске

1. Загружается `provider_registry.yaml`
2. Для каждого провайдера проверяется наличие `env_key` в `.env`
3. Если ключ отсутствует — провайдер помечается как `inactive`
4. Выполняется initial health check для всех enabled провайдеров

### 3.2 Runtime Health Checks

Health checks выполняются периодически (по умолчанию каждые 300 секунд):

- **Успешный check:** provider = healthy
- **3 подряд неудачи:** provider = degraded (удаляется из candidate chains)
- **2 подряд успеха после degraded:** provider = healthy (возвращается в chains)

### 3.3 Provider Disable/Enable

Чтобы отключить провайдера:

```yaml
providers:
  zhipu:
    enabled: false  # GLM-5 не будет использоваться
```

Или через env:
```bash
GLM5_API_KEY=  # Пустой ключ = провайдер не инициализируется
```

## 4. Model Exclusion By Mode

### free_only
- Исключает все модели с `is_premium: true`
- Использует только free/low-cost модели

### balanced
- Допускает premium модели
- Приоритет: качество → стоимость

### premium
- Все модели доступны
- Приоритет: качество (стоимость вторична)
- Требуется `quota_remaining_pct >= 10%`

## 5. Fallback Reason Codes

Каждое решение маршрутизации записывается с reason code:

| Code | Описание |
|------|----------|
| `timeout` | Провайдер не ответил в течение таймаута |
| `quota_exceeded` | Исчерпана квота токенов |
| `rate_limit` | Превышен rate limit провайдера |
| `invalid_response` | Некорректный ответ от провайдера |
| `provider_unavailable` | Провайдер недоступен |
| `policy_rejection` | Модель исключена политикой |
| `health_degraded` | Health check провайдера упал |
| `manual_switch` | Пользователь переключил модель вручную |

## 6. Premium Unlock Policy

Premium модели разблокированы когда:

1. Режим = `premium`
2. Остаток квоты >= 10%
3. Провайдер healthy

Проверяется в `routing/policies.py:is_premium_unlocked()`.

## 7. Updating Provider Registry

### 7.1 Добавление нового провайдера

1. Добавить секцию в `examples/provider_registry.yaml`:
```yaml
providers:
  new_provider:
    display_name: "New Provider"
    base_url: "https://api.new-provider.com/v1"
    env_key: "NEW_PROVIDER_API_KEY"
    enabled: true
    models:
      new-model:
        display_name: "New Model"
        status: "active"
        is_premium: false
        cost:
          input_per_1k: 0.0001
          output_per_1k: 0.0004
```

2. Добавить ключ в `.env`:
```bash
NEW_PROVIDER_API_KEY=your_key_here
```

3. Обновить routing policies для включения модели в цепочки

4. Перезапустить сервис

### 7.2 Изменение стоимости модели

Изменить `cost.input_per_1k` и/или `cost.output_per_1k` в registry file и перезапустить.

### 7.3 Деградация модели

Если модель работает нестабильно:
```yaml
models:
  glm-5:
    status: "degraded"  # Будет использоваться только при fallback
```

## 8. File Locations

| Файл | Назначение |
|------|-----------|
| `examples/provider_registry.yaml` | Основной конфиг реестра |
| `routing/policies.py` | Загрузчик политик маршрутизации |
| `routing/model_registry.py` | Загрузчик моделей |
| `.env` | Секреты провайдеров |
| `.env.example` | Шаблон для настройки |