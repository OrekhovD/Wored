# free_routing_archive — консервация роутинга бесплатных моделей

## Статус: FROZEN (не является частью runtime WORED)

Сюда вынесена **вся система роутинга на бесплатных моделях**, кроме Ollama и
Hermes: OmniRoute, NVIDIA NIM / Nemotron, TokenRouter (Kimi K3 Free),
DashScope/Qwen и minimax-oracle, вместе со «шлюзовой» подсистемой учёта
бюджета/квот/шлюза провайдеров и self-learning-кольца.

В активном WORED остались только:
- `chatbot/ai/models.py` — цепочки **Ollama Cloud Pro** + **локальный Bonsai**;
- `chatbot/ai/router.py`, `dispatcher.py`, `resilience.py`, `prompts.py`,
  `context_builder.py`, `knowledge_base.py`, `model_lab.py`, `quota.py` — лёгкий
  активный путь чата (Ollama + Bonsai);
- `hermes/` — внешний оркестратор (не затрагивался).

## Состав архива

### `routing_ai/` — код выключенного стека
Перенесён из `chatbot/ai/` без изменения содержимого (импорты оставлены в виде
`from ai.*`, чтобы разморозка была чистым `git mv` обратно):
- `provider_gateway.py` — «SOLE inference entry point» (circuit breaker, retry,
  бюджет/гейт, usage ledger);
- `provider_adapters.py` — адаптеры провайдеров (в т.ч. OpenAI-compatible);
- `contracts.py` — нормализованные запрос/ответ, RoutingMode, CostClass;
- `usage_ledger.py` — атомарное бронирование/урегулирование расхода;
- `budget_policy.py`, `token_accounting.py` — бюджетная политика и токен-учёт;
- `strategy_learner.py`, `reflector.py` — self-learning кольцо (block F).

### `config/provider_registry.json`
Реестр бесплатных провайдеров (`ollama-cloud`, `tokenrouter`, `nvidia`),
обслуживавший `provider_gateway.load_registry()`.

### `tests/`
Тесты выключенного стека (исключены из сбора WORED через `norecursedirs` в
`pytest.ini`):
- `test_reflector.py`, `test_strategy_learner_gateway.py`,
  `test_gateway_contracts.py`, `test_selflearn_loop.py`.

## Что вырезано из активного кода (не перенесено, а удалено)
- `chatbot/ai/resilience.py` — мёртвые free-конфиги `omniroute_*` / NIM /
  nemotron; добавлены корректные `analyst_bonsai` / `premium_bonsai` (иначе
  thinking-ответы обрезались дефолтным 60с).
- `chatbot/ai/router.py` / `dispatcher.py` — ветки `dashscope` и NVIDIA/minimax
  `nvapi-` гейт.
- `chatbot/ai/models.py` — legacy `minimax`-оракл (`FALLBACK_ORDER`,
  `MINIMAX_MODEL_CHAIN`).
- `webui/prediction_engine.py` — oracle-модель `minimax` (NVIDIA NIM),
  `dashscope`/`minimax` в кулдауне, related-ветки.

## Процедура разморозки
1. Вернуть код:
   `git mv free_routing_archive/routing_ai/*.py chatbot/ai/`
2. Вернуть реестр:
   `git mv free_routing_archive/config/provider_registry.json config/`
3. Вернуть тесты:
   `git mv free_routing_archive/tests/test_reflector.py free_routing_archive/tests/test_strategy_learner_gateway.py tests/`
   `git mv free_routing_archive/tests/test_gateway_contracts.py tests/stabilization/`
   `git mv free_routing_archive/tests/test_selflearn_loop.py tests/paper_trading/`
4. Убрать `norecursedirs`/`addopts` из `pytest.ini`.
5. Восстановить класс `TestProviderRegistry` в
   `tests/stabilization/test_documentation.py`.
6. Архивный стек зависит от оставшихся слоёв: `ai.models` / `ai.router`
   (Ollama-путь) и `storage.postgres_client` — они доступны при `PYTHONPATH`,
   содержащей `chatbot` (см. `conftest.py`).

> Примечание: `model_lab.py` и `quota.py` НЕ архивированы — они остаются в
> `chatbot/ai/`, потому что используются активным `chatbot/handlers/admin.py`
> (`get_active_route`, `probe_all`, `rotate_active_slot`, `get_quota_status`) и
> в текущем виде работают только с Ollama.
