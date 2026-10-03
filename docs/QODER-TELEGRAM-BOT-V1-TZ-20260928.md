# WORED Telegram Bot V1 — техническое задание для Qoder

**Дата:** 2026-09-28  
**Статус:** спецификация; реализация и runtime-приёмка не проводились.  
**Исполнитель:** Qoder, модель Qwen3.8-Flash.  
**Связанный контракт:** `docs/QODER-MARKET-FORECAST-SIMULATION-V3-TZ-20260928.md` (далее MC-V3).  
**Среда:** локальный Docker Compose WORED; Telegram — быстрый интерфейс, WebUI/Mini App — подробный график и формы. Только paper trading.

## 0. Задание исполнителю и границы

Спроектировать и внедрить Telegram-бота как основной быстрый пульт WORED: за 1–3 действия получать актуальный рынок, прогноз, состояние симуляции, позиции, риск и итог; безопасно запускать и управлять paper-сессией; получать важные события без спама. Один владелец сейчас, модель данных и права подготовить для нескольких разрешённых пользователей. Язык по умолчанию русский, единицы и время всегда явные. Бот не отправляет реальные заявки на биржу и не обещает доходности.

Это новое **устройство пользовательского пути**, а не просьба завести ещё один симулятор. Источник рыночных и финансовых данных общий с WebUI и доменом `paper_trading`; пользователь видит одинаковые `forecast_id`, `session_id`, `account_id`, `position_id`, суммы и статусы в Telegram и WebUI. Бот не пересчитывает P&L своей формулой. Текущее `chatbot/services/sim_engine.py` и фоновый `_sim_ai_monitor()` в `chatbot/main.py` нельзя незаметно объявить новым доменом: в P0 составить карту существующих записей и сценариев, затем мигрировать или оставить явный legacy read-only путь с маркировкой. Никаких скрытых переносов balances/positions.

Реализацию вести по этапам P0–P7 ниже. Перед каждым изменением `chatbot/`, `collector/`, `.env`, production Compose действуют правила корневого `AGENTS.md`: PLAN → DIFF → APPLY → TEST → REPORT, цель, active runtime path, файлы, риск и команды проверки. Сохранить параллельные незакоммиченные изменения. Не останавливать и не перезапускать рабочие сервисы в рамках этого ТЗ без отдельной авторизации. QA изолировать от production БД, volume, секретов и реальной Telegram-доставки.

## 1. Исходные сведения, допущения и неизвестное

На 2026-09-28 в исходниках `chatbot/main.py` подключает роутеры `start/menu/callbacks/market/alerts/analytics/portfolio/predictions/settings/trader/admin/pipeline/news/plans/chat`, запускает polling, alert listener и отдельный monitor старых симуляционных позиций. Меню имеет Command Deck, рынок, аналитику, прогноз, портфель, алерты и сессию. `pipeline.py` содержит текстовые фразы и callback-команды старой дневной сессии. `chat.py` перехватывает продуктовые слова и отдаёт остальное AI. `docs/TELEGRAM_BOT_USAGE.md` описывает ещё один исторический набор команд, режимов и высоких плеч. **Наличие обработчика/документа не доказывает работу на живом Telegram, права доступа, финансовую корректность или согласованность с MC-V3.**

Принятые продуктовые допущения: один авторизованный владелец по числовому Telegram ID; только приватный чат; первая пара — HTX BTC-USDT perpetual; базовая валюта USDT; интерфейс paper trading; время хранения в UTC, показ в выбранной IANA timezone (`Asia/Bangkok` по умолчанию); market data и прогноз приходят из уже принятого MC-V3 backend. Watchlist и другие пары включать только если есть полный контракт данных и исполнение для инструмента. Режимы `live_paper` и `historical_replay` должны быть различимы в каждой карточке.

До кода установить: фактические активные API/таблицы и ownership, какие старые Telegram-команды реально маршрутизируются, есть ли доступный HTTPS URL Mini App на телефоне, как устроен `paper_v2` lifecycle, допустимые финансовые limits, актуальный состав моделей/квот, текущие BotFather command scopes. Всё неизвестное фиксировать в `docs/TELEGRAM-BOT-RFC.md`; не подставлять историческую документацию как доказательство. Если MC-V3 ещё не реализован, Telegram-функции, которые от него зависят, имеют статус `BLOCKED_DEPENDENCY`, а не fake PASS.

## 2. Принцип интерфейса и карта функций

Одна главная карточка **«Сейчас»** вместо набора равноправных экранов и постоянной клавиатуры на 8–10 кнопок:

```text
WORED · HTX BTC-USDT perpetual · LIVE PAPER
Рынок: 67 230.5 USDT | mark 67 228.9 | обновлено 14:32:08 (2 с)
Прогноз 1ч: +0.7% · 67 701 · диапазон 67 050–68 030 · до 15:30
Сессия: RUNNING · план v3 · ручной 102.40 / авто 98.11 USDT
Риск: 1 открытая · текущая просадка 1.9% / лимит 5%
[График] [Прогноз] [Сессия]
[Позиции] [Итоги] [Ещё]
```

Это **пример структуры**, числа не использовать как демо-данные в продукте. Верхняя строка всегда показывает площадку, рынок, режим и источник. Для stale/error вместо свежей цифры показывать время последнего подтверждённого значения и причину. Если прогноза нет — «Нет проверенного прогноза», если сессии нет — «Сессия не запущена», если данных нет — `N/A`; не подменять нулями. Статус рынка, прогноза, сессии и уведомлений раздельный. Меню редактирует текущую карточку; длинные детали — новая карточка или Mini App. Кнопка «Назад» возвращает к предыдущему контексту, «Сейчас» — к главной карточке. Устаревшая карточка не может выполнить действие.

