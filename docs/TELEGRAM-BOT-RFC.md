# WORED Telegram Bot V1 — RFC / P0 Discovery Inventory

**Дата съёмки:** 2026-10-08T09:23:57Z (UTC)
**Commit:** `0f2359a`, branch `main`, синхронен с `origin/main`
**Документ:** выходной gate фазы **P0** по §8 ТЗ `docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`
**Метод:** только read-only — чтение исходников, `grep`, SQL-`select`, HTTP-пробы без авторизации, `docker compose exec` с выводом имён переменных (значения не печатались), `redis-cli pubsub`. Runtime не изменялся: ни один сервис не останавливался, ни один `.env`/compose/обработчик не правился, ни один файл `chatbot/`, `collector/`, `webui/`, `paper_trading/` не тронут.
**Граница P0:** этот документ фиксирует факты и неизвестное. Реализация начинается только после решений куратора (§13).

---

## 1. Резюме P0

1. Backend-домен для бота **существует и доступен**: V3 API рынка, прогноза, плана, сессии, позиций, предложений и paper-ордеров с preview (§7). Зависимость от MC-V3 **не** является блокирующей для большинства функций — вопреки оговорке §1 ТЗ, `SimulationPlanV1`, `plan_hash` и state machine реализованы в коде.
2. В боте **нет слоя авторизации** вообще: ни middleware, ни allowlist, ни проверки private-chat, ни role-scope (§5). Любой, кому известен токен бота, может вызывать мутирующие callback-команды. Это не «слабая реализация», а отсутствие: deny-by-default отсутствует как механизм.
3. **Три параллельных домена «сессии» с реальными данными** плюс четвёртая legacy-таблица симуляции (§6). Один пользовательский сценарий «запустить сессию» уже имеет **две разные реализации**, выбираемые try/except (§6.1) — прямое нарушение §2 ТЗ («никаких двух разных реализаций одного действия»).
4. Два фактических **мёртвых пути доставки** обнаружены и подтверждены живой проверкой: уведомления об алертах в Telegram не работают (Redis-канал имеет 0 подписчиков), а `_sim_ai_monitor` работает и **закрывает позиции без уведомления** (§10, D-01/D-02).
5. Все девять bot-only таблиц (§6 ТЗ) **отсутствуют**; FSM/persistent wizard **отсутствует**; `update_id` dedup **отсутствует**; при старте включён `drop_pending_updates=True` (§4, §10 D-05).
6. Из 12 новых ключей `BOT_*` (§7 ТЗ) в конфигурации **нет ни одного**; при этом один и тот же смысл живёт в нескольких алиасах, а `TELEGRAM_ADMIN_ID(S)` отсутствует в обоих env-файлах, которые реально читают бот и webui (§9).
7. Документ `docs/TELEGRAM_BOT_USAGE.md` (28.06.2026) **не соответствует коду**: обещает `/models`, которого нет в роутерах, описывает FSM-состояния, которых в коде нет, публикует ID администратора, и помечает мутирующие команды как доступные «Все» (§8).
8. Команда верификации из §10 самого ТЗ **не выполняется как написана** (коллизия пакетов `tests`) — исправленная форма приведена в §14.

---

## 2. Транспорт и composition root

| Факт | Доказательство |
|---|---|
| Единственный transport — polling; webhook в коде нет | `chatbot/main.py:217` `dp.start_polling(bot, drop_pending_updates=True)` |
| Токен читается из двух алиасов | `chatbot/main.py:150` `os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")`; фактически задан только `TELEGRAM_BOT_TOKEN` (healthcheck `docker-compose.yml:93` требует именно его, контейнер healthy) |
| Parse mode глобально HTML | `chatbot/main.py:155` |
| 15 роутеров подключаются безусловно | `chatbot/main.py:158-171` (start, menu, callbacks, market, alerts, analytics, portfolio, predictions, settings, trader, admin, pipeline, news, plans) |
| 16-й роутер условный | `chatbot/main.py:172-174` `hermes_admin` при `HERMES_CHATBOT_GATEWAY_ENABLED` |
| Catch-all AI-роутер подключается последним | `chatbot/main.py:181` |
| Global update-логгер печатает JSON апдейта | `chatbot/main.py:177-179` `update.model_dump_json()[:200]` — обрезка есть, но это всё равно содержимое приватного сообщения/ID в логе; ТЗ §3 это запрещает |
| При старте создаются таблицы | `chatbot/main.py:183-196` `ensure_ext_tables()`, `ensure_pipeline_tables()` («8 tables» в логе) |
| Три фоновые задачи | `chatbot/main.py:199` `alert_listener`, `:200` `_sim_ai_monitor`, `:201` `_poll_watchdog` |
| Watchdog при 2 таймаутах делает `os._exit(1)` | `chatbot/main.py:84-88`; восстановление за счёт `restart: unless-stopped` (`docker-compose.yml:82`) |
| Menu Button указывает на Mini App с localhost-fallback | `chatbot/main.py:205` `TG_MINIAPP_URL or WEBUI_URL or "http://localhost:8081/dashboard"` |

Живой лог-след рестартов в текущем сеансе наблюдения (не мои действия): `16:01:05` `Failed to set WORED chat menu button: ClientConnectorDNSError ... api.telegram.org`, `16:02:04` `System Menu Button configured`, `16:02:13` `Sim AI monitor started (3min interval)`.

---

## 3. Карта команд: текущая → целевая

### 3.1 Зарегистрированные slash-команды (факт)

| Команда | Где | Что делает (1 строка) | Категория по ТЗ |
|---|---|---|---|
| `/start` | `handlers/start.py:41` | Клавиатура Command Deck + главная web_app-кнопка | цель (переписать карточку «Сейчас») |
| `/market` | `handlers/market.py:98` | Обзор рынка из Redis-тикеров | цель (нужен `as_of`/quality) |
| `/alerts` | `handlers/alerts.py:42` | Последние алерты | цель |
| `/analytics`, `/analysis` | `handlers/analytics.py:188,189` | AI-анализ по монете | вне целевого ядра → в «Ещё» |
| `/portfolio` | `handlers/portfolio.py:12` | Позиции и история **legacy `sim_positions`** | redirect → `Позиции` |
| `/predictions`, `/forecast` | `handlers/predictions.py:240,241` | Forecast Lab (старый контур) | redirect → `Прогноз` (целевой `/forecast` уже занят старым) |
| `/settings` | `handlers/settings.py:68` | Системные настройки/модели | redirect → `Настройки` |
| `/trader` | `handlers/trader.py:64` | Меню «криптотрейдера» | redirect → `Сессия`/`Ещё` |
| `/admin` | `handlers/admin.py:33` | Админ-панель, inline | цель, но без scope-фильтра (см. §5) |
| `/session` | `handlers/pipeline.py:122` | Статус дневной сессии | цель (нужна state machine из домена) |
| `/plan` | `handlers/pipeline.py:129` | Активный план сессии | redirect → `Сессия → План` |
| `/plans`, `/diff`, `/accuracy` | `handlers/plans.py:19,109,159` | История ревизий, diff, точность | redirect → `Итоги`/`Обучение` |
| `/news` | `handlers/news.py:13` | Новости | вне ядра |
| hermes-команды | `handlers/hermes_admin.py:20` | Единственный фильтр по ID во всём боте | не продукт |

### 3.2 Целевые команды ТЗ, которых нет в коде

`/now`, `/positions`, `/report`, `/help`, `/cancel` — **не обнаружено** ни одного registration. `/ask`, `/mode`, `/models`, `/usage`, `/quota`, `/trace` (ТЗ §U10) — **не обнаружено**; при этом `/models` **обещан документацией** (`docs/TELEGRAM_BOT_USAGE.md:38`) → расхождение док↔код, D-08.

### 3.3 Callback-инвентарь (то, что реально мутирует состояние)

