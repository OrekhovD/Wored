# Техническое задание 02 для QwenCode

## 1. Назначение документа

Этот документ задает следующий этап работ после реализации MVP локального Telegram AI gateway для HTX. Цель этапа не в добавлении случайных новых функций, а в доведении текущей реализации до состояния контролируемого release candidate: с закрытыми архитектурными долгами, проверяемыми quality gates, безопасной конфигурацией, нормализованной документацией, воспроизводимыми тестами и доказуемой надежностью multi-provider маршрутизации.

Документ предназначен для разработчика `QwenCode`, который продолжает работу в существующем репозитории `D:\WORED\hypercube`.

## 2. Базовое состояние проекта

На момент старта этого этапа считаем, что в репозитории уже существуют:

- базовая модульная структура проекта;
- Telegram bot layer;
- FastAPI gateway;
- HTX read-only adapter;
- минимум один рабочий AI adapter;
- routing layer;
- accounting layer;
- quota layer;
- context service;
- SQLite persistence;
- Docker runtime;
- smoke tests;
- README и набор первичной документации.

При этом текущий этап исходит из того, что наличие кода и документации еще не означает эксплуатационную зрелость. QwenCode обязан проверить фактическое состояние проекта и не считать текстовые утверждения о готовности достаточным доказательством.

## 3. Главная цель этапа

Довести проект до release-candidate уровня для локального использования одним оператором с минимальным ручным угадыванием.

Результат этапа должен доказать:

- что multi-provider routing реально воспроизводим и управляется конфигом;
- что quota warning, hard stop и fallback покрыты тестами;
- что context handoff устойчив и не теряет рабочую суть диалога;
- что проект не хранит секреты в репозитории и не светит их в логах;
- что Docker startup, health checks и smoke tests дают надежный go/no-go сигнал;
- что документация годится для слабой модели и junior developer без скрытых шагов;
- что weekly refresh теперь является реальным рабочим контуром, а не только декларацией.

## 4. Границы этапа

### 4.1 Что входит в scope

- аудит текущей реализации и gap analysis;
- hardening routing, quotas, context handoff, health checks и provider failover;
- перевод ключевых политик из hardcoded состояния в config-driven вид;
- security cleanup и устранение утечек секретов;
- выравнивание документации, кодировок и operational runbooks;
- расширение test matrix до внятного release gate;
- weekly refresh subsystem;
- release checklist и итоговый go/no-go report.

### 4.2 Что не входит в scope

- добавление торговых операций;
- добавление новых бирж;
- переход на облачный multi-user режим;
- переписывание проекта на другой стек;
- преждевременный production deployment;
- введение сложной распределенной инфраструктуры.

## 5. Непереговорные требования

- Не добавлять торговые методы HTX.
- Не оставлять секреты в репозитории, docs или тестовых фикстурах.
- Не считать этап завершенным без green quality gates.
- Не писать итоговую “финальную” документацию до завершения проверок.
- Не держать routing policy в хардкоде там, где она должна жить в конфиге или registry.
- Не маскировать провалы тестов фразами вроде “работает в основном”.
- Не подменять отсутствие доказательства декларативным отчетом.

## 6. Рабочие пакеты

## WP-1. Repository Audit And Gap Report

### Цель

Проверить, что реально реализовано, а что только заявлено.

### Обязательные действия

- провести файловый аудит текущей структуры;
- сопоставить код с исходным ТЗ и текущими docs;
- выделить расхождения между заявленным статусом и реальным кодом;
- выделить блокирующие проблемы release-candidate уровня;
- выделить некритичные, но важные улучшения.

### Обязательный результат

Создать документ:

- `docs/16-gap-audit.md`

### Формат документа

Для каждой найденной проблемы указать:

- ID;
- краткое название;
- категория: architecture | security | testing | docs | runtime | provider | context | quotas;
- severity: blocker | high | medium | low;
- текущее состояние;
- почему это проблема;
- что требуется исправить;
- как проверить исправление.

## WP-2. Security Cleanup And Secret Hygiene

### Цель

Привести проект в состояние, где секреты не попадают в git-tracked документы и не утекут через логи или примеры.

### Обязательные действия

- проверить наличие секретов в `.env`, `docs/`, examples и любых текстовых файлах;
- исключить чувствительные файлы из репозитория через `.gitignore`, если это еще не сделано;
- заменить секреты в примерах на безопасные placeholder-значения;
- проверить, что `.env.example` содержит только безопасные значения;
- проверить, что логирование не пишет ключи и bearer tokens;
- зафиксировать процедуру ротации ключей.

### Обязательные артефакты

- `docs/17-security-remediation.md`
- обновленный `.gitignore`, если требуется;
- обновленный `.env.example`;
- runbook по ротации ключей в `docs/14-security.md` или эквивалентном файле.

### Критерий готовности

- в репозитории не остается файлов с live secrets;
- все секреты документированы только как env vars;
- журнал запуска не содержит несанкционированного вывода чувствительных данных.

## WP-3. Config-Driven Routing And Provider Registry