Навигация первого уровня: `Сейчас`, `Прогноз`, `Сессия`, `Позиции`, `Итоги`, `Ещё`. В `Ещё`: `Рынок/список`, `Оповещения`, `Журнал`, `Обучение`, `AI-вопрос`, `Настройки`, `Помощь`; админ-пункт показывается только админу. Главный пользовательский цикл — **«Сейчас → Итоги → Обучение»**: оперативное состояние, доказанный результат, затем предложение изменения для следующей сессии. Для коротких ответов допускаются slash-команды `/start`, `/now`, `/market`, `/forecast`, `/session`, `/positions`, `/report`, `/alerts`, `/help`, `/cancel`; `/admin` только в admin scope. Команда и кнопка вызывают один application use case. Исторические `/trader`, `/predictions`, `/portfolio`, `/plan`, `/plans`, `/accuracy`, `/diff`, `/settings` на переходный период делают явный redirect на новый экран с сохранением deep-link контекста; документировать точный срок совместимости после инвентаризации. Никаких двух разных реализаций одного действия за этими именами.

Бот даёт **быстрый срез**, Mini App — график со свечами, фитилями, объёмом, осью времени/цены, прогнозным диапазоном, маркерами позиции и длинную форму плана. Ссылка должна открывать конкретный инструмент/прогноз/сессию/позицию, проверять доступ заново и не содержать bearer token. В обычном Telegram сообщении можно отдать статичный PNG графика с явно указанными `as_of`, диапазоном, разделителем факт/прогноз, легендой и источником; этот PNG — снимок, не реальное время. Если HTTPS Mini App недоступен, все критичные чтения и управление сессией остаются в чате, а детальная визуализация помечается недоступной.

## 3. Роли, права и безопасность по умолчанию

Роли: `owner` (полный paper-контроль и личные настройки), `viewer` (только разрешённое чтение), `admin` (диагностика и политика моделей), `service` (внутренние события; не Telegram account). Начальный deployment — один `owner`; `viewer/admin` не появляются автоматически из username. Таблица прав:

| Действие | Owner | Viewer | Admin |
|---|---:|---:|---:|
| Рынок/общий прогноз | да | да | да |
| Свои счета, позиции, отчёты | да | только специально делегированные | только при явном owner scope |
| План, запуск, пауза, ручная paper-сделка | да | нет | только если admin также owner |
| Провайдеры, квоты, диагностика | краткая сводка | нет | да |
| Секреты, ключи, полные prompt/log payloads | нет | нет | нет через Telegram |

Middleware до всех routers: только private chat; verify `from_user.id`, `chat.id`, роль/owner scope; deny by default для неизвестных пользователей и групп; rate limit на actor/action; audit отказа без содержания приватного сообщения. Групповые update не участвуют в трейдинге. Не использовать username, display name или client-supplied `owner_id` как авторизацию. Все read/write backend вызовы получают server-derived principal; backend повторно проверяет owner. Ни один универсальный `/start`/callback/URL не обходит middleware. Разные команды BotFather по scope (owner/admin/viewer), но видимость меню не является защитой.

Для Mini App сервер проверяет raw `Telegram.WebApp.initData` по документированному Telegram алгоритму, `auth_date`, срок жизни и привязку к ожидаемому bot ID; `initDataUnsafe` не доверять. **Важная миграция:** действующая главная кнопка `KeyboardButton(web_app=...)` в `chatbot/handlers/start.py` не подходит как единственный вход в авторизованный Mini App: по Telegram docs `initData` для запуска с keyboard button может быть пустым. Для защищённых страниц использовать menu button или inline `web_app` кнопку с валидируемым initData; keyboard launch оставить лишь для публичного read-only/промежуточного экрана, который не принимает mutating API. В P0 проверить фактическое поведение клиента. После проверки выдавать короткую HttpOnly/Secure/SameSite сессию приложения, owner scope и CSRF-защиту для mutating endpoints; не хранить Telegram token и initData в URL, localStorage или логах. WebUI-login и Telegram-login — две независимые доверенные процедуры, доступ к API один и тот же owner-scoped. Развёртывание на реальном телефоне требует доступного HTTPS origin; `localhost` на телефоне не является компьютером с WORED. Точные ограничения кнопок, Mini App и срока данных сверить с официальными Telegram docs в день реализации.

Все callback payload — короткий opaque action ID, сервер хранит `(actor, chat, message, resource_id, resource_version, expiry, nonce)`. Обработчик повторно проверяет все поля и текущее состояние; replay/expired/foreign-user/foreign-session не выполняет команду. Любой callback получает `answerCallbackQuery` быстро; долгий расчёт создаёт job и обновляет статус отдельно. Критичные действия не кодируются только строкой `pipeline_close_all` без контекста и проверки. В логах не печатать полный `Update` JSON, текст AI, токены, cookies и полный initData. Установить redaction и ограничение retention.

## 4. Сценарии пользователя, экраны и результаты

### U01. Первый запуск и восстановление

`/start` → проверка private/allowlist → краткая карточка «Сейчас» и 3 шага: «посмотреть рынок», «посмотреть прогноз», «настроить симуляцию». Если runtime/backend недоступен, показать конкретный компонент и время последней успешной связи, не предлагать рискованную кнопку. После рестарта бота состояние навигации и `command_id` восстанавливается; незавершённый wizard доступен через `/session`, без автоматического запуска. `/help` объясняет paper-режим, значения «факт/прогноз», задержку, комиссии, funding, стоимость AI и действия при инциденте. `/cancel` отменяет только черновик/диалог, уже принятую domain-команду не откатывает.

### U02. Рынок в реальном времени