| Payload | Где объявлен | Мутация | Защита |
|---|---|---|---|
| `pipeline_status`, `pipeline_positions`, `pipeline_balance`, `pipeline_plan`, `pipeline_review` | `handlers/pipeline.py:39-52` | только чтение | нет автора/nonce/expiry |
| `pipeline_close_all` | `handlers/pipeline.py:48` | **закрытие позиций** | нет ни автора, ни version, ни confirmation-контекста (ТЗ §3 запрещает ровно это) |
| `prediction_run:<symbol>:<hours>` | `handlers/predictions.py:73,302` | создаёт forecast-запрос (стоит AI-квоты) | идемпотентность `f"tg-{call.id}"` (`:312`) — привязана к id апдейта, не к operation |
| `prediction_symbol:…`, `prediction_menu`, `prediction_latest:…`, `prediction_status:<request_id>` | `handlers/predictions.py:60,91,246-284` | чтение | нет |
| `menu_back`, `refresh_market`, `back_to_market`, `analytics:*`, `second_opinion:*` | `handlers/callbacks.py:27-78` | чтение / AI-вызов | нет |
| текстовые триггеры сессии («старт сессии 200 aggressive», «остановить сессию», «пауза», «продолжить», «позиции», «баланс») | `handlers/pipeline.py:141-188`, `docs/TELEGRAM_BOT_USAGE.md:47-70` | **старт/стоп/пауза** | парсинг свободного текста, без прав и без preview |

Итог по §3 ТЗ: **opaque action ID, `(actor, chat, message, resource_id, resource_version, expiry, nonce)` — не реализованы**; повторная проверка автора и текущего состояния в обработчике отсутствует.

---

## 4. Состояние диалогов, wizard’ы, dedup

| Требование ТЗ | Факт |
|---|---|
| Persisted wizard/draft 24 ч с `draft_id`, version, expiry (§U04) | **не обнаружено**: ни `FSMStrategy`, ни `StateFilter`, ни `MemoryStorage`, ни `RedisStorage` в `chatbot/**` (grep по всем маркерам — 0 совпадений) |
| `update_id` dedup, устойчивый к рестарту (§6) | **не обнаружено**; `bot_update_inbox` отсутствует |
| Восстановление результата по `command_id` (§5) | В боте **не обнаружено**; на домене идемпотентность есть — `paper_v2_commands` = 117 строк, `Idempotency-Key` в `chatbot/integrations/webui_client.py:107` |
| `drop_pending_updates` без replay-политики (§6 запрет) | **включён**: `chatbot/main.py:217`; alternate retention-политики в коде нет → апдейты, пришедшие во время простоя, теряются без следа |
| Быстрый `answerCallbackQuery` на любой callback | частично: `answer()` вызывается в отдельных обработчиках (напр. `handlers/pipeline.py:220,227`), не гарантированно на всех |

---

## 5. Идентичность и права

### 5.1 Бот (Telegram-сторона)

* **Middleware/allowlist/deny-by-default — отсутствуют.** `dp` собирается без outer-guard кроме debug-логгера; ни один роутер не фильтрует по пользователю, кроме `handlers/hermes_admin.py:20` (`F.from_user.id == ADMIN_TELEGRAM_ID`).
* Идентичность = `message.from_user.id`, передаётся в сервисы как `user_id` напрямую: `handlers/pipeline.py:125,132,142,155,162,169,176,185,187`, `handlers/plans.py:22,112,162`, `handlers/predictions.py:309` (`requested_by = f"telegram:{call.from_user.id}"`).
* Проверки типа чата нет: групповые update ничем не отделены от личных (не обнаружено `ChatType`-фильтров).
* Rate limit на actor/action — **не обнаружено**.
* `/admin` (`handlers/admin.py:33`) зарегистрирован **без scope-фильтра**: доступность по факту определяется только тем, знает ли пользователь команду.

### 5.2 Сервер (WebUI/paper API)

* Principal резолвится на сервере: `webui/app.py:271-281` — сначала cookie-session, затем заголовок `X-Telegram-Init-Data`; `webui/app.py:284-292` берёт allowlist из `TELEGRAM_ADMIN_IDS`/`TELEGRAM_ADMIN_ID`.
* Валидация initData реальна: `webui/principal.py:79-110` — проверка против списка bot-токенов, **отказ при неоднозначности** (`:97-100`), отказ при пустом allowlist (`:90` `if not init_data or not bot_tokens or not allowed_ids: return None`).
* Owner-пространство единое: `paper_trading/adapter.py:48-54` `uuid5(NAMESPACE_URL, "wored:owner:{telegram_user_id}")`, он же для пароля (`:70`); `webui/paper_api.py:59-78` `_owner_from_request` использует то же пространство и **только** серверную сессию.
* CSRF для mutating-вызовов есть: `webui/simulation_api.py:832-836` (`hmac.compare_digest`), `webui/app.py:295-300`.
* Внутренний сервис-токен: `webui/app.py:241-244`, principal `internal_service` с `is_admin=False` (`webui/principal.py:113-130`).
* Поведение проверено живым запросом без сессии: `GET /api/trading-day/current` → **401**, `GET /api/v3/simulation/plans` → **401**, `POST /api/paper/accounts/x/orders` → **401**, `GET /daily-session` → **303**, `GET /healthz` → **200**. То есть auth включён и API закрыт.
* Долг, зафиксированный в самом коде: `webui/paper_api.py:68-69,78` — fallback `_pt_owner_id("admin")` для «single-owner demo / unauth» до «F08 Phase 4b». Пока principal = internal_service, оба оператора сливаются в один owner-бакет → для многопользовательской модели ТЗ это блокирующее условие.

### 5.3 Mini App

* Единственная web_app-кнопка: `chatbot/handlers/start.py:26` `KeyboardButton(text="⚡ Command Deck", web_app=WebAppInfo(url=miniapp_url))` — **ровно тот keyboard-launch, про который §3 ТЗ предупреждает** (initData при запуске с reply-клавиатуры может быть пустым). Альтернатива — menu button (`chatbot/main.py:205-212`), тоже указывает на тот же URL.
* URL собирается из трёх источников с localhost-fallback и **разными портами в разных модулях**: `chatbot/main.py:205` → `:8081/dashboard`, `chatbot/handlers/start.py:23` → `:8080/command-deck`, `chatbot/handlers/pipeline.py:29` → `:8080/daily-session`, `chatbot/integrations/webui_client.py:37-41` → `WEBUI_PUBLIC_BASE_URL > TG_MINIAPP_URL > WEBUI_URL`, fallback `http://localhost:8080`.
* Фактически задан только `TG_MINIAPP_URL`; `WEBUI_URL` и `WEBUI_PUBLIC_BASE_URL` в env отсутствуют → единого источника значения нет, а проверка «HTTPS доступен с телефона» не делалась (§11 U-03).

---

## 6. Хранилище состояния: четыре области, всё ещё живо

### 6.1 Один пользовательский сценарий = две реализации (нарушение §2/§0 ТЗ)

`chatbot/handlers/pipeline.py:421-481`, обработка «старт сессии»:

1. `:432-444` пытается домен: `paper_trading.adapter.start_day(owner_id=owner_id_from_telegram(user_id), mode="baseline_auto")`, идемпотентность через `get_current_state()` (`:435-439`).
2. `:464-467` при **любом** исключении или `ok=False` — «fall through to legacy», и только `log.info`.
3. `:470-481` legacy-контур: `services.session_manager.create_session(user_id=…, budget_usdt=…, duration_hours=8, risk_mode=…)` → пишет **другие таблицы** и живёт по **другой семантике бюджета/плеча/P&L**.
4. Различие видно пользователю только по заголовку карточки: «Торговый день запущен / ID…» (`:453`) против «Сессия запущена / ID… / Статус: ARMED» (`:493-495`).

Это то, что §0 запрещает «незаметно объявлять новым доменом»: выбор домена происходит по факту исключения, а не по решению, и оба контура продолжают накапливать данные.

### 6.2 Объём данных на 2026-10-08 (production Postgres, только `count(*)`)

| Область | Таблица | Строк |
|---|---|---|
| Domain-лeдер paper | `paper_v2_days` | 40 |
| | `paper_v2_commands` | 117 |
| V3 план/сессия | `simulation_sessions` | 3 |
| | `simulation_plan_versions` | 3 |
| | `simulation_reports` | 0 |
| Chatbot pipeline v2 | `trading_sessions` | 29 |
| | `session_plans` | 17 |
| | `session_revisions` | 47 |
| | `planned_entries` | 17 |
| | `executed_trades` | 6 |
| | `plan_accuracy` | 0 |
| Legacy sim (бот) | `sim_positions` | 4 |
| | `sim_evaluations` | 14 |

