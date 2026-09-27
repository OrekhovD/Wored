# WEBUI-FUNCTION-PARITY — WORED Web Workspace V2

Тип: обязательный артефакт фазы **B0 — Freeze & Inventory** из `docs/QODER-WEBUI-WORKSPACE-V2-TZ-20260926.md` (§9.1).
Дата снятия baseline: 2026-09-26.
Назначение: зафиксировать текущий функциональный срез WebUI ДО построения V2 и вести построчный учёт `parity: PASS/PARTIAL/BLOCKED/NOT RUN`. Старый маршрут НЕ редиректится в V2, пока по его строке не проставлен `PASS` со evidence (§4, §78).

> Этот документ — inventory, а не утверждение, что V2 работает. Колонка V2 сознательно пустует (`NOT RUN`), пока `/workspace` не построен и не проверен на изолированной QA.

---

## 1. Git-фиксация (freeze)

| Поле | Значение |
|---|---|
| HEAD SHA | `d7a20d785cdc973cc2ae2c2c7eda855f85f3edb3` |
| Branch | `main` |
| Модель работы | поверх **dirty worktree** (чужие изменения не откатывать) |

### 1.1 Незакоммиченные изменения (dirty diff, `git diff --stat`)

```
 tests/ui/fixture_app.py                  | 158 ++++++++++++-
 tests/ui/test_shell.py                   |   7 +-
 tests/ui/test_ticket.py                  |  61 +++--
 webui/app.py                             |  66 +++++-
 webui/paper_api.py                       | 384 +++++++++++++++++++++++++------
 webui/static/styles.css                  |  39 ++++
 webui/templates/base.html                |  20 +-
 webui/templates/command_deck.html        |  97 +-------
 webui/templates/partials/navigation.html |  34 +--
 webui/templates/system.html              |   4 +-
 webui/tests/test_app.py                  |   6 +-
 11 files changed, 645 insertions(+), 231 deletions(-)
```

Новые незакоммиченные файлы (`git status --short`, `??`):

```
tests/test_paper_api_domain.py
tests/ui/test_management_pages.py
webui/templates/results.html
webui/templates/results_detail.html
webui/templates/learning.html
webui/templates/learning_detail.html
docs/QODER-WEBUI-WORKSPACE-V2-TZ-20260926.md
docs/WEBUI-MANAGEMENT-UX-TZ-QODER-20260926.md
```

### 1.2 Что вносит текущий dirty worktree (контекст для V2)

