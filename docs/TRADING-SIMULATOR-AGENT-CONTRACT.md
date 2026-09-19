# WORED Trading Simulator — контракт агентов и самообучения

Дата: 2026-09-10. Статус: контракт и DDL подготовлены; сетевые вызовы агентов и scheduler ещё не подключены.

## Цель

Агенты помогают симулятору анализировать реальные рыночные данные, формировать прогнозы, предлагать сделки и разбирать ошибки. Ни один ответ модели не изменяет баланс, не создаёт fill и не активирует новую стратегию напрямую.

## Роли

| Роль | Основной слот | Ответственность | Полномочия |
|---|---|---|---|
| signal_worker | `deepseek-v4-flash` | нормализация признаков и дешёвая классификация | только признаки |
| bull_analyst | `deepseek-v4-pro` → `glm-5.2` | доказательства Long или skip | предложение |
| bear_analyst | `deepseek-v4-pro` → `glm-5.2` | доказательства Short или skip | предложение |
| risk_arbiter | `minimax-m3` → `glm-5.2` | trade/skip, проверка противоречий | advisory approval |
| daily_learner | `glm-5.2` → `deepseek-v4-pro` | кандидаты правил по закрытым эпизодам | только candidate |
| promotion_auditor | детерминированный код | replay, out-of-sample, drawdown, fees | перевод между статусами; не генерирует правила |

Qwen/DashScope не включается этим контрактом: текущий активный registry содержит Ollama Cloud GLM/DeepSeek/MiniMax. Legacy-цепочки в `chatbot/ai/models.py` требуют отдельной миграции через единый Provider Gateway.

## Один цикл решения

1. Collector публикует свежий HTX linear-swap snapshot.
2. Forecast pipeline сохраняет прогноз и фактическую будущую оценку.
3. signal_worker получает immutable `snapshot_id` и версию признаков.
4. bull_analyst и bear_analyst независимо возвращают JSON-предложения.
5. risk_arbiter возвращает Long, Short или Skip.
6. `parse_trade_proposal` отклоняет неверную схему, чужой snapshot, другую версию стратегии, невозможные stop/target и недопустимый risk.
7. `select_approved_proposal` требует одного arbiter, поддержку соответствующего directional agent, минимальную confidence и серверный лимит риска.
8. Детерминированный execution engine повторно рассчитывает quantity, margin, fees и допустимость по текущему bid/ask. Именно он создаёт order/fill/ledger.
9. Agent input, output, provider/model, usage и ошибка записываются в `paper_agent_runs`.

## Контур обучения

Каждый закрытый прогноз и каждая закрытая позиция образуют episode с версиями market snapshot, feature schema, модели, стратегии и правил расчёта. Вечерний review объединяет только факты, доступные на момент решения, с последующим исходом.

daily_learner создаёт новую версию только со статусом `candidate`. Дальнейшие переходы:

`candidate → validating → approved → active`

Возможны `rejected` и `rolled_back`. В PostgreSQL разрешена только одна active-версия на владельца.

Минимальные gates:

- не менее 20 сопоставимых закрытых эпизодов;
- replay на неизменённых market snapshots;
- отдельная out-of-sample выборка;
- положительное улучшение net PnL после fees и funding;
- max drawdown не хуже baseline;
- liquidation/risk violations не хуже baseline;
- детерминированная сверка ledger;
- сохранённые причины принятия или отклонения.

Пока любой gate не пройден, `applied_rules` остаётся пустым. Недоступность AI не блокирует бухгалтерию и вечерний отчёт.

## Хранилище

- `paper_agent_runs` — полный audit одного вызова агента.
- `paper_strategy_versions` — неизменяемые версии правил и validation metrics.
- `paper_learning_reviews` — ежедневные findings, размер выборки и ссылки на model runs.
- `paper_trading_ledger` — единственный источник денежных событий текущего прототипа; нормализация fills/cashflows остаётся TD-03.

## Известный разрыв активного runtime

`chatbot/ai/provider_gateway.py` объявлен единой точкой inference, но `chatbot/ai/strategy_learner.py` сейчас вызывает SDK напрямую и сохраняет следующую версию правил без replay/out-of-sample gate. Этот путь нельзя подключать к paper auto account. Исправление требует отдельного подтверждения, поскольку меняет `chatbot/`.

Также `LLM_REGISTRY_PATH=/config/provider_registry.json` указан в конфигурации, но root `docker-compose.yml` не монтирует `./config:/config:ro`. Перед подключением agent runner нужно исправить mount и проверить registry внутри контейнера; изменение Compose требует явного подтверждения.

## Проверка текущего контракта

```powershell
Set-Location D:\WORED
python -m pytest tests/test_paper_agents.py tests/test_paper_learning.py -q -p no:cacheprovider
python -m py_compile webui/paper_agents.py webui/paper_learning.py
python -m ruff check --no-cache --select E9,F821,F822,F823 webui/paper_agents.py tests/test_paper_agents.py
```

Pass означает: агент не может утвердить риск вне роли arbiter, proposal привязан к одному snapshot и strategy version, несогласованная сделка превращается в Skip, превышение риска блокируется, AI не может активировать правила, а DDL содержит audit/version/review таблицы.