Всего в `public` 23 таблицы `paper_v2_*` (перечислены в снимке схемы); бот-доменные таблицы pipeline создаёт сам: `chatbot/services/pipeline_schema.py:20,41,55,68,91,119,134,154,169` (`trading_sessions`, `session_plans`, `session_revisions`, `planned_entries`, `executed_trades`, `execution_events`, `session_metrics`, `daily_reviews`, `plan_accuracy`).

**Bot-only таблиц ТЗ (§6) нет ни одной**: `bot_principals`, `bot_preferences`, `bot_interactions`, `bot_callbacks`, `bot_update_inbox`, `bot_command_links`, `bot_notification_rules`, `bot_notification_outbox`, `bot_audit` — 0 совпадений в `pg_tables` и в `db/`/`migrations/`.

### 6.3 Legacy sim: что можно и нельзя объявлять «своим»

* `chatbot/services/sim_engine.py` работает **только** через фоновый монитор: вызывающие места — `chatbot/main.py:95` (`get_open_positions`, `close_position`, `calculate_unrealized_pnl`) и `chatbot/main.py:136` (`format_position_card`). Прямых handler-вызовов `sim_engine` не обнаружено.
* Но `chatbot/services/sim_math.py` (комиссии/ликвидация/сеттлмент) остаётся **живой общей математикой** для нового контура: `services/execution_engine.py:408`, `services/plan_contract.py:13`, `services/session_manager.py:813,982`. Значит «удалить legacy sim» = задеть активный путь, это отдельное решение куратора (§13 Q-02).
* `sim_positions`/`sim_evaluations` почти пусты (4/14) → миграция или read-only-архивирование дёшевы; скрытый перенос balances не требуется и не делается.

### 6.4 Собственный market-data обходной путь бота

Бот местами читает рынок **мимо collector/MC-V3**, напрямую из HTX REST: `chatbot/services/session_manager.py:1075-1080`, `chatbot/services/plan_accuracy.py:108-110`, `chatbot/ai/knowledge_base.py:15-18` (все три — `https://api.huobi.pro/market/history/kline`). Противоречит §0 («источник рыночных данных общий с WebUI») и порождает расхождение цен между Telegram и WebUI при одинаковых ID.

---

## 7. Backend-контур, доступный боту (реальность вместо BLOCKED_DEPENDENCY)

### 7.1 Чтение

| Возможность | Маршрут | Доказательство |
|---|---|---|
| Список инструментов | `GET /api/v3/instruments` | `webui/market_workspace.py:45,435` |
| Состояние рынка (last/mark/quality/as_of) | `GET /api/v3/market/{instrument_key}/state` | `:447` |
| Свечи | `GET /api/v3/market/{instrument_key}/candles` | `:497` |
| Поток (SSE) | `GET /api/v3/market/{instrument_key}/stream` | `:713` |
| Прогнозы: список/один/точность | `GET /api/v3/forecasts`, `/api/v3/forecast/{forecast_id}`, `/api/v3/forecasts/accuracy` | `webui/forecast_workspace.py:49,437,535,594` |
| Позиции | `GET /api/v3/positions`, `/api/v3/positions/{position_id}` | `webui/positions_workspace.py:48,464,544` |
| Дневное состояние paper | `GET /api/trading-day/current` | `webui/paper_api.py:401` |
| Старый trader read-model | `/api/trader/candles|forecast|state|positions|activity|stream` | `webui/trader_api.py:29,329-631` |

### 7.2 Запись (все mutating-эндпоинты — session+CSRF, `webui/simulation_api.py:832-836`)

| Действие | Маршрут | Доказательство |
|---|---|---|
| Валидация плана (violations, worst loss, `plan_hash`) | `POST /api/v3/simulation/plans/validate` | `:900` |
| Создать/редактировать/список/получить план | `POST /plans`, `POST /plans/{id}`, `GET /plans`, `GET /plans/{id}` | `:945,983,958,969` |
| Утвердить план | `POST /plans/{plan_id}/approve` | `:1004` |
| Создать сессию из плана | `POST /sessions` | `:1057` |
| Переход сессии | `POST /sessions/{session_id}/transitions` | `:1132` |
| AI proposal плана | `POST /api/v3/simulation/plan-proposals`, `GET /plan-proposals/{proposal_id}` | `webui/simulation_proposal.py:449,468` |
| Ручная paper-сделка: **preview, затем order** | `POST /api/paper/accounts/{account_id}/orders/preview`, `POST …/orders` | `webui/paper_api.py:626,656` |
| Старт дня / автоматика | `POST /api/trading-day/start`, `POST /api/trading-day/{day_id}/automation` | `:477,570` |
| Forecast-команда (асинхрон) | `POST /api/v3/forecasts`, `GET /api/v3/forecasts/requests/{request_id}` | `webui/forecast_command_v3.py:84,259,507` |
| Внутренний путь бота (token) | `POST /api/internal/predictions` + `Idempotency-Key`, `GET /api/forecast/{id}/status` | `chatbot/integrations/webui_client.py:103-122` |

### 7.3 Версии контрактов, зафиксированные кодом

* `SimulationPlanV1`: `webui/simulation_api.py:93-99` — `instrument_key, mode, start_at, end_at, timezone, manual_budget, auto_budget, strategy_id, strategy_version, style, allowed_sides, max_leverage, max_risk_per_order, max_daily_loss, max_session_loss, max_total_exposure, max_open_positions, cooldown_minutes, trading_hours, stop_policy, take_profit_policy, close_at_end, fee_schedule_version, slippage_model_version, funding_model_version`.
* `plan_hash` = `sha256:` над canonical JSON (`sort_keys`, фиксированный порядок полей, Decimal-строки) — `webui/simulation_api.py:458-464`; approve пинит хеш, сессия хранит его.
* Plan states: `draft, approved, superseded, rejected` — `:128`.
* Session transitions: `starting→{running,rejected,expired}`, `running→{closing,expired}`, `closing→{settlement_pending}`, `settlement_pending→{closed}`, терминальные `closed/rejected/expired` — `:119-127`.
* Валидаторы лимитов и иерархии потерь: `:344-350`, `:419-434` (risk ≤ min(budget), `max_session_loss ≥ max_daily_loss`, exposure ≥ risk), worst-allowed-loss: `:441-447`.
* `close_at_end=false` не реализован и блокируется violations-ом: `:362-368` — UI не должен показывать этот параметр действующим (ТЗ §U04).
* Миграция V3-хранения: `db/migrations/20260929_simulation_v3.sql` (упомянута шапкой `webui/simulation_api.py:26-27`).

### 7.4 Расхождения контракта с §5 ТЗ (решить в P1)

| Требование §5 | Факт в коде | Статус |
|---|---|---|
| `paused` как состояние сессии и `running→pause`, `paused→resume` | `SESSION_TRANSITIONS` (`webui/simulation_api.py:119-127`) **не содержит `paused`**; `running` умеет только `closing/expired` | **НЕСОВМЕСТИМО** |
| Пауза/продолжить | реализованы на уровне **automation** дня: `paper_trading/contracts.py:75-86` `AutomationState.paused`, `paper_trading/service.py:298,319` `pause_auto/resume_auto`, risk-gate `paper_trading/risk.py:472-473` (`trading_paused`), API-выбор флага `webui/paper_api.py:615` | Реализовано, но **не в V3-сессии**; чат-поверхность — `/day` (P4b, 2026-10-14), легаси-кнопок в `handlers/pipeline.py` больше нет |
| `failed` состояние сессии | есть `rejected/expired`; `failed` нет; в дне есть `recovery_required` (`paper_trading/contracts.py:71`) | Частично |
| `draft → approved → start` c повторной проверкой свежести | plan-машина есть (`:128`), fingerprint start'а из `plan_id+plan_hash` (`:1094-1097`) | Реализовано |
| Раздельные `manual`/`auto` бюджеты и счета | поля плана есть (`:94`); разделение счетов — домен `paper_v2_accounts` | Реализовано в домене |
| Единая ошибка-таксономия `MARKET_STALE`/`RISK_BLOCKED`/`SETTLEMENT_PENDING` для presenter | violations/reason_code частично (`:835` `csrf_invalid`, `:366` `field_enum`) | Частично, presenter-обёртки нет |

