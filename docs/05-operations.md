# Эксплуатация и проверка

## Назначение

Этот документ описывает команды, которые нужны для повседневной эксплуатации, валидации и отладки корневого стека `D:\WORED`.

## Состояние сервисов

Показать состояние контейнеров:

```powershell
Set-Location D:\WORED
docker-compose ps
```

Ожидаемый результат:

- пять сервисов в списке,
- `postgres` в `healthy`,
- `collector` и `chatbot` в `Up`,
- `webui` в `healthy`.

## Логи

Все логи:

```powershell
docker-compose logs -f
```

Только chatbot:

```powershell
docker-compose logs --tail 80 chatbot
```

Только collector:

```powershell
docker-compose logs --tail 80 collector
```

Только Postgres:

```powershell
docker-compose logs --tail 40 postgres
```

Только Redis:

```powershell
docker-compose logs --tail 40 redis
```

Только webui:

```powershell
docker-compose logs --tail 80 webui
```

## Проверки Redis

Посмотреть ключи тикеров:

```powershell
docker-compose exec redis redis-cli KEYS "ticker:*"
```

Прочитать один тикер:

```powershell
docker-compose exec redis redis-cli GET ticker:btcusdt
```

Послушать pub/sub руками:

```powershell
docker-compose exec redis redis-cli SUBSCRIBE market_alerts
```

## Проверки Postgres

Открыть SQL shell:

```powershell
docker-compose exec postgres psql -U bot -d trading
```

Посчитать алерты:

```powershell
docker-compose exec postgres psql -U bot -d trading -c "SELECT COUNT(*) FROM alerts;"
```

Показать последние записи журнала:

```powershell
docker-compose exec postgres psql -U bot -d trading -c "SELECT timestamp FROM ai_journal ORDER BY timestamp DESC LIMIT 5;"
```

Проверить, заполняется ли `market_tickers`:

```powershell
docker-compose exec postgres psql -U bot -d trading -c "SELECT COUNT(*) FROM market_tickers;"
```

Как интерпретировать:

- если растёт `ai_journal`, значит scheduler и pipeline контекста работают,
- если `market_tickers` пуст, это отражает текущее подключение кода, а не обязательно аварию.

## Проверки webui

Проверить health endpoint:

```powershell
Invoke-WebRequest http://localhost:8080/api/health
```

Проверить overview API:

```powershell
Invoke-WebRequest http://localhost:8080/api/overview
```

Проверить свечной endpoint для конкретного окна:

```powershell
Invoke-WebRequest "http://localhost:8080/api/candles?symbol=btcusdt&period=60min&size=120"
```

Проверить Prediction Lab page:

```powershell
Invoke-WebRequest http://localhost:8080/predictions
```

Как интерпретировать:

- если `/api/health` даёт `redis=true` и `postgres=true`, webui видит оба хранилища,
- если `collector_feed=false`, webui жив, но collector давно не писал `ai_journal`,
- если `/api/candles` падает с `502`, проблема во внешнем HTX REST path, а не во frontend shell.

Если `WEBUI_AUTH_ENABLED=true`:

- сначала выполни login через `/login` в браузере,
- `dashboard`, `alerts`, `journal`, `/api/overview`, `/api/alerts`, `/api/journal`, `/api/candles` будут требовать сессию,
- `/api/health` остаётся публичным, иначе compose healthcheck сломается.

## Тестовые команды

### Проверенные команды

Запуск тестов `chatbot` внутри проектного контейнера:

```powershell
docker-compose run --rm chatbot pytest tests -q
```

Запуск тестов `collector` внутри проектного контейнера:

```powershell
docker-compose run --rm collector pytest tests -q
```

Запуск тестов `webui` внутри проектного контейнера:

```powershell
docker-compose run --rm webui pytest tests -q
```

Ожидаемый результат:

- проходят unit/smoke тесты по helper-функциям, auth redirect и HTML-рендерингу.

### Проверка синтаксической собираемости

```powershell
python -m compileall D:\WORED\chatbot D:\WORED\collector D:\WORED\webui
```

Ожидаемый результат:

- компиляция успешно проходит для всех трёх Python-модулей runtime.

## Сигналы здоровья и их смысл

Хорошие сигналы:

- `Connected to HTX WebSocket`
- `AI journal entry written`
- `Recorded 15m AI journal entry`
- `Chatbot started polling`
- `AI 'analyst' responded in ...`
- `GET /api/health 200 OK`

Предупреждения, которые могут самовосстанавливаться:

- `WebSocket disconnected ... Reconnecting in 5s`
- Telegram `Request timeout error` с последующим reconnect

Сигналы реального технического долга:

- частые fallback-срабатывания после `MiniMax` reviewer,
- upstream timeout или нестабильность внешних AI endpoints,
- лишние секреты, которые по-прежнему прокидываются в `postgres` через общий `env_file`,
- стабильные `502` по `/api/candles`, если HTX REST path недоступен.

## Особенности Windows

Корневой `Makefile` ориентирован на Bash и не должен считаться главным интерфейсом эксплуатации на Windows. В PowerShell лучше использовать прямые `docker-compose` команды.

Примеры Unix-специфичных конструкций в текущем `Makefile`:

- `mkdir -p`
- `rm -rf`
- `read`
- `date +%Y-%m-%d`

## Безопасный restart

Если проблема только в `chatbot`:

```powershell
docker-compose restart chatbot
docker-compose logs --tail 80 chatbot
```

Если проблема только в `collector`:

```powershell
docker-compose restart collector
docker-compose logs --tail 80 collector
```

Если проблема только в `webui`:

```powershell
docker-compose restart webui
docker-compose logs --tail 80 webui
```

Если нужен полный перезапуск:

```powershell
docker-compose down
docker-compose up --build -d
docker-compose ps
```

## Текущий контур валидации

Что уже подтверждено:

- Compose config парсится.
- Контейнеры поднимаются.
- Collector пишет `ai_journal`.
- Тесты `chatbot` проходят в контейнере.
- Тесты `collector` проходят в контейнере.
- Тесты `webui` проходят в контейнере.
- Python-исходники компилируются.

## Prediction Lab database check

```powershell
$user = docker exec htx_trading_bot_postgres printenv POSTGRES_USER
$db = docker exec htx_trading_bot_postgres printenv POSTGRES_DB
docker exec htx_trading_bot_postgres psql -U $user.Trim() -d $db.Trim() -c "\dt forecast_*"
```

Ожидаемый результат:

- видны `forecast_requests`, `forecast_model_runs`, `forecast_points`.

Что пока не подтверждено полностью:

- полный Telegram end-to-end под нагрузкой,
- стабильный path второго мнения именно через NVIDIA NIM,
- потоковая запись в `market_tickers`,
- токен-аккаунтинг в `ai_usage_log`,
- полноценный browser smoke test с ручной визуальной проверкой графиков после рестарта.
