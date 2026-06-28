# Техническое задание на фикс daily-session
## WORED — не создаются/не запоминаются сессии, не запускается торговля

**Дата:** 28.06.2026  
**Статус:** Fix Spec / Production Incident

---

## 1. Проблема

Пользователь создаёт новую daily-session, но система ведёт себя так, будто сессия не создана: она не сохраняется как активная, не появляется в runtime execution, не переходит в торговый цикл и не запоминается после обновления интерфейса.

---

## 2. Симптомы

- Новые сессии не создаются корректно.
- Сессии не сохраняются между обновлениями UI.
- Торговля не стартует после создания сессии.
- UI может показывать данные, но runtime не получает активную сессию.
- Execution loop остаётся в idle / paused / stale состоянии.

---

## 3. Вероятные причины

### 3.1. Нет транзакционного создания сессии

Создание session, plan, entries и initial events происходит неатомарно. Если один шаг падает, сессия остаётся неполной.

### 3.2. Нет bootstrap шага

После создания сессии не запускается `sessionBootstrap` или не вызывается переход состояния в `ARMED`.

### 3.3. Нет передачи данных в execution engine

Active plan не попадает в sim engine / execution watcher.

### 3.4. Stale market data

Collector/Redis не дают свежий snapshot, поэтому execution блокируется.

### 3.5. Нет нормального state machine

Статусы session существуют, но нет жёсткой модели переходов.

---

## 4. Цель фикса

Сделать создание daily-session атомарным, гарантированно сохраняемым и запускаемым, чтобы после успешного создания сессия:

- сохранялась в Postgres,
- появлялась в списке активных,
- переходила в runtime execution,
- начинала отслеживание market data,
- при необходимости запускала торговый цикл или честно показывала причину блокировки.

---

## 5. Область работ

Фикс затрагивает:

- backend daily-session creation;
- session persistence;
- bootstrap job;
- execution state machine;
- collector/market snapshot validation;
- UI статусы и диагностику;
- audit log.

---

## 6. Требуемые изменения backend

### 6.1. Атомарное создание сессии

При нажатии `Create session` система должна в одной транзакции:

1. Создать запись в `tradingsessions`.
2. Создать запись в `sessionplans`.
3. Создать записи в `plannedentries`.
4. Создать initial `executionevents`.
5. Сохранить `activeplanversion`.
6. Сохранить initial `sessionmetrics` или пустую метку состояния.

Если любой шаг падает — транзакция откатывается полностью.

### 6.2. Явный bootstrap

После успешного commit должен запускаться `SessionBootstrapJob`:

- проверяет snapshot рынка;
- валидирует plan;
- переводит session в `ARMED`;
- публикует событие в очередь execution;
- пишет audit event.

### 6.3. State machine

Нужно внедрить явные состояния:

- `CREATED`
- `PLANNED`
- `ARMED`
- `IN_POSITION`
- `COOLDOWN`
- `PAUSED`
- `STOPPED`
- `COMPLETED`
- `FAILED`

Переходы должны быть только через разрешённые state transitions.

### 6.4. Execution guardrails

Перед стартом торговли выполнить проверки:

- есть ли свежий market snapshot;
- не stale ли Redis;
- не превышен ли risk limit;
- есть ли валидный active plan;
- не заблокирована ли сессия по стоп-условиям.

Если проверка не проходит — session остаётся в `PAUSED` или `BLOCKED`, а причина пишется в audit log.

### 6.5. Persistence contract

Необходимо гарантировать, что:

- `GET active session` всегда читает данные из Postgres;
- current runtime state синхронизируется через Redis pub/sub или explicit update;
- `executionevents` пишутся при каждом изменении состояния;
- `sessionmetrics` обновляются по таймеру и при закрытии.

---

## 7. Требуемые изменения UI

### 7.1. Явный статус сессии

В верхнем блоке Daily Session показать:

- Session ID;
- Status;
- Plan version;
- Start time;
- Risk mode;
- Market snapshot freshness;
- Execution readiness.

### 7.2. Кнопки

Разделить действия:

- `Create session`.
- `Start execution`.
- `Pause`.
- `Resume`.
- `Stop`.
- `Refresh status`.

Нельзя оставлять один неясный action, который и создаёт, и запускает, и валидирует одновременно.

### 7.3. Диагностические сообщения

Если сессия не запустилась, UI должен показывать точную причину:

- market snapshot stale;
- plan invalid;
- DB write failed;
- bootstrap failed;
- execution loop unavailable;
- risk gate blocked.

---

## 8. Логи и аудит

Добавить audit trail для каждого ключевого шага:

- session created;
- plan created;
- entries created;
- bootstrap started;
- bootstrap success/fail;
- execution armed;
- execution start;
- execution blocked;
- execution paused;
- execution stopped;
- session completed.

Логи должны содержать `session_id`, `plan_version`, `reason_code`, `timestamp`.

---

## 9. Acceptance criteria

Фикс считается успешным, если:

1. Новая session создаётся и после refresh остаётся в системе.
2. Сессия появляется в `tradingsessions` и `sessionplans`.
3. Bootstrap переводит session в `ARMED`.
4. Execution получает active plan.
5. Если торговля не стартует, пользователь видит точную причину.
6. `executionevents` и `sessionmetrics` обновляются.
7. Повторный запуск не создаёт дубликаты.

---

## 10. Edge cases

- Повторное нажатие `Create session`.
- Потеря Redis snapshot.
- Падение collector во время bootstrap.
- Частичный DB commit.
- Нет активного рынка или торговая пауза.
- Ручной stop во время bootstrap.

---

## 11. Приоритет реализации

### P0

- Атомарное создание session.
- Bootstrap job.
- State machine.
- Причины блокировки.

### P1

- UI статусы.
- Audit log.
- Refresh/status indicators.

### P2

- Красивые анимации.
- Вторичные диагностические панели.

---

## 12. Критерии приёмки для разработчика

Разработчик должен показать:

- код транзакционного создания;
- bootstrap job;
- список state transitions;
- обновление audit trail;
- UI, который отображает blocked reason;
- успешный сценарий создания и запуска сессии на тестовом стенде.

---

## 13. Ожидаемый результат

После фикса daily-session должна вести себя как настоящая торговая сессия, а не как набор UI-карточек: сессия создаётся, сохраняется, активируется, исполняется или честно объясняет, почему исполнение заблокировано.
