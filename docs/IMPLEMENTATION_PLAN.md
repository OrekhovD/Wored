# План реализации ТЗ WORED AI Trader System

**Дата:** 27.06.2026
**ТЗ:** `/mnt/d/WORED/TASOCHKI/ТЗ_WORED_AI_Trader_System.md`
**Версия ТЗ:** 1.0 (257 строк, 16 разделов)

---

## 1. GAP-АНАЛИЗ: текущее состояние vs требования ТЗ

| # | Требование ТЗ | Текущий статус | GAP |
|---|---------------|----------------|-----|
| 6.1 | Команда `/models`, каталог моделей | ❌ Нет | Полный |
| 6.2 | Описание назначения моделей | 🟡 Частично (MODELS dict есть, UI нет) | Средний |
| 6.3 | Выбор модели пользователем + auto | ❌ Нет | Полный |
| 6.4 | Показ лимитов (день/неделя/месяц) | ❌ Нет | Полный |
| 6.5 | Предупреждение при 80%/95% квоты | ❌ Нет | Полный |
| 7.1 | Криптотрейдер: анализ + симуляция + обучение | 🟡 sim_engine есть, обучения нет | Средний |
| 7.2 | Только BTC/USDT, cross/isolated, >100x | 🟡 sim_engine поддерживает, валидатор частичный | Малый |
| 7.3 | 6 субагентов трейдера | 🟡 3/6 есть (Market Data, Indicator, Sim Engine) | Средний |
| 7.4 | Источники данных (WS + REST) | ✅ Есть (collector/htx) | Нет |
| 7.5 | Прогнозирование + сохранение в forecast/ai_journal | 🟡 forecast есть, ai_journal частично | Малый |
| 7.6 | Симуляция: regex-first, расчёт PnL/ликвидации | ✅ Есть (sim_engine + regex) | Нет |
| 7.7 | Performance Evaluator + Strategy Learner | ❌ Нет | Полный |
| 8 | Меню «Криптотрейдер» в Telegram | ❌ Нет | Полный |
| 9 | WebUI: Futures Lab, Strategy Performance, Model Manage | ❌ Нет | Полный |
| 10.1 | Model Lab: discover/probe/rank/rotate | ❌ Нет | Полный |
| 10.2 | Управление провайдерами + NVIDIA aliases | 🟡 MODELS dict есть, управления нет | Средний |
| 10.3 | Управление квотами | ❌ Нет | Полный |
| 10.4 | Token Accounting (request ID, tokens, latency) | ❌ Нет | Полный |
| 10.5 | Zero-trust по ключам | ✅ Есть (.gitignore, env-only) | Нет |
| 11 | Таблицы: user_model_prefs, usage_log | ❌ Нет | Полный |
| 12 | Regex-first для trade_sim | ✅ Есть | Нет |
| 13 | Self-healing fallback + auto-promote | 🟡 Fallback есть, auto-promote нет | Малый |
| 14 | Тесты: quota, token, routing, sim, e2e | 🟡 26 тестов есть, покрытия ТЗ нет | Средний |
| 15 | 5 этапов реализации | — | — |

**Итог:** 8 пунктов ✅ готово, 9 🟡 частично, 11 ❌ полностью отсутствует.

---

## 2. ПЛАН РЕАЛИЗАЦИИ ПО ЭТАПАМ ТЗ

### ЭТАП 1. Подготовка архитектуры (1-2 дня)

| Задача | Файл | Описание |
|--------|------|----------|
| 1.1 Схема БД: user_model_prefs | `chatbot/storage/postgres_client.py` | CREATE TABLE user_model_prefs (user_id, model_alias, auto_mode, created_at, updated_at) |
| 1.2 Схема БД: usage_log | `chatbot/storage/postgres_client.py` | CREATE TABLE usage_log (id, request_id, user_id, task_type, routing_mode, requested_model, final_model, prompt_tokens, completion_tokens, total_tokens, latency_ms, status, error_type, error_msg, created_at) |
| 1.3 Схема БД: sim_positions (дополнить) | `chatbot/storage/postgres_client.py` | Добавить поля: strategy_id, evaluation_status, learning_notes |
| 1.4 API-контракт gateway | `docs/gateway-api-contract.md` | Описание эндпоинтов: /v1/models, /v1/chat/completions, probe, quota |
| 1.5 Конфигурация квот | `chatbot/config/quotas.yaml` | Лимиты по tier: worker (1000/день), analyst (100/день), premium (20/день) |

