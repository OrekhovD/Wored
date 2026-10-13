# P3d отчёт — предложение агента в чате (функция ТЗ U05, гейт P3)

Дата: 2026-10-13. Фаза: P3d (последний срез P3). Baseline: `0f2359a`, **ничего не закоммичено**.
ТЗ: `docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`, §5 U05, TB-11, TB-12, §6 (таксономия отказов),
гейт выхода P3 (ТЗ:221): «Из UI виден тот же `plan_hash` и violations, что в WebUI; AI failure safe».

## 0. Инцидент безопасности, который нужно закрыть до коммита

При проверке наличия конфигурации дашборда я выполнил
`printenv WEBUI_ADMIN_PASSWORD | sed 's/=.*/=SET/'` внутри контейнера `webui`.
`printenv <NAME>` печатает **только значение без префикса `NAME=`**, поэтому `sed`-маскировка
не сработала и 32-символьный пароль `WEBUI_ADMIN_PASSWORD` попал в лог сеанса.
Это нарушение правила AGENTS «не печатать секреты; проверка presence — только через
`sed 's/=.*/=***/'`». Правильная форма для `printenv`: `printenv NAME | wc -c` или
`sh -c '[ -n "$NAME" ] && echo set'`.

Что требуется от куратора: **ротиловать `WEBUI_ADMIN_PASSWORD`** (и, если он продублирован,
`WEBUI_ADMIN_PASSWORD` в `.env` / `.env.wored`), затем `docker compose up -d --force-recreate webui`.
Значение нигде не записано мной в файлы репозитория; утечка ограничена журналом сеанса.

## 1. Решения куратора, которые определяли объём

| Решение | Что сделано |
|---|---|
| «Вынести в `trading_math` (рекомендую)» | домен перенесён дословно в `trading_math/simulation_proposal.py`; `webui/simulation_proposal.py` стал HTTP-адаптером с re-exportом имён |
| «Опциональный адаптер сейчас, по умолчанию rules (рекомендую)» | `chatbot/ai/proposal_source.py` существует, достигается только при `BOT_PROPOSAL_SOURCE=llm`; боевая часть доказательства «AI failure safe» сделана без сети и без Credits |
| «Я сам, через клиент (рекомендую)» | живой гейт проведён в десктопном Telegram, карточки списаны с экрана и сверены с логами контейнера |

## 2. Что изменено в коде (runtime path, не legacy)

| Файл | Характер |
|---|---|
| `trading_math/simulation_proposal.py` | **новый** общий домен (551 строка): анкета, rule-engine, сторы, `run_proposal`, `validate_draft`, `adopt_plan_body`, `InvalidAgentEnvelope` |
| `webui/simulation_proposal.py` | стал адаптером: роутер + `_market_provider` + re-export 25 имён |
| `chatbot/services/simulation_proposals.py` | **новый** bot-side кейс: парсер ответа, стор, политика источника, `create/list/get/resolve/adopt` |
| `chatbot/ai/proposal_source.py` | **новый** opt-in агент: `llm_source`, `_extract_envelope`, `_check_owner_quota` |
| `chatbot/ai/prompts.py` | добавлен ключ `plan_proposal_agent` |
| `chatbot/ai/router.py` | добавлены `call_raw` (один модельный вызов, без badge и без цепочки) и `strip_code_fence`; два внутренних место использования заменены на общий хелпер |
| `chatbot/application/presenters.py` | `build_questionnaire_card`, `build_proposal_card`, `build_proposal_list_card` |
| `chatbot/handlers/simulation_v3.py` | `/plans propose│proposals│proposal│adopt`, `prop:*` callbacks, две клавиатуры |
| `tests/telegram/test_wizard_and_proposals.py` | **новый**, 23 теста |
| `tests/test_simulation_proposal.py` | +11 тестов (адаптер, adopt-gate, ctx, typed refusal) |

Схемы БД, миграции, `docker-compose.yml`, `.env` — **не тронуты**. Legacy-зоны не тронуты.

## 3. Гейты на хосте

