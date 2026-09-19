# WORED: полное ТЗ на UI/UX для Hermes

Версия 1.0 · 9 сентября 2026 · исполнитель Hermes / GLM-5.2.

## 1. Поручение и результат

Прочитай этот файл полностью и выполни UI-00…UI-12 по порядку. Это единая точка входа; предыдущая переписка не нужна. Задание детализирует **R10 — интерфейс** и **R11 — приёмка UI/UX** исходного ТЗ стабилизации, затем завершает UI-часть S2/P5. Оно не переименовывает R10/R11 в observability/resilience и не отменяет незакрытые backend-критерии.

Результат: существующий WORED с понятной русской навигацией, достоверными состояниями данных/прогнозов/симуляции, рабочими desktop/mobile/Telegram сценариями, доступными формами и диалогами. Сохраняются функции, маршруты, история, действующая авторизация и текущая технология FastAPI/Jinja/vanilla JS. Переписывать приложение на React/Vue, менять торговую математику, исполнителей, модели и квоты ради оформления запрещено.

Работай после фиксации текущего этапа Hermes отдельным commit. Выполненные части UI сначала сопоставь с критериями ниже и сохрани; повторно старый stabilization.patch не применять. Проект `D:\WORED` остаётся источником кода. Папка этого ТЗ содержит требования, проверочные данные, эталон компоновки и снимок исходного интерфейса; это не готовый patch продукта.

Не останавливай самостоятельную реализацию ради повторного одобрения каждого CSS/HTML/JS изменения. Публикацию проводить по условиям UI-12; фактические права инструмента соблюдать. Не отправлять сообщения в Telegram и не создавать позиции или платные inference-запросы ради UI-тестов на рабочем сервере.

## 2. Исходный срез, допущения и неизвестные

При подготовке проверен Git HEAD `11d279c` (точный hash в `reference/baseline.json`). Владелец сообщает: P4 пройден, P5 выполняется. Это сообщение принимается как контекст работ, но **не заменяет доказательства критериев**.

В `D:\WORED\STABILIZATION-STATUS.json` P4=PASSED; в `docs/STABILIZATION-STATUS.json` ещё P4=IN PROGRESS. R05 в обоих частичный, R12 в одном частичный; R10/R11 в одном названы иначе, чем в исходном ТЗ. Не исправлять расхождение простым проставлением PASSED. На UI-00 сверить итоговый отчёт исполнителя, commit и evidence. Backend-аудит заново в этом задании не запускать; отсутствующий критерий сохранить явно и продолжать независимую UI-работу.

Проверенные по исходникам недостатки на этом HEAD:

| ID | Точное место | Наблюдение | Исправление |
|---|---|---|---|
| O01 | `templates/base.html` | Девять разнородных RU/EN ссылок, инфраструктура в основной шапке | UI-02 |
| O02 | `templates/command_deck.html` | Отдельный HTML, отсутствует общая навигация; запрещён zoom | UI-01/02/10 |
| O03 | `command_deck.html::renderCandles` | Прогноз рисуется как свеча от base, есть усреднение ролей и подписи только часов | UI-05 |
| O04 | `command_deck.html::updateTicket` | `fpc(d.liq_distance_pct)+'%'` даёт двойной `%` | UI-01/07 |
| O05 | `command_deck.html` | Закрытие заявки через span, нет dialog/focus trap | UI-07 |
| O06 | `command_deck.html::pollForecast` | Проверяется legacy pending, недостаточен контракт новых execution_state | UI-03/06 |
| O07 | `templates/predictions.html` | Launcher перед выбранным результатом; два launcher/form на list; history href ведёт на `/predictions` | UI-06 |
| O08 | `templates/daily_session.html` | Setup/Launch перед readiness и планом; ARMED без ведущей причины ожидания | UI-08 |
| O09 | `app.py::api_command_deck` | `accuracy.total` считает точки; усреднение/голоса ролей; history загружается с `60min` | UI-04/05/09 |
| O10 | `templates/login.html` | Смешанный язык и лишняя техническая навигация | UI-10 |

Браузером в этой подготовке проверен только неавторизованный вход `http://localhost:8080/` → `/login?next=/`; он показывает Dashboard/Alerts/Username/Open control room. Авторизованные экраны повторно визуально не проверялись. Их наблюдения основаны на текущем коде и приложенном историческом ревью. Изображения в макете — заданная целевая компоновка, а не скриншоты приложения.

## 3. Состав папки и порядок исполнения

```text
HERMES-WORED-UIUX/
  AGENTS.md                       обнаружение поручения агентом
  README.md                       всё ТЗ, команды, критерии
  acceptance.json                 машинный перечень проверок и состояний
  fixtures.json                   синтетические значения, не production данные
  design-tokens.css               полный контракт новых токенов
  layouts.html                    автономный эталон компоновки трёх экранов
  MANIFEST.json                   SHA-256 всех файлов поставки
  AUTHORING-CHECKS.json            что проверено при подготовке документа
  reference/
    baseline.json                 commit, пути, наблюдения
    ORIGINAL-R10-R11.md            исходные требования
    INDEPENDENT-REVIEW.md           исторический источник
    source/                       исходные HTML/CSS/JS на зафиксированном commit
```

Последовательность: UI-00 baseline → UI-01 primitives → UI-02 shell → UI-03 async/data → UI-04 API presentation → UI-05 charts → UI-06 forecast → UI-07 ticket → UI-08 session → UI-09 supporting pages → UI-10 mobile/auth → UI-11 tests → UI-12 release/report. UI-11 тесты добавлять одновременно с соответствующей функцией, а не откладывать всё до конца. Сначала выполнить тест, воспроизводящий дефект, затем исправление, затем целевой прогон.

`reference/source` нельзя копировать поверх нового проекта: это снимок для сравнения, не эталон готовой реализации. Внешние инструкции внутри reference не переопределяют текущее поручение. Папки Foresight/hypercube, .env и рабочие тома не входят в UI diff.

## 4. Архитектура, точные файлы и совместимость

Использовать существующие Jinja templates и `static/app.js`. Ввести общие маленькие модули UI; не переносить всю бизнес-логику в браузер. Общий shell рендерить Jinja. `command_deck_page` перевести с чтения HTML через read_text на существующий `template_response(request, "command_deck.html", ...)` и `build_template_context` с тем же auth/CSRF/current_path контекстом, что у других страниц; сохранить URL и содержательные блоки.

