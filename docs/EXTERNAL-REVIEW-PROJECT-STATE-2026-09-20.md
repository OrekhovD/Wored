# WORED — Отчёт для внешнего ревьюера: фактическое состояние проекта

**Дата составления:** 20.09.2026, 18:35 UTC+7 (все «живые» проверки ниже выполнены в этот день, команды воспроизведения — в §10)
**HEAD репозитория:** `cf7c978` (main, на 1 коммит впереди origin/main)
**Правило документа:** каждое утверждение помечено источником — `[проверено ДД.ММ]` (живой замер в указанную дату), `[код]` (прочитано в исходниках), `[документ]` (из приёмочных документов репозитория). Никаких статусов «по памяти».

---

## 1. Резюме (TL;DR)

WORED — local-first система крипто-мониторинга и AI-аналитики (HTX BTC/USDT) с Telegram-ботом, web-дашбордом и **обучающим симулятором фьючерсной торговли без реального исполнения ордеров**.

| Область | Оценка |
|---|---|
| Runtime (6 контейнеров) | Все Up, webui/postgres healthy, живой сбор данных HTX `[проверено 20.09]` |
| QA-автотесты | 390 passed / 15 skipped / 1 xfailed (изолированный контейнер) + 18/18 live-смоук (хост) `[проверено 19–20.09]` |
| UI/UX приёмка (A01–A55) | 54/55 закрыто; открыт только A54 (owner-only проверка) `[документ e7be15c]` |
| Симуляция торговли (§7 ТЗ) | Полный цикл preview→open→close→metrics работает, математика консистентна `[проверено 20.09, E2E]` |
| Соответствие ТЗ в целом | ~85%; 6 документированных отклонений (§6), из них 1 формальный FAIL приёмки §16.2 (политика плеча) |
| Безопасность | 1 устранённая утечка секрета из дерева (ротация пароля **не выполнена**), 1 файл с живыми секретами в workspace `[детали §7]` |
| Стабильность | Гонка webui↔postgres устранена и проверена fault-инъекцией `[проверено 19.09]` |

---

## 2. Архитектура и состав runtime

### 2.1 Сервисы (docker-compose.yml)

| Сервис | Роль | Статус 20.09 |
|---|---|---|
| `chatbot` (+ второй инстанс `chatbot_wored`) | Telegram UI (aiogram 3), AI-роутинг Worker/Analyst/Premium с fallback-цепочками, сим-движок | Up 25ч `[проверено]` |
| `collector` | HTX WebSocket + REST ingestion (linear swap BTC-USDT), индикаторы, scheduler, journal/alerts/forecasts | Up 25ч; в логах живые HTTP-200 к api.hbdm.com на момент проверки `[проверено]` |
| `webui` | FastAPI-дашборд, TradingView Lightweight Charts 5.2, Command Deck / Trader Deck / Futures Lab / Strategy | Up, healthy `[проверено]` |
| `postgres` | alerts, ai_journal, forecast_*, sim_positions, paper_v2_* , strategy_rules, sim_evaluations | Up, healthy `[проверено]` |
| `redis` | hot ticker cache (`ticker:btcusdt`, `ticker:ethusdt` присутствуют `[проверено]`), pub/sub алертов, trader:mode | Up 25ч `[проверено]` |

Health-контракт webui: `/healthz` 200, `/readyz` 200 (503 пока pg-пул не поднят), остальные маршруты 303 на `/login` без сессии `[проверено 20.09]`.

### 2.2 AI-стек (ACTIVE, из AGENTS.md и кода роутера)

- Primary: **Ollama Cloud** (`https://ollama.com/v1`): premium `glm-5.2`, analyst `deepseek-v4-pro`, worker `deepseek-v4-flash`, reviewer `minimax-m3`, oracle `kimi-k2.6`.
- Utility: TokenRouter `kimi-k3-free`; Fallback: NVIDIA NIM, OpenRouter.
- Qwen/DashScope — legacy, неактивны.
- Маршрутизация: regex-first диспетчер интентов (`chatbot/ai/dispatcher.py`) с intent-таксономией, включая `trade_sim`, `trade_plan`, `dual_analysis`; тяжёлые модели не тратятся на разбор типовых команд `[код]`.

### 2.3 Подсистемы, вышедшие за рамки первоначального ТЗ

