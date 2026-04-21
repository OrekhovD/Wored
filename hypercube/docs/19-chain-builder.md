# Chain Builder

## 1. Overview

ChainBuilder отвечает за построение упорядоченных цепочек моделей, которые будут использоваться для маршрутизации запросов в зависимости от выбранного режима (routing mode). Цепочки строятся на основе политик, заданных в конфигурационном файле, с падением на жестко заданные значения по умолчанию.

## 2. Функциональность

- **Построение цепочек:** Генерирует упорядоченные списки моделей для каждого режима.
- **Фильтрация:** Исключает модели, которые были явно исключены или принадлежат отключенным провайдерам.
- **Метаданные:** Предоставляет метаданные о политике маршрутизации для каждого режима.

## 3. Методы

### 3.1 `build_chain`

**Параметры:**
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).
- `excluded_models`: Optional[List[str]] — список ID моделей, которые следует исключить из цепочки.
- `disabled_providers`: Optional[List[str]] — список ID провайдеров, которые следует исключить из цепочки.

**Возвращает:**
- `List[str]` — упорядоченный список ID моделей для данного режима.

**Пример:**
```python
chain = chain_builder.build_chain(RoutingMode.BALANCED, excluded_models=["glm-5"], disabled_providers=["zhipu"])
```

### 3.2 `get_chain_metadata`

**Параметры:**
- `mode`: RoutingMode | str — режим маршрутизации (free_only, balanced, premium).

**Возвращает:**
- `dict[str, Any]` — полная метадата политики маршрутизации для данного режима.

**Пример:**
```python
metadata = chain_builder.get_chain_metadata(RoutingMode.PREMIUM)
```

## 4. Принцип работы

### 4.1 Построение цепочек
ChainBuilder использует объект `RoutingPolicy` для получения списка кандидатов на основе текущего режима. Затем он фильтрует этот список, исключая явно указанные модели и модели, принадлежащие отключенным провайдерам.

### 4.2 Логирование
ChainBuilder логирует все построенные цепочки для отладки и мониторинга. Например, если цепочка была построена для режима `balanced`, то будет записано сообщение:
```python
log.debug("Built chain for %s: %s", mode, chain)
```

### 4.3 Конфигурационный файл
ChainBuilder использует конфигурационный файл `examples/provider_registry.yaml` для загрузки провайдеров и их моделей. Если файл недоступен, используются жестко заданные значения по умолчанию.

## 5. Пример использования

### 5.1 Создание ChainBuilder
```python
from routing.chain_builder import ChainBuilder
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy

model_registry = ModelRegistry()
routing_policy = RoutingPolicy()
chain_builder = ChainBuilder(model_registry, routing_policy)
```

### 5.2 Построение цепочки
```python
chain = chain_builder.build_chain(RoutingMode.BALANCED, excluded_models=["glm-5"], disabled_providers=["zhipu"])
print(chain)
```

### 5.3 Получение метаданных
```python
metadata = chain_builder.get_chain_metadata(RoutingMode.PREMIUM)
print(metadata)
```

## 6. Файлы и зависимости

| Файл | Назначение |
|------|-----------|
| `chain_builder.py` | Основной класс ChainBuilder |
| `model_registry.py` | Регистр моделей |
| `policies.py` | Политики маршрутизации |
| `provider_registry.yaml` | Конфигурационный файл провайдеров |

ChainBuilder зависит от `ModelRegistry` и `RoutingPolicy` для получения информации о моделях и политик маршрутизации соответственно.