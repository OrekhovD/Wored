# WORED Telegram Bot — operations runbook (P2 scope)

Обновлён: 2026-10-08. Фаза: P2 read vertical slice.
Область: только read-команды консоли (`/start`, `/now`, `/market`, `/forecast`,
`/session`, `/positions`, `/report`, `/help`, `/cancel`) и deny-by-default guard.
Command/plan/ledger-мутирующие сценарии (P3…P6) здесь не описаны и не работают.

## 1. Назначение

Что делать, если Telegram-бот отвечает не тому, отказывает всем, показывает
устаревшие данные или после деплоя ведёт себя не так, как WebUI.

## 2. Активный runtime path

Бот живёт в сервисе `chatbot` (docker compose), код — `./chatbot` bind-mount.
Критичные файлы фазы P2:

```
chatbot/main.py                       # порядок роутеров + установка guard
chatbot/security/principal.py         # allowlist, роли, timezone, identity
chatbot/security/guards.py            # deny-by-default middleware, throttle
chatbot/integrations/wored_api.py     # read-источники (Redis/postgres/domain)
chatbot/application/presenters.py     # карточки, escaping, error taxonomy
chatbot/handlers/now.py               # экраны консоли
chatbot/handlers/deprecated.py        # редиректы старых команд
```

Карта команд: `docs/TELEGRAM-BOT-COMMAND-MAP.md`. Контекст дефектов:
`docs/TELEGRAM-BOT-RFC.md`.

## 3. Ключи окружения (P2…P3c)

| Ключ | Значение по умолчанию | Эффект |
|---|---|---|
| `BOT_OWNER_IDS` | пусто | канонический allowlist owner; **пусто = отказано всем** |
| `TELEGRAM_ADMIN_IDS`, `TELEGRAM_ADMIN_ID` | пусто | временные алиасы, использование логируется по имени |
| `BOT_ADMIN_IDS` | пусто | роль admin (читатель с расширенным доступом) |
| `BOT_DEFAULT_TIMEZONE` | `Asia/Bangkok` | часовой пояс карточек |
| `BOT_DEFAULT_INSTRUMENT` | из `PAPER_CONTRACTS` | инструмент консоли |
| `PAPER_CONTRACTS` | `BTC-USDT` | канонический инструмент домена/collector |
| `PAPER_MARKET_MAX_AGE_SECONDS` | `5.0` | окно свежести (bounds `0 < x ≤ 60`) |
| `BOT_MARKET_REFRESH_MIN_SECONDS` | `2` | throttle на «🔄 Обновить» |
| `BOT_WIZARD_TTL_HOURS` | `24` | TTL черновика SimulationPlanV1 в хранилище (P3c). Ключ **не** прописан в `.env*`: отсутствие даёт 24 ч. Нечисло или значение ≤ 0 тоже дают 24 (fail-safe, а не падение). Просроченный черновик не удаляется — он остаётся в списке, но любое действие на нём даёт `draft_expired` |
| `BOT_ALLOW_GROUPS` | `false` | групповые чаты запрещены |
| `TG_MINIAPP_URL` / `WEBUI_URL` | localhost | Mini App; кнопка показывается только при `https://` |

Проверка наличия без вывода секретов:

```powershell
Select-String -Path .env,.env.wored -Pattern '^(BOT_OWNER_IDS|TELEGRAM_ADMIN_IDS|TELEGRAM_ADMIN_ID|BOT_ADMIN_IDS|BOT_WIZARD_TTL_HOURS)=' | ForEach-Object { ($_.Line -split '=')[0] + '=***' }
```

Если строки нет — ключ не задан, действует default из таблицы (для
`BOT_WIZARD_TTL_HOURS` это 24 ч; проверить значение в живом контейнере:
`docker compose exec -T chatbot python -c "import os;print(os.getenv('BOT_WIZARD_TTL_HOURS','24'))"`).

## 4. Здоровье / диагностика

```powershell
docker compose ps
docker compose logs chatbot --tail=120
docker compose exec -T redis redis-cli get "market:perpetual:htx:BTC-USDT"
```

Что искать в логах:

* `AUTHZ CONFIG: no Telegram allowlist configured …` — guard установлен,
  allowlist пуст, всем отказывается (типичный симптом «бот молчит»);
* `AUTHZ CONFIG: allowlist read from legacy alias TELEGRAM_ADMIN_ID …` —
  работает, но требуется миграция на `BOT_OWNER_IDS`;
* `market snapshot rejected for …` — payload в Redis не прошёл валидацию
  домена: карточка покажет `MARKET_INVALID`, а не `0`;
* `market read failed for …` — Redis недоступен: `MARKET_BACKEND_UNAVAILABLE`;
* `console screen <name> failed: …` — исключение при рендере экрана; вместо
  битого экрана пользователь получает карточку с machine-кодом.

## 5. Типовые инциденты

