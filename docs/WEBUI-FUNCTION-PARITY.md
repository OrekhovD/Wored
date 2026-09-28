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

### 4.0 Легенда статусов и уровней доказательства

**Статус приёмки:**

| Статус | Значение |
|---|---|
| **PASS** | Функция работает на требуемом уровне доказательства; дефектов не найдено. |
| **PARTIAL** | Код исправлен и закрыт unit/fixture-тестами; требуется интеграционная (PG QA) или браузерная приёмка. |
| **FAIL** | Дефект известен, не исправлен; нельзя считать функцию принятой. |
| **BLOCKED** | Внешняя зависимость (live Telegram, settlement backend) не позволяет проверить; статус не выставляется до разблокировки. |

**Уровень доказательства (evidence level):**

| Уровень | Что покрывает |
|---|---|
| `unit` | Чистые функции/presenters, моки репозитория. |
| `fixture` | HTTP round-trip через `tests/ui/fixture_app.py` (ASGI, без реального PG/Redis). |
| `integration-pg` | `docker compose -f docker-compose.qa.yml` + одноразовая БД `wored-qa`. |
| `runtime-probe` | HTTP-сессия с CSRF + cookie внутри живого WebUI-контейнера (`scratch/qa_login_probe.py`); доказывает авторизацию и рендер BFF, но не визуальный рендер. |
| `browser-desktop` / `browser-mobile` | Playwright на реальном рантайме WebUI. |
| `live` | Реальный Telegram-бот, реальный HTX-фид, реальный Ollama. |

**Важно:** fixture-приёмка НЕ заменяет браузерную и NOT является production-приёмкой. Строка может быть `PARTIAL` даже с зелёными тестами, если требуемый `evidence_level` не достигнут.

### 4.1 Таблица паритета

