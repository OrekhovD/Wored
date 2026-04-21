# Context Handoff Hardening

## 1. Overview

Context handoff обеспечивает бесшовную передачу контекста при переключении между AI-моделями. Улучшенная версия включает версионирование схемы, валидацию, и безопасную сериализацию/десериализацию.

## 2. Schema Versioning

### V1 Schema Structure

```json
{
  "version": "v1",
  "system_rules": "string (required, max 50000 chars)",
  "handoff_summary": "string (required, max 50000 chars)",
  "last_user_request": "string (required, max 50000 chars)",
  "delta_market_update": "string | null (optional)",
  "created_at": "datetime (ISO format)"
}
```

### Validation Rules

- **Required fields:** version, system_rules, handoff_summary, last_user_request
- **Type checks:** Все обязательные поля должны быть строками
- **Empty check:** Обязательные поля не могут быть пустыми
- **Size limits:** Максимум 50000 символов на поле

## 3. HandoffBuilder API

### 3.1 `build_handoff`

**Параметры:**
- `old_session_id`: str — ID текущей сессии
- `new_model`: str — ID новой модели
- `old_model`: str — ID старой модели
- `market_facts`: str | None — опциональные рыночные данные
- `include_token_state`: bool — включить ли состояние токенов

**Возвращает:**
- `HandoffPackage` — валидированный пакет передачи

**Пример:**
```python
handoff = await builder.build_handoff(
    old_session_id="sess_abc123",
    new_model="glm-5",
    old_model="qwen-turbo",
    market_facts="BTC/USDT: $45,230 (+2.3%)",
    include_token_state=True
)
```

### 3.2 `apply_handoff`

**Параметры:**
- `session_id`: str — ID целевой сессии
- `handoff`: HandoffPackage — пакет для применения

**Возвращает:**
- `int` — количество добавленных сообщений

**Пример:**
```python
msg_count = await builder.apply_handoff("sess_xyz789", handoff)
print(f"Applied {msg_count} messages")
```

### 3.3 `serialize` / `deserialize`

**Serialize:**
```python
json_str = builder.serialize(handoff)
```

**Deserialize:**
```python
handoff = builder.deserialize(json_str)
```

## 4. System Prompt Generation

При применении handoff формируется системное сообщение:

```
============================================================
CONTEXT HANDOFF PACKAGE
Version: v1
Created: 2026-04-21T12:34:56.789012
============================================================

You are continuing an AI analysis session that was started with a different model.

Previous model: qwen-turbo
Current model: glm-5

Your responsibilities:
1. Preserve all context, conclusions, and user intent from the previous session
2. Do not repeat information already provided
3. Continue the analysis seamlessly as if you were the same assistant
...

--- HANDOFF SUMMARY ---
**User Requests:**
- Анализ BTC/USDT за последние 24 часа
- ...

**Model Conclusions:**
- BTC демонстрирует восходящий тренд
- ...

**Session Stats:** 15 messages in history

--- LAST USER REQUEST ---
Покажи прогноз на завтра...

============================================================
```

## 5. Summary Generation

Summary формируется из истории сообщений:

- Берется до 5 последних запросов пользователя
- Берется до 5 последних заключений модели
- Добавляются метрики сессии (количество сообщений)
- Ограничение по токенам (по умолчанию 2000)

## 6. Error Handling

### Валидация ошибки

Если валидация handoff пакета не проходит:

```python
try:
    handoff = await builder.build_handoff(...)
except ValueError as e:
    log.error("Handoff validation failed: %s", e)
    # Fallback к продолжению без handoff
```

### Deserialization ошибки

```python
try:
    handoff = builder.deserialize(corrupted_json)
except (ValueError, json.JSONDecodeError) as e:
    log.error("Failed to deserialize handoff: %s", e)
    # Восстановление по умолчанию или ошибка пользователю
```

## 7. File Locations

| Файл | Назначение |
|------|-----------|
| `context/handoff.py` | HandoffBuilder + schema validation |
| `context/service.py` | Управление сессиями и сообщениями |
| `core/schemas.py` | HandoffPackage dataclass |

## 8. Testing Checklist

- [ ] Валидация корректных handoff пакетов
- [ ] Отказ при отсутствии required fields
- [ ] Отказ при превышении размера полей
- [ ] Корректная сериализация/десериализация
- [ ] Применение handoff к новой сессии
- [ ] Экстремальные случаи (пустая история, длинные тексты)

## 9. Future Improvements

- Версия v2: Поддержка структурированных рыночных данных
- Инкрементальные обновления вместо полного summary
- Сжатие long-term памяти
- Автоматическая миграция между версиями схем
