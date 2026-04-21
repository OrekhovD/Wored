# Deprecations

Устаревшие функции, файлы и конфигурации проекта Hypercube.

## 🗑️ Удаленные файлы

### Фаза 1: Security Cleanup (WP-2)

| Файл | Причина удаления | Дата | Замена |
|------|------------------|------|--------|
| `core/db.py` | Неправильное расположение | 2026-04-21 | `storage/db.py` |
| `core/engine.py` | Неправильное расположение | 2026-04-21 | `storage/engine.py` |
| `core/logger.py` | Неправильное расположение | 2026-04-21 | `core/logging.py` |
| `core/context/` | Неправильное расположение | 2026-04-21 | `context/` в корне |
| `core/providers/` | Неправильное расположение | 2026-04-21 | `providers/` в корне |

### Фаза 2: Secret Hygiene (WP-2)

| Файл | Причина удаления | Дата | Замена |
|------|------------------|------|--------|
| `.env` (с реальными ключами) | Утечка секретов | 2026-04-21 | `.env` с placeholder'ами |
| `docs/kluch.txt` (в git) | Утечка секретов | 2026-04-21 | `docs/kluch.txt` в `.gitignore` |

## ⚠️ Устаревшие конфигурации

### ENV переменные

| Переменная | Статус | Дата депрекации | Замена |
|------------|--------|-----------------|--------|
| `DASHSCOPE_MODEL` | ⚠️ Deprecated | 2026-04-21 | `DEFAULT_MODEL=qwen-turbo` |
| `NVAPI_MODEL` | ⚠️ Deprecated | 2026-04-21 | Использовать `routing/model_registry.yaml` |
| `GLM5_MODEL` | ⚠️ Deprecated | 2026-04-21 | Использовать `routing/model_registry.yaml` |
| `AI_STUDIO_MODEL` | ⚠️ Deprecated | 2026-04-21 | Использовать `routing/model_registry.yaml` |
| `FALLBACK_CHAIN` | ⚠️ Deprecated | 2026-04-21 | `routing/policies.yaml` |
| `ROUTING_MODE` | ⚠️ Deprecated | 2026-04-21 | Конфигурация в `provider_registry.yaml` |

### Файлы конфигурации

| Файл | Статус | Дата депрекации | Замена |
|------|--------|-----------------|--------|
| `config/routing.json` | ⚠️ Deprecated | 2026-04-21 | `examples/provider_registry.yaml` |
| `config/models.json` | ⚠️ Deprecated | 2026-04-21 | `examples/provider_registry.yaml` |
| `config/providers.json` | ⚠️ Deprecated | 2026-04-21 | `examples/provider_registry.yaml` |

## 🔄 Устаревшие API endpoints

| Endpoint | Статус | Дата депрекации | Замена |
|----------|--------|-----------------|--------|
| `GET /api/v1/models` | ⚠️ Deprecated | 2026-04-21 | `GET /api/v2/models` |
| `POST /api/v1/ask` | ⚠️ Deprecated | 2026-04-21 | `POST /api/v2/ask` |
| `GET /api/v1/health` | ⚠️ Deprecated | 2026-04-21 | `GET /api/v2/health` |

## 🛠️ Устаревшие классы и функции

### routing/

| Класс/Функция | Статус | Дата | Замена |
|---------------|--------|------|--------|
| `RoutingPolicy.get_free_chain()` | ⚠️ Deprecated | 2026-04-21 | `RoutingPolicy.get_candidate_chain(RoutingMode.FREE_ONLY)` |
| `RoutingPolicy.get_premium_chain()` | ⚠️ Deprecated | 2026-04-21 | `RoutingPolicy.get_candidate_chain(RoutingMode.PREMIUM)` |
| `ModelRegistry.load_from_env()` | ⚠️ Deprecated | 2026-04-21 | `ModelRegistry.load_from_yaml()` |

### providers/

| Класс/Функция | Статус | Дата | Замена |
|---------------|--------|------|--------|
| `ProviderFactory.create_provider()` | ⚠️ Deprecated | 2026-04-21 | `ProviderFactory.build_from_config()` |
| `OpenAICompatibleAdapter.invoke()` (без resilience) | ⚠️ Deprecated | 2026-04-21 | `OpenAICompatibleAdapter.invoke()` (с resilience) |

### context/

| Класс/Функция | Статус | Дата | Замена |
|---------------|--------|------|--------|
| `HandoffBuilder.build_handoff()` (без валидации) | ⚠️ Deprecated | 2026-04-21 | `HandoffBuilder.build_handoff()` (с HandoffSchemaV1) |
| `HandoffBuilder.serialize()` (без версионирования) | ⚠️ Deprecated | 2026-04-21 | `HandoffSchemaV1.serialize()` |

## 📅 График удаления

### Немедленное удаление (сделано)
- ✅ `core/db.py`, `core/engine.py`, `core/logger.py`
- ✅ `core/context/`, `core/providers/`
- ✅ Реальные ключи из `.env` и `docs/kluch.txt`

### Удаление в v0.2.0 (следующий релиз)
- ⏳ `DASHSCOPE_MODEL`, `NVAPI_MODEL`, `GLM5_MODEL`, `AI_STUDIO_MODEL`
- ⏳ `FALLBACK_CHAIN`, `ROUTING_MODE`
- ⏳ `config/routing.json`, `config/models.json`, `config/providers.json`

### Удаление в v0.3.0
- ⏳ `GET /api/v1/*` endpoints
- ⏳ `POST /api/v1/*` endpoints

### Удаление в v1.0.0
- ⏳ Все deprecated классы и функции из таблиц выше

## 🚨 Миграция

### Для ENV переменных

**До:**
```bash
DASHSCOPE_MODEL=qwen-turbo
NVAPI_MODEL=llama3-70b
GLM5_MODEL=glm-5
FALLBACK_CHAIN=qwen-turbo,qwen-plus,glm-5
```

**После:**
```bash
# Использовать examples/provider_registry.yaml
# Модели и цепочки настраиваются в YAML файле
```

### Для конфигурационных файлов

**До:**
```json
// config/routing.json
{
  "free_chain": ["qwen-turbo", "qwen-plus"],
  "premium_chain": ["glm-5", "qwen-plus"]
}
```

**После:**
```yaml
# examples/provider_registry.yaml
routing_policies:
  free_only:
    candidate_chain:
      - qwen-turbo
      - qwen-plus
      - ai-minimax/minimax-m2.7
    exclude_premium: true
```

### Для API endpoints

**До:**
```bash
curl http://localhost:8000/api/v1/models
```

**После:**
```bash
curl http://localhost:8000/api/v2/models
```

## 📝 Примечания

1. **Deprecated функции** будут работать до указанной даты удаления
2. **Миграция** должна быть завершена до удаления
3. **Breaking changes** будут объявлены в CHANGELOG_WEEKLY.md
4. **Поддержка** deprecated функций ограничена багфиксами

## 🔄 Обновления

| Дата | Изменения |
|------|-----------|
| 2026-04-21 | Первоначальное создание документа |
