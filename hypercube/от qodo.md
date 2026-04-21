Короткий вердикт: Needs changes

Краткое резюме (2–4 строки): Я провёл детальный обзор структуры проекта и тестов в репозитории d:\WORED\hypercube. Тестовый раннер не установлен в окружении (pytest не найден), в кодовой базе есть набор unit/integration/smoke тестов, но я нашёл потенциальные проблемные места в реализации модулей, отсутствие некоторых валидаций и пограничных тестов, а также риск нестабильности при параллельном выполнении и в продакшн‑конфигурации. Ниже — конкретные находки с рекомендациями.

Findings:

Severity: High Location: tests/unit/test_handoff.py (и соответствующие модули context/handoff.py, core/schemas.py) Problem: Тесты используют HandoffBuilder._build_summary, _last_user_message и _format_system_message (приватные методы), но в проекте может отсутствовать проверка на None/неожиданные структуры сообщений; сериализация/десериализация HandoffSchemaV1 предполагает, что created_at всегда корректно парсится в datetime. Why it matters: Использование приватных методов в тестах маскирует отсутствие публичного API; отсутствие валидации полей и обработки неверных типов может приводить к исключениям при реальных данных от провайдера/пользователя. Recommended fix: Сделать публичные конструкторы/валидаторы для HandoffPackage и HandoffSchemaV1; добавить явные проверки типов и значения для полей (например, created_at допускает строку ISO и datetime); в HandoffBuilder проверять структуру сообщений и безопасно обрабатывать отсутствующие ключи.
Severity: High Location: tests/unit/test_resilience.py → providers/resilience.py Problem: Тесты предполагают поведение CircuitBreaker (переключения состояний, подсчёт вызовов) и RetryHandler с изменяемой переменной call_count внутри вложенной асинхронной функции, но в тестах call_count объявлен как обычная переменная и изменяется внутри вложенной async функции без nonlocal, что в реальности не увеличит внешнюю переменную — тесты ошибочны или код реализации использует замыкания неправильно. Why it matters: Неправильная мутация переменной в тестах указывает на потенциальную некорректность теста или на риск, что реализация содержит race conditions; если реализация опирается на побочные эффекты, это плохо протестировано и хрупко. Recommended fix: Исправить тесты, используя mutable контейнер (например, list: call_count = [0]; then call_count[0] += 1) или nonlocal в вложенной функции; в реализации resilience убедиться, что счётчики потокобезопасны и атомарны (AsyncLock/Atomic int).
Severity: High Location: providers/resilience.py (наружный интерфейс: CircuitBreaker.execute / can_execute) Problem: Тесты ожидают, что cb.execute(failing_func) выбрасывает исходное исключение (RuntimeError) и при достижении порога переключает состояние в OPEN; реализация может скрывать исключения или перезаписывать стейт в небезопасном порядке, и нет явного теста на race‑condition при параллельных вызовах execute. Why it matters: Неправильная обработка исключений и состязания при переключении состояний могут привести к неверному открытию/закрытию цепи, пропуску запросов к провайдерам или бесконечной блокировке. Recommended fix: Убедиться, что execute атомарно обновляет счётчики и состояние, использовать асинхронные блокировки (asyncio.Lock) и добавить тесты на параллельные выполнения (где N concurrent failing calls) чтобы проверить корректные переходы состояний.
Severity: Medium Location: tests/unit/test_routing.py и modules routing/* Problem: ModelRegistry тесты ожидают 5 моделей по умолчанию и конкретные provider_id / is_premium значения; но инициализация может зависеть от внешних конфигов или env vars, и нет теста, покрывающего некорректную или пустую конфигурацию модели. Why it matters: Жестко кодированные ожидания числа моделей делают тесты хрупкими при изменении конфигурации; в продакшне возможны ситуации с пустой или частично загруженной конфигурацией. Recommended fix: Сделать загрузку дефолтной конфигурации явной и детерминированной (встроенный fixture), добавить тесты на поведение при пустом/частичном конфиге и обеспечить понятные ошибки/логирование.
Severity: Medium Location: requirements.txt + Dockerfile Problem: requirements.txt фиксирует версии пакетов (хорошо), но types-pyyaml имеет странную будущую версию suffix (20241230) — потенциальная ошибка в зависимостях; Dockerfile копирует requirements.txt и устанавливает их, но нет отдельного requirements-dev.txt, что затрудняет CI отделение runtime и dev deps. Why it matters: Неправильные/нестабильные dev-зависимости или смешение prod/dev deps в образе усложняют CI и размер образа. Recommended fix: Разделить requirements.txt (runtime) и requirements-dev.txt (pytest, mypy, black, ruff), и обновить Dockerfile чтобы устанавливать только runtime deps; проверить типы пакетов и убрать некорректные версии.
Severity: Medium Location: tests overall — отсутствие исполнения в текущем окружении Problem: На CI/локальной машине pytest не установлен, и инструкции по установке окружения не автоматизированы; отсутствует Makefile/Make target run-tests в корне, хотя в docs упоминается Makefile. Why it matters: Ручной запуск тестов увеличивает ошибку человека и делает CI менее надёжным. Recommended fix: Добавить Makefile/CLI-скрипт для подготовки виртуального окружения и запуска lint/typecheck/tests; добавить CI workflow (GitHub Actions/GitLab CI) который устанавливает deps и запускает gates.
Severity: Low Location: tests/unit/test_handoff.py (локализация) Problem: Тесты содержат англоязычные ожидаемые строки ("User Requests", "Model Conclusions") в проекте с русской кодовой базой и русскоязычными сообщениями в тестах; смешение языков усложняет поддержку. Why it matters: Трудность поддержки и локализационная ошибка при изменении формата сообщений. Recommended fix: Привести ожидаемые строки в тестах в соответствие с реальным форматом, или использовать структурированные проверки (по ключам) вместо проверки фрагментов текста.
Missing tests:

Concurrency tests for providers/resilience: N concurrent failing calls to CircuitBreaker.execute to verify consistent state transitions (critical).
Serialization edge-cases for HandoffSchemaV1: invalid created_at formats, missing fields, extra fields, very large strings (high).
ModelRegistry robustness: behavior with empty/default/overridden configuration, env var changes (medium).
Routing fallback handling: tests for fallback reasons triggered by timeout, rate_limit, provider_unavailable, policy_rejection (high).
Integration tests for provider adapters: mock provider timeouts, partial token counts, and ensure accounting persists "uncertainty" per repository contract (high).
Tests for error logging and not leaking secrets: ensure exceptions don't include sensitive env vars or tokens (security critical).
Tests for RetryHandler respecting non-retryable exceptions and backoff behavior under load (medium).
Optional improvements (non-blocking):

Replace mutable global counters with AtomicCounters or use asyncio.Lock where shared state is mutated.
Prefer public APIs in tests; avoid asserting on private methods. If private behavior must be tested, add clear justification and comments.
Add a test harness script or Makefile targets: make test, make lint, make typecheck, make docker-test.
Add CI pipeline snippet to docs showing exact commands to run gates.
Enforce stricter mypy/ruff rules in CI to catch subtle type issues early.
Final recommendation:

Нельзя мёржить изменения, не устранив критические и высокие риски: привести в порядок resilience (параллельность и атомарность), исправить тесты с некорректной мутацией call_count, добавить concurrency tests для CircuitBreaker/Retry/Timeout, и добавить подготовку окружения в репозицию (Makefile / requirements-dev.txt). После этого — запустить полный набор gate (lint, mypy, pytest) в CI и устранить оставшиеся падения.
Точные шаги для разработчика:

Исправить тесты, где call_count мутируется — использовать список или nonlocal.
Обновить providers/resilience.py: гарантировать атомарную смену состояний, использовать async locks, и добавить логирование при смене состояния.
Добавить тесты на конкурентные сценарии (с asyncio.gather) для CircuitBreaker.execute.
Разделить зависимости на requirements.txt и requirements-dev.txt, обновить Dockerfile.
Добавить Makefile/CI workflow с командами: pip install -r requirements-dev.txt; ruff ., mypy ., pytest -q --maxfail=1.
Перезапустить тесты и прислать результаты; в ответе я проверю логи и дам дальнейшие указания.
Если нужно, могу помочь с минимальными корректировками тестов (например, показать исправленный пример для call_count) и примером теста на параллельные вызовы CircuitBreaker.execute. В противном случае предполагаю, что следующие действия будут выполнены разработчиком и жду обновлённого прогресса.

GPT-5
напиши ТЗ разработчику на Gemini 3 flash, максимально подробное чтобы он не ошибся.

Цель Внедрить поддержку провайдера Gemini 3 (Flash) в проект hypercube как новый адаптер провайдеров моделей, соблюдая архитектурные контракты репозитория (разделение слоёв, учёт, отказоустойчивость, безопасность) и требования качества: тесты, CI, документация и детерминированная конфигурация. Результат — рабочий, покрытый тестами и безопасный модуль-адаптер, который можно включить в routing/provider_manager без ломающих изменений.

Перечень зада�� (KRs)
Реализовать асинхронный адаптер providers/gemini_flash_adapter.py с поддержкой: sync/streaming ответов (если Gemini поддерживает streaming), обработкой ошибок, backoff/429, retries по конфигу.
Интерфейс адаптера должен соответствовать существующим адаптерам в providers/ и предоставлять структурированный ответ ProviderResponse (см. контракт учёта).
Обеспечить корректную сериализацию/десериализацию токенов и стоимости (cost estimate) и хранение "uncertainty" если данные неполные.
Добавить unit/integration/smoke тесты + mocks для всех ветвей обработки ошибок и успешного пути.
Добавить документацию docs/PROVIDERS/gemini_flash.md и обновить docs/PROVIDER_DIFF.md.
Обновить CI (workflow) чтобы включить линт, mypy, pytest и docker-smoke с новым адаптером.
Не допускать попадания API-ключей в репозиторий, логирование или тест-артефакты.
Предположения и неизвестные
Предполагается, что Gemini 3 Flash имеет HTTP(S) API с ключевой авторизацией; если у него gRPC/нестандартный протокол — сообщить и адаптировать.
Неизвестны точные форматы ответов (полные/частичные token counts, streaming protocol). Реализовать адаптер с конфигурируемым парсингом и ясной обработкой отсутствующих метрик.
Неизвестны реальные rate limits; предусмотреть конфиг для throttle/backoff/retry.
Проект ожидает асинхронные адаптеры (aiohttp/httpx) — использовать httpx.AsyncClient по умолчанию (если в проекте уже используется httpx, обеспечить совместимость).
Архитектурные решения (кратко)
Паттерн: Provider Adapter (в папке providers/) реализует интерфейс ProviderAdapter (методы: async send_request(request: ProviderRequest) -> ProviderResponse, supports_streaming() -> bool).
В адаптере: отдельные слои — transport (httpx), parser (ответ -> ProviderResponse), metrics/logging (без секретов), resilience (CircuitBreaker/Retry/Timeout) — использовать существующие providers/resilience компоненты.
Конфигурация: все параметры (endpoint, key, model mapping, timeouts, rate limits, cost-per-token) — в env vars/Settings (pydantic-settings). Не хранить креденшелы в коде.
Accounting contract: адаптер обязан вернуть: request_id, provider, model, input_tokens (if available), output_tokens (if available), latency_ms, status, cost_estimate, uncertainty_flags (booleans for token counts).
Ошибки: адаптер должен поднимать специализированные исключения ProviderError, ProviderTimeoutError, ProviderRateLimitError, и не выбрасывать сырой httpx исключения наружу.
Целевая структура файлов (предложение)
providers/
gemini_flash_adapter.py # новый адаптер
init.py
tests/
unit/
test_gemini_flash_adapter.py
integration/
test_gemini_flash_integration.py # использует VCR или httpx mocking
smoke/
test_gemini_flash_smoke.py
docs/PROVIDERS/gemini_flash.md
docs/PROVIDER_DIFF.md (обновление)
.github/workflows/ci.yml (обновление)
configs/example.env.gemini # пример env vars (только шаблон, без секретов)
routing/provider_registry.yml (если требуется ручная регистрация модели)
Технические детали реализации (чётко и пошагово)
A. Интерфейс и контракты

В файле providers/base.py (если есть) либо добавить в providers/gemini_flash_adapter.py документированный интерфейс:
class ProviderRequest:
request_id: str
model: str
prompt: str | list[messages] (согласно формату проекта)
max_tokens: int
temperature: float
streaming: bool
metadata: dict
class ProviderResponse:
request_id: str
provider: str # 'gemini_flash'
model: str
content: str | AsyncIterator[str] # если streaming = True, вернуть async iterator
input_tokens: Optional[int]
output_tokens: Optional[int]
latency_ms: int
status: str # 'success'|'timeout'|'rate_limited'|'error'
cost_estimate: Optional[float]
tokens_uncertain: bool # True если токены не вернул провайдер
Исключения:
ProviderError(base)
ProviderTimeoutError(ProviderError)
ProviderRateLimitError(ProviderError)
ProviderResponseError(ProviderError) # для 4xx/5xx
B. Адаптер: основная логика providers/gemini_flash_adapter.py

Инициализация:
Чтение конфигурации через pydantic-settings: GEMINI_FLASH_API_KEY, GEMINI_FLASH_ENDPOINT, GEMINI_FLASH_TIMEOUT_SECONDS, GEMINI_FLASH_MAX_CONCURRENCY, GEMINI_FLASH_COST_PER_TOKEN (опционально), GEMINI_FLASH_STREAMING_ENABLED и т.д.
Transport:
Использовать httpx.AsyncClient(timeout=...) с limits (max_keepalive, max_connections) конфигурируемыми.
Установить заголовок Authorization: Bearer <API_KEY> и User-Agent: hypercube/<<version>>.
Отправка запроса:
Сформировать JSON в формате API Gemini; минимально: модель, prompt/messages, max_tokens, temperature, stream (bool).
Измерить тайминги (start = monotonic(), end = monotonic()) — записать latency_ms = int((end-start)*1000).
Обработка ответа:
При успешном коде (200) — парсить тело:
Если провайдер возвращает токены: использовать; иначе поставить tokens_uncertain = True.
Если streaming: возвращать content как async generator который yield-ит чанки; при каждом чанке — по возможности обновлять output_tokens и accounting (см. заметки ниже).
При 429: выбрасывать ProviderRateLimitError, логировать заголовки Retry-After.
При 4xx: ProviderResponseError с кодом и message.
При 5xx/timeout/connection: ProviderError/ProviderTimeoutError в зависимости от причины.
Безопасность логов:
Никогда не логировать API_KEY, Authorization header, или полные текстовые промпты в логах; логи только метаданные (model, request_id, provider, status, latency_ms) и обрезанные/хэшированные prompt id если нужно.
Accounting:
Перед возвратом адаптер должен вызвать ядро учёта/metrics, либо вернуть ProviderResponse со всеми полями; ядро маршрутизации/manager обязано сохранить запись.
cost_estimate = output_tokens * cost_per_output_token + input_tokens * cost_per_input_token (если неизвестно — поставить None и tokens_uncertain=True).
Resilience:
Перед вызовом обернуть вызов в ResilienceOrchestrator (CircuitBreaker + RetryHandler + TimeoutHandler) с конфигурируемыми настройками; конфиг по умолчанию: retries=2, base_delay_ms=200, timeout=30s.
Circuit breaker: имя gemini_flash::<model>.
C. Edge cases и явная обработка

Частичные ответы/streaming: если ответ прерывается — пометить status='error' и tokens_uncertain=True; вывести лог с request_id и raw status code.
Неверный JSON: попытаться прочитать как text, сохранить сообщение об ошибке; пометить как ProviderResponseError.
Большие ответы: установить max_response_size в httpx и корректно обрабатывать httpx.Response.is_closed/streaming.
Неполные token counts: если провайдер сообщает только output tokens или только общую оценку — заполнять что есть и выставлять tokens_uncertain=True.
Policy rejection / safety filtering: если Gemini возвращает policy rejection — пометить status='policy_rejection' и возвращать четкий код и сообщение (не раскрывая провайдерную политику).
D. Конфигурация (env vars)

GEMINI_FLASH_API_KEY (обязательно)
GEMINI_FLASH_ENDPOINT (по умолчанию canonical endpoint)
GEMINI_FLASH_TIMEOUT_SECONDS (default 30)
GEMINI_FLASH_MAX_CONCURRENCY (default 20)
GEMINI_FLASH_RETRY_MAX (default 2)
GEMINI_FLASH_RETRY_BASE_DELAY_MS (default 200)
GEMINI_FLASH_STREAMING_ENABLED (default False)
GEMINI_FLASH_COST_PER_OUTPUT_TOKEN, GEMINI_FLASH_COST_PER_INPUT_TOKEN (optional)
GEMINI_FLASH_BILLING_CURRENCY (optional)
E. Точки интеграции

ProviderManager / ModelRegistry: зарегистрировать модель(и) Gemini (например gemini-3-flash) с provider_id='gemini_flash' и флагами премиум/не премиум согласно контракту.
RoutingPolicy: возможно обновить политики health check для нового провайдера (timeout/interval).
Accounting service: убедиться, что ProviderResponse передаётся в тот же формат, что и остальные адаптеры.
Тестовая стратегия (полно и детально)
A. Unit tests (требуется покрытие >=90% адаптера)

test_gemini_flash_adapter_success:
мок httpx, вернуть 200 + body с content и token counts; assert ProviderResponse fields, tokens_uncertain=False, cost_estimate корректна.
test_gemini_flash_adapter_timeout:
мок таймаут httpx -> ожидать ProviderTimeoutError; check latency_ms recorded.
test_gemini_flash_adapter_rate_limit:
возвращаем 429 и Retry-After header; ожидаем ProviderRateLimitError с retry_after в атрибутах exception.
test_gemini_flash_adapter_5xx:
вернуть 500 -> ProviderError/ProviderResponseError.
test_gemini_flash_adapter_invalid_json:
вернуть 200 с неjson -> ProviderResponseError и tokens_uncertain=True.
test_gemini_flash_adapter_streaming:
мок поток ответов, собирать их как async iterator, проверять последовательность данных и финальную accounting запись.
test_cost_estimate_and_uncertainty:
сценарии: полные токены, только output, ни одного -> проверить flags и расчёт стоимости.
test_no_secrets_in_logs:
перехват логов: убедиться, что API key не попал в лог (проверить masked headers или отсутствие Authorization).
test_resilience_integration_unit:
мок CircuitBreaker и RetryHandler: адаптер должен вызывать ResilienceOrchestrator.execute; проверить количество попыток при retryable errors.
B. Integration tests (with httpx mocking, VCR or responses)

test_integration_against_staging_endpoint (опционально — не запускать в CI без доступов):
конфиг с тестовым ключом; проверить полный путь: routing -> provider_manager -> gemini_adapter -> response -> accounting persist.
test_end_to_end_routing_premium_unlock:
убедиться, что при разных routing modes gemini-3-flash выбирается или нет.
C. Smoke tests (docker)

Docker build with runtime deps only, run health-check endpoint that использует gemini adapter with mock endpoints, assert readiness.
D. Concurrency tests (критично)

test_circuit_breaker_concurrent_fails:
запустить N concurrent failing calls к адаптеру (через ResortOrchestrator) и проверить, что CircuitBreaker переходит в OPEN ровно раз и что другие вызовы получают CircuitBreakerError после открытия.
test_adapter_max_concurrency:
симулировать >MAX_CONCURRENCY параллельных запросов и убедиться в throttling/queueing либо корректной ошибке (configurable behavior).
CI / DevOps изменения
requirements-dev.txt: добавить httpx, pytest, pytest-asyncio, respx (httpx mocking), ruff/black/mypy.
.github/workflows/ci.yml (pipeline):
steps: checkout, setup-python, pip install -r requirements-dev.txt, ruff ., black --check ., mypy ., pytest -q --maxfail=1 --cov=providers/gemini_flash_adapter.py, build docker image (optional smoke).
Ensure secrets (GEMINI_FLASH_API_KEY) are stored in Actions Secrets and not printed.
Makefile:
make deps, make lint, make test, make smoke.
Документация (обязательно)
docs/PROVIDERS/gemini_flash.md должен содержать:
Overview: что это, поддерживаемые модели (gemini-3-flash), возможности streaming, ограничения.
Config: список env vars с примерами.
API contract: ProviderRequest/ProviderResponse поля.
Errors and mapping: какие провайдерные статусы мапятся на какие исключения.
Accounting: поля, как рассчитывается cost_estimate и когда выставляется tokens_uncertain.
Runbook: common failures и remedial actions (invalid key, rate limit, partial responses).
Обновить docs/PROVIDER_DIFF.md с отличиями Gemini от других провайдеров (token counting, streaming behavior, rate limits).
Безопасность и соответствие (обязательные требования)
Никогда не логировать API-ключи или содержимое приватных данных. Логи должны маскировать Authorization header.
Тестовые ключи — только в CI secrets, не в репозитории.
Обработка ошибок: не раскрывать внутренние стеки в публичных API; возвращать нормализованные сообщения для пользователей.
Если провайдер возвращает потенциально вредоносный контент — пропустить через policy/safety фильтр (существующий в проекте) прежде чем отдавать пользователю.
Метрики и наблюдаемость
Эметировать метрики:
provider.requests_total{provider="gemini_flash",model="gemini-3-flash",status="success|error|timeout|rate_limit"}
latency histogram provider.request_latency_ms
provider.tokens_{input|output}_total
provider.cost_estimate_total
Логи: structured logging JSON минимального набора полей: timestamp, level, request_id, provider, model, status, latency_ms, error_code (если есть).
Контроль качества перед слиянием (delivery protocol) Выполнить в порядке:
Restate the goal — (этот документ).
Assumptions/unknowns — см. выше.
Architecture decisions — см. раздел «Архитектурные решения».
File tree — см. раздел «Целевая структура файлов».
Produce full contents for critical files — строки шаблонов ниже (см. «Шаблоны файлов»).
Run commands (локально):
make deps
make lint
make typecheck
make test
make smoke (docker)
Run tests — ожидать зелёный CI.
Report expected/observed outcomes — добавить в PR описание и CI лог.
Update docs — добавить files в docs/PROVIDERS и изменить PROVIDER_DIFF.
State remaining risks and next steps — см. ниже.
Шаблоны файлов (минимальные skeletons — developer заполняет детали API parsing)

A. providers/gemini_flash_adapter.py (skeleton — pseudocode)

Импортировать: httpx, pydantic, logging, time, typing, resilience компоненты, common Provider types.
Реализовать класс GeminiFlashAdapter(ProviderAdapter):
async def send_request(self, request: ProviderRequest) -> ProviderResponse:
validate request
build payload
with ResilienceOrchestrator.execute(lambda: self._send(payload)):
measure time
parse response
helper async def _send(self, payload): делает httpx request и возвращает httpx.Response
helper def _parse_response(response, streaming=False) -> ProviderResponse
B. tests/unit/test_gemini_flash_adapter.py (skeleton)

использовать respx или pytest_httpx для мока endpoint
покрыть перечисленные unit-тесты
C. docs/PROVIDERS/gemini_flash.md

заполнить как описано в секции документации
Примеры команд для разработчика (copy-paste)
Создать ветку:
git checkout -b feat/gemini-3-flash
Запуск dev окружения:
python -m venv .venv
source .venv/bin/activate # unix
pip install -r requirements-dev.txt
Линт/типчек/тесты:
ruff .
black --check .
mypy .
pytest tests/unit/test_gemini_flash_adapter.py -q
Ожидаемые риски и действия по mitigations
Risk: Неполные token counts от провайдера. Mitigation: tokens_uncertain flag + persist uncertainty in accounting.
Risk: Непредсказуемые rate limits. Mitigation: config-driven backoff, honor Retry-After header, circuit breaker.
Risk: Различия streaming vs non-streaming API. Mitigation: сделать streaming опциональным и тестировать оба режима.
Risk: Утечка ключей в логах. Mitigation: обязательное masked headers + код ревью для логов.
Что должен вернуть разработчик в PR (чеклист для ревью)
Реализация providers/gemini_flash_adapter.py со всеми public методами и документированными типами.
Unit tests (и integration mocks) перечисленные выше, все проходят локально.
Добавленные записи в docs/PROVIDERS/gemini_flash.md и docs/PROVIDER_DIFF.md.
Обновлённый requirements-dev.txt и Makefile targets.
Обновление CI workflow (gitHub actions) и proof: зелёный run.
Обновление ModelRegistry/ProviderManager с регистрацией модели(ей) gemini-3-flash.
Обновлённый CHANGELOG_WEEKLY.md с кратким описанием изменения.
PR description с шагами для тестирования и списком необходимых CI secrets.
Скриншоты/логи/coverage report и list of new metrics.
Критерии приёмки (Definition of Done)
Код проходит линт и typecheck.
Unit tests покрывают не менее 90% адаптера и все проходят.
Integration mocks покрывают основное поведение.
CI workflow зелёный.
Документация присутствует и содержит примеры конфигурации.
Адаптер корректно возвращает ProviderResponse согласно контракту учёта и не передаёт секреты в логах.
Утилиты (Makefile/CI) есть и документированы.
Примеры тест-кейсов, которые обязательно покрыть (кратко)
успешный ответ с полными token counts;
успешный ответ без token counts (tokens_uncertain=True);
429 Rate limit + Retry-After обработан;
500 ошибка -> ProviderResponseError;
таймаут -> ProviderTimeoutError;
streaming ответ: последовательные чанки + полный результат;
concurrent failing calls -> circuit opens предсказуемо;
cost_estimate корректен при разных input/output token combinations;
логирование не содержит API ключей.
Финальная рекомендация Реализация возможна и безопасна при строгом соблюдении этого ТЗ; начинайте с интерфейса ProviderResponse и unit tests (TDD-подход). Требуется особое внимание к обработке токенов (uncertainty), resilience (circuit breaker/timeout/retry) и безопасности логов. После реализации выполнить полный набор quality gates (lint, mypy, pytest, docker smoke) и предоставить CI-логи в PR.

Следующие шаги для разработчика (чётко)

Создать ветку и skeleton файлов по структуре.
Реализовать интерфейс и базовую happy-path логику с моками.
Написать и запустить unit tests; исправлять код до зелёного.
Добавить resilience и concurrency tests.
Обновить docs и CI.
Создать PR и приложить список секретов, необходимых для интеграционного теста (CI secrets only).
Этот документ достаточен для того, чтобы разработчик начал работу без двусмысленностей; если какие‑то детали API Gemini окажутся другими (gRPC, WebSocket streaming и т.п.), адаптировать transport слой, но сохранить контракт ProviderResponse и resilience/secure logging/стоимость учёта.