### Цель

Сделать routing, candidate chains и provider capabilities управляемыми конфигом, а не жестко встроенными значениями.

### Проблема, которую нужно решить

Текущий routing не должен зависеть от hardcoded candidate chains и manual mapping, если проект заявлен как много-провайдерный gateway.

### Обязательные действия

- вынести модельный registry из жестко зашитых списков в машиночитаемый конфиг или DB-backed registry;
- сделать candidate chain настраиваемой по mode, provider availability и premium flag;
- сделать переключение моделей управляемым внешней конфигурацией;
- поддержать disable/enable providers без редактирования core-кода;
- добавить reason codes на exclude/skip/select/fallback;
- формализовать premium unlock policy.

### Обязательные артефакты

- `docs/18-provider-validation-and-registry.md`
- `docs/06-routing-policy.md` с актуальной схемой;
- новый registry файл или DB seed artifact, например:
  - `examples/provider_registry.yaml`, или
  - `data/provider_registry.json`

### Минимальные сценарии

- provider disabled by config;
- model excluded in `free_only`;
- quota stop forces fallback;
- provider health downgrade removes candidate;
- manual switch uses only allowed target models.

## WP-4. Context Handoff Hardening

### Цель

Довести handoff между моделями до устойчивого и измеримого механизма, а не до формальной передачи строки.

### Обязательные действия

- описать versioned handoff schema;
- различать raw conversation history, compressed context и handoff package;
- добавить context freshness marker;
- добавить market delta refresh перед handoff, если рыночные данные устарели;
- документировать compression strategy и limits;
- фиксировать handoff events в storage;
- доказать тестами, что manual switch и fallback switch сохраняют рабочую суть.

### Обязательные артефакты

- `docs/19-context-handoff-spec.md`
- обновление `context/` и `storage/` модулей при необходимости;
- новые unit и integration tests.

### Критерий готовности

После switch новая модель получает:

- актуальный system frame;
- summarized working context;
- последнюю user intent;
- последние релевантные market facts;
- ограничения ответа;
- метку причины переключения.

## WP-5. Provider Reliability, Timeouts, Retries, Circuit Breakers

### Цель

Устранить хрупкость при нестабильности внешних провайдеров.

### Обязательные действия

- ввести timeout policies per provider;
- добавить retries with jitter для retryable failures;
- разделить retryable и non-retryable ошибки;
- добавить circuit breaker semantics;
- добавить provider cool-down state;
- сделать health state observable через `/health/deep` и admin diagnostics;
- проверить, что fallback срабатывает без бесконечных повторов.

### Обязательные артефакты

- `docs/20-reliability-and-observability.md`
- обновленные health endpoints;
- тесты на timeout, retry, circuit-open и recovery.

### Критерий готовности

Система различает:

- transient timeout;
- rate limit;
- quota exceeded;
- malformed response;
- provider unavailable;
- policy rejection.

И каждая из этих причин влияет на routing предсказуемо.

## WP-6. Quality Gates Closure

### Цель

Сделать качество проекта измеримым, а не декларативным.

### Обязательные действия

- привести `ruff`, `black`, `pytest` и `mypy` к воспроизводимому запуску;
- если `mypy` заявлен в `pyproject.toml`, но отсутствует как runtime dev dependency, исправить это;
- разделить unit, integration, smoke тесты по надежным маркерам;
- добавить coverage report для ключевых модулей;
- убедиться, что Docker smoke test не требует ручных догадок;
- добавить один командный сценарий “полный gate”.

### Обязательные артефакты

- `docs/10-testing.md`
- `docs/21-release-checklist.md`
- helper scripts, если нужно:
  - `scripts/doctor.py`
  - `scripts/smoke_test.py`
  - `scripts/run_quality_gate.py`

### Обязательные команды, которые должны реально работать

```powershell
ruff check .
black --check .
mypy .
pytest -m unit
pytest -m integration
pytest -m smoke
docker compose -f docker/docker-compose.yml up --build -d
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml down
```

### Критерий готовности

Все quality gates зеленые либо документированно помечены как blocked с конкретной причиной и remediation plan.

## WP-7. Documentation Normalization And Encoding Repair

### Цель

Убрать технический долг документации, включая возможную порчу кодировок и разнобой по структуре.

### Обязательные действия

- привести markdown-файлы к UTF-8 без сломанной кириллицы;
- синхронизировать README и docs с фактической реализацией;
- добавить Verify / Fail / Fix / Rollback там, где процедуры отсутствуют;
- собрать единый operations runbook;
- объяснить точные команды запуска, миграций, тестов, health checks и recovery.

### Обязательные документы

- `README.md`
- `docs/03-local-setup.md`
- `docs/11-troubleshooting.md`
- `docs/12-operations-runbook.md`
- `docs/15-acceptance-checklist.md`

### Критерий готовности

Слабая модель и junior developer могут:

- поднять проект;
- проверить health;
- запустить тесты;
- понять, как переключается модель;
- понять, как восстановить систему после сбоя;
- не догадываться о скрытых шагах.

