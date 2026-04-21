# Provider Manager

## 1. Overview

ProviderManager отвечает за управление состоянием провайдеров, включая проверки здоровья, fallback и политики разблокировки premium моделей. Провайдеры загружаются из конфигурационного файла, с падением на жестко заданные значения по умолчанию. Проверки здоровья выполняются периодически, и цепочки обновляются по мере необходимости.

## 2. Функциональность

- **Управление провайдерами:** Отслеживает состояние и доступность провайдеров.
- **Проверки здоровья:** Выполняет периодические проверки здоровья провайдеров.
- **Fallback:** Обрабатывает fallback'и при недоступности провайдеров.
- **Premium Unlock Policy:** Управляет политикой разблокировки premium моделей.

## 3. Методы

### 3.1 `get_chain`

**Параметры:**
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).
- `excluded_models`: Optional[List[str]] — список ID моделей, которые следует исключить из цепочки.

**Возвращает:**
- `List[str]` — упорядоченный список ID моделей для данного режима.

**Пример:**
```python
chain = provider_manager.get_chain(RoutingMode.BALANCED, excluded_models=["glm-5"])
```

### 3.2 `get_chain_metadata`

**Параметры:**
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).

**Возвращает:**
- `dict[str, Any]` — полная метадата политики маршрутизации для данного режима.

**Пример:**
```python
metadata = provider_manager.get_chain_metadata(RoutingMode.PREMIUM)
```

### 3.3 `get_disabled_providers`

**Возвращает:**
- `List[str]` — список ID отключенных провайдеров.

### 3.4 `is_provider_enabled`

**Параметры:**
- `provider_id`: str — ID провайдера.

**Возвращает:**
- `bool` — True, если провайдер включен, иначе False.

### 3.5 `get_provider_config`

**Параметры:**
- `provider_id`: str — ID провайдера.

**Возвращает:**
- `dict[str, Any]` — полная конфигурация провайдера.

### 3.6 `get_model`

**Параметры:**
- `model_id`: str — ID модели.

**Возвращает:**
- `dict | None` — информация о модели или None, если модель не найдена.

### 3.7 `get_active_models`

**Параметры:**
- `provider_id`: Optional[str] — ID провайдера (опционально).

**Возвращает:**
- `List[dict]` — список активных моделей для данного провайдера или всех провайдеров, если `provider_id` не указан.

### 3.8 `get_models_by_provider`

**Параметры:**
- `provider_id`: str — ID провайдера.

**Возвращает:**
- `List[dict]` — список моделей для данного провайдера.

### 3.9 `update_model_status`

**Параметры:**
- `model_id`: str — ID модели.
- `status`: str — новый статус модели.

### 3.10 `is_model_available`

**Параметры:**
- `model_id`: str — ID модели.
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).

**Возвращает:**
- `bool` — True, если модель доступна для данного режима, иначе False.

### 3.11 `get_fallback_reason`

**Параметры:**
- `reason_code`: str — код причины fallback.

**Возвращает:**
- `str` — описание причины fallback.

### 3.12 `is_premium_unlocked`

**Параметры:**
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).
- `quota_remaining_pct`: float — процент оставшейся квоты.

**Возвращает:**
- `bool` — True, если premium модели разблокированы, иначе False.

## 4. Принцип работы

### 4.1 Загрузка провайдеров
ProviderManager использует объект `ModelRegistry` для загрузки провайдеров и их моделей из конфигурационного файла. Если файл недоступен, используются жестко заданные значения по умолчанию.

### 4.2 Проверки здоровья
ProviderManager выполняет периодические проверки здоровья провайдеров. Если провайдер не проходит проверку три раза подряд, он помечается как `degraded` и исключается из цепочек. Если провайдер проходит проверку два раза подряд после `degraded`, он возвращается в цепочки.

### 4.3 Fallback
ProviderManager обрабатывает fallback'и, если текущий провайдер недоступен или модель исключена политикой. Каждое решение о fallback записывается с соответствующим reason code.

### 4.4 Premium Unlock Policy
ProviderManager управляет политикой разблокировки premium моделей, проверяя режим, остаток квоты и здоровье провайдера.

## 5. Пример использования

### 5.1 Создание ProviderManager
```python
from routing.chain_builder import ChainBuilder
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy
from routing.provider_manager import ProviderManager

model_registry = ModelRegistry()
routing_policy = RoutingPolicy()
chain_builder = ChainBuilder(model_registry, routing_policy)
provider_manager = ProviderManager(model_registry, chain_builder, routing_policy)
```

### 5.2 Получение цепочки
```python
chain = provider_manager.get_chain(RoutingMode.BALANCED, excluded_models=["glm-5"])
print(chain)
```

### 5.3 Получение метаданных
```python
metadata = provider_manager.get_chain_metadata(RoutingMode.PREMIUM)
print(metadata)
```

### 5.4 Проверка доступности модели
```python
is_available = provider_manager.is_model_available("glm-5", RoutingMode.PREMIUM)
print(is_available)
```

### 5.5 Получение причины fallback
```python
reason = provider_manager.get_fallback_reason("timeout")
print(reason)
```

## 6. Файлы и зависимости

| Файл | Назначение |
|------|-----------|
| `provider_manager.py` | Основной класс ProviderManager |
| `model_registry.py` | Регистр моделей |
| `chain_builder.py` | Построитель цепочек |
| `policies.py` | Политики маршрутизации |
| `provider_registry.yaml` | Конфигурационный файл провайдеров |

ProviderManager зависит от `ModelRegistry`, `ChainBuilder` и `RoutingPolicy` для получения информации о моделях, построения цепочек и политик маршрутизации соответственно.