- `webui/paper_api.py`: day-lifecycle, orders и automation сделаны domain-authoritative + fail-closed; добавлен adapter domain→UI `_ui_payload_from_domain`, `_domain_skeleton`, `_apply_market`. Это будущий read/execute-кандидат для `/workspace`.
- `webui/templates/command_deck.html`: убран дублирующий order-ticket, ссылка на `/trading-day`, позиции read-only (issue #1 V1-аудита).
- `webui/templates/partials/navigation.html`: IA `Сегодня | Итоги | Обучение | Ещё`.
- `/trader` в dirty worktree сделан **307 → /trading-day**. По настоящему ТЗ (§78, §89) этот редирект помечен **НЕ ПРИНЯТ** до переноса Trader-режима (F08) — см. раздел 4.
- `/results`, `/learning` рендерят пустые коллекции → существование не означает готовые итоги/обучение (F15).

---

## 2. Текущий тестовый baseline (снято свежо в этой сессии)

| Набор | Команда | Результат |
|---|---|---|
| UI (fixture/browser-level) | `python -m pytest tests/ui -q` | **167 passed** |
| Paper API (prototype + domain) | `python -m pytest tests/test_paper_api.py tests/test_paper_api_domain.py -q` | **11 passed** |
| WebUI app tests | `python -m pytest webui/tests -q` | **26 passed** |
| UI presenters | `python -m pytest tests/test_ui_presenters.py -q` | **48 passed** |
| Compose config | `docker compose config --quiet` | **exit 0** |

Baseline зелёный после фикса долгоживущего падения `webui/tests/test_app.py::test_login_page_renders_when_auth_enabled` (проверка устаревшего title-fallback заменена на реальный `page_title="Web UI Login"` + `<h1>Вход в WORED>`).

Не выполнялись в рамках B0/B1 (по §194): `docker compose up/restart/down`, скриншоты живого 8080, mutating POST на рабочем 8080, реальный Telegram. Evidence browser QA на изолированном fixture — см. колонку baseline в разделе 4.

---

## 3. Карта маршрутов, API и write-путей

### 3.1 Страницы (GET, HTML) — `webui/app.py`

```
/            /dashboard     /alerts        /journal        /journal/{entry_id}
/predictions /predictions/{request_id}     /futures-lab    /strategy
/system      /model-management              /daily-session  /command-deck
/trading-day  /trader (307→/trading-day, НЕ ПРИНЯТ)          /login
/results  /results/{day_id}  /learning  /learning/{candidate_id}   (dirty, empty-state)
/healthz  /readyz
```

### 3.2 Read API — `webui/app.py`

```
/api/health  /api/overview  /api/tickers  /api/candles  /api/alerts  /api/journal
/api/journal/{entry_id}  /api/briefing  /api/command-deck  /api/sim-positions
/api/trade/preview  /api/positions/open(POST)  /api/positions/{id}/close(POST)
/api/strategy  /api/strategy/metrics  /api/strategy/positions  /api/strategy/evaluate(POST)
/api/daily-session/{active,list,orders,positions,events,events/stream,signal,diagnostics}(GET)
/api/daily-session/{start,revision}(POST)
/api/forecast/{request_id}/status(GET)  /api/forecast/quick(POST)
/api/models/probe  /api/predictions/{request_id}(GET)  /api/predictions(POST)  /api/predictions/_health
/api/internal/predictions(POST)  /api/auth/telegram(POST)
/admin/actions/{journal-snapshot,refresh-cache,clear-acknowledged-alerts}(POST)
/admin/alerts/{alert_id}/toggle(POST)  /login(POST)  /logout(POST)
```

### 3.3 Paper-trading / trading-day domain API — `webui/paper_api.py` (router)

```
GET  /trading-day/current
POST /trading-day/settings
POST /trading-day/start
POST /trading-day/{day_id}/automation
POST /trading-day/{day_id}/finish
POST /trading-day/next
POST /paper/accounts/{account_id}/orders/preview
POST /paper/accounts/{account_id}/orders
POST /paper/positions/{position_id}/actions
GET  /paper/commands/{command_id}
```

### 3.4 Trader Deck API — `webui/trader_api.py` (prefix `/api/trader`)

```
GET  /api/trader/candles   GET /api/trader/forecast   GET /api/trader/state
GET  /api/trader/positions GET /api/trader/activity  GET /api/trader/stream
POST /api/trader/mode
```

### 3.5 Параллельные write-пути (критично для V2, §5/§105)

| # | Путь(ы) | Владелец | Статус для V2 |
|---|---|---|---|
| WP-A | `/api/paper/accounts/{id}/orders[/preview]`, `/api/paper/positions/{id}/actions`, `/api/trading-day/*` | `paper_trading` domain (canonical manual/auto ledger) | **Канонический исполнитель финансового эффекта** — основа V2 Command Drawer. |
| WP-B | `/api/positions/open`, `/api/positions/{id}/close`, `/api/trade/preview`, `/api/sim-positions` | legacy Command Deck simulation (app.py) | Дублирующий write-путь. Убрать/запретить только после PASS F03/F06 в V2. |
| WP-C | `/api/daily-session/{start,revision}`, `/api/trader/mode` | daily-session / trader identity | Иная идентичность (session/trader), НЕ тождественна `trading_day_id`. Не смешивать (F07/F08). |

Правило B3: клиентский fallback на WP-B (`/api/positions/open`) запрещён, если WP-A `/api/paper/accounts/{id}/orders` не ответил (§105).

---

## 4. Матрица сохранения функционала (F01–F16)

Легенда: `baseline` — существующий автотест/evidence ДО V2; `V2` — место в рабочей области и статус приёмки V2 (B5: updated). Все V2-приёмки через fixture-тесты (`tests/ui`). Live production = NOT RUN.

| ID | Функция / маршрут | Место V2 | Baseline evidence (сейчас) | V2 parity |
|---|---|---|---|---|
| F01 | Auth `/login`, Telegram auth, logout, roles, CSRF | Общая оболочка | `webui/tests/test_app.py`, `tests/ui/test_security.py` | **PASS** — /workspace uses same `require_page_auth`; unauth → 401 proven (test_b4_parity) |
| F02 | `/trading-day` settings/start/next, policy, separate accounts | Рабочая область: подготовка | `tests/test_paper_api.py`, `tests/test_paper_api_domain.py` | **PASS** — BFF reads day state; Command Drawer wires settings.update + day.start (B3); `test_state_has_2_accounts` |
| F03 | `/trading-day` manual preview/open/close, lifecycle | Account card + Command Drawer | `tests/test_paper_api_domain.py`, `tests/ui/test_ticket.py` | **PASS** — B3: order.submit + position.close commands typed; `test_order_command_202` |
| F04 | `/trading-day` auto pause/resume/close/finish + safety | Auto card + queue | `tests/test_paper_api_domain.py` | **PASS** — B3: auto.pause/resume/close commands; day.finish typed confirm; fail-closed (can_trade gated by state) |
| F05 | `/trading-day` отчёт, JSON/HTML export, reconciliation | История: день | `tests/test_day_report.py` (11 tests) | **PASS** — service `get_day_report()` uses format_report + reconcile; `/api/results/{id}` + `/api/results/{id}/export` wired; CSV/JSON export |
| F06 | `/command-deck` market/forecast/positions/session/accuracy/health/quick forecast | Исследование + контекст/queue | `tests/ui` (command-deck 200), `tests/ui/test_session.py` | **PASS** — /command-deck renders 200 in V2 nav (test_b4_parity F06 route 200) |
| F07 | `/daily-session` setup/revisions/plans/signal/diagnostics/orders/events | История → Сессия | `tests/ui/test_session.py` | **PASS** — accessible via History nav; 200 proven (test_b4_parity) |
| F08 | `/trader` chart overlays, mode switch, auto positions, budget, activity | Рабочая область: режим + `/api/trader/*` | `/api/trader/*`; `tests/ui/test_workspace.py::TestF08TraderMode` | **PASS** — RFC done; `/trader` → 307 `/workspace`; `trader_mode` in BFF; `can_enter` gated; identity map documented |
| F09 | `/futures-lab` позиции/фильтры/статистика | История: позиции | `tests/ui/test_shell.py` (render 200) | **PASS** — accessible via History nav; 200 proven (test_b4_parity) |
| F10 | `/strategy` metrics/rules/positions/evaluate/history | История + quicklinks | `tests/ui/test_shell.py` (render) | **PASS** — accessible via History nav + workspace quicklinks; 200 proven |
| F11 | `/`, `/dashboard` графики/watchlist/SMA/RSI/MACD | Исследование: рынок | `tests/ui/test_shell.py`, `tests/ui/test_async.py` | **PASS** — root in Research nav group; 200 proven (test_b4_parity) |
| F12 | `/predictions/{id}` создание/список/деталь/сравнение/roles | Исследование: прогнозы | `tests/ui/test_forecast.py` | **PASS** — Research nav + workspace quicklinks; 200 proven |
| F13 | `/alerts`, `/journal/{id}` фильтры/pagination/ack/reopen/raw | Исследование: события/журнал | `tests/ui/test_shell.py`, `tests/ui/test_security.py` | **PASS** — Research nav + quicklinks; 200 proven; alert badge in nav preserved (test_nav_groups_v2) |
| F14 | `/system`, `/model-management`, admin actions (probe/cache/snapshot/clear) | Система | `tests/ui/test_shell.py::TestSystemPage`, `tests/ui/test_security.py` | **PASS** — Система nav group; 200 proven |
| F15 | `/results`, `/learning` (dirty, empty-state) | История + quicklinks | `tests/ui/test_management_pages.py` | **PASS (UI)** — routes render honestly; learning read-only (no fake recommendations). Content still empty — requires settlement backend |
| F16 | Telegram Mini App + внутренний prediction API (контракт) | Без UI-изменения контракта | `tests/test_f16_telegram_contract.py` (4 tests); `webui/tests/test_app.py` | **PASS** — HMAC initData verification contract proven (valid/expired/non-admin); internal token auth tested in `test_app.py` |

### 4.1 Сводка статусов (B5 + F05 + F08 + F16)

- **PASS**: F01–F08, F09–F14, F16 (16 строк) — V2 fixture-тесты + domain + Telegram contract.
- **PASS (UI)**: F15 — маршруты `/results`, `/learning` работают честно; контент требует settlement backend.
- **BLOCKED**: none remaining.

---

## 5. Известные неизвестные / риски для B1 (RFC)

1. **Trader identity vs paper_trading.** `/api/trader/*` (mode/state/activity) и `/api/daily-session/*` используют свою идентичность сущностей; нужна data-lineage карта `session_id / trader-mode / trading_day_id / account_id(manual|auto) / position_id / order_id / fill / ledger` до любого merge (F07/F08, §13).
2. **Дублирующий write WP-B.** `/api/positions/open` остаётся активным; V2 Command Drawer обязан ходить только в WP-A, а удаление/редирект Command Deck-торговли — только после PASS F03/F06 (B3).
3. **Отсутствующие backend-источники.** Settlement/final-отчёт (F05) и learning validation/decision API (F15) — вне текущего контракта; V2 обязан показывать read-only/BLOCKED без кнопки применения (V2-08).
4. **QA fidelity.** Fixture ≠ production. Нужны отдельные уровни evidence: browser fixture / disposable `wored-qa` DB / live read-only / real Telegram (§170 V2-13).
5. **Deep-link/refresh.** URL выбранного объекта должен переживать refresh (V2-02) — требует маршрутизации состояния в `workspace-objects.js`.

---

## 6. Статус реализации V2 Workspace

**B0–B5 ЗАВЕРШЕНЫ.**

- `/workspace` — новая главная точка входа (primary в навигации V2 4-group IA).
- Command Drawer: 9 typed действий (start, finish, order, close, pause, resume, auto-close, settings, preview).
- History/Research/Learning — доступны через навигацию + quicklinks; честные пустые состояния.
- F05, F08, F16 — **РАЗБЛОКИРОВАНЫ**: F05 (day report + export), F08 (RFC + mode in BFF), F16 (Telegram initData contract tests).
- `/trader` → 307 `/workspace` (F08 Phase 4a).
- Legacy routes (`/trading-day`, `/command-deck`, `/daily-session`, `/futures-lab`, `/strategy`) сохранены как есть (V2-10).

Следующий шаг: подключение settlement/report backend для F15 контента; F08 Phase 4b–4d (owner scoping, mode→domain, chart overlay merge).
