
# Техническое задание
## Backend Contract Patch для Telegram Mini App WORED Daily Session

**Дата:** 28.06.2026  
**Версия:** 1.0  
**Основание:** текущий Daily Pipeline WORED v2 и реализованный Telegram Mini App frontend.

---

## 1. Цель

Подготовить backend-слой WebUI/API для боевого Telegram Mini App, чтобы миниапп мог:

- запускать дневную 8-часовую сессию;
- получать текущую активную сессию и связанный runtime state;
- отправлять execution control команды revision;
- получать live-обновление execution events;
- отображать план, сделки, метрики и состояние FSM без ручной адаптации фронтенда.

---

## 2. Контекст

В проекте уже подтверждены:

- daily pipeline на 8 таблиц;
- FSM `IDLE -> ARMED -> IN_POSITION -> COOLDOWN -> PAUSED -> STOPPED -> COMPLETED`;
- execution control команды `continue`, `tighten`, `reduce`, `pause`, `close_all`;
- WebUI маршрут `/daily-session` и API `POST /api/daily-session/start`, `GET /api/daily-session/active`;
- Mini App frontend, который уже умеет работать с `start`, `active`, revision-командами и live events при наличии backend-контрактов.

Ключевой следующий шаг — стандартизировать API-контракты и поток live-событий, чтобы Mini App можно было подключить к боту и выкатывать как production-функцию.

---

## 3. Область работ

В рамках задачи необходимо реализовать и зафиксировать следующие backend-интерфейсы:

1. `POST /api/daily-session/revision`
2. `GET /api/daily-session/events`
3. `GET /api/daily-session/events/stream` (SSE)
4. `GET /api/daily-session/active` — довести до стабильного unified response
5. Опционально: `WS /ws/daily-session/events`
6. Telegram WebApp auth verification через `X-Telegram-Init-Data`
7. Прокладку данных из `session_manager`, `execution_engine`, `stats_audit` и PostgreSQL таблиц pipeline

---

## 4. Архитектурное размещение

### 4.1. Компоненты

Реализация должна использовать текущую архитектуру проекта:

- `webui/app.py` — HTTP API и SSE/WS endpoint-ы;
- `chatbot/services/session_manager.py` — запуск сессии, активный план, revision orchestration;
- `chatbot/services/execution_engine.py` — FSM, deterministic execution, state transitions;
- `chatbot/services/stats_audit.py` — метрики, closeout, audit snapshot;
- `chatbot/services/pipeline_schema.py` — таблицы и их наличие;
- PostgreSQL — source of truth по runtime session state и истории;
- Redis — опционально для pub/sub live events и быстрой fan-out доставки.

### 4.2. Источники данных

Основные таблицы:

- `trading_sessions`
- `session_plans`
- `session_revisions`
- `planned_entries`
- `executed_trades`
- `execution_events`
- `session_metrics`
- `daily_reviews`

---

## 5. Контракты API

## 5.1. POST /api/daily-session/start

### Назначение
Запуск новой сессии из Mini App.

### Request JSON

```json
{
  "session_type": "daily_8h_btcusdt",
  "user_id": 5249526259,
  "symbol": "BTCUSDT",
  "exchange": "HTX",
  "budget_usdt": 100.0,
  "duration_hours": 8,
  "risk_mode": "balanced",
  "source": "telegram_miniapp"
}
```

### Response JSON

```json
{
  "ok": true,
  "session_id": "uuid",
  "status": "armed",
  "message": "session started"
}
```

### Требования

- Если активная сессия уже существует, endpoint должен вернуть 409 Conflict или idempotent response с текущей сессией.
- `risk_mode` допускает только: `defensive`, `balanced`, `aggressive`.
- `symbol` на первом этапе фиксируется как `BTCUSDT`.

---

## 5.2. GET /api/daily-session/active

### Назначение
Получение unified snapshot для главного экрана Mini App.

### Response JSON

