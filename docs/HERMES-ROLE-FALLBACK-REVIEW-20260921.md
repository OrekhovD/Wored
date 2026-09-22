# HERMES — Review: фолбэк ролей в prediction engine

**Дата:** 2026-09-21
**Объект:** `webui/prediction_engine.py` (role bundle: bull / bear / arbiter), `webui/trader_api.py`
**Статус:** diagnostic-only. По решению владельца ни один из пунктов в этом раунде **не исправлен**.
Код не менялся; изменяющие статус проверки не производилось.

**Обновление 2026-09-22:** ниже — исторический снимок диагноза, он не переписывается.
Фактическое состояние: **H3 (P2) исправлен** — `_calibrate_confidence` стала чистой
функцией, точность роли запрашивается в `generate_model_prediction` (коммит `c2fe3bf`,
регрессии в `tests/stabilization/test_contracts.py`). **H1 и H2 (P1) исправлены** —
классификация таймаута по типу исключения с обходом `__cause__`, причина через
`_describe_error`, роль и первичная модель сохраняются в фейловом прогоне, след
попыток — в `attempted_models` и в тексте причины (коммит `92233a4`). При реализации
вскрылось, что ретрай таймаута не влезает в бюджет очереди, поэтому добавлен
`ROLE_ATTEMPT_BUDGET_SECONDS = 100`. Открытыми остаются M4–M7.

---

## 1. Как ставился диагноз

Три источника фактов, без домыслов:

1. **Прод-логи webui** (`docker compose logs webui`), интервал 13:18–13:52 2026-09-21.
2. **БД** (`forecast_requests`, `forecast_model_runs`, `forecast_points`, `forecast_jobs`) через
   `docker compose exec -T postgres psql`.
3. **Одноразовые зонды внутри контейнера webui** (код удалён после прогона):
   рендер исключений openai/httpx в `str()` и поведение `_is_timeout_error()` на реальном
   read-timeout против локального «молчащего» TCP-сервера.

### Триггер

Авто-обновление сессионного прогноза (item B2) создало запрос **#136**, в логе появилась строка
с пустой причиной:

```
2026-09-21 13:27:10  webui - INFO    - Starting background prediction request 136 for btcusdt
2026-09-21 13:28:13  webui.prediction_engine - WARNING - Prediction model analyst failed on glm-5.1:
2026-09-21 13:29:12  webui.prediction_engine - WARNING - Prediction model minimax failed on minimax-m3: Model response does not contain a JSON object or array
```

### Что показали запросы

| Request | Roles → итоговая модель | Status запроса | Points |
|---|---|---|---|
| #136 | bull=`deepseek-v4-pro` (after `glm-5.1` fail), bear=`glm-5.2`, arbiter=`glm-5.1` (after `minimax-m3` fail) | `completed` | 12 (3×4) |
| #137 | bull=`deepseek-v4-pro`, bear=`glm-5.2`, arbiter — **вся цепочка провалена**, run 377 = NVIDIA `minimaxai/minimax-m3` `410 Gone` | `completed` | 24 (2×12) |

То есть фолбэк буквально спас оба прогона: role-цепочка доезжает до следующей модели и
отдаёт результат. Ниже — что при этом сломано.

---

## 2. Находки

### H1 — Таймауты не распознаются: классификаторы слепы к httpx 0.28

**Факт.** Реальный read-timeout в текущем стеке (`httpx 0.28.1`, `openai 3.16.2`) выглядит так:

```
type: httpx.ReadTimeout    str(exc): ''    repr: ReadTimeout('')    __cause__: ReadTimeout(TimeoutError())
_is_timeout_error(exc): False    _is_rate_limit_error(exc): False
```

Все классификаторы (`_is_quota_error`, `_is_access_error`, `_is_auth_error`,
`_is_rate_limit_error`, `_is_timeout_error`, `_is_missing_model_error`,
`prediction_engine.py:409-436`) работают по подстроке от `str(exc)`. Пустое сообщение
не распознаётся **ни одним** из них.

**Подтверждение по таймингу:** 13:27:10 → 13:28:13 = 63 с при `analyst timeout=60.0` —
это таймаут, залогированный как «прочая» ошибка.

**Последствия:**
- `RETRY_BACKOFF_SECONDS = (2.0, 5.0)` не применяется никогда: ветка ретрая
  (`prediction_engine.py:842-850`) требует `_is_rate_limit_error or _is_timeout_error`.
- Ветка «switching from … after retryable exhaustion» (`:851-858`) не логируется.
- В `forecast_model_runs.error_message` при таймауте пишается заглушка
  `"Prediction request failed"`, потому что `str(last_error)` — пустая строка, а пустая
  строка falsy (`:868`). Причина теряется в персистентном журнале.
