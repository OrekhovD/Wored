# WEBUI-WORKSPACE-V2-RFC

Фаза **B1** из `docs/QODER-WEBUI-WORKSPACE-V2-TZ-20260926.md`.
Дата: 2026-09-27.
Статус: утверждён к реализации B2.

---

## 1. Цель и границы

Построить `/workspace` — единую рабочую область WORED WebUI, где основная единица — **объект с состоянием, причиной и допустимым действием**. Не переименование навигации, не перенос карточек, не пустые страницы.

Границы:
- **Внутри WebUI**: read-only BFF presenter, templates, JS, styled components.
- **Вне WebUI** (не менять в рамках V2): `paper_trading/`, migrations, chatbot, collector, .env, Compose.
- Соблюдать: AGENTS.md security rules, WebUI design guardrails, existing palette, chart containers, app.js preserve.

---

## 2. Domain model — объектная схема

### 2.1 object_ref (стабильный идентификатор)

```python
@dataclass(frozen=True)
class ObjectRef:
    kind: str       # "day" | "account" | "order" | "position" | "fill" | "report" | "forecast" | "alert" | "journal_entry" | "learning_candidate" | "command"
    id: str         # domain primary key (UUID string or composite)
    day_id: Optional[str]
    account_id: Optional[str]   # "manual" | "auto" | UUID
    source: str    # "paper_trading" | "daily_session" | "trader" | "forecast_engine" | "collector" | "learning"
```

Все объекты в workspace ссылаются через `object_ref`. Никаких второго P&L и локальных копий — данные приходят из того же идентификатора.

### 2.2 Day (торговый день)

| Поле | Тип | Источник |
|---|---|---|
| `day_id` | UUID | `paper_trading.contracts.Day.id` |
| `state` | DayState enum | domain |
| `start_at` | datetime (UTC) | domain |
| `end_at` | datetime (UTC) | domain (from settings) |
| `owner_ref` | str | `owner_id_from_webui()` |
| `version` | int | optimistic concurrency |
| `accounts[]` | AccountRef[] | domain `get_current_state` |
| `next_action` | "trade" \| "start" \| "finish" \| "report" | presenter-derived |

### 2.3 Account (счёт)

| Поле | Тип |
|---|---|
| `account_id` | UUID |
| `kind` | `"manual"` \| `"auto"` |
| `currency` | "USDT" |
| `cash` | Decimal string |
| `equity` | Decimal string (cash + unrealized) |
| `available_margin` | Decimal string |
| `open_positions` | int |
| `realized_net` | Decimal string |
| `unrealized` | Decimal string |
| `total_costs` | Decimal string (fees + funding) |
| `loss_budget` | Decimal string |

### 2.4 Position, Order, Command — из `paper_trading.contracts`

Позиция: `position_id`, `side (long/short)`, `qty`, `avg_entry`, `stop`, `target`, `origin (manual/auto)`.
Order: `order_id`, `state (pending/submitted/filled/...)`, `side`, `qty`, `price`, `account_id`.
Command (async action result): `command_id`, `status (accepted/pending/completed/rejected/unknown)`, `action_code`, `day_id`, `account_id`.

### 2.5 AutomationState

Состояние автомата (separate from DayState):
```
initializing → armed → waiting_signal → position_open → cooldown → ...
                                    ↓                        ↓
                                  paused                  risk_blocked
                                                       data_stale
                                                       engine_error
```

---

## 3. State machine (Day lifecycle)

Из `paper_trading.contracts.DayState`:

```
idle → starting → running → closing → settlement_pending → closed
                  ↘ recovery_required (from any active)
```

Transitions:
| From | To | Trigger | Domain endpoint |
|---|---|---|---|
| idle | starting | operator `POST start` | `_pt_start_day()` |
| starting | running | domain committed | implicit |
| running | closing | operator `POST finish` | `_pt_finish_day()` |
| closing | settlement_pending | auto: no open positions | domain |
| settlement_pending | closed | ledger reconciliation | domain |
| running | recovery_required | engine failure | domain |
| recovery_required | running | operator resumes | domain |

Presenter maps domain DayState → workspace stages:
- `idle`/`starting` → **Подготовка** (before-start UI)
- `running` → **Работа** (active day)
- `closing`/`settlement_pending` → **Завершение**
- `closed` → **Итоги**
- `recovery_required` → **Blocked** (attention queue entry)