```json
{
  "session": {
    "id": "uuid",
    "user_id": 5249526259,
    "symbol": "BTCUSDT",
    "exchange": "HTX",
    "status": "ARMED",
    "riskmode": "balanced",
    "sessionstart": "2026-06-28T02:00:00Z",
    "sessionend": "2026-06-28T10:00:00Z",
    "failedentries": 1,
    "lastcommand": "tighten"
  },
  "plan": {
    "id": "uuid",
    "version": 2,
    "thesis": "momentum continuation with pullback entries",
    "marketregime": "trend_up",
    "primaryscenario": "long_on_reclaim",
    "alternativescenario": "short_on_failed_breakout",
    "notradecondition": "1h volatility extreme or thesis invalidated",
    "riskmode": "balanced",
    "sessionrisk": {
      "maxsessiondrawdownpct": 6.0,
      "maxfailedentries": 3,
      "maxsimultaneouspositions": 1,
      "cooldownminutesafterstop": 20,
      "stoptradingafterliquidation": true
    },
    "entries": []
  },
  "metrics": {
    "tradecount": 4,
    "wincount": 3,
    "losscount": 1,
    "liquidationcount": 0,
    "totalpnlusdt": 12.4,
    "totalpnlpct": 12.4,
    "maxdrawdownpct": 3.1,
    "profitfactor": 1.82,
    "timeinmarketpct": 42.0
  },
  "trades": [],
  "events": [],
  "revision": {
    "id": "uuid",
    "executioncommand": "tighten",
    "createdat": "2026-06-28T03:00:00Z"
  }
}
```

### Требования

- Если активной сессии нет, endpoint возвращает:

```json
{
  "session": null,
  "plan": null,
  "metrics": null,
  "trades": [],
  "events": [],
  "revision": null
}
```

- Поля `session`, `plan`, `metrics`, `trades`, `events`, `revision` обязательны всегда, даже если они `null` или пустые.
- Ответ должен быть стабилен для фронтенда и не менять key naming без версии API.

---

## 5.3. POST /api/daily-session/revision

### Назначение
Отправка execution control команды из Mini App.

### Допустимые команды

- `continue`
- `tighten`
- `reduce`
- `pause`
- `close_all`

### Request JSON

```json
{
  "session_id": "uuid",
  "command": "tighten",
  "source": "telegram_miniapp"
}
```

### Response JSON

```json
{
  "ok": true,
  "session_id": "uuid",
  "executioncommand": "tighten",
  "accepted": true,
  "applied_at": "2026-06-28T03:00:00Z"
}
```

### Бизнес-правила

- Если `session_id` не найден — 404.
- Если сессия в `STOPPED` или `COMPLETED` — 409.
- Если команда невалидна — 422.
- Команда должна быть записана в `session_revisions` либо в отдельный audit entry с обязательной трассировкой автора и источника.
- Для `close_all` execution engine должен инициировать принудительное завершение открытой позиции и перевод FSM в `STOPPED` или финальное допустимое состояние.

---

## 5.4. GET /api/daily-session/events

### Назначение
Выдача последних execution events для polling fallback.

### Query params

- `session_id=uuid`
- `limit=50` (optional)
- `after=timestamp` (optional)

### Response JSON

```json
{
  "session_id": "uuid",
  "events": [
    {
      "id": "uuid",
      "timestamp": "2026-06-28T03:17:00Z",
      "eventtype": "position_opened",
      "statebefore": "ARMED",
      "stateafter": "IN_POSITION",
      "eventpayload": {
        "side": "long",
        "marginmode": "isolated",
        "leverage": 125,
        "entryprice": 106250.0,
        "reasoncode": "trendpullbackentry"
      }
    }
  ]
}
```

### Требования

- Сортировка по убыванию времени.
- Дедупликация по `id`.
- Лимит по умолчанию: 50.
- Источник данных — `execution_events`.

---

## 5.5. GET /api/daily-session/events/stream

### Назначение
Server-Sent Events поток для live-обновления Mini App.

### Формат SSE

```text
event: execution_event
id: <event_id>
data: { ...json event... }
```

### Требования

