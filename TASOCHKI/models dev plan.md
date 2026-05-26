ТЗ: MODEL-P1.1 — Model-Bound Routing для Hermes/WORED
Задача: MODEL-P1.1 Correct Model-Bound NVIDIA Routing + Chinese Multi-Model Provider Routing

Цель:
Навести порядок в использовании моделей Hermes для проекта WORED:
1. Безопасно описать все доступные ключи и модели.
2. Проверить каждую связку model-bound NVIDIA key → model.
3. Проверить китайские provider keys как single-key → multi-model.
4. Сформировать рабочую матрицу ролей моделей.
5. Подготовить proposed Hermes routing config, но НЕ применять его без отдельного приказа.
6. Не сломать WORED runtime.

Главное правило:
NVIDIA nvapi-* ключи в этой инфраструктуре НЕ считаются универсальными ключами ко всем NIM-моделям.
Каждый nvapi-* ключ считается model-bound/access-bound:
  один конкретный ключ → одна заявленная модель/роль.

Qwen / GLM / ZAI / DashScope / другие китайские провайдеры:
  один ключ может открывать несколько моделей через уже настроенный Singapore-compatible endpoint.

DeepSeek official sk-*:
  отдельный provider, проверять отдельно как single-key multi-model.

────────────────────────────────────
0. КОНТЕКСТ WORED

Проект:
  /mnt/d/WORED

Hermes:
  работает на WSL host.

Активный runtime WORED:
  chatbot
  collector
  webui
  postgres
  redis