---

## 8. Документация против кода

| Утверждение `docs/TELEGRAM_BOT_USAGE.md` | Факт |
|---|---|
| `/models` — «Доступные AI модели + выбор», доступ «Все» (`:38`) | **Команда не зарегистрирована** (grep по `Command(` в `chatbot/handlers/*.py`) |
| `/portfolio`, `/trader`, `/predictions`, `/settings` — доступ «Все» (`:37-40`) | Так и есть в коде: права не проверяются (D-03) |
| «FSM → STOPPED» (`:70`) | FSM в коде отсутствует (§4) — состояние хранится в таблицах pipeline/`paper_v2`, не в aiogram-FSM |
| Command Deck + «HER Console» как web-app-кнопки (`:16-17`) | web_app-кнопка в handlers ровно одна (`handlers/start.py:26`); `HER_MINIAPP_URL` в env есть, кнопки в боте не найдена |
| Фиксированные BTCUSDT / 8 часов (`:63-64`) | Совпадает с legacy-путём (`handlers/pipeline.py:479` `duration_hours=8`); доменный путь этого не фиксирует |
| Шапка `:5-6` публикует `@W_W_O_O_bot` и числовой ID администратора | Публичные данные в репо; ID не секрет, но по §3 он не должен быть и авторизацией |

---

## 9. Env-реестр (имена; значения не печатались)

**Разные файлы у разных сервисов** — источник путаницы с алиасами:

| Сервис | env_file | Доказательство |
|---|---|---|
| chatbot | `.env.wored` | `docker-compose.yml:69-70` |
| webui | `.env` | `docker-compose.yml:111-112` |
| collector | `.env` | `docker-compose.yml:39-40` |
| postgres | `.env.postgres` | `docker-compose.yml:5-6` |

Фактически заданы (проверено и в файлах, и в `env` живых контейнеров, по именам):

* chatbot: `TELEGRAM_BOT_TOKEN`, `WORED_TELEGRAM_TOKEN`, `HERMES_TELEGRAM_TOKEN`, `HERMES_ADMIN_TELEGRAM_ID`, `HERMES_CHATBOT_GATEWAY_ENABLED`, `HER_MINIAPP_URL`, `TG_MINIAPP_URL`, `WEBUI_INTERNAL_TOKEN`, `WEBUI_AUTH_ENABLED`, `WEBUI_ADMIN_PASSWORD`, `WEBUI_SESSION_SECRET`, `WEBUI_COOKIE_SECURE`, `WEBUI_TELEGRAM_BOT_TOKENS`, блок `PAPER_*` (режим рынка, fee, slippage, latency, heartbeat, лимиты AI-квоты `PAPER_AI_MAX_REQUESTS_PER_DAY`/`_TOKENS_PER_DAY`/`_MIN_INTERVAL_SECONDS`/`_PLAN_TTL_SECONDS`), `LLM_ROUTING_MODE`, `LLM_PAID_ENABLED`.
* webui: тот же набор `WEBUI_*` + `TELEGRAM_BOT_TOKEN`, `TG_MINIAPP_URL`.

Отсутствуют (при этом код их читает):

| Ключ | Кто читает | Последствие |
|---|---|---|
| `TELEGRAM_ADMIN_ID` | `chatbot/main.py:33`, `:134` | `alert_listener` выходит сразу → доставка алертов мертва (D-01); уведомления об авто-закрытии пропускаются (D-02) |
| `TELEGRAM_ADMIN_IDS` | `webui/app.py:287` | пустой allowlist → `principal.create_from_telegram` возвращает `None` на **любом** initData (`webui/principal.py:90`) → авторизованный Mini App недоступен (D-04) |
| `TELEGRAM_TOKEN` | `chatbot/main.py:150` | первый алиас всегда пуст → работает только `TELEGRAM_BOT_TOKEN`; `.env.example` nonetheless документирует `TELEGRAM_TOKEN` и `TELEGRAM_ADMIN_ID` |
| `WEBUI_URL`, `WEBUI_PUBLIC_BASE_URL` | `chatbot/main.py:205`, `handlers/start.py:23`, `handlers/pipeline.py:29`, `integrations/webui_client.py:37-41` | четыре места собирают URL по-разному, fallback'ы на localhost с разными портами |
| `BOT_*` (все 12 из §7 ТЗ) | никто | ни одного ключа `BOT_` в `.env`/`.env.wored`; defaults (TTL callback 60 с, wizard 24 ч, refresh 2 с, retention, `BOT_DEFAULT_TIMEZONE`, `BOT_MAX_AI_DAILY_COST_USD`, `BOT_ENABLE_PAID_AI`) не заданы |
| `TELEGRAM_ADMIN_IDS` в `.env.wored.example` | документирован | конфиг-дрейф: пример обещает, рабочий файл не содержит |

Уточнение P3c (задокументированное отклонение от PLAN): `BOT_WIZARD_TTL_HOURS`
**не** добавлялся ни в `.env`, ни в `.env.wored`, ни в `.env.example`. Причина: `BOT_*`
ключи живого бота читаются из `.env.wored` (compose `env_file`), а отсутствие ключа
даёт ровно то же значение, что и явная строка `24` (`wizard_ttl_hours()` fail-safe на
мусор и на ≤ 0); правка env-файлов без необходимости — лишний mutating-шаг и лишний
`up -d`. Значение и семантика зафиксированы в `docs/TELEGRAM-BOT-OPERATIONS.md` §3 и в
`docs/TELEGRAM-BOT-COMMAND-MAP.md` §6. Проверка живого значения:
`docker compose exec -T chatbot python -c "import os;print(os.getenv('BOT_WIZARD_TTL_HOURS','24'))"` → `24`.

`free_routing_archive/` существует на хосте, **0** ссылок в коде `chatbot/**` и `webui/**` — соответствует требованию §U10 «не включать в runtime».

---

## 10. Найденные дефекты (уровень доказательства)

