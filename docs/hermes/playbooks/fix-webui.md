# Playbook: Fix WebUI

## Цель
Диагностика и починка WebUI с соблюдением guardrails.

## Guardrails — ОБЯЗАТЕЛЬНО

### 🚫 Запрещено
- Переписывать WebUI с нуля
- Удалять app.js
- Удалять существующие страницы (index, alerts, predictions, journal)
- Заменять styles.css полностью
- Удалять TradingView Lightweight Charts
- Удалять chart containers (price, volume, RSI, MACD)

### ✅ Разрешено
- Инкрементальные CSS-улучшения
- Добавление новых элементов в шаблоны
- Новые API endpoints в app.py
- Добавление JS-функций в app.js
- Новые шаблоны страниц

## Перед WebUI patch — регрессионный чеклист

1. Проверить `webui/templates/base.html` — структура навбаза
2. Проверить `webui/templates/index.html` — chart containers
3. Проверить `webui/static/styles.css` — критичные стили
4. Проверить `webui/static/app.js` — chart initialization
5. Перечислить chart containers: price, volume, RSI, MACD
6. Подтвердить сохранение роутов: /, /alerts, /predictions, /journal

## Шаги диагностики

### 1. Проверить логи
```
/lw
```

### 2. Проверить роуты
```
/routes
```
Ожидание: все 4 роута OK.

### 3. Проверить health
```
/health
```

### 4. Если роут падает — проверить конкретный
```bash
curl -v http://localhost:8080/<route>
docker compose logs webui --tail=80
```

## Шаги исправления

1. **PLAN** — описать что сломано и как починить
2. **DIFF** — показать точные изменения в файлах
3. Подтвердить guardrails соблюдены
4. **APPLY** — применить patch
5. **TEST** — smoke-test

## Smoke-test после изменений

```bash
curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK" || echo "/ FAIL"
curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK" || echo "/alerts FAIL"
curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK" || echo "/predictions FAIL"
curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK" || echo "/journal FAIL"
docker compose logs webui --tail=80
```

## Когда остановиться и спросить владельца
- Изменение затрагивает chart containers
- Удаление существующих элементов
- Изменение навбаза
- Добавление новых зависимостей
- Перестройка Docker образа

## Критерии готовности
- Все 4 роута возвращают HTTP 200
- Нет 500 в логах
- Chart containers на месте
- styles.css не перезаписан целиком