---

## 4. BFF endpoint — `GET /api/workspace/state`

Read-only presenter поверх `paper_trading.adapter` + existing services.

**Path:** `GET /api/workspace/state?day_id=<optional>`
**Auth:** page session (owner-scoped).
**Response schema:**

```json
{
  "schema_version": 2,
  "as_of": "2026-09-26T08:30:00Z",
  "owner_ref": "owner-uuid",
  "day": { "object_ref": {...}, "state": "running", "local_date": "2026-09-26", "end_at": "21:00", "timezone": "Asia/Bangkok" },
  "stage": "work",
  "accounts": [{ "object_ref": {...}, "kind": "manual", "equity": "1234.56", ... }, { ... }],
  "market": { "bid": "...", "mark": "...", "ask": "...", "quality": "live|stale|unavailable", "source_at": "...", "stale_after": 18 },
  "objects": [{ "object_ref": {...}, "type": "position", "summary": "...", "status": "open" }],
  "attention": [ /* §5 below */ ],
  "capabilities": { "can_start": false, "can_trade": true, "can_finish": true, "reason_code": null },
  "sources": [{ "source_name": "paper_trading", "source_at": "...", "observed_at": "...", "status": "ok|stale|unavailable" }],
  "commands_pending": [{ "command_id": "...", "action_code": "...", "status": "pending" }]
}
```

Rules:
- Presenter **не пишет** торговые таблицы.
- Отсутствующая зависимость → `"status": "unavailable"` + `reason_code`; HTTP 200 с пустым объектом ≠ "счёт 0".
- Domain failure → fail-closed: `capabilities.can_start=false`, `can_trade=false`, reason in `attention[]`.

---

## 5. Attention queue (детерминированная, не LLM)

### 5.1 Формирование

Серверный presenter строит очередь из **фактических состояний**:

```python
@dataclass
class AttentionItem:
    severity: Literal["critical","warning","action_required","info"]
    reason_code: str
    as_of: str
    source: str
    object_ref: Optional[ObjectRef]
    primary_action: Optional[str]   # action_code from registry, or null
    next_check_at: Optional[str]
    message: str                     # deterministic text template
```

Priority order (§2.2 TZ):
1. Unfinished close / protective execution (critical)
2. Market data loss / risk block (warning → critical)
3. User command pending (action_required)
4. Report/candidate ready for owner (action_required)
5. Normal observation (info — only if all above clear)

Dedup: group by `day_id + object_type + object_id + reason_code`. Polling does not create duplicates.

### 5.2 Примеры

```json
[
  {"severity":"warning","reason_code":"market_stale","source":"paper_trading","primary_action":"refresh_market","message":"Рыночный mark устарел 18 с; новые входы заблокированы"},
  {"severity":"info","reason_code":"auto_waiting","source":"paper_trading","message":"Автомат: waiting_signal; входов сегодня 0","next_check_at":"2026-09-26T09:05:00Z"}
]
```

Text "всё хорошо" **запрещён**, если `/healthz` — единственная проверка.

---

## 6. Typed command registry

### 6.1 Structure

```python
@dataclass(frozen=True)
class CommandDef:
    action_code: str         # e.g. "day.start", "order.manual_submit", "auto.pause"
    endpoint: str            # server path, e.g. "/api/trading-day/start"
    method: str              # "POST"
    allowed_states: list[str] # ["idle"] for start, ["running"] for order
    confirmation_level: Literal["none","acknowledge","typed"]
    result_lookup: str       # "/api/paper/commands/{command_id}"
    scope_fields: list[str]  # ["day_id","account_id","owner"]
```

### 6.2 Registry entries

| action_code | endpoint | allowed_states | confirm |
|---|---|---|---|
| `settings.update` | `/api/trading-day/settings` | idle | none |
| `day.start` | `/api/trading-day/start` | idle | acknowledge |
| `order.preview` | `/api/paper/accounts/{id}/orders/preview` | running | none |
| `order.submit` | `/api/paper/accounts/{id}/orders` | running | typed |
| `position.close` | `/api/paper/positions/{id}/actions` | running | acknowledge |
| `auto.pause` | `/api/trading-day/{day_id}/automation` | running | none |
| `auto.resume` | `/api/trading-day/{day_id}/automation` | running | none |
| `auto.close` | `/api/trading-day/{day_id}/automation` | running | acknowledge |
| `day.finish` | `/api/trading-day/{day_id}/finish` | running | typed |
| `day.next` | `/api/trading-day/next` | closed | none |