| ID | Дефект | Доказательство | Уровень |
|---|---|---|---|
| D-01 | **Доставка алертов в Telegram не работает.** `market_alerts` никто не слушает | код `chatbot/main.py:33-35` (выход без подписки) + живой `redis-cli pubsub numsub market_alerts` → `0`, `pubsub channels` → пусто + отсутствие `TELEGRAM_ADMIN_ID` в env контейнера | **PROVEN (runtime)** |
| D-02 | `_sim_ai_monitor` **закрывает позиции без уведомления** и по жёсткой формуле ROI ≤ −50 % / ≥ +100 % | `chatbot/main.py:119-141` (закрытие до проверки `admin_id`), лог `16:02:13 Sim AI monitor started` | **PROVEN (код+лог)** |
| D-03 | Отсутствует любой auth-слой бота: нет deny-by-default, нет проверки private chat, `/admin` без scope, мутирующие текстовые фразы и `pipeline_close_all` доступны любому | §5.1 (grep-инвентарь: единственный ID-фильтр — `hermes_admin.py:20`) | **PROVEN (код)** |
| D-04 | Авторизованный Mini App не может работать: allowlist пуст → все initData отвергаются; единственный вход — keyboard web_app (§3 ТЗ предупреждает о пустом initData именно в этом режиме) | `webui/principal.py:90`, `webui/app.py:287`, `chatbot/handlers/start.py:26`, `chatbot/main.py:205-212` | **PROVEN (код); live-Telegram не проверялся** |
| D-05 | `drop_pending_updates=True` без replay/retention-политики → апдейты за время простоя теряются; команды «старт/стоп» могут исчезнуть незаметно | `chatbot/main.py:217` | **PROVEN (код)** |
| D-06 | Два контура одного действия «запустить сессию» с тихим fallthrough на legacy | `chatbot/handlers/pipeline.py:432-444,464-481` | **PROVEN (код)** |
| D-07 | Owner-fallback `"admin"` в server-derived principal (мульти-пользовательская модель невозможна) | `webui/paper_api.py:68-69,78` | **PROVEN (код, признанный долг)** |
| D-08 | Документация обещает несуществующее (`/models`) и отсутствующий механизм (FSM) | §8 | **PROVEN (док↔код)** |
| D-09 | Логируется содержимое приватных апдейтов (200 символов JSON) | `chatbot/main.py:177-179` | **PROVEN (код)** |
| D-10 | Бот частично читает рынок напрямую из HTX, минуя общий источник с WebUI | `chatbot/services/session_manager.py:1075-1080`, `plan_accuracy.py:108-110`, `ai/knowledge_base.py:15-18` | **PROVEN (код)** |
| D-11 | Идемпотентность forecast-запроса из Telegram привязана к `callback.id`, а не к операции → другой апдейт = новый платный запрос | `chatbot/handlers/predictions.py:312` | **PROVEN (код)** |
| D-12 | Команда проверки §10 ТЗ не выполняется: смешанный запуск `chatbot/tests` и `tests/...` даёт `ModuleNotFoundError: No module named 'tests.…'` (оба корня — пакет `tests`) | `PYTEST_EXIT=2`, сбор: 2 errors; поотдельности: 14 passed и 26 passed, `EXIT=0` | **PROVEN (воспроизведено)** |
| D-13 | Top-level пакет `storage` существует и в `chatbot/storage/`, и в `collector/storage/` с РАЗНЫМ API; корневой `conftest.py` ставит `collector` перед `chatbot`, поэтому хост-тест может молча импортировать не ту копию (в проде `PYTHONPATH=/app:/opt` ⇒ `storage` = chatbot). Проявилось как `AttributeError: module 'storage.postgres_client' has no attribute 'get_latest_prediction_request'` | `collector/storage/postgres_client.py` vs `chatbot/storage/postgres_client.py:529`; обход зафиксирован в `tests/telegram/conftest.py` (`_mirror_container_pythonpath()` + ассерт разрешения модуля) | **PROVEN (воспроизведено и обойдено)** |
| D-14 | `paper_trading.service.get_current_state` кладывает в `day["state"]` сырой `DayState` (str-Enum), тогда как отчётные методы отдают `.value`; `str(enum)` = `"DayState.running"`, JSON = `"running"` → WebUI и наивный `str()` в боте показывают РАЗНОЕ состояние одного дня | `paper_trading/service.py:222` против `:419`, `:525`; воспроизведено read-only пробом внутри контейнера (day `e126c8a2-…`, state `DayState.running`); исправлено нормализацией на границе бота (`wored_api.enum_value`) + фикстура теперь отдаёт настоящий enum | **PROVEN (воспроизведено в проде и исправлено)** |
| D-15 | Подпись часового пояса на карточке была арифметически ложной: `strftime("%z")[:-3]` из `"+0700"` даёт `"+0"`, поэтому Bangkok-часы печатались как `14:26:29 (UTC+0 (Asia/Bangkok))` — время верно, метка враньёт (ТЗ §2 требует явного и правильного TZ) | живой лог владельца 2026-10-12 14:26; воспроизведено в контейнере (`format_time` → `UTC+0 (Asia/Bangkok)`); исправлено `_offset_suffix()` на `utcoffset()`; после фикса `UTC+7 (Asia/Bangkok)` и `UTC+5:30 (Asia/Colombo)` | **PROVEN (воспроизведено в проде и исправлено)** |
| D-16 | Детерминистический план-контракт (каталоги, `normalize_plan`, `check_plan_semantics`, `estimate_worst_case_risk`, `plan_hash`) **и реестр инструментов** жили только в `webui/`, а `./webui` НЕ смонтирован в контейнер `chatbot` (`PYTHONPATH=/app:/opt`) → бот физически не мог проверить план и посчитать тот же `plan_hash`; вторая копия валидатора ТЗ запрещает (§0 «те же числа, что WebUI») | `webui/simulation_api.py:56-465` (до переноса) + `docker-compose.yml` (chatbot volumes); разрешено P3a: код перенесён дословно в `trading_math/plan_contract.py` и `trading_math/instrument_registry.py`, `webui/instrument_registry.py` → шим-реэкспорт, `webui/simulation_api.py` → импорт+реэкспорт; доказательство идентичности: один и тот же план даёт `sha256:e934197c…53fb` в webui (Python 3.11.16), chatbot (Python 3.9.25) и на хосте (3.14.3) | **PROVEN (исправлено, E1+E2)** |
| D-17 | План-домен и paper-домен использовали **два owner-пространства для одного человека**: `webui/simulation_api._owner_id` отдавал `"password:<username>"` / `"telegram:<user_id>"`, тогда как `webui/paper_api._owner_from_request`, `webui/positions_workspace`, `chatbot/security/principal.py` и `paper_trading/adapter.owner_id_from_telegram` — `uuid5(NAMESPACE_URL, "wored:owner:{...}")`. Бот, напиши он план, создал бы данные, невидимые дашборду (и наоборот) — не ошибку, а пустой список | живое чтение прод-БД до правки: `simulation_plan_versions`/`simulation_sessions`/`simulation_plan_proposals` = 3/3/3 строк, у всех `owner_id = password:admin`; разрешено P3b: `_owner_id` → канонический `uuid5` (через `paper_trading.adapter.owner_id_from_webui`, fail-closed 401 сохранён) + `db/migrations/20261012_simulation_owner_unification.sql` (pre-flight 0, `UPDATE 3/3/3/0`, post-flight 0, идемпотентно); доказательство: `list_plans` через `PostgresSimulationStore` в контейнере webui видит все три approved-плана под `4f4d18fb-…`, чужой owner видит 0; остаточных префиксных owner = 0 | **PROVEN (исправлено, применено на живой БД, E1+E2+E3)** |
| D-18 | Сам **конвейер проверки** (normalize → semantics → market gate → coverage gate → worst case → `plan_hash`) и сборка рынка жили не в общем пакете, а в теле эндпоинта `webui/simulation_api.validate_plan_endpoint` + `webui/market_workspace`. Бот мог унаследовать только `plan_contract` (P3a), но не порядок гейтов → гейт ТЗ P3 «тот же `plan_hash` и violations» был бы достигнут копированием, то есть вечным дрейфом. Частный случай: при `identity_ok: False` старый код обращался к `state["quality"]["worst"]`, которого в этом ответе нет → KeyError → HTTP 500 вместо честного отказа | `webui/simulation_api.py:294` (после правки — тонкий адаптер над `trading_math.plan_validation.validate_plan`), `trading_math/market_state.py:114-145` (ветка `identity_ok`); разрешено P3c: `build_market_state` перенесён дословно (SHA-доказательство verbatim-копии) + `trading_math/plan_validation.py`; `identity_mismatch` теперь violation (fail-closed), закреплено `tests/test_plan_validation.py`; идентичность подтверждена двух-рантайм пробом: 6 тел плана дают совпадающие `violations` и `sha256:1c74ca87…` в webui (3.11.16) и chatbot (3.9.25) | **PROVEN (исправлено, E1+E2)** |
| D-19 | Два **теста**, а не продукта, давали ложные падения на длинном прогоне: (а) фикстура `LIVE = {key: snapshot_payload()}` строилась во время импорта модуля, поэтому в прогоне на 117 с снапшот успевал состариться за окно 5 с и карточка честно становилась 🟠 — тест падал на арифметике wall-clock, а не на поведении; (б) `test_chart_paints_candles_MC04` читал строку статуса сразу после появления `<canvas>`, но canvas создаётся инициализацией библиотеки, а счётчик свечей пишет `setStatusLine()` после XHR → гонка (1 падение из 5 прогонов) | воспроизведено: `test_live_market_shows_source_value_age_and_provenance` падал только в широком хост-прогоне; MC-04 падал 1 раз из 5 standalone-запусков. Исправлено: `_live()` вызывается в каждом тесте (`tests/telegram/test_now_card.py`, `tests/telegram/test_screens.py`) и `wait_for_function(...\d+\s*свечей...)` c ограничением 8 с (`tests/ui/test_v3_market_browser.py`); после правок 5/5 прогонов зелёные, полный MC-V3 гейт 73 passed | **PROVEN (воспроизведено и исправлено; паттерн D-14)** |
| D-20 | Дефект в собственном коде P3c, найден до подключения: `simulation_plans.create_draft`/`set_field` проверяли `if plan is None:`, но `_normalize_for_write` при отказе контракта возвращает `{}` (не `None`) → при непрходимом значении бот сохранил бы **пустой план** и показал бы его дашборду как черновик | `chatbot/services/simulation_plans.py` (заменено на `if not plan:` ×2); закреплено `tests/telegram/test_plan_screens.py::test_refused_edit_writes_nothing` (после отказа `field_range` черновик байт-в-байт прежний, `plan_hash` не изменился) | **PROVEN (найдено при ревью своего кода, исправлено до wiring)** |