- Query param: `session_id=uuid`
- `Content-Type: text/event-stream`
- Heartbeat каждые 15-30 секунд
- Автопереподключение на стороне клиента допустимо
- Если live stream недоступен, Mini App должен автоматически уйти в polling

### Источник live-событий

Предпочтительный вариант:

1. `execution_engine` или scheduler пишет события в PostgreSQL;
2. параллельно публикует событие в Redis channel `daily_session_events`;
3. WebUI SSE endpoint подписывается на Redis и стримит event клиентам.

Fallback-вариант:

- SSE endpoint периодически читает новые строки из `execution_events` по `created_at` / `id`.

---

## 5.6. WS /ws/daily-session/events (опционально)

Если в проекте уже есть WebSocket слой в `webui/app.py`, можно добавить WebSocket endpoint.

### Требования

- Функционально эквивалентен SSE;
- приоритет для фронтенда ниже SSE;
- нужен только как дополнительный transport.

---

## 6. Telegram WebApp авторизация

## 6.1. Заголовок

Каждый запрос Mini App должен уметь передавать:

```http
X-Telegram-Init-Data: <Telegram.WebApp.initData>
```

## 6.2. Backend требования

- backend должен валидировать `initData` по правилам Telegram Web App auth;
- после валидации извлекать `user.id`, `username`, `auth_date`;
- `user_id` в payload старта должен либо совпадать с верифицированным Telegram user id, либо игнорироваться и подставляться на backend стороне;
- в случае невалидной подписи возвращать 401.

## 6.3. Минимальный режим на первом этапе

Если полноценная валидация внедряется отдельно, допустим временный режим feature flag:

- `TELEGRAM_WEBAPP_AUTH_DISABLED=true` только для dev/stage;
- в production выключено.

---

## 7. Mapping backend -> frontend

Mini App ожидает следующие поля:

| UI блок | Источник данных | Backend поле |
|--------|------------------|--------------|
| Session state chip | `trading_sessions` / runtime | `session.status` |
| Risk chip | `trading_sessions` / plan | `session.riskmode` |
| PnL | `session_metrics` | `metrics.totalpnlpct`, `metrics.totalpnlusdt` |
| Drawdown | `session_metrics` + plan risk | `metrics.maxdrawdownpct`, `plan.sessionrisk.maxsessiondrawdownpct` |
| Trade count | `session_metrics` | `tradecount`, `wincount`, `losscount` |
| Thesis | `session_plans.plan_json` | `plan.thesis` |
| Entries | `planned_entries` | `plan.entries[]` |
| Trades | `executed_trades` | `trades[]` |
| Events | `execution_events` | `events[]` |
| Last revision | `session_revisions` | `revision.executioncommand` |

---

## 8. Изменения по коду

## 8.1. webui/app.py

Нужно добавить:

- Pydantic схемы request/response для `start`, `active`, `revision`, `events`;
- endpoint `POST /api/daily-session/revision`;
- endpoint `GET /api/daily-session/events`;
- endpoint `GET /api/daily-session/events/stream`;
- optional websocket route;
- unified serializer для active snapshot.

## 8.2. session_manager.py

Нужно добавить или открыть наружу методы:

- `get_active_session(user_id)`
- `get_active_plan(session_id)`
- `apply_revision_command(session_id, command, source, actor_user_id)`
- `build_active_snapshot(session_id)`

## 8.3. execution_engine.py

Нужно обеспечить:

- emission execution events в единый поток;
- нормализованную запись `statebefore/stateafter/eventtype/eventpayload`;
- реакцию на `close_all` и `pause` в рамках FSM.

## 8.4. stats_audit.py

Нужно обеспечить:

- быстрый снимок `session_metrics` для Mini App;
- безопасную выдачу текущих метрик без ожидания конца сессии.

## 8.5. Redis/pubsub

Нужно добавить канал, например:

- `daily_session_events:{session_id}`

Или общий канал:

- `daily_session_events`

с фильтрацией по `session_id`.

---

## 9. Нефункциональные требования