Не трогать runtime:
  chatbot/*
  collector/*
  webui/*
  docker-compose.yml
  .env проекта
  Postgres
  Redis
  Telegram gateway
  data/decision_journal.jsonl

Можно работать только с:
  scripts/hermes/*
  docs/hermes/*
  docs/hermes/playbooks/*
  ~/.hermes/secrets/*
  ~/.hermes/model_routing_config.md
  ~/.hermes/config.yaml — только после отдельного подтверждения пользователя
  ~/.hermes/SOUL.md — только после отдельного подтверждения пользователя

────────────────────────────────────
1. БЕЗОПАСНОСТЬ

Строго запрещено:
  - печатать ключи целиком;
  - использовать cat для secret files;
  - сохранять ключи в /mnt/d/WORED;
  - сохранять ключи в docs;
  - сохранять ключи в scripts;
  - сохранять ключи в git;
  - писать ключи в ~/.hermes/config.yaml;
  - вставлять ключи в финальный отчёт;
  - логировать Authorization headers.

Разрешено показывать только masked key:
  nvapi-yqaPA_6e... -> nvapi-yqaPA***
  sk-a8a785...      -> sk-a8a78***

Права:
  ~/.hermes/secrets должен иметь chmod 700
  ~/.hermes/secrets/model_keys.yaml должен иметь chmod 600

Перед началом:
  mkdir -p ~/.hermes/secrets
  chmod 700 ~/.hermes ~/.hermes/secrets

После создания secret file:
  chmod 600 ~/.hermes/secrets/model_keys.yaml

Secret scan обязателен в конце.

────────────────────────────────────
2. СТРУКТУРА SECRET FILE

Создать файл:

  ~/.hermes/secrets/model_keys.yaml

НЕ выводить его содержимое.

Формат:

nvidia_model_bound:
  - id: nvidia_minimax_m27_1
    provider: nvidia
    model: minimaxai/minimax-m2.7
    role: heavy_reviewer
    key: "PASTE_SECRET_HERE"

  - id: nvidia_deepseek_v4_pro
    provider: nvidia
    model: deepseek-ai/deepseek-v4-pro
    role: bug_hunt
    key: "PASTE_SECRET_HERE"

  - id: nvidia_glm_47
    provider: nvidia
    model: zhipuai/glm-4.7
    role: russian_reports
    key: "PASTE_SECRET_HERE"

  - id: nvidia_minimax_m27_2
    provider: nvidia
    model: minimaxai/minimax-m2.7
    role: heavy_reviewer
    key: "PASTE_SECRET_HERE"

  - id: nvidia_deepseek_32
    provider: nvidia
    model: deepseek-ai/deepseek-v3.2
    role: bug_hunt_fallback
    key: "PASTE_SECRET_HERE"

  - id: nvidia_kimi_k2
    provider: nvidia
    model: moonshotai/kimi-k2
    role: long_context_docs
    key: "PASTE_SECRET_HERE"

  - id: nvidia_qwen3_coder_480b
    provider: nvidia
    model: qwen/qwen3-coder-480b-a35b-instruct
    role: main_coding_driver
    key: "PASTE_SECRET_HERE"

deepseek_official:
  - id: deepseek_official_1
    provider: deepseek_official
    models:
      - deepseek-chat
      - deepseek-reasoner
    role: cross_provider_fallback
    key: "PASTE_SECRET_HERE"

china_multi_model_providers:
  - id: qwen_singapore
    provider: qwen
    base_url: "USE_EXISTING_SINGAPORE_ENDPOINT_FROM_CURRENT_CONFIG"
    models:
      - qwen3-coder-plus
      - qwen-plus
      - qwen-flash
      - qwen3.5-flash
      - qwen3.6-flash
    role: coding_and_fast_fallback
    key_env: "DASHSCOPE_API_KEY_OR_EXISTING_QWEN_ENV"

  - id: glm_singapore
    provider: glm
    base_url: "USE_EXISTING_SINGAPORE_ENDPOINT_FROM_CURRENT_CONFIG"
    models:
      - glm-4-flash
      - glm-4
    role: russian_reasoning_fallback
    key_env: "GLM_API_KEY_OR_ZAI_API_KEY"

Важно:
  Если точный model id на NVIDIA отличается, не менять саму концепцию.
  Записать error_class=model_not_found и вынести в report как “нужно уточнить model id”.

────────────────────────────────────
3. ФАЙЛЫ ДЛЯ РЕАЛИЗАЦИИ

Создать или переписать:

  scripts/hermes/model_inventory.py
  scripts/hermes/probe_nvidia_model_bound.py
  scripts/hermes/probe_china_provider_models.py
  scripts/hermes/probe_deepseek_official.py
  docs/hermes/model-routing.md
  docs/hermes/playbooks/model-routing.md

Опционально:
  ~/.hermes/model_routing_config.md

Не применять:
  ~/.hermes/config.yaml

Пока только подготовить proposed config snippet без ключей.

────────────────────────────────────
4. ЛОГИКА PROBE

4.1 NVIDIA model-bound probe

Для каждой записи nvidia_model_bound:

  - взять только её key;
  - проверить только её model;
  - не пробовать этот key на других моделях;
  - не считать ошибкой, что ключ MiniMax не работает с Qwen;
  - статус относится только к связке id+model+key.

Endpoint:

  base_url: https://integrate.api.nvidia.com/v1
  path: /chat/completions

Request:
  POST https://integrate.api.nvidia.com/v1/chat/completions

Headers:
  Authorization: Bearer <SECRET>
  Content-Type: application/json

Body:
{
  "model": "<model>",
  "messages": [
    {"role": "system", "content": "Return exactly OK."},
    {"role": "user", "content": "healthcheck"}
  ],
  "temperature": 0,
  "max_tokens": 8
}

Timeout:
  45 seconds

Expected:
  ответ содержит OK.

4.2 Chinese multi-model probe

Для каждой записи china_multi_model_providers:

  - найти ключ по key_env из environment или существующего Hermes config/env;
  - использовать base_url из текущей рабочей конфигурации;
  - одним provider key проверить каждую модель из models;
  - каждая модель получает отдельный status;
  - если одна модель failed, остальные всё равно проверять.

Важно:
  Не ломать текущую Singapore-compatible схему.
  Если endpoint не найден — status=not_configured, warning.

4.3 DeepSeek official probe

Для deepseek_official:

  - взять sk-* key из secret file;
  - проверить deepseek-chat;
  - проверить deepseek-reasoner;
  - не смешивать с NVIDIA DeepSeek keys.

────────────────────────────────────
5. ERROR CLASSIFICATION

Каждый probe должен возвращать error_class:

  working
  auth_header_missing_or_env_not_loaded
  invalid_or_expired_key
  forbidden_model_or_permission
  model_not_found
  quota_or_rate_limit
  billing_or_quota
  provider_error
  timeout
  bad_response_format
  not_configured
  unknown_error

Правила:

401 + “missing authentication header”:
  auth_header_missing_or_env_not_loaded

401 + auth header был отправлен:
  invalid_or_expired_key

403:
  forbidden_model_or_permission

404:
  model_not_found

429:
  quota_or_rate_limit

402:
  billing_or_quota

5xx:
  provider_error

timeout:
  timeout

Ответ не JSON или нет choices:
  bad_response_format

────────────────────────────────────
6. ВЫХОДНОЙ JSON

Команда:

  python3 scripts/hermes/model_inventory.py --format json

Должна вернуть валидный JSON:

{
  "status": "ok|partial|no_keys",
  "generated_at": "ISO_TIMESTAMP",
  "routing_model": "nvidia_model_bound_and_china_multi_model",
  "providers": [
    {
      "provider": "nvidia",
      "mode": "model_bound_keys",
      "entries": [
        {
          "id": "nvidia_qwen3_coder_480b",
          "model": "qwen/qwen3-coder-480b-a35b-instruct",
          "role": "main_coding_driver",
          "masked_key": "nvapi-***",
          "status": "working|failed",
          "latency_ms": 1234,
          "error_class": null,
          "error_summary": null
        }
      ]
    },
    {
      "provider": "qwen_singapore",
      "mode": "single_key_multi_model",
      "models_tested": [
        {
          "model": "qwen3-coder-plus",
          "status": "working|failed",
          "latency_ms": 1234,
          "error_class": null,
          "error_summary": null
        }
      ],
      "working_models": [],
      "errors": []
    },
    {
      "provider": "glm_singapore",
      "mode": "single_key_multi_model",
      "models_tested": [],
      "working_models": [],
      "errors": []
    },
    {
      "provider": "deepseek_official",
      "mode": "single_key_multi_model",
      "models_tested": [],
      "working_models": [],
      "errors": []
    }
  ],
  "recommended_routing": {
    "main_coding_driver": null,
    "heavy_reviewer": null,
    "bug_hunt": null,
    "long_context_docs": null,
    "russian_reports": null,
    "fast_fallback": null,
    "patch_validator": null,
    "cross_provider_fallback": null,
    "deterministic_diagnostics": "shell_python_scripts",
    "trade_risk_calculation": "deterministic_python_scripts"
  },
  "warnings": []
}

Статус:
  ok      — есть хотя бы main_coding_driver и heavy_reviewer;
  partial — есть хотя бы одна рабочая модель, но не все роли закрыты;
  no_keys — нет ключей или ни один ключ не найден.

────────────────────────────────────
7. ROLE ASSIGNMENT

Заполнить recommended_routing по приоритетам.

7.1 main_coding_driver

Приоритет:
  1. nvidia_qwen3_coder_480b if working
  2. qwen3-coder-plus via qwen_singapore if working
  3. deepseek_v4_pro if working
  4. deepseek official deepseek-chat/reasoner if working

7.2 heavy_reviewer

Приоритет:
  1. any working minimaxai/minimax-m2.7
  2. nvidia_kimi_k2
  3. nvidia_deepseek_v4_pro

7.3 bug_hunt

Приоритет:
  1. nvidia_deepseek_v4_pro
  2. nvidia_deepseek_32
  3. deepseek-reasoner official

7.4 long_context_docs

Приоритет:
  1. nvidia_kimi_k2
  2. minimax_m27
  3. nvidia_qwen3_coder_480b

7.5 russian_reports

Приоритет:
  1. nvidia_glm_47
  2. glm_singapore working model
  3. qwen_singapore flash model

7.6 fast_fallback

Приоритет:
  1. qwen-flash via qwen_singapore
  2. glm-4-flash via glm_singapore
  3. deterministic summary scripts

7.7 patch_validator

Приоритет:
  1. minimax_m27
  2. nvidia_deepseek_v4_pro
  3. nvidia_kimi_k2

7.8 deterministic

Не использовать LLM для:
  - docker compose ps;
  - healthcheck;
  - json validation;
  - risk calculation;
  - trade plan numeric levels;
  - decision journal scoring.

Для этого:
  deterministic_diagnostics = shell_python_scripts
  trade_risk_calculation = deterministic_python_scripts

Synthetic fallback:
  Не считать AI-моделью.
  Не включать в providers.
  Можно использовать только как smoke-test режим deterministic scripts.

────────────────────────────────────
8. MARKDOWN OUTPUT

Команда:

  python3 scripts/hermes/model_inventory.py --format markdown

Должна вывести:

# WORED Hermes Model Inventory

## Routing Model
nvidia_model_bound_and_china_multi_model

## Providers

### NVIDIA model-bound keys
Таблица:
| ID | Model | Role | Status | Latency | Error |
|---|---|---|---|---|---|

### Qwen Singapore multi-model
| Model | Status | Latency | Error |
|---|---|---|---|

### GLM Singapore multi-model
| Model | Status | Latency | Error |
|---|---|---|---|

### DeepSeek official
| Model | Status | Latency | Error |
|---|---|---|---|

## Recommended Routing
| Role | Provider | Model | Reason |
|---|---|---|---|

## Warnings

## Proposed Config
Без ключей.
Только provider/model/base_url/key_ref.

────────────────────────────────────
9. ДОКУМЕНТАЦИЯ

Создать:

  docs/hermes/model-routing.md

Содержание:

1. Что такое model-bound NVIDIA key.
2. Что такое Chinese multi-model provider key.
3. Где хранятся ключи.
4. Как запускать inventory.
5. Как читать error_class.
6. Как назначаются роли.
7. Какой routing рекомендован.
8. Что запрещено:
   - печатать ключи;
   - коммитить secrets;
   - считать synthetic fallback моделью;
   - давать LLM считать risk вместо Python scripts.
9. Rollback:
   - удалить ~/.hermes/secrets/model_keys.yaml;
   - не применять config.yaml;
   - восстановить прежний config snapshot.

Создать:

  docs/hermes/playbooks/model-routing.md

Содержание:
  - быстрые команды проверки;
  - диагностика 401/403/404/429;
  - как добавить новый model-bound NVIDIA key;
  - как добавить новую модель в Qwen Singapore provider;
  - как проверить SECRET_SCAN_OK.

────────────────────────────────────
10. PROPOSED CONFIG

Подготовить в docs/hermes/model-routing.md раздел:

## Proposed Hermes Routing Config

Важно:
  НЕ применять автоматически.
  НЕ писать ключи.
  Использовать key_ref/env_ref.

Пример:

models:
  main_coding_driver:
    provider: nvidia
    model: qwen/qwen3-coder-480b-a35b-instruct
    key_ref: nvidia_qwen3_coder_480b

  heavy_reviewer:
    provider: nvidia
    model: minimaxai/minimax-m2.7
    key_ref: nvidia_minimax_m27_1

  patch_validator:
    provider: nvidia
    model: minimaxai/minimax-m2.7
    key_ref: nvidia_minimax_m27_2

  bug_hunt:
    provider: nvidia
    model: deepseek-ai/deepseek-v4-pro
    key_ref: nvidia_deepseek_v4_pro

  long_context_docs:
    provider: nvidia
    model: moonshotai/kimi-k2
    key_ref: nvidia_kimi_k2

  russian_reports:
    provider: nvidia
    model: zhipuai/glm-4.7
    key_ref: nvidia_glm_47

  fast_fallback:
    provider: qwen_singapore
    model: qwen-flash
    key_env: DASHSCOPE_API_KEY_OR_EXISTING_QWEN_ENV

  cross_provider_fallback:
    provider: deepseek_official
    model: deepseek-reasoner
    key_ref: deepseek_official_1

routing_rules:
  plan:
    model: main_coding_driver
  patch:
    model: main_coding_driver
    reviewer: patch_validator
  review:
    model: heavy_reviewer
  debug:
    model: bug_hunt
  docs:
    model: long_context_docs
  report_ru:
    model: russian_reports
  quick_status:
    mode: deterministic_scripts
  trade_risk:
    mode: deterministic_python_scripts

────────────────────────────────────
11. ACCEPTANCE TESTS

Выполнить строго в foreground, не background.

cd /mnt/d/WORED

11.1 Проверка файлов

test -f scripts/hermes/model_inventory.py && echo "model_inventory.py OK"
test -f scripts/hermes/probe_nvidia_model_bound.py && echo "probe_nvidia_model_bound.py OK"
test -f scripts/hermes/probe_china_provider_models.py && echo "probe_china_provider_models.py OK"
test -f scripts/hermes/probe_deepseek_official.py && echo "probe_deepseek_official.py OK"
test -f docs/hermes/model-routing.md && echo "model-routing.md OK"
test -f docs/hermes/playbooks/model-routing.md && echo "playbook OK"

11.2 JSON inventory

python3 scripts/hermes/model_inventory.py --format json > /tmp/wored_model_inventory_model_bound.json
python3 -m json.tool /tmp/wored_model_inventory_model_bound.json

11.3 Schema assertion

python3 - <<'PY'
import json
p=json.load(open("/tmp/wored_model_inventory_model_bound.json"))
assert p["status"] in ["ok","partial","no_keys"], p.get("status")
assert p.get("routing_model") == "nvidia_model_bound_and_china_multi_model"
assert "providers" in p
assert "recommended_routing" in p
providers = {x["provider"]: x for x in p["providers"]}
assert "nvidia" in providers
for pr in p["providers"]:
    assert "provider" in pr
    assert "mode" in pr
rr = p["recommended_routing"]
for k in [
    "main_coding_driver",
    "heavy_reviewer",
    "bug_hunt",
    "long_context_docs",
    "russian_reports",
    "fast_fallback",
    "patch_validator",
    "deterministic_diagnostics",
    "trade_risk_calculation"
]:
    assert k in rr, k
print("MODEL_BOUND_INVENTORY_SCHEMA_OK")
PY

11.4 Markdown output

python3 scripts/hermes/model_inventory.py --format markdown > /tmp/wored_model_inventory_model_bound.md
head -120 /tmp/wored_model_inventory_model_bound.md

11.5 Secret scan

grep -RniE "nvapi-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{20,}" \
  scripts/hermes docs/hermes ~/.hermes/config.yaml ~/.hermes/SOUL.md ~/.hermes/model_routing_config.md 2>/dev/null \
  && echo "SECRET_LEAK_FOUND" || echo "SECRET_SCAN_OK"

11.6 Secret file permission

stat -c "%a %n" ~/.hermes/secrets ~/.hermes/secrets/model_keys.yaml

Ожидаемо:
  ~/.hermes/secrets = 700
  ~/.hermes/secrets/model_keys.yaml = 600

11.7 No runtime changes

git status --short

Ожидаемо:
  изменены только:
    scripts/hermes/*
    docs/hermes/*
  Не должно быть изменений:
    chatbot/*
    collector/*
    webui/*
    docker-compose.yml
    .env

11.8 Config not applied

Проверить, что ~/.hermes/config.yaml не был изменён автоматически.

Если был создан snapshot, показать snapshot id.
Если config.yaml изменён без приказа — FAIL.

────────────────────────────────────
12. REPORT FORMAT

Финальный отчёт должен быть строго таким:

MODEL-P1.1 REPORT

1. Files changed:
   - ...

2. Secret handling:
   - model_keys.yaml exists: yes/no
   - permissions: ...
   - secrets printed: no
   - SECRET_SCAN_OK: yes/no

3. JSON validation:
   - python3 -m json.tool: PASS/FAIL
   - MODEL_BOUND_INVENTORY_SCHEMA_OK: PASS/FAIL

4. NVIDIA model-bound results:
   | ID | Model | Role | Status | Error |
   |---|---|---|---|---|

5. Chinese multi-model results:
   | Provider | Model | Status | Error |
   |---|---|---|---|

6. DeepSeek official results:
   | Model | Status | Error |
   |---|---|---|

7. Recommended routing:
   - main_coding_driver:
   - heavy_reviewer:
   - bug_hunt:
   - long_context_docs:
   - russian_reports:
   - fast_fallback:
   - patch_validator:
   - cross_provider_fallback:

8. Proposed config:
   - prepared: yes/no
   - applied: no

9. Runtime:
   - WORED runtime changed: no
   - Docker/Postgres/Redis touched: no

10. Status:
   MODEL-P1.1 PASS / PARTIAL / FAIL

Критерии PASS:
  - JSON валиден;
  - schema assertion прошёл;
  - SECRET_SCAN_OK;
  - ключи не напечатаны;
  - NVIDIA проверен как model-bound;
  - Chinese providers проверены как multi-model;
  - recommended_routing сформирован;
  - config.yaml не применён без приказа.

Критерии PARTIAL:
  - inventory работает, но часть provider не настроена;
  - нет критических утечек;
  - routing сформирован частично.

Критерии FAIL:
  - есть SECRET_LEAK_FOUND;
  - JSON невалидный;
  - schema assertion failed;
  - config.yaml изменён без приказа;
  - NVIDIA ключи снова проверялись как общий пул.
Короткая инструкция перед запуском для Qwen
Перед реализацией:
1. Не трогай runtime WORED.
2. Не печатай ключи.
3. Не считай NVIDIA общим пулом.
4. Сначала создай inventory/probe scripts.
5. Потом docs.
6. Потом тесты.
7. config.yaml не применять.
8. В отчёте дать raw evidence, не пересказ.
Что считать правильным результатом

Правильный результат после выполнения:

MODEL_BOUND_INVENTORY_SCHEMA_OK
SECRET_SCAN_OK
routing_model = nvidia_model_bound_and_china_multi_model
NVIDIA entries проверены по связкам key+model
Qwen/GLM Singapore проверены как single-key multi-model
recommended_routing заполнен
config proposed, но not applied
WORED runtime не изменён