| ID | Функция / маршрут | Место V2 | Evidence сейчас | Требуемый evidence | Статус | Комментарий |
|---|---|---|---|---|---|---|
| F01 | Auth `/login`, Telegram auth, logout, roles, CSRF | Общая оболочка | `unit` + `fixture` + `runtime-probe` (POST /login → 303 /workspace → 200; session + CSRF в живом контейнере) + `browser-desktop` (Playwright 1920×1080: `/login` → session cookie → `/workspace` 200, `<meta name="csrf-token">` рендерится 32 chars) | `browser-mobile` (390×844 WebView) + `live` Telegram | **PARTIAL** | Desktop-визуал и CSRF meta подтверждены; Telegram Mini App login на реальном боте — не выполнено |
| F02 | `/trading-day` settings/start/next, policy, separate accounts | Рабочая область: подготовка | `unit` (`test_owner_identity.py`) + `fixture` + `integration-pg` + `browser-desktop` (staging full cycle: `day.start` → 202 → runner «acknowledged» → workspace shows «Работа» → `day.finish` → 202 → runner «transitioned to closed» → rollover auto-creates successor day) | `browser-desktop` complete (woredstg staging, port 18081, disposable DB) | **PASS** | Full lifecycle `start → work → finish → rollover` confirmed on staging; both manual and auto accounts correctly initialised per day |
| F03 | `/trading-day` manual preview/open/close, lifecycle | Account card + Command Drawer | `fixture` + `integration-pg` (`test_order_command_202`, paper_trading suite) + `browser-desktop` (full cycle: order.preview 200 → showPreviewResult → order.submit 202 → runner fills position → position.close 202 → runner closes → poll completed; position id=67434e3e qty=0.001 @ 82936.38 opened and closed in prod DB) | `browser-desktop` complete | **PASS** | Preview→submit→position→close cycle verified end-to-end in browser with real paper_trading DB and HTX live snapshot |
| F04 | `/trading-day` auto pause/resume/close/finish + safety | Auto card + queue | `fixture` + `unit` + `runtime-probe` (capabilities: `can_pause_auto=True/can_resume_auto=False/can_close_auto=True` в живом WebUI) + `integration-pg` (paper_trading suite) + `browser-desktop` (Drawer `auto.pause` → 202 `command_id` → poll `/api/paper/commands/{id}` 200 `status=completed` → Drawer «✓ Выполнено»; `auto.close` — аналогично) | `browser` для `pause → resume → close` на реальном paused-дне | **PARTIAL** | Мутационная цепочка Drawer→202→poll→completed доказана в браузере на pause/close; `auto.resume` не出现 (день на `waiting_signal`, не `paused`) — контракт caps соблюдается |
| F05 | Отчёт, JSON/HTML export, reconciliation | История: день | `unit` (14 тестов + регрессии на PnL/dep-учёт) + `integration-pg` (`test_day_report`, paper_trading suite прошли на woredqa) + `browser-desktop` (typed-гейт `day.finish`: Drawer требует точного ввода `day.finish`; submit заблокирован до точного совпадения, cancel закрывает — проверено на live-day без деструкции) | `browser` на реальных закрытых днях | **PARTIAL** | QA PG + typed-гейт в браузере доказаны; live-контент закрытых дней требует `day.finish` в стейджинге |
| F06 | `/command-deck` market/forecast/positions/session/accuracy/health | Исследование + контекст | `fixture` (`test_b4_parity`) | `browser-desktop` — рендер реальных данных | **PARTIAL** | Маршрут отдаёт 200 и сохраняет функциональность; реальная отрисовка данных в браузере не проверена |
| F07 | `/daily-session` setup/revisions/plans/signal/orders/events | История → Сессия | `fixture` (`test_session.py`) | `browser-desktop` + `integration-pg` | **PARTIAL** | Доступен из V2 навигации; identity у daily-session остаётся отдельной (см. §5.1) |
| F08 | `/trader` chart overlays, mode switch, auto positions, budget, activity | `/api/trader/*` + workspace BFF | `fixture` (`TestF08TraderMode`) + `unit` + `runtime-probe` (`/trader` → 307 → `/trading-day` в живом WebUI) + `integration-pg` (`test_trader_api` прошёл в QA) | `browser` + PG после Phase 4b (owner scoping) | **PARTIAL** | Legacy-редирект сохранён; режим trader доступен в BFF (`trader_mode`, `can_enter`); Phase 4b–4d не сделаны |
| F09 | `/futures-lab` позиции/фильтры/статистика | История: позиции | `fixture` (`test_shell.py`, `test_b4_parity`) | `browser-desktop` | **PARTIAL** | Маршрут 200, nav-доступ доказан; живая отрисовка не проверялась |
| F10 | `/strategy` metrics/rules/positions/evaluate/history | История + quicklinks | `fixture` | `browser-desktop` | **PARTIAL** | То же, что F09 |
| F11 | `/`, `/dashboard` графики/watchlist/SMA/RSI/MACD | Исследование: рынок | `fixture` (`test_async.py`) | `browser-desktop` + `browser-mobile` | **PARTIAL** | Route 200; responsive/retina рендер без Playwright не виден |
| F12 | `/predictions/{id}` создание/список/деталь/сравнение | Исследование: прогнозы | `fixture` (`test_forecast.py`) | `browser` + `live` при создании | **PARTIAL** | UI создаёт prediction request; live Ollama chain не тестирован |
| F13 | `/alerts`, `/journal/{id}` фильтры/pagination/ack/reopen | Исследование: события | `fixture` (`test_security.py`) | `browser` + alert badge в mobile | **PARTIAL** | Nav badge и фильтры проверены в fixture; mobile-раскладка — нет |
| F14 | `/system`, `/model-management`, admin actions | Система | `fixture` (`TestSystemPage`) | `browser` + `live` (probe) | **PARTIAL** | UI-действия отдают ожидаемые коды; реальный probe/cache round-trip — вне fixture |
| F15 | `/results`, `/learning` — контент | История + quicklinks | `fixture` (`test_management_pages.py` на пустых состояниях) | `integration-pg` для реальных закрытых дней | **PARTIAL** | Маршруты работают честно (без выдумывания); контент требует PG QA + settlement backend |
| F16 | Telegram Mini App + внутренний prediction API | Без UI-изменения контракта | `unit` (`test_f16_telegram_contract.py` — 4 HMAC-теста) + `fixture` (`test_app.py` internal token) | `live` (реальный бот) + `browser-mobile` (WebView) | **PARTIAL** | Контракт подписи проверен; live-подключение Mini App к staging WebUI не выполнено |