---

## 11. Неизвестное (UNVERIFIED) — что нельзя подтвердить методами P0

| ID | Вопрос | Почему не проверено | Команда/метод проверки |
|---|---|---|---|
| U-01 | Какие BotFather command scopes реально настроены для продакшн-бота | требует обращения к Telegram с продакшн-токеном; в P0 не выполнялось | `getMyCommands` отдельным запросом с явного разрешения; либо BotFather вручную |
| U-02 | Доставляет ли бот сообщения живому владельцу (end-to-end) | тестового токена/ограниченного owner ID в проекте нет → по §10 ТЗ статус **BLOCKED**, а не PASS | отдельный test bot + `BOT_OWNER_IDS` |
| U-03 | Доступен ли HTTPS Mini App с телефона | `TG_MINIAPP_URL` задан, внешний TLS-origin не проверялся; `localhost` из fallback'ов не является адресом WORED с телефона | открыть с телефона; проверить сертификат |
| U-04 | Работает ли `paper_v2`-контур авто-торговли в рантайме | `paper_v2_days`=40, но решения/оценки ранее измерены как 0 (`docs/CURATOR-REPORT-2026-10-08.md` §6) — противоречие не разрешено | `select` по `paper_v2_decisions/evaluations` + лог runner |
| U-05 | Реальная задержка/свежесть `as_of`, `quality` в `/api/v3/market/*/state` | не запрашивалось с валидной сессией | авторизованный GET + сравнение с Redis-тикером |
| U-06 | Истинное значение `WEBUI_AUTH_ENABLED` | значения `.env` не печатались (правило AGENTS.md); косвенно — 401/303 подтверждают включённость | оператор проверяет сам |
| U-07 | Сроки совместимости legacy-команд (§2 требует точный срок) | продуктовое решение | решить куратору, зафиксировать в command map |
| U-08 | Допустимые финансовые limits и права на их ослабление | часть закрыта violations-валидатором, политический слой не определён | решить куратору |
| U-09 | Дублируются ли `trading_sessions` и `paper_v2_days` по датам/owner | сравнение содержимого не делалось | SQL-joined сверка 29 против 40 |

Отдельная самокоррекция: в более раннем обороте я предполагал существование модулей `paper_trading/session.py` и `webui/paper_v3_session.py`. На 2026-10-08 их **нет ни на диске, ни в git** (`git log --all -- <пути>` пуст), и **ни один файл репозитория их не импортирует** (repo-wide grep — 0 совпадений). Ни один документ (включая `docs/CURATOR-REPORT-2026-10-08.md`) на них не ссылается, поэтому публичного отзыва в документах не требуется. Реальный контур: `paper_trading/adapter.py|service.py|contracts.py` + `webui/paper_api.py|simulation_api.py`.

---

## 12. Готовность функций к фазам P2–P7

Обозначения: `ГОТОВО-К-СТРОИТЕЛЬСТВУ` (контракт существует, бот его не использует), `ТРЕБУЕТ-РЕШЕНИЯ` (нужно решение P1), `BLOCKED_DEPENDENCY` (зависит от непроверенного), `BLOCKED_EXTERNAL` (нужен отдельный test bot/устройство).

| Функция ТЗ | Статус | Причина |
|---|---|---|
| U02 рынок в чате (`/now`) | ГОТОВО-К-СТРОИТЕЛЬСТВУ | `GET /api/v3/market/{key}/state` + `/candles` + SSE |
| U03 прогноз и проверка | ТРЕБУЕТ-РЕШЕНИЯ | read/write-контур есть; не решено, какой forecast_id показывает карточка: `/api/v3/forecasts` или legacy `/api/forecast/*` (бот сейчас ходит во второй, `webui_client.py:105`) |
| U04 ручная симуляция (wizard→plan→approve→start) | ТРЕБУЕТ-РЕШЕНИЯ | план/сессия и `plan_hash` есть; нет `paused` в V3 (§7.4) и нет bot-side draft storage |
| U05 proposal от AI | ЗАКРЫТО В ЧАТЕ (P3d, 2026-10-13) | домен вынесен в `trading_math/simulation_proposal.py` (дословно), webui-эндпоинт стал адаптером; чат идёт по той же анкете/валидатору/хэшу. Paid-агент — только opt-in `BOT_PROPOSAL_SOURCE=llm`, по умолчанию бесплатный детерминированный rule-engine; refusal-путь (`llm_timeout`) показан живьём без единого платного вызова. Гейт-числа: `docs/CURATOR-REPORT-P3D-2026-10-13.md` |
| U06 вести сессию | ТРЕБУЕТ-РЕШЕНИЯ | transitions есть, но набор состояний уже §5 ТЗ; `settlement_pending` поддержан |
| U07 ручная paper-сделка preview→confirm | ЗАКРЫТО В ЧАТЕ (P4, 2026-10-14) | единый доменный тикет-гейт `paper_trading/ticket.py::build_ticket` (лимиты из `paper_v2_days.settings_snapshot`, veto — `risk.check_order_risk`, объём — `calculate_position_size` с шагом 0.001, ликвидация — изолированная маржа); `webui/paper_api.py::_preview` стал presenter-ом над ним, чат (`chatbot/handlers/paper_deal.py` + `services/paper_orders.py`) идёт в тот же `build_ticket`, минуя HTTP. Закрытие позиции из чата **не строится** (объём P4; долг вынесен в P4c, RFC Q-24). Управление днём (пауза/продолжить/стоп авто/завершить/лимиты) — отдельный экран `/day`, P4b. Гейт-числа: `docs/CURATOR-REPORT-P4-2026-10-14.md` и `docs/CURATOR-REPORT-P4B-2026-10-14.md` |
| U08 итоги/обучение | ТРЕБУЕТ-РЕШЕНИЯ | `simulation_reports` = 0 строк; экспорт-сервис существует в домене, но связь «отчёт ⇄ session_id» не подтверждена |
| U09 оповещения | BLOCKED_DEPENDENCY | нет `bot_notification_rules/outbox`; D-01 ломает и текущий pubsub; durable queue отсутствует |
| U10 AI-вопрос, mode/usage/quota/trace | ТРЕБУЕТ-РЕШЕНИЯ | конфигурация квот есть (`PAPER_AI_*`, `LLM_ROUTING_MODE`, `LLM_PAID_ENABLED`), но evaluation-gate (§U10) не формализован и датасета нет |
| P6 Mini App (защищённый) | BLOCKED_DEPENDENCY | D-04: allowlist пуст; keyboard-launch не даёт initData |
| TB-29 (Android/iOS) | BLOCKED_EXTERNAL | нет отдельного test bot/owner ID |
| TB-32 (финансовая корректность) | BLOCKED_DEPENDENCY | требует MC-V3/AC-доказательств, а не fixture |
| Middleware/perm (P2, TB-01) | ГОТОВО-К-СТРОИТЕЛЬСТВУ | ничего не блокирует: слой просто не написан |

---

## 13. Решения, необходимые от куратора до P1

