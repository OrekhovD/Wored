
# Техническое задание
## WORED — Daily Session Trading Dashboard v2 (Simulation)

**Дата:** 28.06.2026  
**Версия:** 2.0  
**Статус:** Product + UX + Backend Specification

---

## 1. Цель и контекст

Цель дашборда — дать трейдеру/оператору понятный и удобный инструмент для:

1. Настройки торговой симуляции перед началом сессии (стиль торговли, время, цели прибыли, бюджет, риск и пр.).
2. Мониторинга текущих торговых сессий (статусы, KPI, риск, экономика).
3. Просмотра и управления ордерами/позициями и их статистикой.

Система базируется на Daily Pipeline WORED v2 (8‑часовой BTCUSDT perp HTX), FSM Execution Agent, таблицах PostgreSQL (`trading_sessions`, `session_plans`, `session_metrics`, `executed_trades`, `execution_events`, `daily_reviews`) и WebUI FastAPI backend.[1]

Дашборд не является ручным торговым терминалом биржи; он — control surface для симуляции и AI-управляемой торговли, но UX должен опираться на лучшие практики HTX Futures, Binance Futures и Bybit: разделённые зоны настроек, ордерного ввода, графиков, позиций и истории.[2][3][4]

---

## 2. Общая информационная архитектура

Страница `/daily-session` делится на три вертикальных зоны:

1. **Session Setup Panel (верх)** — мастер настройки новой симуляции.
2. **Session Monitor Panel (середина)** — статус активной/выбранной сессии и ключевые метрики.
3. **Orders & Events Panel (низ)** — позиции, открытые ордера, закрытые сделки, execution events.

Навигация:

- Breadcrumb/таб Navigation: `WORED · Dashboard · Alerts · Predictions · AI Journal · Futures Lab · Daily Session · Strategy · Models`.
- Selector активной сессии (dropdown + search) над Session Monitor Panel.

---

## 3. Session Setup Panel (Pre-Trade Configuration)

### 3.1. Назначение

Панель должна позволить за 1–2 действия сконфигурировать и запустить новую симуляцию/торговую сессию, аналогично тому, как биржевые терминалы (HTX/Binance/Bybit) дают выбрать контракт, margin mode, leverage, позиционный режим, размер и тип ордера, TP/SL.[2][3][4]

### 3.2. Блоки панели

Панель состоит из пяти блоков:

1. **Instrument & Market Block**
2. **Trade Style & Horizon Block**
3. **Budget & Risk Block**
4. **Profit Target & Costs Block**
5. **Review & Launch Block**

#### 3.2.1. Instrument & Market Block

Поля:

- `Symbol` (dropdown) — на первом этапе фиксируется `BTCUSDT Perpetual (HTX)`, но UI готов к расширению.
- `Exchange` (readonly или dropdown) — HTX.
- `Contract Type` — `USDT-M Perpetual` (в будущем Coin-M).[3][4]
- `Position Mode` — `One-way` / `Hedge` (на будущее, сейчас readonly `One-way`).
- `Margin Mode` — `Cross` / `Isolated` (текущий pipeline использует `isolated` для fast-режима).[1]

Отдельные подсказки по funding rate, времени до следующего funding, базируясь на практике HTX UI: отображать expected funding и countdown.[2]

#### 3.2.2. Trade Style & Horizon Block

Поля:

- `Trade Direction` — radio-buttons `LONG`, `SHORT`, `BOTH`, `AUTO`.
- `Trade Horizon` — radio-buttons `FAST`, `MEDIUM`, `LONG`.
- `Session Duration` — selector `4h / 8h / 12h` (по умолчанию 8h).
- `Session Start Time` — `Now` или планирование на будущее.
- `Execution Mode` — `Simulation only` / `HTX paper` / `HTX live` (на первом этапе `Simulation only`).

Horizon mapping:

- FAST — скальпинг, удержание 1–15 мин, цель 1–2 USDT чистой прибыли.
- MEDIUM — внутрисессионные сделки 15–90 мин.
- LONG — позиционный режим 90+ мин в рамках session window.[1]

#### 3.2.3. Budget & Risk Block

Поля и контролы:

- `Budget USDT` — numeric field + slider (25 / 50 / 100 / custom).
- `Risk Mode` — `defensive`, `balanced`, `aggressive` (как в Daily Pipeline).[1]
- `Max Session Drawdown %` — формируется из risk mode (например 6% для balanced).[1]
- `Max Failed Entries` — из risk mode (2/3/4).[1]
- `Max Simultaneous Positions` — порог (по умолчанию 1).[1]
- `Max Trade Duration` — зависит от horizon.

Отдельный блок `Leverage Policy`:

- `Allowed Leverage Values` — 100x, 125x, 150x, 200x (как в risk policy).[1]