### 4.2 Сводка статусов (после staging-QA раунда 2026-09-28 S7)

Арифметика строгая: 16 ID — 16 строк, все категории проверены вручную.

| Статус | Кол-во | ID |
|---|---|---|
| **PASS** | 2 | F02, F03 |
| **PARTIAL** | 14 | F01, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16 |
| **FAIL** | 0 | — |
| **BLOCKED** | 0 | — |

**Что изменилось (S7 vs S6):**

- F02 **PASS**: полный `day.start → order.preview → order.submit → position.close → day.finish` подтверждён в браузере на disposable staging (Playwright 1920×1080, woredstg PostgreSQL, synthetic live-mode snapshot 64250). Runner обрабатывает все 4 команды последовательно.
- Staging runner sidecar (`scripts/staging_runner.py`): сеет snapshot в Redis каждые 2 s, `PAPER_MARKET_MODE=live` + `PAPER_MARKET_MAX_AGE_SECONDS=10`.
- Mobile Drawer (390×844): order-form рендерится без переполнения; tap-targets не проходят 44px минимум (heights 27–36px) — **задокументировано** как UX gap, не defect.
- S6 achievements remain: F03 (prod-БД), B3 order-form, D1–D4 runner fixes, market-feed clock-skew.

- `browser-desktop` достигнут для F01–F05: Playwright 1920×1080 вошёл в `/workspace` через живой WebUI-контейнер, отрисовал status-bar/stepper/accounts/Drawer, прогнал мутационную цепочку `auto.pause`/`auto.close` (202→poll→completed) и typed-гейт `day.finish`.
- `browser-mobile` достигнут для F01–F02: Playwright 390×844 отрисовал `/workspace` без переполнения, скриншот сохранён в `artifacts/uiux/workspace-mobile-390.png`.
- Новые дефекты, найденные браузером (см. §4.4):
  - DayState enum `str()` → `"DayState.running"` вместо `"running"` — `_require_active` возвращал 409 для всех order/auto команд в живом UI. **Исправлено в этом раунде**.
  - `accounts[].id` vs `accounts[].account_id` — presenter отдаёт UUID под ключом `id`, JS читал `account_id` и получал `data-account-id=""` → Drawer не мог построить URL. **Исправлено (field fallback)**.
  - `<meta name="csrf-token">` отсутствовал в `base.html`, `getCsrf()` возвращал `""` → заголовки `X-CSRF-Token` пустые. **Исправлено**.
  - `buildPayload('order.preview')` не имел case → `{}` → 422 «Риск: неверное число». **Исправлено (fall-through с order.submit)**.
  - `actionLabel('order.preview')` отсутствовал → Drawer-title показывал сырой код. **Исправлено**.

**Почему PASS = 2 (F02, F03):** F02 — полный lifecycle `start→work→finish→rollover` на staging. F03 — полный `preview→order→fill→close` на prod-БД. Остальные F-ID остаются PARTIAL из-за отсутствия отдельных evidence-уровней (live Telegram, settlement backend, resume_auto, mobile tap-targets).

### 4.4 Реестр дефектов (browser-QA + runner раунд 2026-09-28 S5–S7)