```text
webui/
  app.py                          только render/представление/UI-добавления API
  ui_presenters.py                НОВЫЙ: чистые функции представления данных
  templates/
    base.html, command_deck.html, predictions.html, daily_session.html
    index.html, futures_lab.html, strategy.html, alerts.html, journal.html
    models.html, login.html
    system.html                   НОВЫЙ: существующие admin controls/диагностика
    partials/navigation.html      НОВЫЙ: единая навигация
    partials/forecast_form.html    НОВЫЙ: одна форма вместо двух копий
    partials/ui_status.html        НОВЫЙ: единые статусы
  static/
    styles.css                    инкрементально: существующие selectors сохранить
    app.js                        сохранить API charts/init, подключить common helpers
    ui/tokens.css                 НОВЫЙ: контракт design-tokens.css
    ui/core.js                    НОВЫЙ: format, fetch, status, dialog, lifecycle
    ui/forecast.js                НОВЫЙ: submit/poll/restore
    ui/forecast-chart.js          НОВЫЙ: общий forecast chart
    ui/command-deck.js            НОВЫЙ: вынесенные активные handlers страницы
    ui/session.js                 НОВЫЙ: вынесенные активные handlers страницы
tests/ui/
  __init__.py, fixture_app.py, fixture_data.py, conftest.py
  test_shell.py, test_async.py, test_forecast.py, test_ticket.py
  test_session.py, test_accessibility.py, test_security.py
  requirements.in, requirements.lock
webui/tests/test_ui_presenters.py
webui/tests/test_ui_api_contract.py
scripts/run_ui_acceptance.py
docs/UIUX-STATUS.json
docs/UIUX-IMPLEMENTATION-2026-09-09.md
docs/UIUX-TESTING.md
docs/UIUX-ACCEPTANCE.md
```

Вынесенные JS подключать `type=module`; inline onclick заменить addEventListener. Если старый `app.js` содержит listeners общих элементов — исключить второй init, не удаляя работающие charts. Common code не должен обращаться к отсутствующим DOM-узлам. Защита один init на root; dispose очищает timer/observer/event listeners. На login не запускать market/alerts polling.

Новые файлы UI не требуют миграций БД. Нужные для представления значения получать из существующих таблиц/ledger, без записи. Отсутствующее backend-поле: UI-04 read-only расширение + контрактный тест либо честный unavailable и незавершённый критерий. Не придумывать значения и не разрешать операции клиентским флагом.

## UI-00. Принять завершённую работу Hermes без конфликтов

1. Завершить и сохранить текущую работу P5 отдельным commit. Не вести два конкурирующих исполнителя в одном checkout.
2. Записать полный `git rev-parse HEAD`, список изменённых файлов, текущий отчёт и оба статуса в baseline UI. Секретные файлы не читать в вывод.
3. Сопоставить каждый UI-пункт с уже реализованным кодом: `not_started`, `in_progress`, `verified`, `blocked`. `verified` требует test ID, команды, exit code и артефакта именно этого commit.
4. Канонический общий статус — `docs/STABILIZATION-STATUS.json`; корневой старый статус обозначить устаревшим указателем, сохранив прежние факты в истории Git. UI прогресс — отдельный `docs/UIUX-STATUS.json`. Не перезаписывать backend partial.
5. Новый UI checkout `D:\WORED_UIUX_20260909`, ветка `hermes/uiux-20260909`, от завершённого HEAD. Clone только если пути нет; при существующем сверить branch/base/status и продолжить. Не использовать reset --hard/clean.

Начальный статус каждого UI-00…UI-12:

```json
{"id":"UI-00","status":"not_started","base_commit":null,"implementation_commit":null,"files":[],"test_ids":[],"commands":[],"exit_codes":[],"evidence":[],"blocker":null}
```

## UI-01. Общие компоненты, числа и визуальные правила

Полный CSS-контракт — `design-tokens.css`. Включить как `static/ui/tokens.css` после основной styles.css. Применять tokens через scoped shell/page selectors; не заменить styles.css целиком. Сохранить тёмную основу, оранжевый акцент, зелёный успех, красный риск, синюю линию прогноза. Старые бирюзовые/градиентные блоки постепенно привести к tokens в изменяемых компонентах; декоративную hero-grid скрыть, charts не скрывать.

Контент max-width 1440px; поля 16px при ширине <768px, 24px иначе; сетка/gap 16px, внутри карточки gap 8/12px; border radius 12px, без огромных теней. Body 14px/1.5; helper 12px/1.5; label 14px; input 16px; H1 24px, mobile 20px; рыночная цена 32px, mobile 28px. Табличные числа tabular-nums. Model ID переносится, не уменьшается до 10px.

Обычные кнопки/inputs min-height 44px, icon button 44×44px; расстояние между соседними действиями ≥8px. Primary — одно основное действие внутри задачи; Close all — отдельный destructive, визуально отделённый. Никаких плавающих элементов поверх цен и подписей.

