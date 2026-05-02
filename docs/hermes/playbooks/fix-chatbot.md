# Playbook: Fix Chatbot

## Цель
Диагностика и починка Telegram chatbot.

## Что проверять

### aiogram
- Startup polling успешен
- Bot identity получен (`bot.me()`)
- FSM (Finite State Machine) в Redis работает

### AI Provider Routing
- GLM-5.1 (deep_analysis) — open.bigmodel.cn
- Qwen3.6 Flash (quick_chat) — dashscope.aliyuncs.com
- Qwen3.6 (backtest_code) — dashscope.aliyuncs.com
- Perplexity Sonar Pro (market_news) — api.perplexity.ai
- Circuit breaker + fallback: Qwen → GLM → MiniMax

### Telegram
- Token presence (НЕ значение!) — `env | grep TELEGRAM_TOKEN | cut -c1-25`
- Admin ID корректный
- Notification delivery

## Шаги диагностики

### 1. Проверить логи
```
/lb
```
Искать: TelegramUnauthorizedError, AI provider errors, Redis FSM errors, circuit breaker trips.

### 2. Проверить контейнер статус
```
/ps
```
Если "Restarting" — токен невалиден или критичная ошибка.

### 3. Проверить env (presence only!)
```bash
docker exec htx_trading_bot_chatbot env | grep TELEGRAM_TOKEN | cut -c1-30
```
Ожидание: `TELEGRAM_TOKEN=8343265724:AAF...` (первые символы, НЕ весь токен)

### 4. Проверить Redis FSM
```bash
docker compose exec -T redis redis-cli --scan --pattern 'fsm:*' | head -10
```

### 5. Проверить AI routing
```bash
docker compose logs chatbot --tail=200 | grep -Ei 'ai.*rout|fallback|circuit|provider'
```

### 6. Проверить health
```
/health
```

## Типовые проблемы

### TelegramUnauthorizedError
**Причина**: невалидный или отозванный TELEGRAM_TOKEN.
**Решение**:
1. Обновить `TELEGRAM_TOKEN` в `.env`
2. ⚠️ `docker compose restart chatbot` НЕ перечитывает env_file!
3. Нужно: `docker compose stop chatbot && docker compose rm -f chatbot && docker compose up -d chatbot`

### AI provider timeout/failure
1. Проверить логи на circuit breaker trips
2. Проверить API ключи (presence only): `docker exec htx_trading_bot_chatbot env | grep -E 'GLM|QWEN|PERPLEXITY' | sed 's/=.*/=***/'`
3. Проверить fallback chain работает

### Redis FSM не работает
1. `docker compose exec -T redis redis-cli ping`
2. Проверить REDIS_HOST/REDIS_PORT в env чатбота

### Chatbot молчит (нет ответов в Telegram)
1. Проверить polling: логи должны показывать updates
2. Проверить handlers注册
3. Проверить AI provider availability

## Файлы
### Можно трогать (с подтверждения)
- `chatbot/handlers/` — обработчики команд
- `chatbot/ai/router.py` — AI роутинг
- `chatbot/ai/prompts.py` — системные промты
- `chatbot/ai/dispatcher.py` — multi-model dispatch
- `chatbot/ai/resilience.py` — circuit breaker
- `chatbot/context/builder.py` — сборка снапшота
- `chatbot/ui/` — formatter, keyboards
- `chatbot/integrations/` — внешние сервисы

### Нельзя трогать без явного запроса
- `chatbot/main.py` — точка входа
- `chatbot/ai/models.py` — конфиг моделей (критично для роутинга)
- `chatbot/storage/` — Redis/Postgres clients

## Когда остановиться и спросить владельца
- Изменён AI routing (модель → интент)
- Добавлен новый AI provider
- Изменён circuit breaker порог
- Изменена FSM логика
- Требуется пересборка Docker образа

## Критерии готовности
- aiogram polling работает
- Bot identity получен (лог: `Run polling for bot @...`)
- AI providers отвечают (или fallback работает)
- FSM states сохраняются в Redis
- Команды в Telegram получают ответы