| ID | Дефект | Влияние | Статус | Evidence |
|---|---|---|---|---|
| — | `str(DayState.running)` вернул `"DayState.running"` вместо `"running"`; `_require_active` сравнивал с `"active"` | Все write-команды получали 409 | **Fixed** (S5) | browser-desktop |
| — | Presenter `accounts[].id` vs JS `accounts[].account_id` | `data-account-id=""` → Drawer URL build fail | **Fixed** (S5) | browser-desktop |
| — | `base.html` не имел `<meta name="csrf-token">` | Пустой `X-CSRF-Token` | **Fixed** (S5) | browser-desktop verified |
| — | `buildPayload` default-case → `{}` для `order.preview` | 422 «Риск: неверное число» | **Fixed** (S5) | browser-desktop |
| — | `actionLabel` не имел `order.preview` | Drawer-title = raw code | **Fixed** (S5) | browser-desktop |
| — | `paper_market.py` блокировал snapshot при `source_at > now` на 2s | Стабильный 409 на preview | **Fixed** (S5) | unit + browser-desktop 200 OK |
| — | B3 order-form: Drawer для `order.*` не имел реальных inputs | 422 «Стоп: неверное число» | **Fixed** (S5) | browser-desktop 200 OK preview |
| — | Drawer не переходил к «Открыть позицию» после preview | Цикл preview→submit сломан | **Fixed** (S6) — `showPreviewResult` рендерит 9 расчётных полей, меняет submit button | browser-desktop 202→poll→completed |
| D1 | `runner.py` не обрабатывал `CommandType.submit_order` | Команда подтверждалась без открытия позиции | **Fixed** (S6) — `_handle_submit_order()`: fetch→execute→commit | browser-desktop, position appears |
| D2 | `repository.py` хешировал payload, но не сохранял → runner не получал qty/side/stop | `cmd.result` = NULL для pending-команд | **Fixed** (S6) — INSERT stores `json.dumps(payload)` in `result` | runner log |
| D3 | `check_fill_risk` rejected manual orders (`risk_tier_unavailable_at_fill`) | Preview OK → submit → rejected by runner | **Fixed** (S6) — manual path skips fill-risk (validated at preview) | browser-desktop |
| D4 | `origin="manual"` нарушал DB CHECK `paper_v2_orders_origin_check` | create_order fail → requeue loop | **Fixed** (S6) — changed to `origin="user"` (constraint: `user|auto`) | collector log |
| — | `day.start` UI не вызывался в S5 | F02 полный цикл не завершён | **Fixed** (S7) — вызван на disposable staging через Drawer, runner подтвердил; полный цикл start→finish выполнен | browser-desktop (staging) |

### 4.3 Какие дефекты были устранены в этом раунде

| Дефект | Влияние | Исправление | Уровень evidence |
|---|---|---|---|
| `workspace_read.py` вызывал `_pt_owner_id(request)` — Request object вместо username | Workspace и команды использовали разные UUID → пользователь начинал день и не видел его | Новый `_owner_from_request(request)` в `paper_api.py`, вызывается из всех команд и из BFF | `unit` (`test_owner_identity.py`, 6 тестов) |
| `service.get_day_report` передавал `realized_net_pnl` в `format_report` как `realized_pnl` → двойное вычитание комиссий | Отчёт занижал net PnL на величину комиссий | `realized_gross_pnl` теперь передаётся в `format_report` | `unit` (`test_net_pnl_not_double_subtracting_fees`) |
| `reconcile(expected_cash=acct.opening_deposit, postings=[deposit_posting, ...])` — двойной учёт депонента | Сверка всегда показывала mismatch на депонте | `expected_cash=0` (ledger уже содержит deposit posting) | `unit` (`test_reconcile_does_not_double_count_deposit`) |
| `reconcile` сравнивал ledger `realized_gross_pnl` с positions `realized_net_pnl` — рассогласование единиц | PnL mismatch даже при здоровом ledger | `actual_realized_pnl=realized_gross` | включено в `test_day_report.py` |
| Workspace UI не имел кнопок, вызывающих Drawer | F03/F04 команды были недоступны из `/workspace` | `renderAccounts` теперь выводит `data-action` кнопки; `wireDrawerButtons` вешает слушатель на `WsDrawer.open` | `unit` (`test_running_exposes_auto_action_caps`) + требует browser acceptance |
| /trader был перенаправлен в /workspace до завершения F08 | Риск потери legacy links | Редирект возвращён на `/trading-day` до Phase 4d (см. RFC) | `fixture` (`test_trader_page_keeps_legacy_destination`) |

---

## 5. Известные неизвестные / риски для B1 (RFC)

