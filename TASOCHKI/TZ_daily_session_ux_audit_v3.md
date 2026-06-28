# Жёсткий UX-аудит /daily-session и ТЗ на новый layout
## WORED Daily Session Dashboard v3

**Дата:** 28.06.2026  
**Статус:** UX Audit + Implementation Specification

---

## 1. Цель

Перестроить текущий `/daily-session` в понятный операционный дашборд для симуляции торговли: быстрый запуск сессии, мониторинг активной сессии, просмотр ордеров/событий и контроль качества исполнения. Текущий экран функционален, но перегружен, смешивает сценарии, иерархию и диагностические логи.

---

## 2. Жёсткий UX-аудит текущего экрана

### 2.1. Что оставить

Оставить нужно только то, что помогает принимать решение или управлять сессией прямо сейчас:

- `Session summary` с ID, symbol, direction, horizon, risk mode, status.
- `Active Plan` как компактную карточку с ключевым тезисом и no-trade condition.
- KPI метрики: trades, win/loss, total PnL, net PnL after costs, max drawdown, profit factor, time in market.
- `Positions` таблицу.
- `Orders` таблицу.
- `Execution Events` как отдельный диагностический слой, но не в центре экрана.
- `Last Revision` как короткая сводка, не длинный журнал.

### 2.2. Что убрать или спрятать

Убрать из основного потока:

- Длинный `Execution Event Log` из первого экрана.
- Повторяющиеся служебные строки без пользы для принятия решения.
- Любые блоки, которые дублируют уже показанное в summary.
- Слишком детальные технические payload-строки из видимого UI.

Спрятать в collapsible / tabs:

- Полный event payload.
- Полную revision history.
- Внутренние debug labels.

### 2.3. Что перенести наверх

Наверх должны уйти только элементы, необходимые для запуска и быстрого понимания состояния:

- Session launch / setup блок.
- Active session status.
- Current regime / thesis.
- Immediate risk state.
- Current cost filter / target profit status.

### 2.4. Что объединить

Нужно объединить:

- `Active Plan` + `Last Revision` в единый `Plan & Revision` блок.
- `Metrics` + `Risk` в единый `Session Health` блок.
- `Positions` + `Orders` + `Events` в tabbed `Execution` zone.

---

## 3. Новый layout в 3 колонки

### 3.1. Общий принцип

Страница должна быть собрана в три колонки:

- **Left column — Setup & Control**.
- **Center column — Session & Performance**.
- **Right column — Orders, Events, Diagnostics**.

Приоритет слева направо: запуск → контроль → детали.

---

## 4. Точная схема 3 колонок

### 4.1. Left column: Setup & Control

Приоритет: **P1**.

Содержимое:

1. `Start new session` wizard.
2. `Trade Direction`.
3. `Trade Horizon`.
4. `Budget`.
5. `Risk Mode`.
6. `Target Net Profit`.
7. `Max Trade Duration`.
8. `Cost Filter`.
9. `Launch / Reset` buttons.

Правила:

- Это главный action surface.
- Блок должен быть sticky на desktop.
- На mobile должен идти первым.
- Должен занимать не больше 25–30% ширины экрана.

### 4.2. Center column: Session & Performance

Приоритет: **P0**.

Содержимое сверху вниз:

1. `Session Summary Card`.
2. `Health KPI Grid`.
3. `Active Plan & Revision`.
4. `Performance Trend` или мини chart.
5. `Session status timeline`.

Правила:

- Это главный экран принятия решения.
- Самый крупный блок на странице.
- Должен показать, живёт ли сессия, есть ли входы, есть ли риски, есть ли net profit.

### 4.3. Right column: Orders, Events, Diagnostics

Приоритет: **P2**.

Содержимое:

1. `Execution tabs`.
2. `Positions`.
3. `Orders`.
4. `Execution Events`.
5. `Revision log`.
6. `Debug / raw payload` only in advanced mode.

Правила:

- Это вторичная зона.
- Должна быть сворачиваемой.
- На mobile должна переходить в tabs или accordion.

---

## 5. Новая иерархия контента

### P0 — must see immediately

- Session status.
- Net PnL after costs.
- Total PnL.
- Drawdown.
- Open positions count.
- Active plan regime.
- No-trade / risk block.

### P1 — see next

- Direction.
- Horizon.
- Budget.
- Risk mode.
- Target net profit.
- Last revision.

### P2 — inspect on demand

- Orders.
- Events.
- Full revision chain.
- Raw execution payload.

---

## 6. Технические задачи по фронту

### 6.1. Компоненты

Нужно реализовать:

- `SessionSetupWizard`
- `SessionSummaryCard`
- `SessionHealthGrid`
- `PlanRevisionCard`
- `ExecutionTabs`
- `PositionsTable`
- `OrdersTable`
- `EventsFeed`
- `DebugDrawer`

### 6.2. Responsive behavior

Desktop:

- 3 колонки.
- Left sticky.
- Center fluid.
- Right collapsible.

Tablet:

- 2 колонки: left + center, right под ними tabs.

Mobile:

- 1 колонка.
- Сначала setup.
- Потом summary.
- Потом health.
- Потом execution tabs.

### 6.3. Interaction rules

- Setup form должна быть видна без скролла при открытии страницы.
- Summary должен обновляться без перезагрузки.
- Events feed должен работать в streaming mode, но быть свернутым по умолчанию.
- Debug payload показывать только при включённом advanced toggle.

---

## 7. Изменения в данных

### 7.1. Session state model

В snapshot должны быть группы:

- `setup`
- `summary`
- `health`
- `plan`
- `revisions`
- `orders`
- `positions`
- `events`

### 7.2. API requirements

`GET /api/daily-session/active` должен вернуть:

- session metadata.
- setup defaults.
- metrics.
- active plan.
- latest revision.
- counts for orders/positions/events.

`GET /api/daily-session/orders` и `GET /api/daily-session/events` должны поддерживать пагинацию и фильтры.

---

## 8. Acceptance criteria

1. Пользователь понимает статус сессии за 3 секунды.
2. Настройки сессии доступны без поиска по странице.
3. Самые важные KPI находятся в центре.
4. Логи не мешают восприятию, но доступны по запросу.
5. На 1366×768 экран остаётся читаемым.
6. На mobile layout не ломается и сохраняет приоритеты.

---

## 9. Что именно менять в текущем UI

### Оставить

- Общий dark theme.
- Card-based layout.
- KPI tokens.
- Status badges.

### Убрать

- Линейный длинный event log в первом экране.
- Перенасыщение метриками без группировки.
- Повтор summary в нескольких местах.

### Перенести наверх

- Session setup.
- Active status.
- Risk state.
- Target profit state.

### Перестроить

- Active plan и revision в один card.
- Orders/positions/events в tabs.
- Metrics в health grid.

---

## 10. Итоговая рекомендация

Текущий интерфейс нужно считать не финальным, а **инженерной заготовкой**. Он хорош как backend cockpit, но для операционного трейдинг‑дашборда ему нужна жёсткая перекомпоновка: один экран — одна задача, один главный акцент, один путь взгляда.