```
py -m pytest tests/telegram tests/test_simulation_proposal.py tests/test_simulation_api.py \
    tests/test_plan_validation.py tests/test_session_plan_contract.py tests/test_ui_presenters.py -q
→ 331 passed, 15 skipped in 6.39s        (повторено после рестартов сервисов)

py -m ruff check <изменённые файлы>       → 5 findings, все предсуществующие
  (доказано сравнением с `git show HEAD:<файл> | py -m ruff check --stdin-filename <файл> -`)

py output/p3d_verbatim_check.py            → RESULT: VERBATIM (only the approved deltas differ)
  normalize_questionnaire / validate_draft / SQL — идентичны;
  ProposalRecord: +adopted_plan_id; PostgresProposalStore: +mark_adopted,+list_recent;
  MemoryProposalStore: +list_recent,+mark_adopted; run_proposal: +ветка InvalidAgentEnvelope
```

Допустимые дельты в домене (ровно три, все обоснованы): подписанный дефолт инструмента
(`DEFAULT_INSTRUMENT_KEY`), привязка принятого черновика (`adopted_plan_id` внутри
существующего `result` jsonb — без миграции), и аддитивная ветка `InvalidAgentEnvelope`
→ `llm_invalid_response`, без неё сбой формата ответа был бы назван `llm_unavailable`,
что прямо запрещено §6 («называть реальное условие»).

## 4. Контейнерные пробы (p3d8)

Все пробы — внутри живых контейнеров, на реальных mounts (`/opt/trading_math`), не на хосте.

### 4.1 Равенство дайджеста между двумя образами

Один и тот же скрипт (`output/p3d_probe_hash.py`, фиксированные `now`) прогнан через
`docker compose exec -T`:

| Образ | Python | `plan_hash` |
|---|---|---|
| chatbot | 3.9.25 | `sha256:a9930f1cfb64dabfec8075abd53d4053b9eee53da5d2ddb007936491eb026666` |
| webui | 3.11.16 | `sha256:a9930f1cfb64dabfec8075abd53d4053b9eee53da5d2ddb007936491eb026666` |

Побайтово совпало. Из 14 проверяемых имён адаптера 13 — те же объекты; единственный
отличающийся — `router` (он и обязан принадлежать webui: домен не знает транспорта).

### 4.2 Postgres round-trip через собственный слой бота

`output/p3d_probe_pg.py` внутри `chatbot`, owner-заглушка `5249526259-p3d-probe`:

```
rules_create   status=completed model=wored-rules-v1 adoptable=true violations=[]
               hash=sha256:76ee84d1f73f62c7ae0cce0d7efa084132c98a8d6a8d0b11e98c35e8257879b8
hash_equality  record == recomputed(plan_hash(normalize_plan(draft))) == wire(proposal_to_dict) → equal=true
pg_reload      из БД: same_status/same_hash/same_draft=true, adopted_before=null
adopt_first    already=false plan=8d80cd69 status=draft plan_hash == hash предложения
adopt_second   already=true, same_plan_id=true, bound_now=true
list_recent    contains_probe=true bound_in_store=[8d80cd69…]
```

Кросс-табличный контроль прямым SQL (`simulation_plan_proposals` ⨝ `simulation_plan_versions`):

```
proposal|4dcdf916…|proposal_hash|sha256:76ee84d1…
plan_id |8d80cd69…|plan_hash    |sha256:76ee84d1…|status=draft|owner=5249526259-p3d-probe
```

### 4.3 Таксономия отказов без единого платного вызова

`output/p3d_probe_llm.py`: `BOT_PROPOSAL_SOURCE=llm`, все `api_key_env` модели обнулены.

```
chain=analyst_ollama,analyst_bonsai,worker_ollama,worker_bonsai,premium_ollama,premium_bonsai
elapsed 21.49s → status=failed reason_code=llm_timeout  (wall 20.0)
elapsed  5.15s → status=failed reason_code=llm_timeout  (BOT_PROPOSAL_TIMEOUT=5.0)
draft=нет, adoptable=нет, строка отказа: «agent did not answer within Ns; manual form stays available»
```