`/now` и `/market` показывают инструмент с `last`, `bid`, `ask`, `mark`, `index`, funding и временем следующего funding, изменением за выбранный период, состоянием data quality и отдельно возрастом quote/mark/closed candle. Default-карточка компактна; «Детали» открывает order book depth/24h диапазон/объём только если их источник валиден. «Обновить» не чаще 1 раза в 2 секунды на актор/ресурс и не шлёт новый чат-пост на каждый тик. Telegram не является непрерывным биржевым терминалом: он возвращает snapshot по запросу, Mini App рисует поток. При stale/отсутствии bid/ask вход в позицию заблокирован, аварийное закрытие обрабатывается отдельной fail-safe политикой домена. Смена пары не смешивает spot/perpetual и пересоздаёт контекст прогноза/сессии.

### U03. Прогноз и проверка качества

`Прогноз` → горизонт 15m/1h/4h/24h → показать последний валидный forecast или «Рассчитать». Карточка: `forecast_id` (краткий), инструмент/таймфрейм, фактический `base_price` и `base_time`, `predicted_close`, абсолютное и процентное изменение, калиброванный диапазон с названием метода, `target_at`, модель/роль, возраст и статус `pending|partial|complete|failed|stale`; 2–4 предложения объяснения и использованные входы. «График» открывает MC-V3 с будущими свечами только если есть валидные forecast OHLC; при point+band показывает линию+полосу без выдуманных фитилей. «Проверить позже» сохраняет `forecast_id` и сравнивает неизменный прогноз с фактом, метрики accuracy показываются только при достаточной выборке. Новый запрос асинхронный; повторный тап возвращает один `job_id`. Timeout/AI quota/плохой ответ даёт явную ошибку, старый forecast остаётся помеченным stale. Никакой прогноз не исполняет сделку напрямую.

### U04. Создать симуляцию вручную

`Сессия` → `Новая` → выбор `live_paper|historical_replay` → инструмент → окно start/end/timezone → стратегия/стиль из **реализованного каталога** → раздельные бюджеты `manual` и `auto` → плечо/стороны/максимальная доля риска на сделку/дневной и общий лимит потерь/max exposure/max open positions/cooldown/trading hours/SL/TP/close-at-end → итоговая карточка плана. Поля и валидаторы идентичны `SimulationPlanV1` из MC-V3; UI не показывает неподдерживаемый параметр как действующий. Обязательные шаги можно пропускать только при показанном значении по умолчанию и его последствиях. Черновик хранится 24 часа с `draft_id`, actor, version, expiry; пользователь может вернуться, изменить, сравнить diff. `Validate` даёт нарушения, estimate worst allowed loss, snapshot/data coverage, fee/funding/slippage policy versions и `plan_hash`. `Approve` фиксирует версию, отдельный экран `Запустить` требует свежую повторную проверку и подтверждение. Нулевой баланс, дата в прошлом для live, отсутствующий interval, stale mark/quote или превышенный cap блокируют старт с точной причиной.

### U05. Доверить параметры агенту

Кнопка `Помощь агента` запускает короткую анкету: цель (изучение/сравнение/контроль риска), срок, совокупный либо два раздельных бюджета, максимальный допустимый убыток в USDT и %, ручное участие, предпочтение long/short, время активности. Каждый вопрос имеет «Назад» и «Отмена»; отсутствие ответа не превращается в aggressive default. AI возвращает **proposal**, `proposal_id`, текст причин по каждому параметру, model/policy version, использованные market/forecast IDs, missing evidence и явно отмеченные допущения. Детерминированный валидатор домена проверяет весь план; опасный/невыполнимый proposal остаётся отклонённым, модель не «чинит» его молча. Пользователь видит diff «задал / предложено / допустимо», может редактировать и только потом утвердить. AI outage, quota или стоимость не блокируют ручной путь и не вызывают платный fallback без разрешённой политики. Подтверждение выбора proposal не означает старт сессии.

### U06. Вести симуляцию

`Сессия` показывает `draft|approved|starting|running|paused|closing|settlement_pending|closed|failed` и разрешённые действия по состоянию. `Пауза` немедленно блокирует новые авто-входы, не скрывает уже открытый риск; `Продолжить` повторно проверяет рынок, лимиты и текущую версию плана. `Ужесточить риск` создаёт новую версию с diff; ослабление risk caps во время текущей сессии запрещено, если домен не поддерживает отдельную явно утверждённую политику. `Завершить` показывает preview: открытые позиции и ожидаемую процедуру закрытия; подтверждение создаёт команду. `accepted/processing/unknown` не выдавать как `closed`. При `settlement_pending` показывать причину, блок новых входов и action «Проверить статус», без кнопки повторного non-idempotent запуска. Различать live и replay в каждом ответе.

### U07. Ручная paper-сделка и позиции

`Позиции` → вкладка `Ручной` или `Авто`, затем `Открыты / Ожидают / Закрыты / Ликвидированы / Все` с пагинацией. Каждая позиция: account, side, qty, average entry/current mark/exit, unrealized или realized net, fee/funding, margin/leverage, SL/TP/liquidation threshold, причина и время статуса. `Открыть` доступно **только** на manual account: инструмент, long/short, объём (USDT или qty с явной конверсией), тип входа из поддерживаемых, плечо, SL/TP; preview показывает bid/ask, mark, ожидаемые fees/slippage, максимально разрешённый убыток, остаток лимита, версию данных и предупреждение о ликвидации. Затем краткое подтверждение, которое истекает через 60 секунд или при изменении существенных полей/рыночного snapshot; domain всё равно перевалидирует. Частичное/полное закрытие — отдельные команды с qty и preview; аварийное закрытие остаётся возможным через допустимый доменный путь при stale data, но не должно притворяться исполненным, пока fill не записан. Auto account не принимает ручное открытие; контроль пользователя через pause/finish/risk policy. Позиция получает один постоянный `position_id`, статус не меняет ID; ликвидация — отдельное состояние с единственным fill/postings, не замаскированный «стоп».