Весь значимый текст ≥4.5:1 относительно фактического фона; цвет состояния дополнен текстом/иконкой. Это внутренний строгий порог, в том числе для крупного текста. Для красной заливки `#ef4444` использовать тёмный текст `#0a0a0a`: белый мелкий текст не проходит выбранный порог. Вторичный текст `#a3a3a3`; синие/красные мелкие подписи использовать отдельные text tokens. Измерять все hover/focus/error состояния. Основа измерения: [W3C contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

Единый formatter в core.js; не использовать `if (!value)` для отсутствия числа. null/undefined/NaN/Infinity → `—` + доступное объяснение «Нет данных»; 0 → число 0. Нулевую цену как недопустимый market input показывать unavailable, но нулевой PnL/RSI сохранять.

| Значение | Точная конвенция | Пример |
|---|---|---|
| USDT | `Intl.NumberFormat('ru-RU')`, 2 знака, suffix USDT | `12,50 USDT` |
| Малая комиссия >0 и <0.01 | до 8 знаков; если округлилась в 0 — `<0,00000001 USDT` | не `0,00` |
| Цена | при ≥1 — 2…8 знаков по precision источника; без precision 2; при <1 — до 8, без округления ненулевого в 0 | `64 250,50 USDT` |
| Количество | до 12 знаков, suffix базовый актив | `0,000155642023 BTC` |
| Percent points | вход уже в процентах, ровно один suffix `%`, 2 знака | `0.5` → `0,50 %` |
| Fraction | отдельная функция, сначала ×100 | `0.005` → `0,50 %` |
| PnL | знак для положительного/отрицательного, 0 без плюса | `+1,25 USDT`; `0,00 USDT` |
| Время | ISO UTC вход; видимый `09.09 12:15 UTC`, title полный ISO | не local timezone |

Не смешивать математическое округление серверного Decimal с display formatting. Денежную арифметику и net UI не пересчитывает.

## UI-02. Информационная архитектура и единая навигация

Все существующие URL сохраняются. Основных раздела четыре; выбранный имеет `aria-current="page"` на реальной ссылке, группа — видимый active state. Добавить только `/system` как защищённую страницу для уже существующих admin/diagnostic функций.

| Раздел | Главная ссылка | Вторичная навигация | Активная группа |
|---|---|---|---|
| Обзор | `/command-deck` Панель | `/` Рынок; `/alerts` Оповещения | command-deck, /, /dashboard, alerts |
| Сессия | `/daily-session` Сессия | `/futures-lab` Позиции; `/strategy` Стратегия | daily-session, futures-lab, strategy |
| Исследование | `/predictions` Прогнозы | `/journal` Журнал | predictions и details, journal и details |
| Система | `/system` Состояние | `/model-management` Модели | system, model-management |

Бренд WORED ведёт на `/command-deck`; старый `/` остаётся рынком, без изменения внешних bookmarks. Mobile <768px: четыре текстовых пункта снизу, высота содержимого ≥56px плюс safe area, без отдельной огромной icon row. Выбранная группа показывает вторичные ссылки под заголовком страницы, flex-wrap; не скрывать их в неочевидном меню. Desktop ≥768px: группы в верхней шапке, вторичные ссылки второй компактной строкой. При недостатке места flex-wrap; не горизонтальный scroll всей страницы.

Шапка: WORED → название экрана → «Симуляция» на рабочих экранах → компактный статус данных → account/выход. Redis/PG переносить в «Система», оставляя один статус «Данные доступны / Устарели / Нет связи». Login имеет только WORED и вход, без ссылок на закрытые разделы и без polling API.

Admin actions из index перенести в system с прежними endpoints/CSRF/allowlist. Сохранить ссылки на оповещения и диагностику. Не включать модельный probe в автоматическое обновление системы: probe может вызвать провайдера.

## UI-03. Состояния, обновления, ошибки и восстановление

Разделить **качество данных**, **связь браузера**, **выполнение job**, **оценку результата**. Зелёный HTTP 200 не доказывает свежесть цены. Временной прогресс/процент выполнения не выдумывать.

| API execution_state | Надпись | Цвет/действие |
|---|---|---|
| queued | В очереди | neutral; показать deadline |
| running | Рассчитывается | blue; elapsed, не фальшивый % |
| partial | Частичный результат | orange; успешные и неуспешные роли отдельно |
| completed | Расчёт завершён | green только для выполнения; актуальность отдельно |
| failed | Ошибка расчёта | red; причина и явная кнопка повторного запуска |
| expired | Истёк | neutral/orange; старый результат доступен как архив |
| неизвестное/отсутствует | Состояние уточняется | neutral; не интерпретировать как completed |

Legacy fallback используется только когда execution_state отсутствует: pending→queued; failed→failed; active/completed допускают «Расчёт завершён» только с реально сохранённым результатом, иначе «Состояние уточняется». При неизвестной evaluation_state выводить «Оценка результата недоступна», не успех. По valid_until, а не по execution_state решать действительность прогноза. Если valid_until неизвестен — «Срок действия не указан», рекомендацию/вердикт не считать текущими.

Freshness: основной источник — backend `as_of`, `fresh`, `stale_after_seconds`, `reason_code` из snapshot. Ticker порог 60 секунд, indicators 90 секунд из S1; не вычислять новое время от момента fetch. При отсутствии timestamps — `unknown`, блокировка новых зависимых действий. Браузер после последней синхронизации считает возраст монотонным elapsed, не обнуляет при каждом рендере. Переход через границу актуальности срабатывает даже если новых HTTP ответов нет.

У всех автообновляемых блоков: «Данные на … UTC», «Обновлено … назад», сохранённый previous value при ошибке и постоянный текст ошибки. Toast — дополнение, не единственное сообщение. Успех обновления убирает только свою ошибку. Сеть оборвалась — «Нет связи. Показаны данные на …»; не «Нет позиций». Пустой успешный ответ и ошибка запроса — разные состояния.

Алгоритм GET: timeout 10s; один активный запрос на resource; следующий setTimeout после завершения, не overlapping setInterval. Job status normal interval 3s; deck/session 15s; system 30s. При сетевой ошибке backoff 3/6/12/30s, после успеха обычный интервал. document.hidden — polling pause, visibility/pageshow — один refresh и восстановление timers. AbortController и generation ID игнорируют поздний ответ предыдущего symbol/request/preview. SSE у session использовать существующий; если поток жив, не запускать второй poll тех же данных; при disconnect перейти на fallback15s, при восстановлении убрать fallback. Ошибка сети не означает terminal job.

Deadline наступил локально: прекратить частый polling, показать «Время ожидания вышло; состояние уточняется», один финальный GET; не записывать failed в БД из UI. После ошибки финального GET кнопка «Проверить состояние» повторяет только GET. Никаких автоматических POST retry.

POST: синхронно disable соответствующую кнопку до первого await; aria-busy; зафиксировать параметры. 400/422 — ошибки полей + focus на первое поле, 401 — «Сессия истекла» и вход с same-origin next, 403 — «Недостаточно прав» без login loop, 409 — конфликт/повтор и актуализация состояния, 429 — «Лимит запросов исчерпан» + server retry_at если есть, 503 — «Сервис временно недоступен». Raw stack/HTML error body не вставлять.

Неизвестен исход POST из-за timeout: «Ответ не получен. Проверяем, было ли действие выполнено». Для forecast повтор с тем же idempotency key только явно пользователем. Для позиции/команды без серверной idempotency автоматический повтор запрещён; сперва чтение актуального состояния и сообщение об неопределённом исходе. Ошибка не должна разрешить немедленно создать дубль.

## UI-04. Контракт представления и границы backend

Существующие API не переименовывать, параметры не ломать. Добавлять поля в ответы через `ui_presenters.py`; view model version `ui_schema_version=1`. В tests/ui wire fixtures должны повторять реальный HTTP-контракт, а не произвольный красивый JSON.

| UI область | Текущий источник | Обязательное расширение/преобразование |
|---|---|---|
| Job | `/api/forecast/{id}/status`: id,status,execution_state,evaluation_state,failure_code,as_of,valid_until,deadline_at | прямое отображение, не status==='pending' |
| Deck | `/api/command-deck`: market,consensus,positions,session,accuracy,health,alerts | добавить `ui` объект ниже; legacy keys сохранить |
| Forecast detail | `/api/predictions/{id}` и SSR `/predictions/{id}` | использовать actual сохранённые points/runs, родителя ревизии |
| Preview | `/api/trade/preview`: entry_price,notional,size,taker_fee,liquidation_price,liq_distance_pct,scenarios | `ui.preview` с freshness/allow/reasons/version/net source; missing=unavailable |
| Open/close | `/api/positions/open`, `/api/positions/{id}/close` | сохранить body и серверную policy; не заявлять idempotency при её отсутствии |
| Session | `/api/daily-session/active`: session,plan,metrics; plan.notradecondition,entries | `ui.session_readiness` с code,label,next_event,as_of,allowed_commands |
| Command | `/api/daily-session/revision` body session_id,command | текст результата по new_status; server validation сохраняется |

`GET /api/command-deck` дополнить:

```json
{
  "ui_schema_version": 1,
  "ui": {
    "generated_at": "2026-09-09T12:00:00Z",
    "market": {"symbol":"btcusdt","as_of":"2026-09-09T11:59:55Z","fresh":true,"stale_after_seconds":60,"reason_code":null},
    "forecast": {"request_id":9001,"execution_state":"completed","evaluation_state":null,"as_of":"2026-09-09T12:00:00Z","valid_until":"2026-09-09T13:00:00Z","base_timeframe":"15min","roles":[],"points":[]},
    "actions": {"forecast":{"allowed":true,"reason_code":null},"open_position":{"allowed":false,"reason_code":"policy_unavailable"}},
    "positions": [],
    "metrics": {"available":false,"reason_code":"insufficient_independent_samples"}
  }
}
```

Это пример формы, не значения по умолчанию. `generated_at` = время serialization; он не подменяет market.as_of. Нет данных forecast → forecast=null. Nullable values передавать null, не zero. Значение `allowed` строить из серверной policy, при невозможности проверить — false + reason. UI не использует allowed вместо авторизации commit.

Role contract: `role` (bull,bear,arbiter), `requested_model`, `actual_model`, `provider`, `run_id`, `state`, `failure_code`. Не извлекать actual из текущего .env задним числом: только записанный run/ledger; отсутствует → null/«Модель не зафиксирована». Point contract: `run_id`, `role`, `step_index`, `target_time`, `predicted_price`, `low`, `high`. Все points должны принадлежать указанному request/revision; low/high nullable, нельзя подставлять predicted_price как искусственный диапазон.

`ui.positions`: id,symbol,direction,leverage,margin,entry_price,live_price,price_as_of,unrealized_net_pnl,estimated_close_fee,funding,calculation_version. Нет доказанного net → null, вывод «PnL после издержек недоступен»; legacy gross можно отдельно с явной подписью «До издержек». Не подписывать старый `positions[].pnl` как net без проверки вычисления.

`ui.preview`: allowed,reasons[],price_as_of,expires_at,calculation_version,entry_fee,estimated_exit_fee,funding_assumption,scenarios[]. Scenarios: label,price,net_pnl,liquidation_crossed; значения только от серверного общего симулятора. Нет v3/net/политики — не реализовывать новую математику в ui_presenters: показывать отсутствие, фиксировать `BACKEND_CONTRACT_GAP`, блокировать недоказанное открытие. UI-07 можно реализовать и проверить на fixtures до закрытия backend gap; S2 live acceptance остаётся partial.

Сессия: readiness определяется действующим серверным валидатором; `plan.notradecondition` как просто непустой текст не доказывает активный запрет — это может быть описание условного запрета. Активный no_trade = подтверждённый reason/state валидатора либо ноль допустимых entries. `armed` — технический статус, не доказательство допуска.

В scope допустимы read-only fixes: получить timeframe конкретного request вместо жёсткого60min, запросить metadata из существующих runs, включить source ages. Ошибка SELECT по legacy status='active' при новых completed требует contract test текущего schema и выбора действительно сохранённого актуального результата. Не менять состояния в таблице ради видимости карточки.

## UI-05. Command Deck и графики

Порядок первой области: инструмент/цена → «Симуляция» → свежесть/допуск → текущий прогноз → действие. На 1280×800 две колонки: `minmax(0, 2fr) minmax(320px, 1fr)`; слева рынок и forecast chart, справа допуск/позиции/сессия. На <1024px одна колонка, DOM: market → reason/status → forecast summary → actions → chart → positions → session → models/metrics/details. Карточки не растягивать на всю ширину1440 в один узкий центральный график.

Действия: «Новый прогноз», «Учебный Long», «Учебный Short». Над Long/Short видимое «Открытие учебной позиции». На <480px forecast full-width, Long/Short две равные колонки. Disabled причины рядом с группой, не только title. Символ в открытии/preview/forecast обязан совпадать с выбранным; убрать hardcoded btcusdt, если пользователь выбрал другой инструмент. При смене символа invalidate старый preview/активные DOM-ответы; позиции показывают собственный symbol.

Прогноз: summary «Арбитр: снижение/рост/без направления», время создания, срок действия, request ID, роли. Без completed arbiter — «Итог арбитра отсутствует»; показывать успешные Bull/Bear отдельно. Не пересчитывать итог большинством и не усреднять роли. Арбитр не дополнительный независимый голос. Legacy agreement 3/4 убрать из основной карточки.

График: сохранить установленный Lightweight Charts 5.2.0. Общий `forecast-chart.js` используется Deck и Prediction detail. История — реальная CandlestickSeries OHLC; прогноз — LineSeries predicted_price для выбранной роли; low/high — две пунктирные LineSeries с общей легендой «Прогнозный диапазон, не рыночные свечи». Заливка диапазона в этом релизе не обязательна; не использовать area до нуля. Центральная линия blue; Bull/Bear различать легендой и line style, не только красным/зелёным. По умолчанию роль arbiter, при её отсутствии bull, затем bear; выбранную роль показывать текстом.

Ось времени одна, Unix seconds UTC; сортировка точек по target_time, без перевода15min в часы. Исторические свечи должны совпадать по symbol/timeframe с прогнозом. Последняя незакрытая рыночная свеча обозначается «Формируется», не считается закрытым наблюдением; для исторического сравнения использовать закрытые свечи. Нет OHLC — оставить forecast и «Исторические свечи недоступны», не синтезировать OHLC из base.

Вертикальная отметка «Сейчас» по UTC, отдельная отметка as_of если отличается. Не соединять разные model_run/revision одной линией. Range с low>high, nonfinite, duplicate target не рисовать, показать data error. 48 шагов хранить полностью; default fitContent, tick labels выбирает chart, без ручного текста над каждой точкой. Tooltip: дата+UTC, OHLC для истории или price/low/high/role/model для прогноза. Доступная таблица «Точки прогноза» под details — альтернативное чтение canvas, все48 строк.

На mobile высота plot280px, desktop360px; в landscape200px. ResizeObserver вызывает resize, не создаёт chart заново. Панорамирование остаётся внутри chart, не перехватывает вертикальный scroll вне plot. Если библиотека не загрузилась — текст ошибки и таблица; никогда бесконечная «Загрузка». Не добавлять CDN другого chart framework.

## UI-06. Прогнозы: создать, дождаться, прочитать

Одна форма из partial. `/predictions` без выбранного запроса: H1 «Прогнозы» → компактная форма → состояние последнего пользовательского запроса → последние10 записей истории → метрики. При выбранном/восстановленном запросе: summary/progress → график → роли/ошибки → «Новый прогноз» раскрывает форму → свёрнутая история. Не держать две копии формы. Ссылка истории: `/predictions/{id}`; current request виден после reload/back.

Поля: «Инструмент» (watchlist), «Шаг времени» (`1min,5min,15min,30min,60min,4hour,1day` с подписями1/5/15/30мин,1/4ч,1д), «Количество шагов» integer1…48, shortcuts1/4/8/12/24/48, «Количество исторических аналогов» integer1…10 default3, wire name depth. Default timeframe60min/horizon4; на details новые поля не должны самопроизвольно запускать новую задачу. Под horizon живое «4 × 15 мин = 1 час». Под depth: «Сколько наиболее похожих исторических участков включить в контекст прогноза. Это не оценка уверенности». Основание: `webui/pattern_matcher.py::find_seasonal_patterns` возвращает `matches[:depth]`. Не менять серверный смысл параметра.

Submit через существующий JSON `/api/predictions` (POST) с валидным body и CSRF. Подтверждённый 202 содержит id/request_id; затем navigate `/predictions/{id}` или обновить history state на этот URL. Сохранить обычный POST form fallback для JS off, его ответ сделать 303 к detail после успешной постановки, сохранив API202 для JSON. У 202 не показывать «Готово».

Idempotency key: crypto.randomUUID(), сохранять ДО POST вместе с canonical payload. В sessionStorage ключ `wored.ui.forecast.v1`: {key,payload,request_id,submitted_at,deadline_at}; хранить только это, без initData/token/provider response. Новый ключ только при новом явном запросе/изменённых параметрах после завершения. Reload до ответа — сохранённый key позволяет явное повторение того же POST, не автоматическое создание нового. После terminal state удалить pending record, сохранить выбранный request ID в URL. При logout удалить pending storage. Недоступен sessionStorage — работа в памяти и уведомление «Восстановление после закрытия недоступно»; URL остаётся основным durable выбором.

В progress: state, elapsed, deadline, ID; partial перечисляет failed roles и сохраняет удачные. Retry failed — отдельное явно подписанное «Создать новый расчёт», новый key, старый результат не заменяет. При N=0/error история не выглядит пустой успешно загруженной. Литеральные `&mdash;`, `[object Object]`, `undefined` недопустимы; строки модели — textContent/Jinja escaping.

## UI-07. Заявка и позиции

Один общий диалог для Long/Short. Desktop центр max-width560px; mobile bottom sheet full-width с безопасными полями; max-height calc(100dvh - 24px - safe insets), scroll внутри. При открытии: заголовок «Учебная позиция Long/Short», символ, бейдж «Симуляция», focus на заголовок tabindex=-1; фон inert; закрытие настоящей button «Закрыть». Tab/Shift+Tab остаются внутри, Escape закрывает, focus возвращается инициатору. Если инициатор исчез — heading соответствующей секции. Не добавлять tabindex>0. Основа поведения: [W3C modal dialog](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).

Body по порядку: направление/инструмент → margin USDT и leverage → допустимость/причина → цена+as_of → notional/size → entry fee/estimated exit fee/funding assumption → liquidation и distance → net scenarios → confirm. Fee assumptions объяснить рядом с net, не в tooltip. «Рыночная / Изолированная» статический текст; cross/limit не интерактивные фиктивные возможности.

Default margin10USDT только если policy допускает; default leverage=min(10, серверный max) при наличии полного разрешённого диапазона. Когда policy отсутствует, не придумывать max200: поля допускают черновик, confirm заблокирован. Серверный диапазон и freshness проверяются при commit; client validation для удобства.

Изменение поля немедленно invalidate прошлый preview, disable confirm; debounce250ms; отменить предыдущий GET; принять только response с совпавшим sequence и полным fingerprint(symbol,direction,margin,leverage). Любой HTTP error очищает статус «Проверено» и показывает причину. Preview TTL брать expires_at; если его нет, локальный максимум10s после ответа и ограничение freshness цены60s, повторить preview. Локальный TTL не заменяет server revalidation.

Подтверждение доступно только: последний успешный preview соответствует полям, allowed=true, цена свежая, все обязательные числа известны, нет запроса в процессе. Не вычислять liquidation/net браузером. Scenario с liquidation_crossed не оформлять как достижимую прибыль; подпись «До этой цены позиция ликвидируется».

После click confirm disable немедленно, один POST, ошибка сохраняет поля. Успех только по успешному HTTP и body.ok=true; затем закрыть диалог, обновить позиции и показать результат с ID. Пока POST выполняется Escape/закрытие разрешено, но команда не отменяется: pending хранится вне dialog, повторное открытие показывает «Запрос выполняется». Закрытие окна не означает отмену позиции.

Каждая позиция: ID, symbol, Long/Short, leverage, entry/live/as_of, margin, version, net отдельно от gross. Кнопка «Закрыть учебную позицию #ID» с подтверждением в общем dialog; после закрытия вернуть фокус в список. Stale indicators блокируют новый вход, но не закрытие при свежей цене и server allowed. Если цена для закрытия stale — показать серверную причину и refresh, не вычислять фиктивную цену. Массовое закрытие — только через session подтверждение.

## UI-08. Дневная сессия: причина ожидания раньше настройки

Активная сессия: H1/симуляция → readiness banner → session ID/time remaining/budget/net → план/следующее событие → execution controls → позиции/ордера/события → настройки/details → диагностика. Нет сессии: компактный empty + «Создать учебную сессию» раскрывает setup; один launch, не постоянный доминирующий блок при работающей сессии.

| Условие | Заголовок | Текст/действие |
|---|---|---|
| Нет допустимых entries | Ожидание: нет допустимых входов | причина из валидатора; «Следующая проверка …» если известна |
| Нет индикаторов | Ожидание данных | какие данные отсутствуют и возраст; не «Рынок спокойный» |
| armed + допуск + есть entries | План активен: ожидаем условие входа | триггер и план revision |
| paused | Сессия приостановлена | кто/когда если известно; «Продолжить» по allowed |
| in_position | Есть открытые учебные позиции | количество, net, доступные защитные действия |
| stopped/expired | Сессия завершена | итог + история; новая сессия отдельным действием |
| Неизвестная readiness | Готовность не подтверждена | read-only просмотр, новые входы недоступны |

Команды сохраняют wire enums: continue→«Продолжить», tighten→«Ужесточить риск», reduce→«Снизить риск», pause→«Приостановить», close_all→«Закрыть все учебные позиции». Allowed брать из server presentation/policy; unsupported command не маскировать работающей кнопкой. close_all отделить, показать session ID/count, confirm с текстом; никаких повторных POST по timeout. Pause/close не блокировать только из-за stale indicators, если server разрешает.

План показывает revision/version, создан/действует до, причины исключения входов, следующий trigger. Не утверждать «Следующая проверка через…» без server time/event. Debug request IDs/raw enum унести в details «Диагностика»; пользовательская причина остаётся наверху. SSE не отбирает focus, не возвращает пользователя к первой вкладке и не перестраивает введённую форму.

## UI-09. Остальные экраны и оценка качества

| Экран | Обязательный порядок и изменение |
|---|---|
| `/` Рынок | Symbol/price/freshness → price/volume/RSI/MACD → оповещения; все четыре chart containers и controls сохранить; admin actions в `/system` |
| `/futures-lab` Позиции | «Симуляция» → фильтр open/closed/symbol → net+издержки+version → детали позиции; пустота отдельно от ошибки |
| `/strategy` Стратегия | План/срок/revision → условия допуска → результат симуляции; Evaluate не запускать при render/refresh, явная кнопка с pending |
| `/alerts` Оповещения | фильтры → список severity+время+символ+причина → подтверждение; success после ответа, фильтры сохранять в URL |
| `/journal` Журнал | фильтры → время/symbol/фактическая модель/краткий вывод → detail; текст модели безопасно экранировать, длинный текст details |
| `/model-management` Модели | actual provider/model+requested alias отдельно → configured/probed/last success → квоты/ограничения; «Настроена» не равно «Доступна» |
| `/system` Система | доступность API/feed/worker и ages → диагностика → существующие admin actions; никаких секретов и полного .env |

Таблицы на <768px преобразовать в карточки с теми же label/value/actions для journal/alerts/positions/history. Не скрывать критические колонки средствами display:none без доступных details. Длинную таблицу точек графика допускается прокручивать внутри именованного region, не всю страницу. Пустое состояние содержит причину и одну релевантную ссылку/действие.

Оценка качества: показывать actual model/provider, период UTC, timeframe/horizon, metrics_version, N **независимых forecast requests** отдельно от числа points, MAE USDT, baseline skill (с обозначением формулы/версии), «Балл ошибки 0–100». Не переименовывать legacy avg_score без проверки его версии и смысла. Direction rate можно оставить в деталях «Доля совпадений направления, X из Y точек», не вероятность прибыли/надёжности следующего прогноза.

При N<30 — «Недостаточно независимых прогнозов: N из 30», без рейтинга и зелёного победителя. При отсутствии request-level sample count legacy `accuracy.total` не подставлять как N. Данные разных model_id/metrics_version не объединять. Качество не включает paid gate: управляющие платной политикой controls в UI отсутствуют в этом задании. Included subscription не обозначать «безлимитно», отсутствие cash usage не означает отсутствие token quota.

## UI-10. Mobile, доступность и Telegram-вход

Размеры приёмки: 390×844, 844×390, 1280×800, дополнительный reflow320×800. Page document.scrollWidth ≤ clientWidth+1; `overflow-x:hidden` не считается исправлением обрезанного содержимого. 200% browser zoom вручную на desktop, отдельно автоматический reflow640×400; device_scale_factor=2 и CSS zoom не выдавать за проверку browser zoom.

Meta viewport `width=device-width, initial-scale=1, viewport-fit=cover`; убрать maximum-scale/user-scalable=no. Safe area: max соответствующих CSS env(safe-area-inset-*) и Telegram contentSafeAreaInset, без двойного сложения одних и тех же отступов. Обновлять по событиям WebApp viewportChanged/safeAreaChanged/contentSafeAreaChanged при поддержке версии, fallback CSS env + visualViewport. Нижний nav не перекрывает button/dialog; keyboard open — sheet content скроллится и поля16px, focus виден.

Для Mini App загрузить официальный WebApp SDK до кода login, обработчик работает только при наличии initData. Telegram `initDataUnsafe` не использовать как доказательство identity. Передавать исходный initData по существующему `/api/auth/telegram`, server HMAC/age/allowlist сохраняются. Не писать initData/session/token в localStorage, console, screenshot filenames или test report. safe area API и правила доверия: [Telegram Mini Apps](https://core.telegram.org/bots/webapps).

Login текст: H1 «Вход в WORED», пояснение «Доступ для разрешённых администраторов»; поля «Имя пользователя», «Пароль»; button «Войти»; loading «Проверяем доступ…»; ошибка «Не удалось войти. Проверьте данные»; Telegram403 «Доступ к WORED не разрешён для этого аккаунта». Username/current-password autocomplete, Enter submit, visible label, error association. АвтовходTelegram не скрывает навсегда fallback пароль. Нет Telegram SDK/пустой initData — обычный login, без бесконечного spinner.

CSRF для forms и POST fetch сохранять по действующему middleware-контракту; token получать из отрендеренного meta/hidden, не из env. Same-origin requests credentials='same-origin'; локальный next проверяется сервером, UI не исполняет произвольный URL. `/system` под тем же admin allowlist, unauthenticated HTML redirect/login, API401/403. Не вводить auth=false ради screenshots.

Иконки декоративные aria-hidden; у icon-only controls aria-label. Порядок DOM совпадает с визуальным. Focus ring ≥2px с offset2; видим на всех поверхностях; aria-live=polite для результата операции, не для каждого изменения цены. prefers-reduced-motion отключает pulse/transition; loading не мигает. Tooltip не содержит единственную важную причину. Метрики/статусы читаются без цвета.

Реальная ручная проверка в Mini App обоих ботов (`@RACHELLO_BOT`, `@W_W_O_O_bot`, сверить с итоговым отчётом P4): вход разрешённого пользователя, возврат/повторное открытие, safe areas portrait/landscape, клавиатура в форме, восстановление существующего request ID, отказ неразрешённому пользователю на fixture/отдельном тестовом контуре. Зафиксировать ОС и Telegram version, результат, дату. Автоматический mock WebApp и Chromium не заменяют эту часть; пока подтверждение реального клиента отсутствует — `telegram_manual=pending`, S2=partial.

## UI-11. Детерминированные тесты и доказательства

Полный перечень проверок в `acceptance.json`. Каждому test ID соответствует настоящая проверка поведения, не поиск строки в template. `fixtures.json` содержит канонические синтетические сценарии и ожидаемый UI; это семантические fixtures. При реализации `fixture_data.py` преобразует их в текущий API shape из UI-04, отдельные contract tests сверяют с endpoint serializers.

Изолированный тестовый сервер `127.0.0.1:18080`, никаких рабочих .env/PG/Redis/Telegram/LLM вызовов. `fixture_app.py` использует **реальные шаблоны/static и route handlers через ASGI app factory/подменённые dependency clients**, не отдельную копию UI. Подменить lifespan и get_pool/feed/provider на fixtures до создания app; server routes имеют тот же auth middleware и тестовую локальную login-сессию. Fixture helpers существуют только в tests, никогда не подключаются production app. Browser route interception допускается для network failure/late responses, но auth/CSRF проверяются через реальные handlers на тестовом сервере.

Runner `scripts/run_ui_acceptance.py` реализовать с CLI `--host 127.0.0.1 --port 18080 --browser chromium|webkit|all --artifacts PATH`. Запрещать другой host/port для автоматических mutation-тестов. При занятом18080 вывести `UI_QA_PORT_IN_USE`, не убивать чужой процесс. Runner создаёт fixture server subprocess, ждёт его специальный `/__qa__/health` с nonce (не production health), запускает pytest, завершает только свой subprocess в finally. Браузерные запросы вне127.0.0.1:18080 abort с test failure; библиотеку charts обслуживать локально точной версией5.2.0 и сохранить license/notice. Telegram SDK в автоматическом тесте fixture shim; production SDK URL не изменять на shim.

QA Python3.11 из уже подготовленного Hermes окружения, отдельно от production runtime. Для browser runner закрепить `playwright==1.51.0`, `pytest==8.3.4`; полный transitive lock с hashes сгенерировать и сохранить в `tests/ui/requirements.lock`, ввод в requirements.in. Выбранная версия — фиксированный тестовый baseline, не заявление о новейшем браузере; её поддержка Python3.11/Windows проверена по [PyPI](https://pypi.org/project/playwright/1.51.0/). Browser binaries устанавливаются командой ниже. Реальный текущий Telegram проверяется отдельно. Не менять runtime Python/Docker image для установки browser runner.

Данные и время: фиксированный clock `2026-09-09T12:00:00Z`, IDs9001/7001/501; fake provider model IDs из fixtures. Тесты stale/expiry используют управляемый clock, не sleep90сек. Network tests задерживают ответы контролируемыми events. 48-step chart должен иметь48точек в table/series data, а не48tick labels. Внешние запросы провайдера =0, рабочие mutation requests =0.

Обязательная матрица: shell всех13 путей из acceptance на четырёх viewport Chromium; ключевые async/ticket/login/Telegram shim на WebKit390×844; UI states ready/stale/error/queued/running/partial/failed/expired/no_trade/preview_invalid на целевых экранах. A11y: keyboard assertions, DOM semantics, computed foreground/background contrast (включая alpha composition), no crop, form labels; automated audit не заявлять полным WCAG сертификатом.

Артефакты: `artifacts/uiux/<commit>/junit.xml`, `results.json`, `console.json`, `network.json`, `contrast.json`, `screenshots/`, `manual-checks.md`. Для screenshot имя `<test-id>_<page>_<state>_<width>x<height>.png`; до/после одной fixture и viewport. Все pageerror/unhandled rejection —0; ожидаемые 401/403/503 отдельно помечены test ID, нельзя подавить весь console.error. Screenshot update не автоматическое одобрение отличий; Hermes визуально просматривает ключевые before/after и описывает исправление.

## 5. Точные команды исполнителя

Эти команды относятся к **будущей реализации** UI-00…UI-12; scripts/run_ui_acceptance.py должен быть создан по контракту выше. В подготовке ТЗ они не выдаются за выполненные.

PowerShell, без изменения глобального ExecutionPolicy. Из WSL использовать `powershell.exe -NoProfile` и Windows пути. Исходное Python окружение уже указано в отчёте Hermes; если путь не существует — `QA_ENV_MISSING`, восстановить по прежнему bootstrap пакета, не диагностировать Docker вместо Python.

```powershell
$Repo = 'D:\WORED'
$UiRepo = 'D:\WORED_UIUX_20260909'
$Python = 'D:\WORED\TASOCHKI\HERMES-WORED\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'QA_ENV_MISSING' }
git -C $Repo status --short
if ($LASTEXITCODE -ne 0) { throw 'GIT_STATUS_FAILED' }
$BaseCommit = git -C $Repo rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'BASE_COMMIT_FAILED' }
if (-not (Test-Path -LiteralPath $UiRepo)) {
    git clone --no-hardlinks $Repo $UiRepo
    if ($LASTEXITCODE -ne 0) { throw 'UI_CLONE_FAILED' }
    git -C $UiRepo switch -c hermes/uiux-20260909
    if ($LASTEXITCODE -ne 0) { throw 'UI_BRANCH_FAILED' }
}
git -C $UiRepo status --short
git -C $UiRepo branch --show-current
```

Перед реализацией убедиться, что BaseCommit содержит законченный текущий P5 Hermes, tracked changes отсутствуют либо отдельно сохранены владельцем/исполнителем. Существующий UiRepo не переинициализировать: проверить baseline/status. Untracked backup/scratch не удалять.

После реализации runner создать `tests/ui/requirements.in` с полным содержимым:

```text
playwright==1.51.0
pytest==8.3.4
```

Один раз собрать transitive lock установленным на хосте `uv`, сохранить в Git и использовать тот же lock для всех повторных прогонов. До проверки S1 этот шаг не менять. Отсутствие uv — `UI_LOCK_TOOL_MISSING`, использовать установленный `uv.exe` по абсолютному пути; не подменять lock установкой latest без закрепления.

```powershell
Set-Location $UiRepo
$Uv = Get-Command uv.exe -ErrorAction SilentlyContinue
if (-not $Uv) { throw 'UI_LOCK_TOOL_MISSING' }
& $Uv.Source pip compile tests/ui/requirements.in --python $Python --generate-hashes --output-file tests/ui/requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'UI_LOCK_FAILED' }
```

После реализации runner и requirements.lock:

```powershell
Set-Location $UiRepo
& $Python -m pip install --require-hashes -r tests/ui/requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'UI_DEPENDENCIES_FAILED' }
& $Python -m playwright install chromium webkit
if ($LASTEXITCODE -ne 0) { throw 'UI_BROWSER_INSTALL_FAILED' }
& $Python -B -m pytest -q -p no:cacheprovider webui/tests/test_ui_presenters.py webui/tests/test_ui_api_contract.py
if ($LASTEXITCODE -ne 0) { throw 'UI_CONTRACT_FAILED' }
& $Python -B scripts/run_ui_acceptance.py --host 127.0.0.1 --port 18080 --browser all --artifacts artifacts/uiux
if ($LASTEXITCODE -ne 0) { throw 'UI_ACCEPTANCE_FAILED' }
& $Python -m ruff check --no-cache --select E9,F821,F822,F823 webui tests/ui scripts/run_ui_acceptance.py
if ($LASTEXITCODE -ne 0) { throw 'UI_LINT_FAILED' }
& $Python -m mypy --follow-imports=skip --ignore-missing-imports webui/ui_presenters.py
if ($LASTEXITCODE -ne 0) { throw 'UI_TYPECHECK_FAILED' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'UI_DIFF_WHITESPACE_FAILED' }
```

Runner обязан проверять syntax всех новых JS и inline scripts через реально отрендеренные страницы в браузере. Ruff/Mypy здесь не проверка JavaScript. После изменения app.py/common auth — весь существующий `webui/tests`, `tests/stabilization/test_http_boundary.py`, `test_principal.py`, `test_forecast_lifecycle.py` тем же настроенным QA runner, что подтвердил S1. Полный release regression выполняется один раз после финального UI diff, с реальной отдельной `wored_qa`; не заменять SQL mocks и не направлять на trading.

При Docker pipe denied различать сетевой SQL/HTTP доступ; UI fixtures вообще не требуют Docker. Пользоваться уже рабочим способом QA из P4 отчёта; не повторять диагностику sandbox десятки раз. Если обязательный backend regression не выполнен, оставить release gate partial, UI тесты продолжать.

## UI-12. Внедрение, документация и итоговая приёмка

1. Сохранить UI diff отдельными commits по группам: common/shell; async/chart/forecast; ticket/session; supporting/auth; tests/docs. Не менять migration checksums и backend версии. Полные файлы реализации лежат в Git, не фрагменты в отчёте.
2. Проверить tracked file list: только согласованные UI/представление/tests/docs и vendored chart asset+license. Изменения .env, provider config, trading formulas, migrations, collector/chatbot требуют отдельного обоснованного backend пункта, не прятать их в UI commit.
3. До live update собрать существующий WebUI image в изолированном staging по рабочему runbook S1. Не вызывать `compose up` для всего стека из нового clone и не стартовать вторую пару Telegram polling bots. Staging dummy env не копировать в production.
4. Зафиксировать работающий pre-UI commit/image tag и доказанный rollback WebUI. Если D:\WORED изменился с UI baseline, merge/rebase в UI staging, решить конфликты, повторить затронутые tests; не применять старые полные source snapshots.
5. Только после зелёной обязательной автоматической приёмки и существующего разрешения на внедрение обновить код/образ сервиса webui штатным выборочным `up -d --no-deps --build webui` рабочего Compose-проекта. Не перезапускать collector/chatbot/PG/Redis ради UI. Если разрешение на deploy не входит в продолжаемое поручение, подготовить commit/image/отчёт полностью и запросить только финальное внедрение; не задерживать реализацию.
6. Read-only smoke: login/authenticated existing routes/new system, new assets200, active selection/reload, existing forecasts/positions читабельны, no JS errors. Live tests не открывают/закрывают позиции и не запускают inference. Browser auth tokens не сохранять в отчёт.
7. Выполнить реальные Telegram проверки UI-10; если нет устройства/владельца — явно pending, не выставлять S2 completed. Весь backend P5 также не становится готовым только по UI тестам.
8. Rollback только UI commits/image до pre-UI, сохраняя весь принятый S1 backend и данные; git revert UI commits в проверенном порядке или штатное восстановление pre-UI image. Никаких down -v, DB restore и возврата старой незащищённой WebUI. Если UI changes смешаны с другими commits, сначала отдельный revert diff в staging и проверка.

Обновить docs/UIUX-IMPLEMENTATION, UIUX-TESTING, UIUX-ACCEPTANCE, UIUX-STATUS и общий STABILIZATION-STATUS. README проекта: четыре раздела навигации, запуск UI QA, критерии Telegram. AGENTS проекта: новые active UI modules, тестовые команды, сохранённые design guardrails.

Итоговый отчёт строго содержит: base/release commit и image; UI-00…12 и все test IDs; команды/exit codes/count passed/failed/skipped; ссылки на screenshots/contrast/console/network; фактические ОС/Telegram versions; что изменено по каждому O01…O10; backend gaps; непроверенные пункты; rollback reference. Статус `complete` только при всех обязательных автоматических и ручных критериях. Не считать количество тестов, красивый screenshot или health200 достаточной приёмкой.

### Обязательные ограничения остаточного риска

На старте не доказана полнота v3/net/idempotency/допуска в каждом live endpoint — это явно отмеченный UI-04 bridge gate. Нет actual model/period/N — отображать отсутствие, не выдумывать. Полученный позднее отчёт Hermes может закрыть часть пунктов; принимать его только вместе с воспроизводимой проверкой на актуальном commit. Неизвестность не должна приводить к переписыванию стабилизированного backend или бесконечной остановке независимой UI работы.
