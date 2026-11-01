# QD-16 — QD-10B2c: подключение funding-writer'а к живому runtime-циклу runner'а

**Mode: PLAN_ONLY → APPLY (после ревю Hermes'а). Применение к прод-BD/рестарты
контейнеров — НЕ входит в гейт: это отдельные live-действия Hermes'а с
runbook'ом и решением владельца.**
Базлайн QA: 1263 passed / 0 failed. Прод после QD-15: финконтурные таблицы
V4 (8 funding-* + fee_schedule_observations + provenance/decision-колонки)
**накатены в `trading`**, schema_version 20261028/20261030 записаны.

## Контекст (проверено Hermes'ом по исходникам)

- Writer `PaperRepository.commit_funding_settlement(...)` — **реализован и
  QA-принят** (QD-10B2a + B2b + R1), но в строке 2953 честно написано:
  «**Nothing calls this.**» — он не подключён ни к чему.
- Runner-цикл (`paper_trading/runner.py::run`, строки 1370-1396): poll каждые
  2 s; SL/TP, funding-обязательства и manual close — «every cycle, never
  blocked». Реально funding в цикле runner'а УЗНАЁТ только из daily-risk
  read-model (`_day_realized_state_on` читает settlements для total_net),
  но **никто не создаёт settlement'ы**.
- В проде: 27 closed позиций с `-61 USDT` PnL и **0 funding-сеттлментов** (все
  `funding_cashflow = 0`, факт проверен read-only).
- Нужен wiring: каждые funding-час HTX (00:00/08:00/16:00 UTC) для каждой
  ОТКРЫТОЙ позиции должен писать settlement через
  `commit_funding_settlement` с реальным rate из
  `paper_v2_funding_rate_observations` (или venue_history — что вернее; смотри
  ниже).

## Что сделать

### Фаза PLAN_ONLY (deliver first, Hermes'а ревю)

Планируем wiring в 3 шага и отдаём diff-план:

1. **Источника rate — честный выбор.** Варианты:
   - (A) **venue_history fetch на границе funding-часа** (HTTP /funding rate
   endpoint HTX, запись в `paper_v2_funding_rate_observations` через
   existing store) → settlement через writer;
   - (B) **ticker-цена в момент T из Redis** (`market:perpetual:htx:BTC-USDT`
   mark) + rate из venue_history, обе — до записи;
   - (с) **modelled** (политика уже отвергнута QD-09 для live-контрактов — не
   применять; в карточке оставлено для полноты, выбрать A или B).
   Что выбираешь — запиши в отчёте ПОЧЕМУ, с критерием fail-closed: если rate
   недоступен в окне ±120 s от funding_time → **REFUSAL** (не writing,
   re-try в следующем цикле, gap_id создаётся).

2. **Время вызова.** В runner-цикле (run/run_cycle) — отдельный watcher:
   каждые funding-интервал (00:00/08:00/16:00 UTC, Asia/Bangkok 07:00/15:00/23:00)
   собирает ОДНУ строку `paper_v2_funding_settlements` на ОТКРЫТУЮ позицию.
   Не дублировать: если settlement за (position_id, funding_time) уже есть —
   **skip** (UNIQUE (position_id, funding_time) уже защищает в DDL).

3. **Интеграция в run_cycle.** Не в SL/TP ветку (она каждые 2 s); отдельный
   `FundingWatch`-шаг после `run_cycle` (или в самом начале — реши сам), с
   коротким локом (один runner экземпляр), чтобы не ронять цикл при сетевых
   ошибках HTX. Ошибки fetch — обернуть и логировать `funding_fetch_error`,
   НЕ останавливать runner.

### Фаза APPLY (после ревю)

Разрешённые файлы:

```
paper_trading/runner.py                  (только FundingWatch wiring)
paper_trading/funding_wiring.py           (NEW - сам watcher, отдельный класс)
paper_trading/repository.py              (минимум: если writer требует обёртки)
collector/htx/funding.py                  (если fetch нужен отсюда; read-only)
tests/paper_trading/test_funding_wiring_pg.py   (NEW)
tests/paper_trading/test_runner_funding_wiring.py (NEW, fast)
artifacts/qoder-handoff/QD-16-DONE.md
```

### Запрещено

- запись в прод-БД (после QA применим Hermes'ом);
- рестарт/пересборка контейнеров (Hermes);
- изменение `fees.py` / `funding_verifier.py` / schema-файлов;
- ослабление существующих assert'ов; включение funding в `total_net` для
  daily-risk НЕ через существующий B2b-гейт (он уже это делает);
- commit/push (Hermes делает после верификации).

## QA

Targeted:
```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build --no-deps checks \
  python -m pytest tests/paper_trading/test_funding_wiring_pg.py tests/paper_trading/test_runner_funding_wiring.py -q -p no:cacheprovider --tb=short
```

Full ladder:
```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --no-deps checks \
  python -m pytest tests/paper_trading -q -p no:cacheprovider --tb=short
```
Цель: 1263+ passed / 0 failed. Батч прод уже применён (QD-15), так что PG-тесты
 funding-схемы должны пройти в QA без дополнительных миграций.

## Отчёт

`artifacts/qoder-handoff/QD-16-{DONE|NEEDS_INPUT|BLOCKED}.md`, ≤12 KiB:
- выбор источника rate (A/B) + почему;
- изменённые файлы + SHA-256 до/после (СНЯТЬ ДО — не повторяй промах QD-13);
- счётчики тестов;
- red-before: если удаляешь/добавляешь поведение — покажи, что старый код
  проходит, новый валит;
- остаточные риски.

## Модель

Qwen3.8-Flash Thinking medium. Если считаешь, что нужен DeepSeek-Flash-1017
Thinking high — вопрос в NEEDS_INPUT, не самодеятельность.