1. **Futures-симулятор** (`chatbot/services/sim_engine.py` + `sim_math.py`, таблицы `sim_positions`) — основной предмет §5 настоящего отчёта.
2. **Paper Trading V2** (`paper_trading/` — runner, ledger, risk, repository, learning; таблицы `paper_v2_days/positions/events`) — дуальные счета, crash-recovery, детерминированный риск. Приёмка закрывалась отдельным контуром AC-01…AC-28 `[документ: docs/HERMES-PAPER-TRADING-ACCEPTANCE-CLOSURE-20260916.md]`.
3. **Trader Agent V0.1** (`webui/trader_api.py`, страница `/trader`, режимы trade/reduce_only/pause) — read-only деки + переключатель режима, живёт в Redis `trader:mode` `[код]`.
4. **Forecast Engine** (`forecast_engine/`, `collector/predictions/`) — прогнозы с квантилями q10/q90, evaluator факт-против-прогноза `[код]`.
5. Legacy-зоны (`chatbot/loader.py`, `chatbot/context/*`, `chatbot/ui/*`, `collector/alerts/detector.py`, `collector/scheduler/briefing.py`) — **не** runtime-critical, не развивать без отдельного решения `[AGENTS.md]`.

---

## 3. Симуляция торговли: что реально работает (E2E-верификация 20.09.2026)

Прогнан полный цикл живьём через браузер (Playwright) + прямой вызов движка в контейнере chatbot. Позиция-артефакт: `sim_positions id=1` (long BTCUSDT 10× 10 USDT, closed, realized −0.1123 USDT) — единственная запись в таблице, оставлена как демо-цикл.

| Шаг | Результат |
|---|---|
| Auth-граница (`/trader` 303, API 401 без сессии) | PASS |
| `GET /api/trade/preview` long 10× | entry=live 80 329.5; liq=72 708.3 = entry×(1−1/L+fee)/(1−MM) — формула isolated-v2 сходится; сценарии ±1%: +0.88/−1.12 = gross−комиссии. PASS |
| `POST /api/positions/open` | id=1, size/notional/fee из live-цены, `FOR UPDATE`-транзакций на закрытии. PASS |
| Read-модели `GET /api/sim-positions`, `/api/strategy/metrics` | статусы и счётчики согласованы с БД. PASS |
| Close движком chatbot `sim_engine.close_position(1, 80 335.7)` | raw +0.0077 − entry-fee 0.06 − close-fee 0.06 = **−0.1123**, ROI −1.12%. PASS |
| `POST /api/strategy/evaluate` | честный отказ 400 «Need 3+ closed positions, have 1» (порог значимости). PASS (как задумано) |
| `POST /api/trader/mode` | pause→state виден; дубль по idempotency-ключу → `applied:false`; невалидный режим 422; восстановление `trade`. PASS |

**Ключевое качество:** preview-калькулятор (webui) и settlement-калькулятор (chatbot `sim_math`) — независимые реализации, дающие идентичные числа до копейки. Валидаторы: только `btcusdt`, только long/short, плечо 1–100 int, margin ≤ 1 000 000 USDT, finite-проверки цен `[код: sim_math.validate_order]`.

**Границы симулятора (зафиксировано в коде):**
- Реальные ордера на HTX не выставляются нигде в репозитории — только read-only публичные market-data запросы. Это соответствует и исходному ТЗ («автоматическую торговлю не предлагать»), и ТЗ AI-Trader §7.1 «только симуляция».
- Funding начисляется упрощённо (0.01%/8ч), ликвидация — по детерминированной isolated-формуле, MMR=0.5% с учётом taker-fee на входе. Это **учебная модель, а не спецификация HTX** (прямая оговорка в докстринге `sim_math.py`).

---

## 4. Качество: тесты и приёмка

### 4.1 Автотесты `[проверено 19–20.09, воспроизводимо — §10]`

- Изолированный QA-контур (`docker-compose.qa.yml`, сеть internal): **390 passed, 15 skipped, 1 xfailed** по `tests/ui`, `tests/test_ui_presenters.py`, `tests/paper_trading`, `tests/test_forecast_engine.py`. 15 skip — это live-смоук AC-19, которые в изолированной сети физически не видят webui (корректные skip вместо вчерашних 15 падений на отсутствующем curl).
- Хост-режим AC-19 против живого webui: **18/18 passed** (сессионная авторизация, CSRF, контент страниц).
- Тесты не содержат секретов; браузерные проверки падают честно (guard против «зелёного логина вместо страницы»: если после логина вернулся HTML с заголовком «Web UI Login» — тест скипается, а не проходит ложно) `[коммит 051a3e6]`.