#### 3.2.4. Profit Target & Costs Block

Поля:

- `Target Net Profit per Trade (USDT)` — по horizon: FAST 1.5, MEDIUM 3.0, LONG 5.0.
- `Cost Filter Enabled` — checkbox (для FAST всегда включен).
- `Estimated Taker Fee (%)` — 0.046% (как в pipeline).[1]
- `Estimated Slippage (bps)` — конфиг.
- `Min Expected Net Profit after Costs` — вычисляется и показывается пользователю.

Цель блока — визуально показать, как gross PnL, fees и slippage влияют на net PnL, по аналогии с панелью fees и liquidation risk в биржевых интерфейсах.[2][3]

#### 3.2.5. Review & Launch Block

Отображает итоговый summary выбранных настроек:

- Инструмент, направление, горизонт.
- Duration, risk mode, budget.
- Target net profit, cost filter.

Кнопки:

- `Launch Session` — POST `/api/daily-session/start`.
- `Save Preset` — сохранить профиль настроек.
- `Reset` — сброс к дефолтам.

Launch подтверждение:

- ID созданной сессии.
- Статус `ARMED`.
- Horizon/Direction/Target/Risk/Budget.

---

## 4. Session Monitor Panel

### 4.1. Назначение

Средняя часть дашборда отвечает за обзор активной или выбранной торговой сессии: статус, профиль торговли, ключевые метрики и активный план. Она должна быть ближе к Trading Activity Dashboard на Binance Futures и панелям Positions & Open Orders у HTX/Bybit.[2][3][4]

### 4.2. Структура панели

Панель делится на три горизонтальных блока:

1. **Session Card**
2. **Metrics Grid**
3. **Active Plan Card**

#### 4.2.1. Session Card

Поля:

- `ID` — UUID.
- `Symbol` — BTCUSDT.
- `Exchange` — HTX.
- `Status` — `IDLE / ARMED / IN_POSITION / PAUSED / STOPPED / COMPLETED`.[1]
- `Direction` — auto/long/short/both.
- `Horizon` — fast/medium/long.
- `Risk Mode` — defensive/balanced/aggressive.
- `Plan Version` — vN.
- `Start` / `End` — timestamps.
- `Target Profit per Trade` — net.
- `Max Duration per Trade` — minutes.
- `Max Session Drawdown %`.
- `Failed Entries`.
- `Last Command`.

В правом верхнем углу — статус чип (`LIVE`, `COMPLETED`, `PAUSED`) и pipeline метка `BTCUSDT Perpetual · HTX · 8h Pipeline`.

#### 4.2.2. Metrics Grid

Карточка с KPI:

- `Trades` — count.
- `Win / Loss` — 3/1.
- `Liquidations`.
- `Total PnL` — USDT.
- `PnL %` — %.
- `Net PnL (after costs)` — USDT.
- `Gross PnL` — USDT.
- `Fees` — USDT.
- `Slippage` — USDT.
- `Max Drawdown %`.
- `Profit Factor`.
- `Time in Market %`.
- `Avg Trade Duration (min)`.
- `Cost Filter Rejections`.
- `Target Hits`.

Карточка должна читаться как компактный PnL dashboard по аналогии с Binance Trading Activity Dashboard и панелью Positions на HTX Futures.[2][3]

#### 4.2.3. Active Plan Card

Показывает текущий план:

- `Regime` — текст (trend up / range / trend down).
- `Thesis` — описание.
- `Primary Scenario`.
- `Alternative Scenario`.
- `No-Trade Condition`.
- `Max DD`, `Max Fails`, `Max Positions`, `Cooldown`.
- `Direction`, `Horizon`, `Risk Mode`.

Ниже — `Last Revision` (команда, время). Это блок уже частично реализован в текущем UI; ТЗ требует выровнять его визуально и добавить нужные поля из новой trade profile схемы.[1]

---

## 5. Orders & Events Panel

### 5.1. Назначение

Нижняя часть дашборда предназначена для работы с позициями, ордерами и execution events, в духе модулей Positions & Open Orders в HTX/Binance/Bybit: таблицы с размером позиции, PnL, ликвидацией, TP/SL, статусом ордеров и транзакционной историей.[2][3][4]

### 5.2. Подпанели

Панель разделена на три вкладки:

1. `Positions` — текущие симулированные позиции.
2. `Orders` — заявки симулятора.
3. `Execution Events` — лог событий FSM.

#### 5.2.1. Positions Tab

Колонки:

- `#` — идентификатор позиции.
- `Symbol` — BTCUSDT / ETHUSDT.
- `Side` — LONG/SHORT.
- `Margin Mode` — Cross/Isolated.
- `Leverage` — x.
- `Entry Price`.
- `Mark Price`.
- `Size (USDT)`.
- `Margin Used (USDT)`.
- `Unrealized PnL (USDT/%).`
- `Realized PnL (USDT).`
- `Fees (USDT).`
- `Slippage (USDT).`
- `Net PnL (after costs).`
- `Liquidation Price`.
- `Status` — `OPEN / CLOSED / LIQUIDATED`.
- `Open Time` / `Close Time`.
- `Trade Horizon`.
- `Target Net Profit`.

В правой части строк — контекстные действия симуляции: `Close`, `Close All`, `Force Timeout`, `Mark As Reviewed`.

#### 5.2.2. Orders Tab

Колонки:

- `Order ID`.
- `Type` — Market / Limit / Conditional.
- `Side`.
- `Price`.
- `Size`.
- `Status` — `NEW / PARTIALLY_FILLED / FILLED / REJECTED / CANCELED`.
- `Reason` — для rejected.
- `Linked Position ID`.

Таблица позволяет фильтровать по статусу и времени.

#### 5.2.3. Execution Events Tab

Лог FSM событий (уже реализован частично):

- `Event Type` — `position_opened`, `position_closed`, `entry_rejected_cost_filter`, `revision_continue`, `revision_reduce`, `revision_tighten`, `revision_pause`, `session_paused`, `session_resumed`, `session_stopped`.[1]
- `State Before`.
- `State After`.
- `Timestamp`.
- `Payload Summary` — side, leverage, reason code, economics.

Нужна фильтрация по типам событий и время.

---

## 6. Источники данных и API

### 6.1. Источники

- PostgreSQL: `trading_sessions`, `session_plans`, `session_metrics`, `executed_trades`, `execution_events`, `daily_reviews`.[1]
- Redis: real-time tickers, если нужно отображать цены.[1]
- WebUI FastAPI: `GET /api/daily-session/active`, `POST /api/daily-session/start`, `GET /api/daily-session/events`, `GET /api/daily-session/events/stream` (SSE).[1]

### 6.2. Основные API для дашборда

1. `POST /api/daily-session/start` — запуск новой сессии по настройкам из Session Setup Panel.
2. `GET /api/daily-session/active` — unified snapshot для Session Monitor Panel.
3. `GET /api/daily-session/list` — список исторических/активных сессий для селектора.
4. `GET /api/daily-session/positions?session_id=` — данные для Positions Tab.
5. `GET /api/daily-session/orders?session_id=`.
6. `GET /api/daily-session/events?session_id=&limit=`.
7. `GET /api/daily-session/events/stream?session_id=` — серверные события для live‑ленты.

---

## 7. UX‑принципы и лучшие практики

На основе HTX/Binance/Bybit UI и статей по терминалам:[2][3][4]

1. **Чёткое разделение зон:** настройки / мониторинг / ордера.
2. **Минимум текста — максимум структурированных блоков:** Session Card, Metrics Grid, Plan Card.
3. **Цветовой код по риску:** зелёный — нормальный режим, жёлтый — warning (drawdown/риск ликвидации), красный — критика.
4. **Визуальные индикаторы margin/liquidation:** как на HTX Futures — показывать margin ratio, liquidation price.[2]
5. **Позиции и ордера в единой таблице, как на Binance/HTX:** sortable и фильтруемые колонки.[2][3]
6. **Кастомизируемые модули:** в будущем возможность включать/выключать блоки (как у Binance в Advanced/Pro режимах).[2]

---

## 8. Acceptance criteria

1. Перед стартом сессии пользователь может настроить стиль торговли, горизонт, бюджет, риск и целевую net‑прибыль и запустить сессию из Session Setup Panel.
2. Session Monitor Panel показывает все ключевые параметры сессии и метрики в одном экране.
3. Orders & Events Panel отображает позиции, ордера и события в читаемых таблицах, без ручного похода в БД.
4. WebUI API возвращают все нужные поля для фронта.
5. Дашборд остаётся читабельным на 1920×1080 и 1366×768.
6. Любая ошибка backend отображается user-safe, без сырых исключений.

---

## 9. Рекомендуемый порядок реализации

1. Расширить API `start/active/list/positions/orders/events` под новые поля.
2. Обновить backend сериализацию под unified snapshot.
3. Реализовать Session Setup Panel (форма + валидация + запуск).
4. Реализовать Session Monitor Panel (карточки + метрики + план).
5. Реализовать Orders & Events Panel (таблицы + фильтрация + live‑лента).
6. Настроить SSE/polling для Execution Events.
7. Пройти smoke‑тесты с реальными симуляциями.

---

## 10. Артефакт

Данный документ хранится как `TZ_daily_session_dashboard_WORED_v2.md` и должен быть добавлен в репозиторий в раздел `docs/` или аналогичный.
