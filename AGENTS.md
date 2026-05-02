# WORED — Hermes Agent Context

## Роль Hermes

Hermes — host-level technical orchestrator для WORED.

Он управляет проектом снаружи:
- запускает Docker Compose;
- смотрит логи;
- читает Redis/Postgres;
- помогает писать и проверять код;
- диагностирует webui/chatbot/collector.

Hermes НЕ заменяет runtime-сервисы:
- chatbot остаётся Telegram UI;
- collector остаётся ingestion/scheduler service;
- webui остаётся FastAPI dashboard;
- postgres/redis остаются инфраструктурой данных.

## Активный runtime stack

Docker Compose runtime содержит 5 сервисов:

1. chatbot
   - Telegram UI на aiogram 3
   - AI routing
   - fallback-цепочка AI-провайдеров
   - пользовательские команды и уведомления

2. collector
   - HTX WebSocket ingestion
   - market data processing
   - technical indicators
   - scheduler jobs
   - запись alert/journal/forecast данных

3. webui
   - FastAPI dashboard
   - TradingView Lightweight Charts
   - Alerts UI
   - Prediction Lab
   - AI Journal

4. postgres
   - alerts
   - ai_journal
   - forecast tables
   - historical data

5. redis
   - hot ticker cache
   - market_alerts pub/sub
   - realtime snapshots

## Активная AI-цепочка WORED

Основная цепочка:
- Qwen auto-switch
- qwen3.6-flash
- qwen3.5-flash
- qwen-flash
- glm-4-flash

Fallback:
- GLM
- MiniMax reviewer

Hermes сейчас может работать на GLM 5 как технический агент.
Это НЕ означает, что chatbot runtime должен быть переписан под GLM 5.

## Важные runtime-файлы

### WebUI

- webui/app.py
- webui/templates/base.html
- webui/templates/index.html
- webui/templates/alerts.html
- webui/templates/predictions.html
- webui/templates/journal.html
- webui/templates/login.html
- webui/static/styles.css
- webui/static/app.js

Правило:
не заменять весь WebUI шаблоном с нуля.
Развивать текущую дизайн-систему инкрементально.

### Collector

- collector/main.py
- collector/htx/*
- collector/indicators/*
- collector/storage/*
- collector/scheduler/*
- collector/journal/*

### Chatbot

- chatbot/main.py
- chatbot/handlers/*
- chatbot/ai/*
- chatbot/storage/*

## Legacy / caution zone

Эти файлы не считать активным runtime path без отдельной проверки:

- chatbot/loader.py
- chatbot/context/*
- chatbot/ui/*
- collector/alerts/detector.py
- collector/scheduler/briefing.py

Правило:
не чинить и не развивать эти зоны как runtime-critical без явного запроса.

## Security rules — DESTRUCTIVE GUARDRAILS

### 🚫 КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО (без исключений)

1. **`docker compose down -v`** — удаляет PostgreSQL данные навсегда.
2. **`rm -rf`** на проектных директориях (/mnt/d/WORED, collector/, chatbot/, webui/).
3. **`docker volume rm`** — потеря данных.
4. **Печатать секреты** — содержимое .env, .env.postgres, API-ключи, токены, пароли, bearer tokens. Проверка presence только через `sed 's/=.*/=***/'`.
5. **`cat .env`** или `grep` по секретам с выводом значений.
6. **`git push`** без явной команды адмирала.

### ⚠️ ТОЛЬКО С ЯВНЫМ ПОДТВЕРЖДЕНИЕМ АДМИРАЛА

7. `docker compose down` (даже без -v — останавливает продакшн).
8. `docker compose restart` на живых сервисах.
9. Изменение .env или .env.postgres.
10. Изменение docker-compose.yml.
11. Удаление любых файлов.
12. Изменение логики chatbot/ или collector/.
13. Переписывание WebUI с нуля (только инкремент).
14. Трогать legacy-зоны без явного запроса.

### ✅ ПРОЦЕДУРА ИЗМЕНЕНИЙ (PLAN → DIFF → APPLY → TEST → REPORT)

1. **PLAN** — показать цель и список файлов.
2. **DIFF** — назвать active runtime path или legacy area, риск регрессии.
3. **APPLY** — предложить patch (не применять без подтверждения).
4. **TEST** — дать команды проверки.
5. **REPORT** — зафиксировать результат.

### Для WebUI — дополнительный guardrail:
- Не удалять app.js.
- Не удалять существующие страницы.
- Сохранять chart containers (price, volume, RSI, MACD).
- Сохранять роуты: /, /alerts, /predictions, /journal.
- Не заменять styles.css целиком.

## Команды диагностики

```bash
docker compose ps
docker compose logs --tail=120
docker compose logs collector --tail=120
docker compose logs chatbot --tail=120
docker compose logs webui --tail=120
docker compose exec -T redis redis-cli keys "ticker:*"
docker compose exec -T redis redis-cli get ai:journal:latest
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
```

## Правило перед изменениями

Перед патчем Hermes должен назвать:
- цель изменения;
- активный runtime path или legacy area;
- список файлов;
- риск регрессии;
- команды проверки.

## Проверка после изменений

Минимум:
```bash
docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8080/ >/dev/null
curl -fsS http://localhost:8080/alerts >/dev/null
curl -fsS http://localhost:8080/predictions >/dev/null
curl -fsS http://localhost:8080/journal >/dev/null
```
