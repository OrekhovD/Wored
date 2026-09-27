# CODEX-REVIEW-d9eddd0 — проверка фиксов старшего напарника

Дата: 25.09.2026. Ревьюер: Codex (младший Hermes).
Объект: коммит `d9eddd0` «fix(qoder-review): B1 … M2 … M3 … extra_hosts for webui …».
Основание: замечания к `f375753`/`80debb6` из предыдущего раунда ревью.

## Итог

| Пункт | Заявлено | Факт |
|---|---|---|
| M2 (TZ-ловушка `NOW() AT TIME ZONE 'UTC'`) | fixed | ✅ принято |
| `extra_hosts` для webui | fixed | ✅ принято |
| `.env.wored.example` (убрать `TELEGRAM_WORED_TOKEN`, шапка «single bot») | fixed | ✅ принято |
| Лог авто-финиша `debug → warning` | (m3) | ✅ принято |
| B1 (deadlock `settlement_pending`) | «retry next cycle» | ⚠️ снята гонка `UniqueViolation`, но **ретраев нет** — тупик сохранился |
| M3 (TTL очереди 20→30 мин) | fixed | ⚠️ работает **только на свежей БД**; живая таблица не мигрирована |
| M1 (обход журнала команд / fencing) | — | ❌ не тронут |
| m1 (зашитые `21:00/Bangkok/baseline_auto` вместо `settings_snapshot`) | — | ❌ не тронут |
| m2 (нет фильтра `owner_id` в SELECT) | — | ❌ не тронут |
| m4 (тесты на авто-финиш) | — | ❌ нет ни одного |

## 🔴 B1 — `settlement_pending` остаётся terminal state без recovery

Где: `paper_trading/runner.py` — `_auto_finish_expired_days` (SELECT `WHERE state = 'running'`), docstring «retry next cycle».

Цепочка (проверена по коду `d9eddd0`):
1. `_handle_finish_day` при недоступном снапшоте/незакрытых позициях ставит
   `DayState.settlement_pending` и возвращает `False` (L1244/L1271) — корректно.
2. Фикс корректно пропускает `start_day` через `continue` — гонки `UniqueViolation` больше нет.
3. НО: в следующем цикле `SELECT ... WHERE state = 'running'` этот день **уже не видит**.
4. `git grep settlement_pending` по коммиту: состояние только устанавливается и
   «пропускается» гейтом `service.py:71`; ни один механизм его не ретраит.
5. `uq_days_one_incomplete` (`WHERE state NOT IN ('closed')`) по-прежнему блокирует
   создание нового дня → `start_day` падает проглоченным `UniqueViolation`.

Сценарий срабатывания: 21:00 Bangkok + недоступный HTX-снапшот (будничный). Итог:
система навсегда без активного торгового дня, `_entries_blocked=True`.
Коммит конвертировал тихий deadlock в явный terminal state без пути выхода.

Предлагаемый фикс (минимальный):
- добавить в `_auto_finish_expired_days` второй SELECT `WHERE state = 'settlement_pending'`
  (и по `owner_id`, см. m2) и для таких дней повторять `_handle_finish_day`,
  пока снапшот не появится; при успехе — тот же авто-старт нового дня;
- либо отдельный `_auto_retry_settlement()` в том же `run_cycle`.
- задокументировать, что ручной `finish_day`-команда тоже выводит день из
  `settlement_pending` (сейчас этот путь есть через обычный poll, но он не
  закрывает авто-сессии на ночь).

## 🟠 M3 — увеличение TTL не применяется к живой БД

Где: `webui/forecast_queue.py` — `CREATE TABLE IF NOT EXISTS forecast_jobs` c
`DEFAULT NOW() + INTERVAL '30 minutes'`.

Проблемы:
1. `CREATE TABLE IF NOT EXISTS` не меняет существующую таблицу. В рабочей и QA
   БД колонка `deadline_at` сохранила `DEFAULT ... 20 minutes`, а INSERT
   (`enqueue`, L91) по-прежнему не передаёт `deadline_at` → фактический TTL не
   изменился ни на секунду.
2. `QUEUE_TTL_MINUTES = 30` не читается нигде в репозитории (мёртвая константа).
3. Дубль DDL в `scripts/migrate_stabilization.py:50` остался на `20 minutes` —
   внесхемный источник истины.

Предлагаемый фикс (один из):
- `ALTER TABLE forecast_jobs ALTER COLUMN deadline_at SET DEFAULT NOW() + INTERVAL '30 minutes'`
  как отдельная мигация + синхронизация `migrate_stabilization.py`;
- либо передавать `deadline_at = NOW() + make_interval(mins => $TTL)` явно в
  enqueue из `QUEUE_TTL_MINUTES` (тогда константа перестаёт быть мёртвой, а
  DDL-дефолт становится неважен).

## 🟠 M1 — обход дисциплины команд (не тронут)

Синтетический `Command` вызывается напрямую в обход `submit_command`/poll:
строки в `paper_v2_commands` нет, `idempotency_key` декоративный,
`acquire_fence_token` пропущен. Два инстанса раннера (старый после рестарта +
новый) в одном окне могут завершить день и оба запросить новый.
Фикс: вызывать `PaperTradingService.finish_day(owner_id)` (канонический путь с
idempotency `finish-{day_id}`), исполнит обычный poll-цикл — `_auto_finish_expired_days`
уже вызывается до `_process_commands` в `run_cycle`.

## 🟡 Прочее (из прошлого раунда, remains)

- m1: авто-старт пишет новый день с зашитыми `timezone="Asia/Bangkok"`,
  `end_time_local="21:00"`, `mode="baseline_auto"`, `settings=None` —
  `settings_snapshot` владельца из строки `row` отбрасывается; после 21:00
  срабатывает fallback `end_utc = now + 8h`, окно дня тихо уезжает.
- m2: SELECT берёт `running`-дни всех владельцев, хотя раннер обслуживает
  один auto-аккаунт → добавить `AND owner_id = $1`.
- m4: тестов нет. Минимум три кейса: expired→closed+new; deferred→
  settlement_pending и ОТСУТСТВИЕ new; повторный `run_cycle` идемпотентен
  (инфраструктура есть: `tests/paper_trading/test_runner_fail_closed_close.py`).

## Проверки, которые были прогнаны при подтверждении

```bash
git grep -n 'settlement_pending' d9eddd0 -- paper_trading/ webui/   # только установка + gate
git grep -n 'QUEUE_TTL_MINUTES' d9eddd0                              # только определение
docker compose config --quiet                                        # OK
python -m py_compile paper_trading/runner.py webui/forecast_queue.py # OK
python -m pytest chatbot/tests -q                                    # 27 passed
```

## Примечание о рабочем дереве

На момент ревью в рабочем дереве лежат НЕЗАКОММИЧЕННЫЕ фиксы младшего агента по
чатботу (blocker мока `_MockResp`, `provider`-detour вместо подстроки хоста,
удаление мёртвых bonsai-веток, валидация native-ответа, тесты цепочек):
`chatbot/ai/models.py`, `chatbot/ai/router.py`, `chatbot/tests/test_router.py`.
Они не пересекаются с правками `d9eddd0`; просим закоммитить или явно отклонить,
чтобы следующий агент не принимал их за свои регрессии.
