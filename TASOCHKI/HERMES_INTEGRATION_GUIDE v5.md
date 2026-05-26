ТЗ: Hermes NVIDIA API Keys Support
1. Цель

Добавить в WORED/Hermes поддержку API-ключей NVIDIA формата:

nvapi-*

Чтобы Hermes мог:

1. Хранить один или несколько NVIDIA API keys безопасно.
2. Проверять наличие ключей без вывода значений.
3. Проверять доступность NVIDIA API.
4. Делать тестовый chat-completion запрос.
5. Переключать активный ключ.
6. Показывать статус ключей без раскрытия секретов.
7. Использовать ключи из quick command.

В рамках этого ТЗ не решаем:

- какая модель главная;
- какой провайдер для каких задач;
- reviewer/fallback routing;
- изменение основного Hermes model config;
- изменение WORED chatbot AI-routing.
2. Область изменений
Файлы создать
scripts/nvidia_api_doctor.py
scripts/nvidia_key_manager.py
docs/hermes/playbooks/nvidia-api-keys.md
Файлы обновить
docs/hermes/README.md
~/.hermes/config.yaml
~/.hermes/.env
Не трогать
AGENTS.md — только если нужно добавить короткую ссылку на playbook
SOUL.md — не менять без необходимости
chatbot/*
collector/*
webui/*
docker-compose.yml
.env проекта WORED
3. Хранение ключей
3.1 Основной env-файл Hermes

Ключи хранятся только здесь:

~/.hermes/.env

Права:

chmod 700 ~/.hermes
chmod 600 ~/.hermes/.env
3.2 Формат одного ключа
NVIDIA_API_KEY=nvapi-...
3.3 Формат пула ключей
NVIDIA_API_KEY_1=nvapi-...
NVIDIA_API_KEY_2=nvapi-...
NVIDIA_API_KEY_3=nvapi-...
NVIDIA_API_KEY_ACTIVE=1
3.4 Приоритет выбора ключа

Скрипты должны выбирать ключ так:

1. Если есть NVIDIA_API_KEY — использовать его.
2. Иначе прочитать NVIDIA_API_KEY_ACTIVE.
3. Использовать NVIDIA_API_KEY_<ACTIVE>.
4. Если ничего нет — вернуть key_present=false.
3.5 Запрещено
- печатать ключ;
- cat ~/.hermes/.env;
- добавлять ключ в ~/.hermes/config.yaml;
- добавлять ключ в git;
- писать ключ в snapshot/log/report;
- передавать ключ в командной строке.
4. NVIDIA API параметры

Значения по умолчанию:

Base URL: https://integrate.api.nvidia.com/v1
Chat endpoint: /chat/completions
Auth: Authorization: Bearer <NVIDIA_API_KEY>
Content-Type: application/json

OpenAI-compatible endpoint:

POST https://integrate.api.nvidia.com/v1/chat/completions

Модель указывать через env:

NVIDIA_MODEL=minimaxai/minimax-m2.7

Если переменная отсутствует, использовать default:

minimaxai/minimax-m2.7

Так мы не пришиваем модель к архитектуре Hermes и можем поменять её без правки кода.

5. Скрипт scripts/nvidia_api_doctor.py
Назначение

Проверяет, что Hermes может использовать NVIDIA API-ключи:

- env-файл читается;
- ключ найден;
- endpoint доступен;
- chat completion работает;
- вывод не содержит секретов.
CLI
python scripts/nvidia_api_doctor.py
python scripts/nvidia_api_doctor.py --mode presence
python scripts/nvidia_api_doctor.py --mode models
python scripts/nvidia_api_doctor.py --mode chat
python scripts/nvidia_api_doctor.py --model minimaxai/minimax-m2.7
Аргументы
--env-file       default ~/.hermes/.env
--base-url       default https://integrate.api.nvidia.com/v1
--model          default env NVIDIA_MODEL or minimaxai/minimax-m2.7
--mode           presence | models | chat
--timeout        default 30
--json           print JSON
Mode: presence

Проверяет только наличие ключей.

Вывод:

{
  "ok": true,
  "mode": "presence",
  "keys": {
    "NVIDIA_API_KEY": false,
    "NVIDIA_API_KEY_1": true,
    "NVIDIA_API_KEY_2": true,
    "NVIDIA_API_KEY_ACTIVE": "1"
  },
  "active_key_present": true,
  "secrets_printed": false
}
Mode: models

Делает:

GET https://integrate.api.nvidia.com/v1/models

Вывод:

{
  "ok": true,
  "mode": "models",
  "http_status": 200,
  "model_count": 12,
  "target_model": "minimaxai/minimax-m2.7",
  "target_model_found": true
}

Если список моделей большой, не печатать весь список по умолчанию.

Mode: chat

Делает тестовый запрос:

{
  "model": "minimaxai/minimax-m2.7",
  "messages": [
    {
      "role": "user",
      "content": "Reply with exactly: NVIDIA_API_OK"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 32,
  "stream": false
}

Вывод:

{
  "ok": true,
  "mode": "chat",
  "provider": "nvidia",
  "base_url": "https://integrate.api.nvidia.com/v1",
  "model": "minimaxai/minimax-m2.7",
  "key_present": true,
  "http_status": 200,
  "latency_ms": 1100,
  "content_preview": "NVIDIA_API_OK",
  "secrets_printed": false
}
Ошибки

Если ключа нет:

{
  "ok": false,
  "error": "NVIDIA API key missing",
  "key_present": false,
  "secrets_printed": false
}

Если 401/403:

{
  "ok": false,
  "http_status": 401,
  "error": "Unauthorized or invalid NVIDIA API key",
  "secrets_printed": false
}

Если model not found:

{
  "ok": false,
  "http_status": 404,
  "error": "Model not available for this account or endpoint",
  "model": "minimaxai/minimax-m2.7"
}
6. Скрипт scripts/nvidia_key_manager.py
Назначение

Управляет пулом NVIDIA keys без вывода значений.

CLI
python scripts/nvidia_key_manager.py status
python scripts/nvidia_key_manager.py set-active 2
python scripts/nvidia_key_manager.py list
Команды
status
{
  "env_file": "/home/hermes/.hermes/.env",
  "single_key_present": false,
  "active_index": "2",
  "active_key_present": true,
  "pool": [
    {"name": "NVIDIA_API_KEY_1", "present": true},
    {"name": "NVIDIA_API_KEY_2", "present": true},
    {"name": "NVIDIA_API_KEY_3", "present": false}
  ],
  "secrets_printed": false
}
set-active 2

Меняет только строку:

NVIDIA_API_KEY_ACTIVE=2

Если строки нет — добавляет её.

Вывод:

{
  "ok": true,
  "active_index": "2",
  "active_key_present": true,
  "secrets_printed": false
}
list

То же, что status, но без изменения файла.

Требования
- не печатать значения ключей;
- сохранять комментарии и остальные env-переменные;
- не трогать WORED .env;
- работать только с ~/.hermes/.env;
- делать backup перед изменением:
  ~/.hermes/.env.bak.YYYYMMDD_HHMMSS
7. Quick commands для Hermes

Добавить в ~/.hermes/config.yaml:

quick_commands:
  nvidia-key-status:
    type: exec
    command: python scripts/nvidia_key_manager.py status

  nvidia-key-1:
    type: exec
    command: python scripts/nvidia_key_manager.py set-active 1

  nvidia-key-2:
    type: exec
    command: python scripts/nvidia_key_manager.py set-active 2

  nvidia-presence:
    type: exec
    command: python scripts/nvidia_api_doctor.py --mode presence --json

  nvidia-models:
    type: exec
    command: python scripts/nvidia_api_doctor.py --mode models --json

  nvidia-chat:
    type: exec
    command: python scripts/nvidia_api_doctor.py --mode chat --json

Если у Hermes конфиг не допускает вложенный quick_commands: повторно, команды надо добавить внутрь существующего блока, а не создавать второй блок.

8. Документация
Файл
docs/hermes/playbooks/nvidia-api-keys.md
Содержание
1. Назначение.
2. Где хранятся ключи.
3. Формат одного ключа.
4. Формат пула ключей.
5. Как проверить presence.
6. Как проверить /v1/models.
7. Как проверить chat completion.
8. Как переключить активный ключ.
9. Что запрещено.
10. Troubleshooting: missing key, 401, 403, 404, timeout, model unavailable.
9. Тесты
TEST-NVIDIA-01: env permissions
test "$(stat -c '%a' ~/.hermes)" = "700"
test "$(stat -c '%a' ~/.hermes/.env)" = "600"

Ожидание:

exit 0
TEST-NVIDIA-02: key status без утечки
python scripts/nvidia_key_manager.py status | tee /tmp/nvidia_key_status.out

grep -E "nvapi-|Bearer|API_KEY=.*[A-Za-z0-9_-]{8,}" /tmp/nvidia_key_status.out \
  && exit 1 \
  || echo "NO SECRET LEAK"

Ожидание:

NO SECRET LEAK
TEST-NVIDIA-03: presence
python scripts/nvidia_api_doctor.py --mode presence --json | python3 -m json.tool

Ожидание:

- valid JSON
- active_key_present=true
- secrets_printed=false
TEST-NVIDIA-04: models endpoint
python scripts/nvidia_api_doctor.py --mode models --json | python3 -m json.tool

Ожидание:

- valid JSON
- ok=true или понятная ошибка HTTP
- нет ключей в выводе
TEST-NVIDIA-05: chat endpoint
python scripts/nvidia_api_doctor.py --mode chat --json | python3 -m json.tool

Ожидание:

- valid JSON
- если ключ и модель доступны: ok=true
- content_preview содержит короткий ответ
- если модель недоступна: понятная ошибка, без traceback
- secrets_printed=false
TEST-NVIDIA-06: switch active key
python scripts/nvidia_key_manager.py set-active 2 | python3 -m json.tool
python scripts/nvidia_key_manager.py status | python3 -m json.tool

Ожидание:

- active_index=2
- backup ~/.hermes/.env.bak.* создан
- ключи не напечатаны
TEST-NVIDIA-07: missing key не даёт traceback
tmp_env=$(mktemp)
python scripts/nvidia_api_doctor.py --env-file "$tmp_env" --mode chat --json | python3 -m json.tool || true
rm -f "$tmp_env"

Ожидание:

- valid JSON
- ok=false
- key_present=false
- no traceback
TEST-NVIDIA-08: quick commands

В Hermes TUI:

/nvidia-key-status
/nvidia-presence
/nvidia-models
/nvidia-chat

Ожидание:

- команды выполняются;
- ключи не выводятся;
- ошибки читаемые.
10. Acceptance criteria

Готово, когда:

1. NVIDIA keys лежат только в ~/.hermes/.env.
2. Права ~/.hermes=700, ~/.hermes/.env=600.
3. Hermes умеет проверить key presence.
4. Hermes умеет проверить /v1/models.
5. Hermes умеет сделать /v1/chat/completions.
6. Hermes умеет переключить active key.
7. Все выводы маскируют секреты.
8. Quick commands работают.
9. Документация добавлена.
10. Основная модель Hermes и runtime WORED не менялись.
11. Готовый промпт для Hermes
Задача: NVIDIA API Keys Support для Hermes.

Цель:
научить Hermes пользоваться NVIDIA API-ключами формата nvapi-* без изменения основной модели Hermes и без изменения WORED runtime.

Создать:
- scripts/nvidia_api_doctor.py
- scripts/nvidia_key_manager.py
- docs/hermes/playbooks/nvidia-api-keys.md

Обновить:
- docs/hermes/README.md
- ~/.hermes/config.yaml quick_commands

Не трогать:
- chatbot/*
- collector/*
- webui/*
- docker-compose.yml
- .env проекта WORED
- AI-routing WORED
- основную модель Hermes

Требования:
- ключи только в ~/.hermes/.env;
- права 700/600;
- не печатать nvapi-*;
- не печатать Bearer tokens;
- поддержать NVIDIA_API_KEY и пул NVIDIA_API_KEY_1..N;
- поддержать NVIDIA_API_KEY_ACTIVE;
- default base_url: https://integrate.api.nvidia.com/v1;
- default model из NVIDIA_MODEL или minimaxai/minimax-m2.7;
- modes: presence, models, chat;
- key manager: status, list, set-active;
- set-active делает backup ~/.hermes/.env.bak.<timestamp>;
- все ошибки возвращать JSON без traceback.

Сначала дай:
PLAN
FILES
RISK
TESTS

После patch выполни:
python scripts/nvidia_key_manager.py status
python scripts/nvidia_api_doctor.py --mode presence --json
python scripts/nvidia_api_doctor.py --mode models --json
python scripts/nvidia_api_doctor.py --mode chat --json

REPORT должен подтвердить, что секреты не выводились.