- Фолбэк работает **только** за счёт `break` во внешний цикл `for candidate` (`:860`).

**Предлагаемый фикс (не применён):** классифицировать по `isinstance`
(`httpx.TimeoutException`, `httpx.ConnectError`, `openai.APITimeoutError`,
`openai.APIConnectionError`), а по подстроке оставить только HTTP-коды; в лог и
`error_message` писать `f"{type(exc).__name__}: {exc!r}"`.

### H2 — У упавших ролей теряется атрибуция роли и модели

`agent_role=role` проставляется только в успешном возврате (`:825`); в возврате фейла
(`:862-869`) поля `agent_role` нет. В БД это видно напрямую — runs **368** и **377**:
`agent_role = NULL`, а `model_id` = последняя *попытавшаяся* модель (NVIDIA), не конфигурационная.

**Последствия:** вся роль-аналитика фильтрует по роли — `_fetch_role_accuracy`
(`:294-300`, окно 7 дней) и `app.py:3774`, `app.py:3802`. Значит **провалы роли не
засчитываются в её точность**: статистика self-learning контура систематически
оптимистична, а журнал показывает «модель NVIDIA упала» там, где падал `minimax-m3`.

**Предлагаемый фикс:** `agent_role=role` в фейловый `ModelPredictionResult`, плюс поле
вида `attempted_models: list[str]` / `primary_model_id`, чтобы `model_id` не зависел от
того, на каком кандидате свет погас.

### H3 — Калибровка уверенности является мёртвым кодом

`parse_prediction_payload` (`:358`) вызывает синхронную `_calibrate_confidence` (`:265-277`),
которая изнутри делает `loop.run_until_complete(_fetch_role_accuracy(role))`. Вызов идёт из
работающего event loop, поэтому по семантике asyncio **всегда** бросает `RuntimeError`, а он
проглатывается `except Exception: pass`.

**Последствие:** демпфирование уверенности по исторической точности роли
(`damp = 0.7 + 0.3 * acc/100`) не применялось ни разу. Уверенность в `forecast_points` —
сырая, из ответа модели.

**Предлагаемый фикс:** либо вынести предвыбор точности роли в async-кэш (заполняется раз за
цикл, читается синхронно), либо признать механизм недействующим и удалить, чтобы не
создавать ложного чувства контроля качества.

### M4 — Хвост каждой цепочки — гарантированно мёртвый провайдер

`strict_nvapi = False  # Ollama-only now, no NVIDIA NIM strict check` (`:771`) противоречит
построению цепочек: для worker/analyst/premium/oracle NVIDIA-кандидат добавляется при
наличии ключа (`:455-468`, `:487-500`, `:519-532`, `:550-560`), а в контейнере webui задано
~20 переменных `NVIDIA_*_API_KEY`. `integrate.api.nvidia.com` отвечает `410 Gone`;
в истории это 15+ фейлов `minimaxai/minimax-m3` и, например, 6 фейлов
`deepseek-ai/deepseek-v4-pro-0813`.

**Последствие:** «последняя надежда» не является надеждой — лишний HTTP round-trip, шум в
логах и (см. H2) некорректная атрибуция модели в фейловом прогоне.

**Предлагаемый фикс:** убрать NVIDIA-кандидатов из цепочки либо закрыть их отдельным
флагом (`PREDICTION_ALLOW_NIM=false` по умолчанию) и отметить в AGENTS.md как retired tier.

### M5 — Карта ролей в AGENTS.md расходится с прод-окружением

Значения из `printenv` в контейнере webui (модели — не секреты; ключи не выводились):

| Переменная | Значение в проде | Что говорит AGENTS.md |
|---|---|---|
| `OLLAMA_ANALYST_MODEL` | `glm-5.1` | Analyst = `deepseek-v4-pro` |
| `OLLAMA_ANALYST_FALLBACK_MODEL` | `deepseek-v4-pro` | — (не документирован) |
| `OLLAMA_PREMIUM_MODEL` | `glm-5.2` | Premium = `glm-5.2` ✅ |
| `OLLAMA_ORACLE_MODEL` | `minimax-m3` | Oracle = `kimi-k2.6` / `kimi-k2:1t` |
| `OLLAMA_ORACLE_FALLBACK_MODEL` | `glm-5.1` | — |

Заметим, что дефолт в коде для analyst — тоже `glm-5.1` (`:178`, `:473`), так что расхождение
не сводится к одному `.env`. Практический эффект: «бык» систематически теряет ~60 с на
таймящийся `glm-5.1` и лишь затем уходит на `deepseek-v4-pro`.