### 6.3 Execution flow

```
User clicks action → Command Drawer opens
  → context: show object, day, account, old→new state, risk/expenses
  → validation: preview / allowed_states check / owner / version
  → POST with Idempotency-Key, payload hash, CSRF token
  → 202 accepted → poll GET result_lookup until terminal
  → result: completed/rejected/unknown
```

Lost response → search by same key/id, **не** repeat POST. Frontend never computes official balance.

---

## 7. Permission model

| Level | Applies to | Check |
|---|---|---|
| authenticated | all workspace pages | session |
| owner | commands, account data | server `owner_id_from_webui` matches day/account |
| CSRF | all POST | header/session token |
| version precondition | day-scoped mutations | `expected_state` in domain; stale → 409 |
| risk validation | order.submit, auto.* | server-side; reject → 4xx + reason in drawer |
| admin role | clear alerts, snapshot, probe | session + can_admin |

Нефинансовые admin actions: отдельная permission policy, явный эффект, число affected records. Typed allowlist (no universal `action` param).

---

## 8. Responsive layout (zones)

### Desktop 1440×900

| Zone | Width | Content |
|---|---|---|
| Status bar | full width | Day/stage, market/feed age, both accounts equity, pending commands count, auth pill |
| Route of day (left) | 180px | Pipeline: Подготовка → Работа → Завершение → Итоги → Обучение (compact stepper) |
| Context (center) | flex | Main workspace object per current stage (accounts/positions/ticket/report/learning) |
| Attention queue (right) | 320px | Ordered problems list + search + filters |

### Mobile 390×844

Status bar → 2 compact lines. Attention queue follows status bar. Context = main scroll. Route of day = collapsible stepper. Command Drawer = full-screen with Back.

### Breakpoints
`320×800`, `390×844`, `844×390` (landscape), `1280×800`, `1440×900`.

Both account cards visible without horizontal scroll on mobile. Command Drawer respects safe areas + keyboard. No overlap of Confirm/Stop.

---

## 9. Secondary spaces (navigation IA)

| Group | Contains | TZ ref |
|---|---|---|
| Рабочая область | `/workspace` (default), day objects, accounts, queue, Command Drawer | §2.1 |
| Исследование | `/` (market), `/predictions`, `/alerts`, `/journal`, charts | §2.4, F11–F13 |
| История | `/results`, `/results/{id}`, `/futures-lab`, `/strategy`, learning objects | F09,F10,F15 |
| Система | `/system`, `/model-management`, admin actions, health/readiness, legacy diagnostics | F14 |

Search/filter by: ID, date, account, symbol, object type. Deep link restores selected object after refresh.

---

## 10. Data lineage (mapping → canonical)

From B0 findings (§3.5 of PARITY):

```
paper_trading domain (canonical for V2):
  DayState (7 states), AccountKind (manual/auto), Order, Position, Fill, Ledger
  → workspace reads via GET /api/workspace/state, writes via command registry

daily-session (legacy, separate identity):
  session_id, trading_sessions table, revision/start/plan/signal
  → F07: accessible via "Инструменты → Сессия", NOT merged into day_id

trader_api (legacy, separate identity):
  /api/trader/{state,candles,forecast,positions,activity,mode,stream}
  → F08: accessible via "Инструменты → Trader", NOT merged into trading_day_id

Command Deck legacy sim (WP-B):
  /api/positions/open, /api/positions/{id}/close, /api/sim-positions
  → Deprecated: V2 routes only through WP-A (paper_trading)
  → Removal of WP-B action buttons ONLY after F03/F06 PASS (B3)
```

---

## 11. File structure (confirmed)