Уточнение честности: «без сети» здесь означает **без облачного вызова и без Credits**.
Облачный тир отвалился мгновенно (`OLLAMA_CLOUD_API_KEY is not set`), дальше цепочка ушла в
локальный Bonsai-сервер, который жив (`host.docker.internal:8088`, проверено `connect_ex=0`)
и генерирует дольше стены в 20 с. Поэтому итог — `llm_timeout`, и это правильное имя
условия: агент действительно не ответил за отведённое время. `llm_quota`, `llm_unavailable`
и `llm_invalid_response` в живом контейнере не проверялись (нужны управляемые ответы
провайдера) — они покрыты хост-тестами: `test_quota`, `test_unavailable`,
`test_invalid_response`, `test_answer_without_a_plan_is_invalid_response_not_an_outage`,
`test_typed_agent_refusal_keeps_its_own_reason_code`.

### 4.4 Живой дашборд как источник черновика

`output/p3d_probe_dash.py` внутри `webui` — настоящий HTTP-круг с сессией:
`POST /api/v3/simulation/plan-proposals` → `201`, затем `GET …/{id}` → `status=completed`,
`plan_hash=sha256:514c9da59528560a0ef97f38e2ba1a020eae1946f2d026fc856e5d1573b70a17`,
`adoptable=true`, `violations=[]`, `missing_data=[]` (качество live из Redis-снапшота).

Тот же черновик, переданный в образ `chatbot` (`output/p3d_cross_chat.py`):

```
dashboard_hash = sha256:514c9da5…  chatbot_recomputed = sha256:514c9da5…
chatbot_domain_hash = sha256:514c9da5…  identical=true
```

Два артефакта пробы, не продукта: httpx не переотправляет cookie, выданный на хосте-IP,
и не ставит `Origin`, который требует CSRF-middleware дашборда для всех не-GET. Оба
обойдены в самом пробнике; браузер их посылает штатно.

## 5. Рестарты и живой гейт в Telegram (p3d9)

```
docker compose restart chatbot webui
docker compose ps → chatbot Up (healthy), webui Up (healthy)
логи chatbot после рестарта: «Chatbot started polling», без ImportError
```

Прогон в десктопном Telegram, бот `@W_W_O_O_bot`:

* `/plans propose` → анкета со списком ключей, допустимыми значениями enum из домена, инструментом по
  умолчанию `htx:linear-swap:BTC-USDT` и подписью источника
  «детерминированные правила (бесплатно, воспроизводимо)».
* `/plans propose goal=risk_control duration=24 side=both involvement=supervised manual=1000 auto=1000 maxloss=300 hours=any`
  → карточка «Предложение плана 6d1d7de7»: бюджеты 1000/1000, риск/ордер 10.00, день 200.00,
  сессия 300.00, exposure 20.00, позиции 2, плечо 3×, cooldown 5, стороны long/short,
  обоснование (9 пунктов), допущения, «Рынок на момент черновика: качество live · snapshot fc4be25d»,
  «Нарушений нет — контракт и гейты домена пройдены»,
  **`sha256:4f5a4638151eb3841d3d65c8bf742538eb234d8da68bfbb055339ff686f40780`**.
  Кнопки: 🔄 Обновить · 📥 Принять как черновик · ⬅️ Список · 🏠 Сейчас.
* Нажатие «📥 Принять как черновик» → карточка предпросмотра черновика `792de7f2`
  с тем же набором полей, худшим допустимым убытком 20 USDT и **тем же
  `sha256:4f5a4638151e…`**; в подвале «предложение 6d1d7de7 → черновик 792de7f2».
  Кнопки утверждения не нажимались (старт сессии — только WebUI).
* `/plans proposals` → «Показано: 1», `6d1d7de7 готово · wored-rules-v1 · risk_control 24 ч ·
  нарушений нет · принято как черновик 792de7f2`.
* `/plans propose goal=get_rich …` → честный отказ
  «анкета не принята: домен отклонил поля / questionnaire_invalid /
  goal field_enum — goal must be one of ['learning', 'comparison', 'risk_control']»;
  новое предложение не создано.