| ID | Вопрос | Варианты |
|---|---|---|
| Q-01 | Какой домен объявляется единственным для «сессии» бота: V3 `simulation_sessions`+`paper_v2_*` или chatbot `trading_sessions`? | (а) только домен V3/paper, legacy pipeline → read-only archive; (б) оставить pipeline как единственный и не строить V3-обход; (в) переходный период с явным маркированием контура в каждой карточке |
| Q-02 | Судьба `sim_engine`/`sim_math`: `sim_math` — живой общий код, поэтому «удалить legacy sim» нельзя без задевания активного пути | (а) заморозить `_sim_ai_monitor` и перевести `sim_*` в read-only; (б) вынести `sim_math` в общий пакет и удалить монитор; (в) оставить как есть с маркировкой |
| Q-03 | `paused` в V3-сессии: расширять `SESSION_TRANSITIONS` (правка backend-контракта, вне юрисдикции бота) или отображать паузу на `AutomationState.paused` (доменный флаг дня) | решение по §7.4 |
| Q-04 | Канонические env-ключи и миграция алиасов: `TELEGRAM_BOT_TOKEN` vs `TELEGRAM_TOKEN` vs `WORED_TELEGRAM_TOKEN` vs `WEBUI_TELEGRAM_BOT_TOKENS`; owner-allowlist `BOT_OWNER_IDS` vs `TELEGRAM_ADMIN_IDS`; единый Mini App URL | выбрать канон + временный валидируемый alias; трогать `.env`/`.env.wored` можно только с подтверждения (AGENTS.md) |
| Q-05 | Сроки совместимости legacy-команд (`/portfolio`, `/trader`, `/predictions`, `/plans`, `/diff`, `/accuracy`, `/settings`, `/plan`) | конкретная дата/релиз в command map |
| Q-06 | Тестовый Telegram-токен для live-приёмки (TB-29) | выделить или принять `BLOCKED` |
| Q-07 | Что делать с D-01 прямо сейчас: это не «новые права», а сломанный существующий путь | (а) точечный fix allowlist-ключа + тест; (б) не трогать до P2 middleware |
| Q-08 | Логирование `model_dump_json()` (D-09): redaction и retention | политика redaction до P2 |
| Q-09 | Путь P3 к план-контракту: HTTP в тот же V3 API или вынести детерминистическое ядро в общий пакет | **РЕШЕНО куратором 2026-10-12: вынести ядро в общий пакет** → P3a выполнена (`trading_math/plan_contract.py`, D-16); хранилище планов/сессий тоже переносится (P3b) |
| Q-10 | Кто вправе мутировать план-домен из Telegram | **РЕШЕНО куратором 2026-10-12: owner-only, старт сессии только вручную** (авто-спуск дня не из бота) |
| Q-11 | Как унифицировать owner-пространство план-домена (D-17) до появления bot write-path | **РЕШЕНО куратором 2026-10-12: uuid5 + миграция 9 строк**; файл миграции создать и применить сразу (не откладывать) → выполнено в P3b, гейт-числа в `docs/CURATOR-REPORT-P3B-2026-10-12.md` |
| Q-12 | Объём среза P3b | **РЕШЕНО куратором 2026-10-12: вынос хранилища + гейты, без bot-экранов**; экраны `/plan` вынесены в P3c |
| Q-13 | Какие права на план-домен даёт чат (расширение Q-10) | **РЕШЕНО куратором 2026-10-12: «черновик + утверждение из чата»** — бот создаёт, редактирует и **утверждает** план (двухшаговое подтверждение, перепроверка pipeline в момент нажатия); **старт сессии остаётся только в WebUI**. Реализовано в P3c: `handlers/simulation_v3.py` + `services/simulation_plans.py`, мутации — `MUTATING_ROLES = {owner}` |
| Q-14 | Какая команда каноническая и что делать с общим кодом проверки (P3c) | **РЕШЕНО куратором 2026-10-12: «плюс каноническая /plans»** (`/plan` → редирект на тот же экран, TB-03) и **«да, вынести в trading_math»** → `trading_math/plan_validation.py` + `trading_math/market_state.py` (дословный перенос), webui-эндпоинт стал адаптером над тем же вызовом (D-18) |
| Q-05 (уточнение) | Судьба `/plan` и `/plans` в списке legacy-имён | **ЧАСТИЧНО ЗАКРЫТО P3c**: `/plans` выведено из редиректов и стало каноническим экраном V3, `/plan` остался редиректом на него; `/trader`, `/diff`, `/accuracy` ещё редиректятся на `paper_trading` read-model, `/settings`, `/alerts`, `/news`, `/analytics`, `/admin` — P5/P6 |
| Q-15 | Живой прогон P3c (13.10, §6.1): `/plans,` (прилипшая запятая) прошёл мимо `Command("plans")` и был отвечён AI-фолбэком как выдуманный торговый план (LIVE-1) | **РЕШЕНО куратором 2026-10-13: «Отсечь + починить матчинг»** — обе правила сразу. Подтверждено в живом 3.22-рантайме: `Command("plans")` на `"/plans,"` → `matched=False`, сообщение доходило до `handlers/chat.py::cmd_chat` → `trade_plan` intent → `premium` генерировал вход/СТО/тейк (вразрез с «no invented numbers»). Реализовано в P3c.2: `chatbot/handlers/telegram_surface.py` (нормализация токена в outer middleware до всех роутеров + честный отказ вместо LLM для slash-ввода в `cmd_chat`), гейт-числа и живая проба — `docs/CURATOR-REPORT-P3C-2-2026-10-13.md` |
| Q-16 | Живой прогон P3c: повторное открытие неизменённого консоли-экрана даёт красный `BACKEND_UNAVAILABLE` (LIVE-2) | **РЕШЕНО куратором 2026-10-13 (объём «Оба дефекта»).** Telegram `message is not modified` (штатный no-op идемпотентного `edit_text`) ловился `except Exception` в `now.py::_render` и рисовался как падение сервиса; в `simulation_v3._reply` то же не ловилось вовсе. Реализовано в P3c.2: `surface.is_no_op_edit` — единственный предикат, его вызывают `_reply` консоли и план-роутера; `show_now` переведён на общий `_reply` (TB-03); красный цвет означает только настоящие сбои чтения (тест `test_genuine_screen_failure_still_produces_the_error_card`) |
| Q-17 | Объём P3d (U05 в чате): где живёт proposal-домен и как доказать «тот же `plan_hash`» | **РЕШЕНО куратором 2026-10-13: «Вынести в `trading_math`»** — дословный перенос в `trading_math/simulation_proposal.py`, `webui/simulation_proposal.py` остаётся HTTP-адаптером с re-exportом имён (22 существующих теста импортируют прежние имена). Доказательство равенства: один и тот же черновик, прогнанный через образы chatbot (Python 3.9) и webui (Python 3.11), даёт побайтово одинаковый `plan_hash`; живой отклик дашборда был пересчитан внутри чат-бота — `sha256:514c9da5…` совпал во всех трёх местах |
| Q-18 | Платный агент в proposal: включать ли сейчас | **РЕШЕНО куратором 2026-10-13: «Опциональный адаптер сейчас, по умолчанию rules»** — `chatbot/ai/proposal_source.py` существует, но достигается только при `BOT_PROPOSAL_SOURCE=llm`; импорт стоит внутри этой ветки, поэтому установка без флага никогда не открывает клиент провайдера. Доказательство «AI failure safe» сделано реальной веткой: цепочка исчерпана, `status=failed`, `reason_code=llm_timeout`, черновика нет, ручной путь (`/plans new` + `/plans set`) остался жив; ни одного платного вызова (облачных ключей в пробе не было) |
| Q-19 | Кто проводит живой гейт в Telegram | **РЕШЕНО куратором 2026-10-13: «Я сам, через клиент»** — прогон выполнен в десктопном Telegram (бот @W_W_O_O_bot), карточки и кнопки списаны с экрана, затем сверены с `docker compose logs chatbot`: `PROPOSAL_RAN … status=completed source=rules model=wored-rules-v1 hash=sha256:4f5a4638151e`, `PLAN_DRAFT_CREATED … hash=sha256:4f5a4638151e`, `PROPOSAL_ADOPTED … bound=True` |
| Q-20 | UX входа в анкету: ответ отдельным сообщением или аргумент команды | **РЕШЕНО куратором 2026-10-13: «Явный отказ с подсказкой»** — голое сообщение, все токены которого `ключ=значение` и ≥3 ключа принадлежат анкете, больше не уходит в LLM-чат: `handlers/chat.py::_bare_payload_refusal` отвечает карточкой с полным шаблоном `/plans propose <ключ>=<значение> …` (тот же парсер `simulation_proposals.parse_proposal_answer`, TB-03). Доказательство «ноль вызовов модели» — тест `tests/telegram/test_questionnaire_routing.py` со шпионом на `ai.router.call_raw`. Тот же механизм признаёт голые аргументы `/deal` (`side=… stop=…`), иначе они молча ушли бы в AI-фолбек | 
| Q-21 | Две реализации тикета ручной сделки: `webui/paper_api._preview` считал объём/ликвидацию сам, а доменный `check_order_risk` (15 проверок) ручная сделка не проходил | **ЗАКРЫТО P4 (2026-10-14): «Делегируем домену»** — единственная реализация `paper_trading/ticket.py::build_ticket` (+ `TicketDenied` с кодами и русскими текстами); 12 локальных проверок и локальная формула ликвидации из WebUI удалены, `_preview` только маппит `TicketDenied→HTTPException` (`day_not_active→409`, `not_manual_account→403`, `account_not_found→404`, остальное `→422`), форма ответа не изменилась. Побочный эффект по плану: тикет WebUI стал строже (RR, шаг объёма, стоп до ликвидации) — пересчитанные числа в `tests/test_paper_api.py`/`test_paper_api_domain.py` | 
| Q-22 | Исполнение ручной заявки не проверяло ни тип счёта, ни владельца, ни принадлежность дню (проверки жили только в UI-слое) | **ЗАКРЫТО P4 (2026-10-14): гейты в `paper_trading.service`** — `submit_manual_order` после активации дня отдаёт `no_manual_account` / `account_not_owned` / `day_mismatch` (порядок: сначала владение), `close_position` на чужой позиции даёт `position_not_found` (не раскрывая существования), `IdempotencyConflict` не глотается: `idempotency_conflict` + существующий `command_id`. прослежено на изолированном QA-контуре (`output/p4_qa_trace.py`), включая MC-15: статус `conflict` проставляется на существующей строке команды |
| Q-23 | `close_position` не имел owner-фильтра: по `position_id` можно было закрыть чужое | **ЗАКРЫТО P4 (2026-10-14)** — владелец проверяется по аккаунту позиции, отказ недифференцируемый; тест `tests/paper_trading/test_service_guards.py` | 
| Q-24 | `pipeline_pause`/`pipeline_close_all` в чате ходят в legacy `trading_sessions`, а не в домен; закрытие позиции из чата отсутствует | **ЗАКРЫТО P4b (2026-10-14), кроме одного** — решение куратора по объёму: «всё, включая tighten/reduce», «закрытие позиции из чата — отдельный срез P4c», «WebUI «Стоп авто» → доменный `close_all` — в этом же срезе». Легаси-писатели удалены, а не переписаны: `_handle_stop_safe`/`_handle_revision_safe`, коллбэки `pipeline_pause`/`pipeline_tighten`/`pipeline_reduce`/`pipeline_close_all` и кнопка «🛑 Close all» в `handlers/pipeline.py`; фразы по-прежнему распознаются и отвечают редиректом на `/day` (иначе ушли бы в AI-фолбек). В WebUI метка «Стоп авто» вела на доменный `pause_auto`, обещая в JS ликвидацию — теперь она зовёт `close_all_positions(scope=auto)`, а метка prototype-mode больше не обещает закрытие. Единая мутирующая поверхность — `/day <pause\|resume\|finish\|closeauto\|limits …>\|status` (`chatbot/handlers/day_control.py` + `services/day_control.py`) на тех же правилах, что `/deal`: тикет, TTL `TICKET_TTL_SECONDS`, owner-only, `expected` уходит в доменный CAS. Ключ идемпотентности теперь принадлежит попытке (`tg-<owner>-<ticket>`), и только `finish_day` остаётся привязанным к дню (`finish-<day>`), потому что его же ставит в очередь планировщик runner'а каждые 2 с. «Ужесточить/снизить» стали доменной командой `set_automation`: `paper_trading/runner.py::_handle_set_automation` + `paper_trading/repository.py::merge_day_limits` (CAS внутри `FOR UPDATE`), проигрыш — терминальный `failed`/`limits_changed` и не переиздаётся. **Открытый долг: закрытие отдельной позиции из чата (P4c)** — `paper_deal` по-прежнему не импортирует `close_position` (тест `test_the_screen_never_offers_to_close_a_position`); `/day closeauto` закрывает позиции автомата, ручную позицию закрыть из чата нельзя |
| Q-25 | Лимиты риска: `risk_policy` настроек UI vs `settings_snapshot` дня | **ОТКРЫТО (долг, зафиксирован P4; изменено P4b)** — источник один (`paper_v2_days.settings_snapshot`), и с P4b он ещё и изменяемый посреди дня: `/day limits <ключ>=<число>` → доменная команда `set_automation`. Форма значения одна на показ и на сверку (`paper_trading.ticket.current_limits` + `risk.canonical_limit`, фиксированная точка — «10», а не «1E+1»), неизвестный ключ отсекает `parse_limits` (`unknown_limit`). Исторический дрейф значений остаётся: при отсутствии ключа в снапшоте берётся дефолт `RiskSettings` и он **обязательно** называется и в тикете (`settings_notes`), и в карточке чата («лимиты дня, взятые из дефолта RiskSettings»), молча не подставляется. Не решено: что делать с `/trading-day/settings` внутри живого дня — запись оттуда по-прежнему отказана (409/502) с указанием на `/day limits`. Живой день на 2026-10-14 отдаёт `min_net_rr`, `risk_tier_absent` — см. `output/p4-live-preview.log` |