- Response time `GET /api/daily-session/active` <= 500 ms при тёплом кеше.
- Response time `POST /api/daily-session/revision` <= 700 ms.
- Live event latency через SSE <= 2 секунды от момента записи события.
- Mini App не должен ломаться при отсутствии SSE/WS; polling fallback обязателен.
- Все endpoints должны логироваться с `user_id`, `session_id`, `source`, `status_code`, `latency_ms`.

---

## 10. Ошибки и коды ответа

| Код | Сценарий |
|-----|----------|
| 200 | Успешный `GET` |
| 201 | Успешный `POST /start` при создании сессии |
| 200 | Idempotent `POST /start` при уже активной сессии |
| 400 | Ошибка структуры запроса |
| 401 | Не прошла Telegram WebApp auth валидация |
| 404 | Сессия не найдена |
| 409 | Конфликт состояния FSM |
| 422 | Невалидная команда revision |
| 500 | Внутренняя ошибка сервиса |

Формат ошибки:

```json
{
  "ok": false,
  "error": "invalid_revision_command",
  "message": "command close_everything is not supported"
}
```

---

## 11. Acceptance scenarios

## 11.1. Старт сессии

**Given:** у пользователя нет активной сессии  
**When:** Mini App вызывает `POST /api/daily-session/start`  
**Then:** создаётся новая сессия, `GET /api/daily-session/active` возвращает `session.status=ARMED`

## 11.2. Повторный старт

**Given:** активная сессия уже есть  
**When:** пользователь повторно нажимает «Старт сессии»  
**Then:** backend не создаёт дубликат, а возвращает текущую активную сессию или 409 по согласованному контракту

## 11.3. Revision команда

**Given:** активная сессия в состоянии `ARMED`  
**When:** пользователь нажимает `tighten`  
**Then:** backend принимает команду, пишет audit trail, обновляет `last revision`, фронт видит новую команду в active snapshot

## 11.4. Close all

**Given:** есть открытая позиция и FSM в `IN_POSITION`  
**When:** пользователь нажимает `close_all`  
**Then:** движок инициирует закрытие позиции, пишет execution event, состояние сессии меняется на допустимое финальное или остановочное

## 11.5. Live events

**Given:** execution engine создаёт новое событие  
**When:** Mini App подписан на `events/stream`  
**Then:** событие появляется на экране без ручного refresh

## 11.6. Fallback на polling

**Given:** SSE недоступен  
**When:** Mini App не может установить stream  
**Then:** UI автоматически переключается на polling и продолжает получать новые события через `GET /events`

## 11.7. Нет активной сессии

**Given:** активной сессии нет  
**When:** открывается Mini App  
**Then:** `GET /active` возвращает `session:null`, а UI показывает форму запуска без ошибок

## 11.8. Auth fail

**Given:** подпись Telegram WebApp initData невалидна  
**When:** Mini App делает запрос к API  
**Then:** backend возвращает 401, UI показывает ошибку авторизации

---

## 12. Definition of Done

Задача считается выполненной, если:

- реализованы `revision`, `events`, `events/stream`;
- `active` возвращает стабилизированный unified payload;
- Mini App без правок фронта умеет стартовать сессию, читать состояние, отправлять команды и получать события;
- есть fallback с SSE/WS на polling;
- есть Telegram WebApp auth check;
- есть smoke test и ручной сценарий проверки в Telegram;
- документация по контрактам и endpoint-ам приложена к репозиторию.

---

## 13. Рекомендуемый порядок внедрения

1. Зафиксировать response schema `GET /active`.
2. Реализовать `POST /revision`.
3. Реализовать `GET /events`.
4. Реализовать `GET /events/stream`.
5. Подключить Redis pub/sub или DB polling для live delivery.
6. Добавить Telegram auth verification.
7. Прогнать end-to-end через Telegram Mini App.

---

## 14. Артефакты результата

В результате должны появиться:

- обновлённый `webui/app.py`;
- при необходимости патчи в `session_manager.py`, `execution_engine.py`, `stats_audit.py`;
- тесты контрактов API;
- рабочий Telegram Mini App без mock-заглушек;
- инструкция по запуску и проверке.