## WP-8. Weekly Refresh Activation

### Цель

Превратить weekly refresh из идеи в реально обслуживаемый операционный контур.

### Обязательные действия

- определить provider registry update workflow;
- описать manual weekly review checklist;
- завести changelog artifacts;
- фиксировать deprecations и known limits;
- добавить правила обновления цены, лимитов и token semantics.

### Обязательные артефакты

- `CHANGELOG_WEEKLY.md`
- `docs/KNOWN_LIMITS.md`
- `docs/DEPRECATIONS.md`
- `docs/PROVIDER_DIFF.md`
- `docs/13-weekly-refresh.md`

### Критерий готовности

Есть понятный ритм weekly refresh и audit trail по каждому циклу.

## 7. Дополнительные функциональные требования к текущему коду

### 7.1 Telegram UX Around Low Quota

При low quota бот обязан:

- предупреждать до отправки длинного запроса;
- объяснять риск прерывания;
- предлагать краткий ответ;
- предлагать manual switch;
- логировать предупреждение.

### 7.2 Telegram UX Around Model Switch

После switch бот обязан коротко сообщать:

- с какой модели ушли;
- на какую модель перешли;
- почему произошел switch;
- был ли handoff;
- были ли потери детализации;
- сохраняется ли текущий режим.

### 7.3 Admin Diagnostics

Админ должен видеть:

- provider health;
- circuit state;
- recent fallback events;
- recent quota stops;
- recent context handoff events;
- model usage by day/week/month.

## 8. Требования к новым или обновленным тестам

Минимум нужно покрыть:

- config-driven registry loading;
- provider disable/enable behavior;
- fallback after timeout;
- fallback after quota exceeded;
- no candidate available path;
- manual switch with valid handoff package;
- stale market facts cause delta refresh before handoff;
- low quota warning path through Telegram command flow;
- hard stop path through Telegram command flow;
- `/health/deep` returns degraded status when provider circuit is open;
- smoke test реального startup path через Docker Compose.

## 9. Ожидаемое дерево файлов для этого этапа

Минимум должны появиться или быть обновлены:

```text
docs/
  16-gap-audit.md
  17-security-remediation.md
  18-provider-validation-and-registry.md
  19-context-handoff-spec.md
  20-reliability-and-observability.md
  21-release-checklist.md
  KNOWN_LIMITS.md
  DEPRECATIONS.md
  PROVIDER_DIFF.md
CHANGELOG_WEEKLY.md
data/
  provider_registry.json or provider_registry.yaml
scripts/
  doctor.py or doctor.ps1
  run_quality_gate.py
tests/
  unit/
  integration/
  smoke/
```

Допускается небольшое отклонение в именах файлов, но только если итоговая структура остается явной и хорошо документированной.

## 10. Порядок выполнения работ

QwenCode обязан работать в этом порядке:

1. Audit and gap report
2. Security cleanup
3. Config-driven routing and registry
4. Context handoff hardening
5. Reliability hardening
6. Test matrix closure
7. Documentation normalization
8. Weekly refresh activation
9. Release checklist and go/no-go report

Нельзя начинать “финальный polishing” до закрытия audit findings категории blocker/high.

## 11. Формат отчетности для QwenCode

Каждый отчет по пакету работ должен содержать:

1. Goal restatement
2. Current baseline
3. Findings or decisions
4. File tree for the batch
5. Full contents for critical files
6. Commands run
7. Tests run
8. Expected and observed outcomes
9. Documentation updates
10. Remaining blockers and next step

Если этап заблокирован, отчет обязан содержать:

- конкретный блокер;
- почему он блокирует;
- что уже проверено;
- какой минимальный input нужен от Lead Agent или пользователя.

## 12. Критерии приемки этапа

Этап считается принятым только если:

- секреты очищены из репозитория и примеров;
- routing policy больше не живет только в hardcoded lists;
- context handoff доказуемо работает через тесты;
- fallback и quota stop доказуемо работают через тесты;
- timeout, retry и circuit breaker отрабатывают предсказуемо;
- docs читаемы, согласованы и не содержат сломанной кодировки;
- weekly refresh artifacts существуют;
- quality gate выполняется одной воспроизводимой последовательностью команд;
- release checklist содержит явный go/no-go verdict.

## 13. Явные запреты

- Не скрывать незавершенные части под формулировкой “позже доделаем”.
- Не держать live keys в репозитории ни в каком виде.
- Не считать README заменой runbook и acceptance checklist.
- Не переписывать архитектуру без audit-обоснования.
- Не пропускать тесты для fallback, quotas и handoff.
- Не удалять пользовательские данные без резервной процедуры.

## 14. Следующий шаг после завершения этого ТЗ

После полного выполнения этого этапа следующим документом должен стать:

- либо `TZ-03` на расширение аналитических сценариев HTX и operator tooling;
- либо `TZ-03` на controlled VPS migration and deployment hardening;

Но только после получения green release-candidate verdict по текущему этапу.