### 5.1 Отказано всем после деплоя

Причина: guard включён (bind-mount кода), а `BOT_OWNER_IDS` не задан.
Исправление: добавить в `.env.wored` строку `BOT_OWNER_IDS=<telegram user id>`,
затем **пересоздать** сервис: `docker compose up -d chatbot` (рестарт — только
по явному решению оператора).

⚠️ Критично: `env_file` читается при СОЗДАНИИ контейнера. `docker compose
restart chatbot` (и `docker start` после апгрейда демона) новые ключи НЕ
подтягивает — контейнер продолжает работать со старым окружением, и guard
всё ещё видит пустой allowlist. Проверить фактически:

```powershell
docker compose exec -T chatbot python -c "import os;print(bool(os.getenv('BOT_OWNER_IDS')))"
```

Дать `True` может только `up -d` (recreate). Обратная сторона: изменение кода в
bind-mount монтируется сразу, но модули импортированы запущенным процессом, —
для нового кода достаточно `restart`, для нового env — `up -d`.

Проверка: `/now` отвечает владельцу, неизвестный пользователь
получает отказ без деталей.
Rollback: вернуть предыдущий образ/код; данных фаза не меняла.

### 5.2 Карточка рынка показывает stale

`age_seconds` выше `PAPER_MARKET_MAX_AGE_SECONDS`: collector не пишет или пишет
медленнее окна. Бот честно помечает `stale` и показывает последнее
подтверждённое значение с возрастом; значение не обнуляется. Либо починить
collector, либо осознанно увеличить окно (не выше 60 s).

### 5.3 Telegram и WebUI показывают разные числа

Проверить, что обе стороны читают один источник: Redis-ключ снапшота,
`get_latest_prediction_request` для прогноза, `paper_trading.adapter` для
сессии/позиций. Бот не пересчитывает финансы сам; расхождение означает
расхождение источника, а не презентера.

### 5.4 Бот не создаёт черновик плана (`/plans new`)

Два честных отказа, оба — не баг презентации:

* `simulation_store_unavailable` — Postgres не подключён. У plan-writer'а нет
  memory-fallback намеренно: черновик в памяти процесса был бы невидим дашборду,
  и TB-03 (один owner, один план, одна цифра) сломался бы молча.
  Проверка (в PowerShell backslash удваивается, иначе парсер съедает `\d`):
  `docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dt"'` —
  в списке обязаны быть `simulation_plan_versions` и `simulation_sessions`.
* `contract_refused` (карточка «значение не записано») — поле не проходит
  **разбор** (`field_range` / `field_enum` / `unknown_field`). Черновик при этом
  остаётся байт-в-байт прежним. Смысловые нарушения (`loss_hierarchy`,
  `live_window_past`, `missing_intervals`) записываются и блокируют утверждение —
  это то же разделение, что у формы дашборда.

Утверждение плана двухшаговое: карточка → `plan:appr:<id>:<hash>` →
`plan:confirm:<id>:<hash>`. Если рынок деградировал между показом карточки и
нажатием, вторая кнопка не утвердит план, а покажет `stale_quote` (и
`can_approve: false`); повторное нажатие на уже утверждённый хэш даёт
`plan_immutable`. Старт сессии из бота отсутствует намеренно (Q-10/Q-13).

### 5.5 Черновик из чата не виден в дашборде

Обе стороны обязаны использовать канонический uuid5-ключ owner
(`uuid5(NAMESPACE_URL, "wored:owner:<telegram_user_id>")`, P3b/D-17). Если
появился четвёртый namespace owner'а — править не презентер, а точку вычисления
ключа (`chatbot/security/principal.py` / `webui/simulation_api._owner_id`).

## 6. Тесты и границы

Раздельные прогоны обязательны: в одном pytest-прогоне `chatbot/tests` и
корневые `tests/` падают на сборе (коллизия пакета `tests`, дефект D-12 в RFC);
однотипная коллизия top-level пакета `storage` (collector против chatbot,
D-13) обходится в `tests/telegram/conftest.py`:

```powershell
python -m pytest tests/telegram -q
python -m pytest chatbot/tests -q
python -m pytest tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q
```

Известные ограничения фазы: real Telegram smoke не выполнялся (отдельного
test-бота нет → TB-04/TB-05/TB-06 остаются `PARTIAL`/`NOT RUN`);
`bot_*`-таблицы и dedup по `update_id` ещё не введены; legacy
`pipeline._handle_stop_safe` и старые callback-поверхности (`trader_*`,
Аналитика/Прогнозы/Алерты) живут по-прежнему и в P2 не трогались.

P3c добавляет экраны планов, но не добавляет прав запуска: старт сессии остаётся
за WebUI. Живое подтверждение сквозного пути получено на данных (chatbot →
Postgres → webui, идентичный `plan_hash`), текст карточки из Telegram
фиксируется отдельно по команде пользователя.