* `/plans proposal 6d1d7de7` → та же карточка с пометкой «Это предложение уже стало
  черновиком 792de7f2», кнопка «Принять» исчезла (идемпотентность видна в UI).

Сверка с `docker compose logs chatbot`:

```
20:39:25 chatbot.proposals - INFO - PROPOSAL_RAN actor=5f44d066 prop=6d1d7de7 status=completed source=rules model=wored-rules-v1 hash=sha256:4f5a4638151e
20:50:43 chatbot.plans     - INFO - PLAN_DRAFT_CREATED actor=5f44d066 plan=792de7f2 hash=sha256:4f5a4638151e
20:50:43 chatbot.proposals - INFO - PROPOSAL_ADOPTED actor=5f44d066 prop=6d1d7de7 plan=792de7f2 hash=sha256:4f5a4638151e bound=True
```

**Гейт P3 закрыт:** один и тот же `plan_hash` и тот же список `violations` видны из чата и
из дашборда; ветка отказа агента безопасна (нет черновика, ручной путь жив, имя условия
соответствует §6).

## 6. Открытое и не сделанное намеренно

1. **UX входа в анкету (Q-20 в RFC).** Голый ответ `goal=… duration=…` без `/plans propose`
   не попадает в конвейер — его принимает обычный AI-чат и отвечает карточкой анализа.
   Первый живой прогон сорвался именно на этом (моя ошибка в постановке прогону, а не дефект
   валидатора). Сейчас это зафиксировано как открытое решение, а карточка анкеты печатает
   полную команду-пример. Правка требует решения куратора (wizard-состояние vs явный отказ),
   в объём P3d не входила.
2. **Панель предложения в браузере дашборда** (`workspace.html`, `v3-sim-proposal-*`) визуально
   не снималась экраном: доказательство равенства получено на уровне живого HTTP-ответа этого же
   процесса и рекомпута в чат-образе. Если нужен именно скриншот — это отдельная проба.
3. **`llm_quota` / `llm_unavailable` / `llm_invalid_response` в живом контейнере** — только
   хост-тесты (см. §4.3).
4. **Дублирование `_route_trade_sim` в `chatbot/ai/router.py`** (определение ~452 строка
   затенено определением ~632). Не трогал: legacy-зона, активный путь — второй. Фиксирую как
   известную границу.
5. **Ряды проб оставлены в БД как evidence** (решение куратора «Оставить как evidence»):
   owner `5249526259-p3d-probe` — 1 completed + 1 черновик `8d80cd69`, 3 `failed/llm_timeout`;
   владелец дашборда — предложение `243ba2b4`. Живой гейт добавил `6d1d7de7`/`792de7f2`
   под каноническим владельцем.
6. **Ничего не коммитилось** (`git status` показывает изменения против `0f2359a`).

## 7. Команды повторной проверки

```powershell
cd D:\WORED
$env:PYTHONPATH="D:\WORED;D:\WORED\tests;D:\WORED\webui;D:\WORED\chatbot;D:\WORED\collector"
py -m pytest tests/telegram tests/test_simulation_proposal.py -q          # 56 passed
Get-Content -Raw output/p3d_probe_hash.py | docker compose exec -T chatbot python -
Get-Content -Raw output/p3d_probe_hash.py | docker compose exec -T webui   python -
docker compose logs chatbot --since 30m | Select-String PROPOSAL
```

## 8. Уборка

Пробники оставлены в `output/` (директория в `.gitignore`, в working tree не попадает):
`p3d_probe_hash.py`, `p3d_probe_pg.py`, `p3d_probe_llm.py`, `p3d_probe_dash.py`,
`p3d_probe_pg.sql`, `p3d_gen_cross.py`, `p3d_cross_chat.py`, `p3d_verbatim_check.py`,
`p3d_dash_out.txt`, `_orig_sim_proposal.py` (копия HEAD для AST-доказательства).
Удалены одноразовые: `output/p3d_schema_probe.sql`, `output/p3d_probe_llm.err`.