### U08. Итог, сравнение и обучение

`Итоги` показывает последнюю сессию и историю по месяцам; итоговый report только после `closed` и ledger reconciliation. Для `manual` и `auto` **отдельно**: начальный/конечный equity, gross, entry/exit fees, funding, slippage, net P&L, ROI, максимальная просадка, число сделок, win rate при достаточной выборке, liquidation count, причины отказов, качество источника. Сравнение также использует нормированные ROI/риск при разном бюджете; никакого сложения счетов в «общий баланс». Replay-отчёт содержит data interval/hash, strategy version, in-sample/holdout и baseline `no_trade/buy_and_hold` на том же окне; если данных недостаточно — `N/A`. `Журнал` собирает события «почему открыл/закрыл/не открыл», версии прогноза и плана, переходы риска и ошибок, без фантазий AI. Экспорт JSON/CSV/HTML делается через общий MC-V3 report service; Telegram отправляет ссылку или документ, не собирает отчёт заново.

Экран `Обучение` после финального отчёта показывает: конкретное наблюдение из ledger/market, гипотезу, ожидаемый эффект и риск, число сделок и качество выборки. AI может предложить только `candidate` стратегии/параметров; далее нужны воспроизводимый replay, отдельный holdout, сравнение с baseline и проверка risk caps. Только явно одобренная новая версия может примениться **со следующей** сессии, не задним числом. При недостатке данных писать «Гипотеза не проверена» и не включать её. Здесь же пользователь видит, чем новая версия отличается от старой и почему она была отклонена.

### U09. Оповещения

Настраиваемые классы: `critical` (достижение risk cap, liquidation/pending, settlement failure, длительно stale market при открытой позиции), `trade` (order/fill/open/partial-close/close), `forecast` (готов/истёк/проверен по факту), `market` (цена/изменение/volatility/funding), `digest` (утро/вечер/итог дня), `system` (бот/backend degraded). У правила есть owner, instrument, threshold, channel, enabled, cooldown, quiet hours и expiry; создание/редактирование через wizard с preview. Critical не выключаются обычным quiet hours, но антиспам действует: один инцидент = одна начальная карточка + изменение severity + закрытие инцидента. `Прочитано` или `Не напоминать 1ч` не изменяет торговый risk state. Дедуп по `(owner,event_type,entity_id,version)`, durable outbox, retry с backoff и TTL; Telegram 429 учитывать. Уведомление содержит факт, время/источник, account/session/position ID, что произошло и кнопку к деталям. Не слать старую рыночную тревогу как новую после восстановления polling.

### U10. AI-вопрос и модели

Свободный текст служит **вопросом**; команды, изменяющие торговлю/лимиты/модели, исполняются только через типизированный intent, preview и права. При неоднозначном запросе показать варианты, не угадывать. Ответ AI получает источник данных, `as_of`, использованную модель, стоимость/токены если доступны, пометку неопределённости. Цитируемый market text, news и пользовательский ввод не являются инструкцией к system/tool. На сервере policy router отдельно от provider adapters, free/zero-cost first **если доступен и разрешён**, ordered fallback, quota/budget/circuit breaker, запрет premium по умолчанию и разблокировка только по оценке качества и явно установленному бюджету. При этом действующий runtime WORED может использовать Ollama Cloud и локальный Bonsai; исторический `free_routing_archive/` не включать в runtime по этому ТЗ. P0 должен согласовать общую AI policy без восстановления архива и без дублирования моделей в боте. `/admin` показывает active route, квоты, latency, failures и request trace без ключей и чужих текстов; изменение маршрута/платного режима требует роли admin, preview, отдельного подтверждения и audit.

Отдельные пользовательские команды AI-шлюза: `/ask` (явный вопрос), `/mode` (`free_only|balanced|premium` в пределах прав), `/models` (доступные роли и причина недоступности), `/usage` (свои токены/запросы/оценка стоимости за день, неделю, месяц), `/quota` (остатки и время сброса), `/trace` (последний request: выбранная роль, последовательность попыток, причина fallback без содержимого приватного prompt). Команда `/mode premium` не разблокирует платную модель сама: нужен пройденный gate, admin policy, отдельный owner budget и расходный потолок. При неизвестной цене/cached/reasoning/tool token semantics писать `estimate_unknown` или `unsupported`, не ноль. Простые рыночные факты бот отвечает из данных без LLM и без траты квоты. Настройка «AI выключен» оставляет рынок, симуляцию и отчёт доступными.

Lifecycle каждого AI-запроса: (1) принять и классифицировать; (2) проверить право, выбранный mode и remaining quota; (3) построить ordered candidate chain по роли, цене, контексту, latency и reliability; (4) вызвать разрешённый адаптер с timeout; (5) при timeout/quota/invalid response/policy violation перейти к следующему **разрешённому** кандидату; (6) сохранить решение, токены, latency, ошибку и cost estimate каждой попытки; (7) вернуть ответ и краткие metadata. Одинаковая нормализованная schema адаптера для Ollama Cloud, локального Ollama и будущих провайдеров; новый provider добавляется конфигурацией/адаптером, без изменений торговых handlers. Нормализованные ошибки `auth`, `quota`, `timeout`, `rate_limit`, `invalid_response`, `provider_unavailable`, `policy_block`, `context_overflow`; fallback запрещён, когда нарушает budget или платный gate. Лимиты считать на user/provider/model и день/неделю/месяц, причём hard stop обрабатывается до вызова модели и после точного учёта результата.