---

## 14. Команды проверки (исправленные)

Из корня `D:\WORED` (PowerShell), только read-only:

```powershell
git status --short
docker compose -f docker-compose.qa.yml config --quiet   # факт: exit 0

# НЕ использовать как единую строку (коллизия пакета `tests`, D-12):
#   python -m pytest chatbot/tests tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q
# Поотдельности (фактические результаты этого обхода):
python -m pytest tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q   # 14 passed, EXIT=0
python -m pytest chatbot/tests -q                                                                    # 26 passed, EXIT=0
```

Отдельный прогон `tests/test_simulation_proposal.py` тем же семейством коллизий
(D-12: оба корня — пакет `tests`) не собирается сам по себе: строка
`from test_simulation_api import …` даёт `ModuleNotFoundError`. Рабочий обход —
добавить `tests` в `PYTHONPATH` (только для хоста, в контейнере не требуется):

```powershell
$env:PYTHONPATH = "D:\WORED;D:\WORED\tests;D:\WORED\webui;D:\WORED\chatbot;D:\WORED\collector"
python -m pytest tests/test_simulation_proposal.py -q    # 22 passed, EXIT=0
Remove-Item Env:PYTHONPATH
```

Авторизация/доставка (без секретов, имена переменных):

```powershell
docker compose exec -T chatbot sh -c "env | cut -d= -f1 | grep -E '^(TELEGRAM|TG_|WEBUI|BOT_)' | sort"
docker compose exec -T redis redis-cli pubsub numsub market_alerts   # факт: 0
docker compose logs --tail 400 chatbot | Select-String "Sim AI monitor|menu button"
```

---

## 15. Что сознательно не делалось в P0

* Не изменялись `chatbot/main.py`, handlers/services, `collector/`, `webui/`, `paper_trading/`, `.env*`, `docker-compose*.yml`.
* Не перезапускались и не останавливались сервисы; прод-стек на момент съёмки: 5 контейнеров `Up`; `chatbot` перезапускался до начала этого оборота (логи `16:01`/`16:02`), мной не перезапускался.
* Не вызывались Telegram API продакшн-токеном, не отправлялись сообщения владельцу.
* Не переносились balances/positions между областями; `sim_positions` (4 строки) тронуты не были.
* Не создавались bot-таблицы и миграции — это P1/P2 по §8.
* Незакоммиченные правки предыдущего оборота сохранены нетронутыми: `docs/OPERATIONAL-PROCESS.md`, `tests/test_episodes.py`, `tests/test_paper_api.py`, `tests/test_market_stream_unit.py`, `tests/ui/test_v3_market_browser.py`, `tests/ui/test_v3_market_ui.py`, `docs/CURATOR-REPORT-2026-10-08.md` (untracked).