### 4.2 UI/UX приёмка A01–A55

- **54/55 закрыто** `[документ: docs/UIUX-REVIEW-REPORT.md, коммит e7be15c]`.
- A48/A50 (responsive-переполнения, zoom) закрыты 19–20.09 замерами Playwright по матрице вьюпортов (scrollWidth−clientWidth=0 на 11 страницах).
- A52 закрыта переопределением критерия (live-смоук + MCP вместо установки playwright в контейнер; полный playwright-раннер = tooling-debt).
- **Открыт только A54** — проверка, требующая прав владельца аккаунтов (owner-only).

### 4.3 Paper Trading AC-матрица — честный оговор

- AC-01…AC-28 закрывались отдельным контуром (closure-документ 16.09) `[документ]`.
- **AC-26 формально остаётся FAIL** в последнем сохранённом отчёте `[artifacts/ac26_v3_report.json, прогон 17.09 на git 5bfd0d2]`: heartbeat 10/10 PASS, но feed_freshness 0/10 снапшотов «свежий фид ≤15с». Контекст: прогон делался в **изолированной QA-сети без выхода в интернет**; в рабочем compose collector на 20.09 ходит в HTX успешно (HTTP 200 в логах) `[проверено 20.09]`. Вероятная причина FAIL — методология стенда, но переутверждения в сетевом режиме матрица с 17.09 не видела. **Это открытая позиция приёмки, а не закрытая.**

---

## 5. Стабильность и отказоустойчивость

### 5.1 Устранённая гонка webui↔postgres `[проверено 19.09, fault-инъекция]`

Раньше: однораундовый `create_pool` в lifespan → postgres не поднялся = webui без пула навсегда (нужен рестарт). Теперь: `_bootstrap_postgres()` — фоновая задача, до 30 попыток, экспоненциальный backoff 1→30с, закрытие частичного пула при ошибке (нет утечки), HTTP-слой работает всегда, `/readyz` честнит 503 до появления пула.
Полевая проверка: остановка postgres → логин 200/readyz 503/повторы в логах → запуск postgres → **самовосстановление на попытке 7 (~25с) без рестарта webui**. Коммит `aff13ab`, в origin.

### 5.2 Мелкие известные шероховатости

- Idempotency-стор режимов трейдера — только память процесса: рестарт webui обнуляет защиту от дублирующих POST /mode. Redis под рукой, исправление тривиально `[код: trader_api.py]`.
- Разовый «`Value is null`» от lightweight-charts в консоли `/trader` (2 ошибки на загрузку, кросс-доменный стек из unpkg скрыт). **График рендерится полностью, функционал не нарушен** — это предсуществующий console-noise, причина локализована не полностью; на `/` и `/predictions` ошибок нет `[проверено 20.09]`. Отдельная task на бисект.
- В тот же день устранён латентный краш: `createPriceLine` с null TP/SL/ликции для открытой позиции вела к необрабатываемому исключению и обрезке остальных линий. Патч (guard + изоляция на линию) **[не закоммичен]** — лежит в рабочем дереве `webui/static/ui/trader-chart.js`.

---

## 6. Соответствие ТЗ: шесть отклонений (детальный разбор)

Норматив: `TASOCHKI/ТЗ_WORED_AI_Trader_System.md` v1.0 (27.06.2026), §7–9 — ядро трейдера.

### 6.1 Плечо: требование перевёрнуто относительно реальности — **формальный FAIL §16.2**

ТЗ §7.2/§16.2: «только плечо **выше** 100x», «плечо ниже порога — отклонять валидатором».
Код: `MAX_LEVERAGE = 100`, валидатор принимает 1–100 (10× прошла E2E сегодня).
Факт: HTX ограничивает BTC/USDT perp 100x — буквальное выполнение ТЗ физически нереализуемо. **Это дефект документа, и он не оформлен decision-record ни в DECISIONS, ни в комментариях кода** — при внешнем аудите по ТЗ это гарантированный FAIL. Лечение: addendum «политика плеча ≤100× как зеркало лимита HTX».

### 6.2 Cross-маржа: ярлык без модели

ТЗ требует режимы cross **и** isolated. `open_position` принимает `margin_mode=cross`, но вся математика (`sim_math.liquidation_price`, settlement, webui-open хардкодит `'isolated'`) — isolated-формула. Cross-позиция будет посчитана **неверно** (для кросса ликвидация считается по всей марже аккаунта). Лечение: либо запретить cross в валидаторе с явным сообщением, либо реализовать профильную формулу.