Gate на платные пути формализовать до реализации: versioned evaluation set минимум из 100 задач по `simple`, `analysis`, `forecast_explanation`, `plan_proposal` и `safety`; датасет зафиксировать hash и разделить development/holdout. На holdout платная цепочка должна дать не менее 95% валидных ответов, ноль критичных нарушений tool/risk policy и измеримое улучшение качества над доступной бесплатной цепочкой: минимум +5 процентных пунктов в заранее зафиксированном score при одинаковой rubric. Если бесплатный baseline недоступен, gate остаётся `BLOCKED` и платный путь закрыт. Отчёт содержит качество по классам, p50/p95 latency, input/output/cached/reasoning/tool tokens по поддержке провайдера, стоимость и количество fallback. Платный режим нельзя включать из текста модели, из одного успешного вызова или только потому, что бесплатная quota исчерпана.

### U11. Настройки и помощь

Часовой пояс, язык (ru в V1; en только после локализации), единицы, watchlist, уведомления, compact/detailed cards, privacy/export/retention, AI budget и согласие на premium в пределах политики. Кнопка «Проверить связь» показывает Telegram delivery, backend read, market feed, DB, AI route и версии отдельно; не сводить к одному зелёному сердцу. Неподдерживаемые настройки не показывать. Ошибки: понятная причина, что не изменилось, `request_id`, допустимое действие «Повторить статус/Вернуться»; не показывать traceback.

## 5. Переходы, идемпотентность и ответы

Единая схема mutating-команды: Telegram update → middleware role/owner → typed intent → current domain snapshot → preview and policy checks → short-lived confirmation → application command with `command_id`/idempotency key → domain accepted → poll/subscription result → final card. Для подтверждения хранить actor/chat/resource/version/nonce/expiry; повтор с тем же key+payload возвращает тот же command/result, тот же key с иным payload — conflict. Потерянный Telegram ответ восстанавливать запросом по `command_id`, не создавать новую операцию. Два телефона и WebUI могут одновременно открыть одну сессию: version mismatch возвращает свежий diff и требует нового подтверждения. Серверная state machine первична: бот не выставляет `running`/`closed` по факту отправленного POST.

Контракт ответа карточки: `status`, `title`, `body`, `as_of`, `source`, `quality`, `entity_ids`, `next_actions`, `request_id`; presenter применяет HTML escaping ко всему пользовательскому/AI тексту. Для `pending` показать ETA как оценку, кнопку «Проверить» и опциональный push по готовности; для `failed` — reason code и safe retry. Сообщения длиннее лимита Telegram делить на смысловые блоки или открывать документ/Mini App, не обрезать таблицы со значимыми суммами. В каждом числе разрядность и округление display-only; расчёты на Decimal в домене.

| Переход/состояние | Допустимое действие | Ответ при запрете |
|---|---|---|
| `draft` | edit, validate, discard | `draft_expired` с восстановлением новой копии |
| `approved` | start, clone | `plan_version_conflict` с diff |
| `starting` | read status | «Запуск принят, ещё не подтверждён» |
| `running` | pause, tighten, manual preview, finish | конкретный risk/data constraint |
| `paused` | resume after recheck, close, finish | `market_stale`/`limit_breached` |
| `closing` | read status | «Закрытие в обработке» |
| `settlement_pending` | read status, admin diagnostics | «Расчёт не завершён», без итогового ROI |
| `closed` | report, clone | «Сессия закрыта» |

## 6. Архитектура и границы модулей

```text
Telegram Bot API
    ↓ updates (один polling consumer локально; webhook опционально на VPS)
chatbot/transport + authorization + dedup + rate limit
    ↓ typed intents / callback sessions
chatbot/application (market, forecast, plan, session, position, report, notification)
    ↓ versioned API/DTO, command_id, owner scope
WORED market/forecast backend + paper_trading service/repository + ledger
    ↓ events/read models
Postgres durable state and outbox · Redis cache/pubsub · collector HTX
    ↑
WebUI/Mini App — тот же domain/read-model, детальный график и формы
```

Не внедрять финансовые алгоритмы в handlers. `chatbot` отвечает за доставку, краткие presenter, user flow и policy checks; фактическая trade/risk/accounting логика в `paper_trading` и MC-V3 backend. Redis — cache/events, не единственный источник command state. PostgreSQL — durable command, session, preferences, audit и outbox. Один dispatcher принимает updates; при локальном polling webhook должен быть выключен, при переходе на webhook polling остановлен. `update_id` dedup устойчив к рестарту; не использовать `drop_pending_updates=True` без документированной replay/retention политики, иначе команды и инциденты могут исчезнуть. При shutdown закончить подтверждённые ACK и сохранить jobs; продолжение выполняет server command state. Telegram доставка at-least-once, финансовый side effect exactly-once **по идемпотентному domain contract**, не по обещанию транспорта.

Планируемое дерево модулей — адаптировать после P0 к существующим пакетам, без удаления `chatbot/main.py`/старых страниц WebUI:

```text
chatbot/
  main.py                           # composition root; один transport и middleware
  handlers/now.py                   # короткие команды/кнопки чтения
  handlers/forecast_v3.py           # forecast navigation/jobs
  handlers/simulation_v3.py         # wizard + session actions
  handlers/positions_v3.py          # manual preview/confirm + views
  handlers/reports_v3.py            # reports/journal
  handlers/notifications_v3.py      # subscriptions/preferences
  handlers/admin_v3.py              # scoped diagnostics
  application/intents.py            # типизированный parse/dispatch, no execution by LLM text
  application/commands.py           # idempotent command orchestration
  application/wizards.py            # persisted state and version checks
  application/presenters.py         # cards, escaping, units, timezone
  integrations/wored_api.py         # typed shared market/forecast/paper API client
  security/principal.py             # Telegram actor→owner/role mapping
  security/callbacks.py             # opaque actions/nonce/expiry/replay
  notifications/outbox.py           # durable send/dedup/retry
  storage/bot_repository.py         # bot-only state, no duplicate financial ledger
tests/telegram/
  test_permissions.py
  test_navigation.py
  test_wizard_and_proposals.py
  test_command_idempotency.py
  test_forecast_and_market_cards.py
  test_notification_outbox.py
  test_miniapp_auth.py
  test_paper_lifecycle_contract.py
docs/TELEGRAM-BOT-RFC.md
docs/TELEGRAM-BOT-OPERATIONS.md
docs/TELEGRAM-BOT-ACCEPTANCE.md
docs/TELEGRAM-BOT-COMMAND-MAP.md
```

Минимальные bot-only таблицы, миграции additive и owner-scoped:

| Таблица | Обязательные поля/ключи | Назначение |
|---|---|---|
| `bot_principals` | `(bot_id, telegram_user_id)` PK; owner_id, role, enabled, version | allowlist и отзыв доступа |
| `bot_preferences` | `(owner_id, telegram_user_id)` PK; timezone, locale, card_mode, updated_at | настройки |
| `bot_interactions` | `interaction_id` PK; actor/chat, flow, state_json, version, expires_at | возобновляемые wizard/карточки |
| `bot_callbacks` | `opaque_id` PK; actor/chat/message, entity/version, action, nonce, expires_at, consumed_at | проверка callback |
| `bot_update_inbox` | `(bot_id, update_id)` unique; received_at, handled_at, outcome | dedup Telegram updates |
| `bot_command_links` | `interaction_id`, `domain_command_id` unique, state, last_checked_at | восстановление результата |
| `bot_notification_rules` | `rule_id` PK; owner, type, instrument, threshold, cooldown, quiet_hours, enabled | правила |
| `bot_notification_outbox` | `event_key` unique; owner/chat, priority, payload_ref, next_attempt, delivered_at, expires_at | надёжная доставка |
| `bot_audit` | event_id, actor, action, entity_id, decision, request_id, timestamp | неизменяемый аудит без секретов |

Поля с sensitive text хранить только при необходимости, определить retention до миграции. Финансовые `orders/fills/postings/balances`, прогнозы и отчёты не копировать в bot-таблицы. Интеграционный DTO: `MarketSnapshotV1`, `ForecastSummaryV1`, `SimulationPlanV1`, `SessionStateV1`, `PositionV1`, `SessionReportV1`, `CommandStatusV1`; контракт DTO содержит `schema_version`, `owner_id` (только server-side), IDs, ISO UTC timestamps, Decimal-строки, `source`, `quality`, `as_of` и reason codes. Несовместимая версия — явная ошибка, не default zeros.

Единая taxonomy ошибок для Telegram-presenter: `AUTH_DENIED` (нет доступа), `CONTEXT_EXPIRED` (повторить preview), `VERSION_CONFLICT` (показать diff), `INVALID_INPUT` (поле и допустимый диапазон), `MARKET_STALE` (последнее достоверное время; новые входы запрещены), `RISK_BLOCKED` (конкретный лимит), `BACKEND_UNAVAILABLE` (request ID и безопасный retry), `COMMAND_PENDING`/`COMMAND_UNKNOWN` (проверить по ID), `AI_QUOTA`/`AI_INVALID` (ручной путь), `SETTLEMENT_PENDING` (без финального ROI). Ошибка не должна включать traceback, token, полный provider response или SQL. HTTP 202/telegram ACK сам по себе не меняет domain status.

## 7. Конфигурация, развёртывание и эксплуатация

Составить `docs/04-env-vars.md` и `.env.example` на основе фактического Compose; в P0 зафиксировать действующие alias `TELEGRAM_TOKEN`/`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`/`TELEGRAM_ADMIN_IDS` и `TG_MINIAPP_URL`/`WEBUI_URL`, затем выбрать один канонический ключ каждого типа с временным валидируемым alias. Не печатать значения. Предлагаемые **новые** ключи: `BOT_TRANSPORT=polling`, `BOT_OWNER_IDS` (comma-separated numeric IDs), `BOT_ALLOWED_CHAT_TYPES=private`, `BOT_MARKET_REFRESH_MIN_SECONDS=2`, `BOT_CALLBACK_TTL_SECONDS=60`, `BOT_WIZARD_TTL_HOURS=24`, `BOT_INITDATA_MAX_AGE_SECONDS=300`, `BOT_NOTIFICATION_RETENTION_DAYS=30`, `BOT_AUDIT_RETENTION_DAYS=90`, `BOT_DEFAULT_TIMEZONE=Asia/Bangkok`, `BOT_MAX_AI_DAILY_COST_USD=0`, `BOT_ENABLE_PAID_AI=false`. Значения и диапазоны проверить с операционными требованиями; не вводить конфликтующий default для production. Secrets: Telegram token только из env/secret store, `POSTGRES_DSN` и service auth только внутри сети Compose, TLS у внешнего Mini App, key rotation с restart plan. `BOT_ENABLE_PAID_AI=true` само по себе не проходит validation gate.