### M6 — Независимость бандла не гарантирована

Fallback арбитра (`glm-5.1`) совпадает с primary аналитика (`glm-5.1`). На #136 это и
сработало: bull сидел на `deepseek-v4-pro` после фейла glm-5.1, а «второе независимое
мнение» арбитра отдал `glm-5.1`. Дедупликации моделей между ролями нет ни в
`generate_role_prediction_bundle` (`:895-940`), ни в `_build_runtime_candidates`.

**Предлагаемый фикс:** непересекающиеся fallback-наборы по ролям + исключение моделей,
уже успешно отработавших в текущем бандле.

### M7 — Выпадение роли не видно в деке и не влияет на статус запроса

`/api/trader/forecast` (`trader_api.py:219-227`) агрегирует **все** точки запроса по
`step_index` в q10/q50/q90 без фильтра по роли. На #137 арбитра нет вовсе — полоса
собрана из 2 сэмплов на шаг, а бейдж источника по-прежнему `postgres`; при одном
дожившем роли q10 = q50 = q90, то есть график рисует иллюзию нулевой неопределённости.
При этом `execution_state` запроса остаётся `completed`, хотя роль бандла провалена.

**Предлагаемый фикс:** отдавать в API покрытие ролей (`roles: {bull,bear,arbiter}` и число
сэмплов на шаг) и отдельный provenance-статус `partial`; либо считать запрос `partial`
при провале любой роли.

### Low — Косметика, зафиксированная попутно

- `_attempt_schedule` для `minimax` = `(0.0,)` (`:758-761`): арбитр в принципе не имеет
  второй попытки на одной модели. Осознанно, но в связке с H1 ретраев фактически нет ни у
  кого.
- Декоративный скелет в `trader_api.py:~100-112` перечисляет агентов на моделях
  `deepseek-v4.1-flash`, `minimax-m2.7`, `glm-5.3-flash` и бюджет `0.62 / 1.6` — вне
  активного реестра моделей. Подпись `МАКЕТ` (`trader.html:21`) есть, но имена моделей
  читаются как реальные.
- **Противоречие, внесённое item A:** сноска `trader.html:156` утверждает
  «Данные демонстрационные при отсутствии подключения к Redis/Postgres», тогда как после
  item A (`trader_api.py`) mock-ветки удалены и при отсутствии данных возвращается честная
  пустота с `source="unavailable"`. Текст устарел и требует синхронизации с новой data policy
  (плюс захардкоженный `0.0100% (демо)` funding там же).

---

## 3. Что проверялось и как это воспроизвести

```bash
# логи роли-фолбэка
docker compose logs webui --since 60m | grep -E "Prediction (model|minimax|analyst)|switching"

# итог по ролям и моделям за всю историю
SELECT model_key, agent_role, model_id, status, count(*)
FROM forecast_model_runs GROUP BY 1,2,3,4 ORDER BY 1,5 DESC;

# покрытие ролей в конкретном запросе
SELECT id, model_key, model_id, agent_role, status, left(error_message,70)
FROM forecast_model_runs WHERE request_id = 137 ORDER BY id;

# фактические модели ролей в рантайме (без ключей)
docker compose exec -T webui sh -lc "printenv OLLAMA_ANALYST_MODEL OLLAMA_ANALYST_FALLBACK_MODEL \
  OLLAMA_ORACLE_MODEL OLLAMA_ORACLE_FALLBACK_MODEL OLLAMA_PREMIUM_MODEL"
```

Регрессия-тесты на момент обзора: локально `python -m pytest tests/paper_trading -q
-p no:cacheprovider`; полный Docker-набор `docker compose -f docker-compose.qa.yml
-p wored-qa run --rm --build checks pytest tests/ -q -p no:cacheprovider` — baseline
**818 passed / 5 documented pre-existing failures**.
`--build` здесь обязателен: QA-образ копирует репозиторий на сборке, без него
прогоняются тесты предыдущего кода (стоило 2026-09-22 отдать «зелёный» результат
на коде до правок; см. `docs/OPERATIONAL-PROCESS.md` §1).

## 4. Приоритеты на следующий заход

| Приоритет | Пункт | Почему именно там |
|---|---|---|
| P1 | H1 + H2 | Один файл, меняется только ветка обработки ошибок; возвращает читаемую причину и честную атрибуцию роли |
| P2 | H3 | Без этого статистика точности и уверенность в UI — самообман |
| P3 | M4 + M6 | Гигиена цепочек: мёртвый tier и каннибализация моделей между ролями |
| P4 | M5 + M7 | Требуют решения по `.env`/AGENTS.md и правки контракта API + UI-бейджа |