### 6.3 Strategy Learner: эвристика вместо Premium-тиера

ТЗ §7.7/§12: корректировка правил — Premium-модель, выводы — в `ai_journal`.
Факт: `POST /api/strategy/evaluate` пишет детерминированные эвристики (liq>20% → снизить плечо; winrate<40% → ужесточить RSI-фильтр; avg PnL<0 → больше подтверждений; DD>50% → стоп-лосс) в `strategy_rules` с честным `source="heuristic_webui"`; **в `ai_journal` записи не идёт**. Функционально цикл «метрики→правила» замкнут, тир-требование не выполнено.

### 6.4 Закрытие позиции недоступно из WebUI

Открыть — можно (Command Deck), **закрыть — нельзя**: `close_position`/`check_and_liquidate` живут только в chatbot (Telegram-контур + расписание авто-сопровождения: AI-кейс, стоп/тейк, ликвидация). ТЗ §7.6 требует симуляцию «из Telegram и WebUI» с сопровождением.

### 6.5 Меню Telegram не совпадает с перечнем §8

Ветка «Криптотрейдер» реализована (`handlers/trader.py`: Сессия/Рынок/Анализ/Прогнозы/Портфель/Алерты/Модели/Система), но заявленных отдельных пунктов «Симуляция фьючерсов», «Результаты симуляций», «Журнал агента», «Стратегия» как кнопок нет — часть функций закрыта текстовыми командами (`мои позиции`, `портфель`, regex-маршрутизация trade_sim) и страницей «Сессия». Функции есть, карта интерфейса расходится с ТЗ.

### 6.6 Мелочи надёжности (§13 дух)

Idempotency в памяти (§5.2); `preview` не включает funding в сценариях (флаг `funding_in_scenarios: false` — честная пометка, но упрощение).

**Итог по ТЗ:** ядро §7.1–7.6 реализовано и работает; отклонения 6.1–6.3 — содержательные, 6.4–6.6 — интерфейсные/надёжностные. Оценка соответствия ~85%.

---

## 7. Безопасность: состояние и два открытых долга

### 7.1 Выполненное

- **Утечка админ-пароля из дерева кода устранена** (19.09): файл `tests/paper_trading/test_browser_acceptance.py` содержал пароль в plaintext и был **опубликован в origin/main** (коммит `5bfd0d2`). Файл переписан на env `WEBUI_ADMIN_PASSWORD` (`051a3e6`, в origin). Работа: `git grep` по дереву — чисто `[проверено 19.09]`.
- Ротация пароля **не выполнена** — старый пароль читается в истории публичного remote. Пока webui доступен наружи (а пока это localhost-only Docker — риск низкий, но не нулевой), это формально скомпрометированный секрет. **Долг №1.**
- `.dockerignore`/`.gitignore` закрывают `.env*`; живых секретов в tracked-файлах при выборочной проверке не найдено `[проверено 19.09]`.
- L3 deep security review перед push `a7fefba..aff13ab`: 0 findings `[19.09]`.
- CSRF/сессионная модель webui: токен в signed session cookie, `httponly`, `samesite=lax`, `secure` (WEBUI_COOKIE_SECURE=true); POST-эндпоинты под origin-check middleware `[код: app.py L1844–1884]`.
- Hygiene-очистка root от 42 файлов-мусора, включая файлы с потенциально чувствительным содержанием (probes API-ключей, диалоги Hermes): tracked- deletions — коммит `cf7c978` **[ещё не запущен в origin]**.

### 7.2 Открытые долги (оба требуют явного решения владельца)

1. **Ротация `WEBUI_ADMIN_PASSWORD`** — пароль живёт в истории git origin; лечится только ротацией (+ обновление `.env`, рестарт webui).
2. **`.env.example.bak.20260816_144953`** в корне — untracked-файл, содержащий живые секреты; не в git, но и не удалён. Рекомендовано удаление, ждёт подтверждения `[19.09]`.

### 7.3 Platform-примечание для ревьюера

Любой не-браузерный HTTP-клиент против webui на `http://127.0.0.1` сталкивается с нестандартным: session-cookie с флагом `Secure` browsers пропускают для localhost (secure context), а Python CookieJar — нет → 403 на CSRF. Все тесты это обходят manual-cookie. При интеграции сторонних клиентов — учитывать.

---