`docker-compose.yml` остаётся стандартным локальным runtime. Health разделить: процесс polling/webhook, Telegram reachability, market freshness, domain command processing, outbox backlog, AI route, DB/Redis. Один failed component не объявляет всё приложение мёртвым без причины. Метрики: response latency p50/p95 для простых карточек, stale rate, update dedup count, command unknown/pending age, notification lag/drops, provider fallback/quota, token/cost. Цели V1 при здоровом локальном backend: `/now` p95 ≤2 с, открытие меню p95 ≤1 с, ACK callback ≤1 с, принятие команды ≤3 с; фактическое исполнение измерять отдельно. При недоступном backend — ответ с понятной ошибкой ≤3 с, не бесконечный spinner. Мониторинг без раскрытия приватного текста.

Еженедельный refresh: официальные Telegram Bot API/Mini Apps, aiogram release, активные provider docs/pricing/quota, HTX market contract, Docker dependency changes; обновить machine-readable registry моделей, `CHANGELOG_WEEKLY.md`, `docs/KNOWN_LIMITS.md`, command map и таблицу рисков. При отсутствии live проверки пометить `UNVERIFIED` и создать backlog, без тихого изменения маршрута или цены. Для production перехода polling→webhook описать один consumer, секрет webhook header, TLS, switch/rollback и сохранение update inbox. **В этом ТЗ переключение не выполняется.**

## 8. Порядок реализации Qoder

| Фаза | Работа | Выходной gate |
|---|---|---|
| P0 Discovery | Карта действующих handlers/routes, account/forecast/paper APIs и таблиц; legacy sim, dirty files, transport, права, Mini App URL, тесты. | `TELEGRAM-BOT-RFC.md`: факты/неизвестное, current→target command map, версии контрактов, риски. Без изменения runtime. |
| P1 RFC/архитектура | Context/module/normal/fallback/quota-exhausted sequence, schema/migrations/rollback, security model, env registry, error taxonomy, UX flow и mockups карточек. | Согласованные DTO/state machine с MC-V3 и план обратной совместимости. |
| P2 Read vertical slice | Middleware deny-default, `/start`, `/now`, market freshness, forecast summary, главное меню; bot-only repo и typed API. | Реальный source→backend→Telegram mock; old commands redirected; permission negative tests. |
| P3 Plan | persisted wizard, manual plan, agent questionnaire/proposal, deterministic validation, version/diff/approval. | Из UI виден тот же `plan_hash` и violations, что в WebUI; AI failure safe. |
| P4 Commands | start/pause/resume/tighten/finish, manual trade preview/confirm, positions, idempotency and recovery. | Full paper command→order→fill→position→ledger trace в изолированной QA. |
| P5 Reports/notifications | ledger-derived summary, journal, durable outbox, rules, quiet hours, incident dedup. | Telegram/WebUI суммы идентичны, retry без дублей. |
| P6 AI/admin/Mini App | controlled chat, model policy/quotas, scoped admin, valid initData, deep links and mobile UI. | Security suite + Android/iOS Telegram smoke; нет обхода прав. |
| P7 Hardening/docs | failures/soak/restart/rollback, lint/type/tests, docs, weekly refresh procedure, legacy sunset after verification. | TB-01…TB-32 PASS и MC dependency PASS; иначе partial/blocked report. |

В начале каждой фазы Qoder сообщает цель, active path, список файлов, diff/изменение контракта, риск регрессии, команды проверки; после — фактический stdout/артефакты, статус TB и оставшиеся ограничения. Файлы `chatbot/main.py` и production Compose уже могут иметь параллельные правки: перед merge повторить diff и не перетирать их. Code changes — отдельно от этого документа.

## 9. Приёмка TB-01…TB-32

Статусы: `PASS` только с указанным доказательством; `PARTIAL`, `FAIL`, `BLOCKED`, `NOT RUN` по остальным. Изолированная PG/mock Telegram проверка не называется live Telegram acceptance. Общий релизный PASS невозможен при required `FAIL/BLOCKED` или непройденном MC-V3 финансовом lifecycle.