### ЭТАП 2. Модельный терминал в Telegram (2-3 дня)

| Задача | Файл | Описание |
|--------|------|----------|
| 2.1 Handler `/models` | `chatbot/handlers/models.py` | Команда /models → список моделей с назначением, статусом, лимитом |
| 2.2 Inline-кнопки выбора | `chatbot/handlers/models.py` | Каждая модель → кнопка «Выбрать», плюс «Auto» |
| 2.3 Сохранение выбора | `chatbot/storage/postgres_client.py` | save_user_model(user_id, alias), get_user_model(user_id) |
| 2.4 Передача alias в router | `chatbot/ai/router.py` | route_request() читает user_model_prefs, использует выбранную модель |
| 2.5 Quota checker | `chatbot/ai/quota.py` | check_quota(user_id, tier) → bool; record_usage(user_id, tier, tokens) |
| 2.6 Token accounting | `chatbot/ai/token_accounting.py` | Логирование каждого AI-запроса в usage_log |
| 2.7 Предупреждения 80%/95% | `chatbot/handlers/chat.py` | При 80% — предупреждение, при 95% — блокировка heavy intent |
| 2.8 Регистрация в main.py | `chatbot/main.py` | dp.include_router(models_router) |
| 2.9 Тесты | `chatbot/tests/test_models.py` | pytest: выбор, квота, token accounting |

### ЭТАП 3. Криптотрейдер (3-4 дня)

| Задача | Файл | Описание |
|--------|------|----------|
| 3.1 Меню «Криптотрейдер» | `chatbot/handlers/trader.py` | Inline-кнопки: Анализ, Симуляция, Результаты, Стратегия, Журнал, Модель |
| 3.2 Валидатор >100x | `chatbot/services/sim_engine.py` | reject_leverage < 100 → ошибка |
| 3.3 Signal Analyst (субагент) | `chatbot/ai/router.py` | _route_signal_analysis(): Analyst + индикаторы → сигнал long/short/neutral |
| 3.4 Performance Evaluator | `collector/predictions/evaluator.py` | evaluate_sim_series(): winrate, avg_pnl, max_drawdown, liquidation_rate |
| 3.5 Strategy Learner | `chatbot/ai/strategy_learner.py` | Premium-модель анализирует серию симуляций → корректирующие правила |
| 3.6 Сохранение правил | `chatbot/storage/postgres_client.py` | save_strategy_rules(rules), get_strategy_rules() |
| 3.7 Scheduler: Evaluation | `collector/main.py` | Раз в N симуляций → запуск evaluator + learner |
| 3.8 Журнал агента | `chatbot/handlers/trader.py` | Команда «Журнал» → последние записи ai_journal |
| 3.9 Регистрация в main.py | `chatbot/main.py` | dp.include_router(trader_router) |
| 3.10 Тесты | `chatbot/tests/test_trader.py` | pytest: валидатор, evaluator, strategy_learner mock |

### ЭТАП 4. Админский контур (2-3 дня)

| Задача | Файл | Описание |
|--------|------|----------|
| 4.1 Model Lab: discover | `chatbot/ai/model_lab.py` | Сканирование /v1/models по всем провайдерам |
| 4.2 Model Lab: probe | `chatbot/ai/model_lab.py` | Тестовый запрос к модели → статус |
| 4.3 Model Lab: rank | `chatbot/ai/model_lab.py` | Ранжирование по latency, availability, cost |
| 4.4 Model Lab: rotate | `chatbot/ai/model_lab.py` | Продвижение fallback-модели в active slot |
| 4.5 Active route диагностика | `chatbot/handlers/admin.py` | /admin/route → активный слот, fallback, ошибки |
| 4.6 Health-check | `chatbot/handlers/admin.py` | /admin/health → Docker, Redis, Postgres, WebUI, Gateway |
| 4.7 Quota dashboard | `chatbot/handlers/admin.py` | /admin/quotas → сводка по всем пользователям |
| 4.8 NVIDIA aliases | `chatbot/ai/models.py` | NVIDIA_QWEN_CODER_API_KEY, NVIDIA_MINIMAX_M27_API_KEY, NVIDIA_BASE_URL |
| 4.9 Self-healing | `chatbot/ai/resilience.py` | Auto-promote успешного fallback в active slot |
| 4.10 Регистрация в main.py | `chatbot/main.py` | dp.include_router(admin_router) — только для admin_id |
| 4.11 Тесты | `chatbot/tests/test_admin.py` | pytest: model_lab mock, health-check, quota |