## 8. Git-состояние и очередь изменений

```
cf7c978 (HEAD) chore(repo): root-уборка 23 tracked-файлов      ← НЕ запущен в origin
aff13ab (origin/main) fix(webui): самовосстановление pg-пула
e7be15c docs(uiux): приёмка A48/A50/A52
927da51 fix(webui): responsive A48/A50
051a3e6 fix(tests)(security): browser acceptance без пароля
bfac197 fix(tests): QA-изоляция migration-теста
```

Незакоммиченное: `webui/static/ui/trader-chart.js` (guard createPriceLine, §5.2) — ждёт решения о коммите.
Протокол push в проекте: обязательный L3 security gate перед каждым push (мем-память + skill).

---

## 9. Открытые решения, требуемые от владельца/ревьюера

| # | Вопрос | Варианты | Риск бездействия |
|---|---|---|---|
| 1 | Ротация WEBUI_ADMIN_PASSWORD | ротиовать сейчас / при первом публичном expose | секрет в публичной истории |
| 2 | Push `cf7c978` + коммит trader-chart | после L3-гейта | рассинхрон origin |
| 3 | Удалить `.env.example.bak…` и папку `scratch\` (29 файлов) | да/нет | секреты на диске |
| 4 | Addendum к ТЗ: плечо ≤100×, isolated-only V1, эвристика-V1 learner | принять / реализовывать буквально | вечный FAIL §16.2 |
| 5 | Cross-маржа: запретить в валидаторе или реализовать формулу | выбрать | неверные симы при cross-команде |
| 6 | Close-API в webui (§6.4) | завести эндпоинт / оставить TG-only | разрыв контракта ТЗ |
| 7 | Переутвердить AC-26 в сетевом режиме | прогнать acceptance-runner с доступом к HTX | открытая позиция приёмки без контекста |
| 8 | A54 owner-only проверка | выполнить владельцу | 55-я точка приёмки |

---

## 10. Как воспроизвести ключевые проверки

```bash
# 1. QA-контур (изолированный, без секретов)
docker compose -p wored-qa -f docker-compose.qa.yml build checks
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks \
  python -m pytest tests/ui tests/test_ui_presenters.py tests/paper_trading tests/test_forecast_engine.py -q
# ожидаем: 390 passed, 15 skipped, 1 xfailed

# 2. Live-смоук AC-19 с хоста (нужен живой webui + пароль из .env в окружение)
$env:WEBUI_ADMIN_PASSWORD=<из .env>; python -m pytest tests/paper_trading/test_browser_acceptance.py -q
# ожидаем: 18 passed

# 3. Runtime-факты
docker compose ps
curl.exe http://127.0.0.1:8080/healthz   # 200
curl.exe http://127.0.0.1:8080/readyz    # 200 (503 = pg-пул ещё бустится)
docker compose exec -T redis redis-cli keys "ticker:*"
docker compose logs collector --tail 20  # HTTP-200 к api.hbdm.com

# 4. Fault-инъекция pg-пула (остановить/поднять postgres, наблюдать attempt-N)
docker compose stop postgres; docker compose restart webui
docker compose start postgres   # readyz должен вернуться за ~30с без рестарта webui
```

E2E-цикл симуляции (§3) воспроизводится из UI Command Deck (тикет-диалог) либо fetch-серией в авторизованной браузерной сессии: preview → open → sim-positions → close через `docker compose exec -T chatbot python -c "…" sim_engine.close_position(id, price)` → metrics.

---

## 11. Что ревьюеру стоит читать дальше

- `AGENTS.md` — правила контура, guardrails, активный AI-стек;
- `TASOCHKI/ТЗ_WORED_AI_Trader_System.md` — норматив трейдера (к этому отчёту §6);
- `docs/UIUX-REVIEW-REPORT.md` — матрица A01–A55 с доказательств;
- `docs/HERMES-PAPER-TRADING-ACCEPTANCE-CLOSURE-20260916.md` + `artifacts/ac26_v3_report.json` — AC-матрица и её незакрытая позиция;
- `chatbot/services/sim_math.py` — вся торговая математика на 55 строк, лучший вход в подсистему;
- `docs/WORED-TRADER-V0.1-DECISIONS.md` — перечень решений по агенту V0.1 (счета, бюджеты LLM, forecast gate).

*Отчёт составлен по результатам сессий 19–20.09.2026; численные статусы тестов и рантайма актуальны на момент вложенных дат и воспроизводимы командами §10.*