| ID | Проверка и обязательное доказательство |
|---|---|
| TB-01 | Unknown user, group, чужой callback, viewer mutation получают deny без утечки. Middleware/security tests. |
| TB-02 | `/start` и `/now` показывают один рынок, явные mode/source/age/quality; no-data/stale не становятся 0/green. Fixture + Telegram mock. |
| TB-03 | Старые команды вызывают целевой use case или документированный deprecation ответ; нет разных балансов у `/portfolio` и `/positions`. Contract tests. |
| TB-04 | 1–3 нажатия до рыночной цены/последнего прогноза/открытых позиций/итога; пользовательское тестирование с логом действий. |
| TB-05 | Callback ACK быстрый, old/expired/replayed/foreign callback не мутирует состояние. Timed/security tests. |
| TB-06 | Repeated Telegram `update_id` после перезапуска не удваивает command. DB integration. |
| TB-07 | Forecast 15m/1h/4h/24h содержит ID/base/target/value/range/method/status/model; ошибки не скрыты. Contract tests. |
| TB-08 | График forecast wick только для валидных OHLC; point+band без ложных фитилей. MC-V3 browser + mobile. |
| TB-09 | Прогноз в Telegram и WebUI с одним ID/значениями; forecast-vs-fact не изменяет исходную запись. API equality. |
| TB-10 | Manual wizard сохраняется после restart, истекает по TTL и валидирует все поля `SimulationPlanV1`. PG tests. |
| TB-11 | AI proposal не стартует сессию, проходит тот же deterministic validator; wrong/timeout/quota → manual route. Negative tests. |
| TB-12 | Preview плана показывает обе суммы, лимиты, data quality и hash; изменение версии требует нового approval. Integration. |
| TB-13 | Start accepted/processing/completed/unknown различаются; двойной tap/lost response один session. Crash/retry PG. |
| TB-14 | Pause блокирует новые auto entries, не объявляет открытые позиции закрытыми; resume rechecks. Domain integration. |
| TB-15 | Finish/close all требует preview+confirmation, `settlement_pending` не выдаёт финальный отчёт. PG/Telegram mock. |
| TB-16 | Manual и auto счета разделены по account/ledger/budget, ручное открытие не пишет в auto. PG invariant. |
| TB-17 | Ручной preview показывает bid/ask/mark, fees/funding/slippage/risk; stale/limit/invalid size блокирует confirm. Negative tests. |
| TB-18 | Open→fill→position→partial/full close→postings→net traced через IDs и совпадает с WebUI. QA integration. |
| TB-19 | Liquidation/pending/closed имеют отдельные статусы, reason, single fill/posting при retry. PG crash/retry. |
| TB-20 | Позиции фильтруются по account/status/session, owner scope и пагинации; ID/суммы совпадают с API. Contract tests. |
| TB-21 | Report из закрытой сессии содержит обе независимые метрики/расходы/reconciliation; старый report immutable. Golden tests. |
| TB-22 | CSV/JSON/HTML, Telegram и WebUI совпадают по нормализованным числам и status. Export round-trip. |
| TB-23 | Replay помечен replay, содержит data hash и holdout/baseline; N/A вместо недоказанной accuracy. Integration. |
| TB-24 | Outbox переживает restart, dedup и 429; critical один incident, quiet hours не скрывают critical. Failure injection. |
| TB-25 | Lost Telegram delivery не создаёт вторую финансовую команду; статус доступен по `command_id`. Failure injection. |
| TB-26 | AI chat не исполняет свободный текст как trade/admin; prompt injection из market/news/response не получает tool rights. Security tests. |
| TB-27 | Free/paid quota gate, token/cost trace, fallback on timeout/invalid/quota; premium не включается одним ответом модели. Adapter contract tests. |
| TB-28 | Mini App: validated initData/auth_date/owner/CSRF; tampered/expired/other bot rejected; HTTPS deep links. Security/browser. |
| TB-29 | Реальный Telegram Android и iOS: safe area, back navigation, buttons, keyboard, chart link, Russian text. Device evidence; иначе BLOCKED. |
| TB-30 | Backend/Redis/Telegram outage, restart, stale feed, pending commands дают честные карточки и recovery. Fault tests. |
| TB-31 | Логи/экспорты не содержат токен, initData, чужие IDs/сообщения; retention/permission audit. Security scan. |
| TB-32 | Финансовые показатели подтверждены MC-V3/AC-01…AC-28; ни fixture PASS, ни HTTP 200 не выдаются за торговую готовность. Evidence bundle. |

## 10. Команды проверки, документы и формат сдачи

Перед правками из `D:\WORED` (PowerShell; только read-only):

```powershell
git status --short
git diff -- chatbot/main.py docker-compose.yml
docker compose -f docker-compose.qa.yml config --quiet
python -m pytest chatbot/tests tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q
```

Если `docker-compose.qa.yml` отсутствует или QA environment не поднимается, статус соответствующих интеграционных тестов `BLOCKED`; не подменять production Compose. После реализации запускать новые `tests/telegram/`, необходимые `tests/paper_trading/`, UI acceptance из MC-V3 и `docker compose config --quiet`; конкретные команды обновить в `docs/TELEGRAM-BOT-OPERATIONS.md` после фактического P0 inventory. Для QA-Postgres использовать только `wored-qa`/`wored_qa` согласно текущему изолированному контуру. Никакой `docker compose down -v` в рабочем проекте, никаких secrets в stdout. Для real Telegram smoke использовать отдельный test bot/token и ограниченный owner ID, не отправлять тестовые уведомления рабочему владельцу; если отдельного токена нет, отметить `BLOCKED`, а не послать в production.

Необходимые документы по завершении: RFC с diagram/sequence/schema/env/security/error taxonomy; operations runbook с setup/health/migrations/backup/rollback/transport switch/инцидентами; command map; acceptance matrix с доказательствами; обновлённые `README.md`, `docs/04-env-vars.md`, `docs/09-telegram-commands.md`, `docs/10-testing.md`, `docs/11-troubleshooting.md`, `docs/13-weekly-refresh.md`, `docs/14-security.md`, `docs/15-acceptance-checklist.md`, `CHANGELOG_WEEKLY.md`, `docs/KNOWN_LIMITS.md`. В каждом документе: цель, предпосылки, точные copy-paste команды после env setup, ожидаемый output, проверка, типовые ошибки, исправление, rollback. Если названный файл пока отсутствует — создать в соответствующей фазе, не оставлять незаполненных критичных разделов.

Формат ответа Qoder по каждой фазе: (1) цель; (2) допущения/неизвестное; (3) архитектурное решение; (4) дерево файлов и полный diff новых критичных файлов; (5) команды запуска/миграции/rollback; (6) фактически выполненные тесты и stdout; (7) таблица TB/MC статусов и evidence level; (8) обновлённые документы; (9) риски и следующий шаг. Для runtime доказательства приложить UTC time, commit SHA, environment, `forecast_id/session_id/command_id/position_id/report_id`, sanitized trace, mobile screenshots и ledger reconciliation. Не писать «готово», пока требуемый end-to-end не прошёл.

## Официальные источники для проверки на дату реализации

- Telegram Bot API: https://core.telegram.org/bots/api — `getUpdates`/webhook, `update_id`, callback query, command scopes, лимиты payload.
- Telegram Mini Apps: https://core.telegram.org/bots/webapps — `initData` validation, `auth_date`, client capabilities и launch modes.
- Telegram Bots FAQ: https://core.telegram.org/bots/faq — получение updates и эксплуатационные ограничения.
- aiogram dispatcher: https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html — polling concurrency/transport behaviour выбранной установленной версии.