### ЭТАП 5. WebUI (2-3 дня)

| Задача | Файл | Описание |
|--------|------|----------|
| 5.1 BTC/USDT Futures Lab | `webui/templates/futures_lab.html` | График (TradingView), RSI/MACD, форма симуляции, список позиций |
| 5.2 API: /api/futures/positions | `webui/app.py` | GET → список sim_positions |
| 5.3 API: /api/futures/simulate | `webui/app.py` | POST → запуск симуляции |
| 5.4 Strategy Performance | `webui/templates/strategy.html` | Winrate, avg PnL, drawdown, ликвидации, история правил |
| 5.5 API: /api/strategy/metrics | `webui/app.py` | GET → метрики из evaluator |
| 5.6 API: /api/strategy/rules | `webui/app.py` | GET → текущие правила Strategy Learner |
| 5.7 Model Management | `webui/templates/models.html` | Модель пользователя, лимит, active slot, альтернативы |
| 5.8 API: /api/models/list | `webui/app.py` | GET → список моделей с квотами |
| 5.9 Навигация | `webui/templates/base.html` | Ссылки на новые страницы в навбаре |
| 5.10 Тесты | `webui/tests/test_futures_api.py` | pytest: API endpoints |

### ЭТАП 6. Приёмка (1 день)

| Задача | Описание |
|--------|----------|
| 6.1 E2E: модель → запрос → ответ | Пользователь выбирает модель → получает ответ через неё |
| 6.2 E2E: симуляция | Команда → симуляция → результат в Telegram + WebUI |
| 6.3 E2E: обучение | Серия симуляций → evaluator → learner → обновлённые правила |
| 6.4 E2E: админ | /admin/health → все зелёные, /admin/quotas → корректные данные |
| 6.5 Safety: нереальные ордера | Проверка: симуляция не отправляет ордера на HTX |
| 6.6 Safety: секреты | Проверка: ни в одном логе/UI нет raw ключей |

---

## 3. ПРИОРИТЕТЫ

| Приоритет | Этап | Обоснование |
|-----------|------|-------------|
| P0 | Этап 3 (Криптотрейдер) | Основная функция, sim_engine уже есть |
| P1 | Этап 2 (Модели в TG) | Пользовательский интерфейс выбора |
| P2 | Этап 5 (WebUI) | Визуализация, можно после TG |
| P3 | Этап 4 (Админ) | Управление, можно после базового функционала |
| P4 | Этап 6 (Приёмка) | После всех этапов |
| P5 | Этап 1 (Архитектура) | Параллельно с P0, БД-схемы нужны сразу |

---

## 4. РИСКИ

| Риск | Вероятность | Mitigation |
|------|-------------|------------|
| Ollama Cloud — единственный живой провайдер | Высокая | Model Lab probe → поиск новых |
| Flash-модели дают кривой JSON | Высокая | Regex-first уже работает |
| Quota API отсутствует у Ollama | Высокая | Token accounting на нашей стороне |
| WebUI ломает существующие роуты | Средняя | Не удалять /alerts, /predictions, /journal |
| Секреты в git | Низкая | .gitignore + GitHub push protection |

---

## 5. ЗАВИСИМОСТИ

```
Этап 1 (БД-схемы) → Этап 2 (Models TG) → Этап 4 (Admin)
                  → Этап 3 (Trader)   → Этап 5 (WebUI)
                                        → Этап 6 (Приёмка)
```

Этапы 2 и 3 можно делать параллельно после Этапа 1.

---

## 6. ОЦЕНКА ТРУДОЁМКОСТИ

| Этап | Дней | Файлов | Тестов |
|------|------|--------|--------|
| 1. Архитектура | 1-2 | 3 | 0 |
| 2. Models TG | 2-3 | 5 | 3 |
| 3. Криптотрейдер | 3-4 | 6 | 3 |
| 4. Админ | 2-3 | 4 | 2 |
| 5. WebUI | 2-3 | 6 | 2 |
| 6. Приёмка | 1 | 0 | 6 (e2e) |
| **Итого** | **11-16** | **24** | **16** |