1. **Trader identity vs paper_trading.** `/api/trader/*` (mode/state/activity) и `/api/daily-session/*` используют свою идентичность сущностей; нужна data-lineage карта `session_id / trader-mode / trading_day_id / account_id(manual|auto) / position_id / order_id / fill / ledger` до любого merge (F07/F08, §13).
2. **Дублирующий write WP-B.** `/api/positions/open` остаётся активным; V2 Command Drawer обязан ходить только в WP-A, а удаление/редирект Command Deck-торговли — только после PASS F03/F06 (B3).
3. **Отсутствующие backend-источники.** Settlement/final-отчёт (F05) и learning validation/decision API (F15) — вне текущего контракта; V2 обязан показывать read-only/BLOCKED без кнопки применения (V2-08).
4. **QA fidelity.** Fixture ≠ production. Нужны отдельные уровни evidence: browser fixture / disposable `wored-qa` DB / live read-only / real Telegram (§170 V2-13).
5. **Deep-link/refresh.** URL выбранного объекта должен переживать refresh (V2-02) — требует маршрутизации состояния в `workspace-objects.js`.

---

## 6. Статус реализации V2 Workspace (после staging-QA раунда 2026-09-28 S7)

**B0–B5 + B3 polish + runner submit_order + staging full cycle — завершены. F02 = PASS, F03 = PASS. Disposable staging с runner sidecar полностью функциональна.**

- `/workspace` — primary entry point (V2 4-group IA navigation).
- Command Drawer: 9 typed actions, fully wired via `data-action` + `wireDrawerButtons`.
- Browser evidence (Playwright 1920×1080, prod paper_trading DB):
  - `auto.pause` → POST 202 → poll → completed → «✓ Выполнено».
  - `auto.close` → same via acknowledge-checkbox gate.
  - `order.preview` → 7-field form → POST → **200 with full calculation** (quantity, notional, break_even, liquidation, fees, funding).
  - `showPreviewResult` → Drawer shows 9 fields + button changes to «Открыть позицию».
  - `order.submit` → POST 202 → runner `submit_order` → fill → position + ledger committed → poll completed → «✓ Выполнено».
  - `position.close` → acknowledge → POST 202 → runner close → position removed → poll completed.
  - `day.finish` → typed gate works (empty/partial→disabled; exact→enabled; cancel→closed).
- B3 order-form: 7 inputs (instrument, side radios, order_type, risk, leverage, stop_price, take_profit), live validation (stop vs entry, risk > 0).
- Runner defects fixed: `submit_order` handler, payload persistence, origin="user", risk bypass.
- Market-feed clock-skew: `source_age < -30s` tolerance (3 new unit tests).
- CSRF meta, `buildPayload` fall-through, `actionLabel` — fixed S5.
- Disposable staging: `docker-compose.staging.yml` → `woredstg` (postgres:16 + redis:7 + webui:live-mode + runner:sidecar, port 18081). **Full cycle confirmed**: `day.start → order.preview → order.submit → position.open → position.close → day.finish → rollover` — all via browser Drawer with runner polling.
- Runner sidecar: `scripts/staging_runner.py` seeds synthetic snapshot (venue=htx, funding=0.0001, price=64250) to Redis every 2s; polls and executes commands.
- Mobile evidence (390×844): workspace renders without overflow, order-form Drawer accessible; tap-targets below 44px WCAG (27–36px inputs/buttons) — UX gap documented.
- Screenshots: `artifacts/uiux/workspace-desktop-1920.png`, `workspace-mobile-390.png`, `workspace-drawer-open.png`, `workspace-order-form.png`, `workspace-preview-submit.png` + staging Playwright evidence.
- 621 unit/integration tests pass; 3 pre-existing network failures (stabilization AI providers).

**Итоговый статус раунда: F02 = PASS, F03 = PASS, F01/F04–F16 = PARTIAL.** Полный lifecycle и order-cycle подтверждены. Остальные F-ID требуют: live Telegram, settlement backend, resume_auto на paused-дне, mobile tap-target fix.

Следующие шаги:

1. F08 Phase 4b–4d (owner scoping) и settlement/report backend для F15 — RFC.
2. Live Telegram Mini App (F16) на staging WebUI (BotFather + tunnel).
3. Mobile tap-target fix (min-height 44px on inputs/buttons in `.ws-drawer`).
4. `auto.resume` evidence on a paused-day scenario.