```text
webui/
  workspace_read.py              # Read model + owner scope + provenance
  workspace_presenters.py        # Attention priority, display model, NO financial arithmetic
  app.py                         # route registration (workspace + existing)
  paper_api.py                   # domain bridge (existing, extended for workspace)
  trader_api.py                  # preserved for F08
  templates/
    base.html                    # shell (incremental extend)
    workspace.html               # main V2 workspace
    workspace_object.html        # contextual inspector (object detail)
    partials/
      workspace_status_bar.html
      workspace_route_day.html
      workspace_attention.html
      workspace_account_card.html
      workspace_command_drawer.html
      workspace_search.html
  static/ui/
    workspace-state.js           # polling / SSE, version tracking, stale handling
    workspace-actions.js         # typed Command Drawer controller
    workspace-objects.js         # inspector, links, filters, URL routing
    workspace-chart.js           # reuses Lightweight Charts, real axes/grid
tests/
  test_workspace_read.py
  test_workspace_presenters.py
  test_workspace_actions.py
  ui/
    fixture_workspace.py         # BFF mock responses for fixture
    test_workspace.py            # browser assertions per V2-01…V2-14
docs/
  WEBUI-WORKSPACE-V2-RFC.md      (this file)
  WEBUI-FUNCTION-PARITY.md       (B0, updated per APPLY)
  WEBUI-WORKSPACE-V2-ACCEPTANCE.md  (B5)
```

---

## 12. Wireflow (V2-01…V2-14 → phases)

| Scenario | Key UI flow | Phase target |
|---|---|---|
| V2-01 Orientation | /workspace → status bar → both accounts → auto reason → next action | B2 |
| V2-02 Object search | filter by ID → open position → deep link → refresh restores | B2 |
| V2-03 Account independence | manual command → auto unaffected → DB/UI verify | B3 |
| V2-04 Manual lifecycle | preview→submit→202→fill→position→partial close→ledger→net | B3 |
| V2-05 No false success | 202/pending/stale/quota/fail → never "completed"/"final" | B2+B3 |
| V2-06 Auto + intervention | pause/resume/close-auto/reduce-only + plan diagnostics | B3 |
| V2-07 Close/history | both accounts reconciled → final after settlement → export | B4 |
| V2-08 Learning | candidate pipeline, read-only if backend not ready | B4 (BLOCKED until backend) |
| V2-09 Analytics | charts/periods/series/forecast/alerts/journal/strategy/models preserved | B4 |
| V2-10 URL compat | every old URL + deep link works; redirect only after PASS | B4→B5 |
| V2-11 Mobile | 5 breakpoints, no overflow, both cards, drawer, charts | B2 responsive |
| V2-12 Security | auth/CSRF/owner/replay/injection/fail-closed | B3 |
| V2-13 QA fidelity | fixture/disposable/live/Telegram separate evidence levels | B5 |
| V2-14 Performance | 500 objects/100 events interactive without loading all | B2 |

---

## 13. Performance thresholds (to validate B2)

| Metric | Target | Measurement |
|---|---|---|
| Workspace initial load | <300ms server (fixture) | `GET /api/workspace/state` TTFB |
| Attention items with 500 objects | <50ms presenter | unit test timer |
| Poll cycle (active day) | 5s interval, <10KB payload | network log |
| Chart data (200 candles) | <100ms render | browser Performance API |
| No full DB load | server paginates objects, max 50/page | code review + integration test |

---

## 14. Open questions (resolve during B2 implementation)

1. **SSE vs polling** for status bar + commands: start with polling (simpler, proven), evaluate SSE for `/api/workspace/state/events` only after B3 baseline.
2. **URL scheme for object deep-link**: `/workspace?obj=<kind>/<id>` vs `/workspace/position/{id}` — prefer query param (single route, no 50 new routes).
3. **`/trader` redirect**: NOT accepted until B4 proves F08 parity; workspace links `/trader` → "Инструменты → Trader" (legacy route stays 200).
4. **Feature flag**: workspace accessible at `/workspace` from B2; NOT promoted to `/` until B5 acceptance.

---

## 15. Constraints carried from TZ (normative)

- AGENTS.md security rules + WebUI design guardrails.
- `docs/TRADING-SIMULATOR-AGENT-CONTRACT.md` — domain commands.
- `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/ACCEPTANCE.md` — AC-01…AC-28 (separate from V2).
- Paper_trading `DayState`, `AccountKind`, `AutomationState` — no new states without migration.
- Preserve: `app.js`, existing pages, chart containers, palette, routes `/`, `/alerts`, `/predictions`, `/journal`.
- No `docker compose down -v`, `.env` change, collector/chatbot change, WebUI rewrite.
- Financial actions: server-side owner + CSRF + version + risk validation. Frontend never computes official balance.
