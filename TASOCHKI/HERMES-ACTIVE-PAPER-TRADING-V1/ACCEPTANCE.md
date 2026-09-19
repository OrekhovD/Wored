# Приёмка: проверяемые сценарии и обязательные доказательства

Этот документ нормативный. PASS ставится только после выполнения кейса на указанном уровне. План теста, созданный fixture и описание будущей проверки не являются PASS.

## Формат evidence

Для каждого кейса: case_id, requirement_ids, status PASS/FAIL/BLOCKED, UTC start/end, среда, Git SHA и хэши dirty runtime файлов, команда, ожидаемое/полученное, assertions, artifact paths/hashes, residual limits. Для trade cases добавить owner/account/day/strategy/plan/snapshot/order/fill/position IDs, fees/funding/gross/net/reconciliation. В опубликованных артефактах обезличивать владельцев, не сохранять токены и DSN.

Новые обязательные тесты: 0 skipped. Backend mocks, disposable PostgreSQL, browser fixtures, recorded replay и live помечаются разными evidence_level. Провал обязательного уровня блокирует общую приёмку, но не отменяет уже доказанные более узкие результаты.

## Матрица

| ID | Требования | Проверка | Критерий PASS и артефакт |
|---|---|---|---|
| AC-01 | REQ-01, REQ-02 | Read-only discovery RACHELLO | Связь bot→service→owner→session/day→runner; действующий план, pending/open, последний успешный цикл и записанная причина отказа. Нет доступа → BLOCKED, не догадка. Sanitized diagnosis.json |
| AC-02 | REQ-01, REQ-03, REQ-14 | Чистая сборка release | Все runtime зависимости, включая прежние untracked, включены; сборка/импорт chatbot, chatbot_wored, collector, webui из release inventory успешны. Build logs и hashes |
| AC-03 | REQ-03, REQ-04 | Один доменный контур | Telegram и HTTP создают одинаковую команду сервиса и читают одинаковый ledger; ни один адаптер сам не рассчитывает/коммитит fills. Contract tests и карта вызовов |
| AC-04 | REQ-04, REQ-09 | Изоляция владельцев и счетов | Чужие account/position/command/day IDs дают отказ без утечки; Telegram mapping проверенный. Ручная заявка меняет только manual; вмешательство в auto сохраняет origin auto. Integration assertions |
| AC-05 | REQ-04, REQ-08 | Idempotency и гонки | Два одновременных одинаковых запроса и retry после потерянного ответа дают ровно один финансовый эффект; тот же key с другим payload отклонён. PostgreSQL parallel tests |
| AC-06 | REQ-04, REQ-08 | Crash/recovery и fencing | Сбой до/после commit, два runner, истёкший lease: максимум один fill, нет осиротевшего списания, старый token не пишет. Выходы восстанавливаются. Fault-injection traces |
| AC-07 | REQ-05 | Рыночный контракт | bid>ask, wrong instrument/market, stale mark при свежем bid, stale volume, missing tick/multiplier, gaps/clock skew, нулевая/отрицательная цена блокируют вход с точным reason. Signed funding допускает отрицательное значение. Unit/ingest tests |
| AC-08 | REQ-05, REQ-06 | Исполнение long и short | Закрытая свеча подтверждает, последующая котировка исполняет; long ask→bid, short bid→ask; latency/slippage/fees записаны. Переход цены после preview вызывает revalidation. Golden calculations |
| AC-09 | REQ-05, REQ-06 | Ликвидность, rounding, partial fill | IOC не исполняет больше разрешённого объёма, остаток отменён; Decimal/step/min quantity соблюдены; partial close правильно уменьшает margin и распределяет entry fee. Ledger reconciliation |
| AC-10 | REQ-05, REQ-06 | Защитные выходы и gap | SL, TP, liquidation, gap, обе стороны; SL trigger не гарантирует fill по stop. При потере цены protection_degraded, не fake close. Документирован приоритет конфликтующих событий. Event traces |
| AC-11 | REQ-05, REQ-06 | Funding | Long/short при +/− rate, открытие после funding event, закрытие до event, повтор ingest/restart, поздняя итоговая ставка. Одно начисление и корректный pending_settlement. Golden ledger |
| AC-12 | REQ-06 | Все gates риска | Недостаточная margin, max risk/order, sum open risk, daily drawdown с unrealized, leverage, SL/liquidation buffer, net RR, spread, qty, expired intent — индивидуальные отказы. Reduce-only выходы доступны при заблокированном входе |
| AC-13 | REQ-07 | Baseline стратегия | EMA/ATR seed и расчёт на golden bars; зеркальные long/short triggers; warmup, gaps, TTL, deviation, no-lookahead, dedup signal. Одни параметры в live/replay |
| AC-14 | REQ-07, REQ-08 | Полный recorded replay | Не менее одного long и одного short полного цикла на записанных perpetual данных; fixture содержит котировки для fill после сигнала, mark и необходимые metadata. Никаких принудительных intents в этом кейсе. Если архив не содержит условий — другой явно выбранный период, без изменения правил стратегии. Dataset provenance/hash и trade chain |
| AC-15 | REQ-07, REQ-12 | AI contract / отказ провайдера | Valid entries, no_trade для всех профилей, extra trailing text, empty final, malformed/truncated JSON, timeout, quota, stale response. Нет фиктивных ордеров; budget не сбрасывается restart; fallback не обходит policy. Mock tests отдельно от authorized live model audit |
| AC-16 | REQ-07, REQ-08 | Гонки плана/управления | Ответ AI после pause/day_end/новой версии не включает торговлю; pending старого плана superseded, защиты открытой позиции сохранены. PostgreSQL race tests |
| AC-17 | REQ-08, REQ-09 | Ноль позиций понятен | waiting_regime, waiting_trigger, no_trade, cooldown, risk_blocked, data_stale, quota, plan_expired, engine_error и missing heartbeat дают разные тексты и next action/time. Значения и timestamps относятся к последнему реальному циклу |
| AC-18 | REQ-08 | Cooldown/pause/restart | По deadline cooldown прекращается, но pause/day_end имеют приоритет; просроченный сигнал не исполняется. Защита открытой позиции продолжает работать при AI down, pause и plan expiry |
| AC-19 | REQ-09 | Браузер desktop/mobile | «Сегодня» → manual preview/open/partial close/close → auto status/plan/pause/resume → «Итоги». Desktop 1440×900 и mobile 390×844; screenshots+assertions, без overflow, preserved charts/routes. Fixture browser и live browser отдельно |
| AC-20 | REQ-09 | Telegram / Mini App | Mock command/callback suite; затем согласованная проверка в реальном Telegram: читаемый status/plan, повтор callback, Back/navigation/safe area, command pending→result. Эмулятор viewport не заменяет настоящий Telegram client |
| AC-21 | REQ-10 | Финансовое завершение | Оба счёта с позициями/pending orders: повтор finish, deadline одновременно с fill, потеря цены, ошибка AI. Один closeout, без новых entries, closed только после settlement/reconcile; next day сохраняет balances |
| AC-22 | REQ-06, REQ-10 | Сводка и равенства | Ручной/auto отчёт полностью сверяется с fills/postings; liquidation не удваивает trade count; zero trades win rate отсутствует; slippage не списан дважды. Golden reports+reconciliation |
| AC-23 | REQ-07, REQ-10 | Обучение | Недостаточно данных→insufficient_data; failing replay/holdout→rejected; passing gates→активация только следующего дня; rollback; неизменность текущих позиций. Минимум один положительный и отрицательный pipeline fixture, synthetic отдельно от реального evaluation |
| AC-24 | REQ-11, REQ-04 | Миграция и rollback rehearsal | На disposable legacy+paper fixture dry-run counts, orphan detection, unknown attribution, cutover races, повтор миграции, восстановление backup; одна owner_engine_version на позицию и ledger неизменён. Migration report |
| AC-25 | REQ-02, REQ-12, REQ-13 | Безопасность/изоляция QA | Нет production volumes/env, реальных ордеров или внешней рассылки в тестах; invalid config отклонён; secrets отсутствуют в logs/evidence; scoped lint/type/tests pass. QA config report |
| AC-26 | REQ-08, REQ-12, REQ-13 | Live observation 60 минут | Непрерывные timestamps heartbeat/feed/decision/recovery, минимум один переход состояния, health multi-endpoint checks. Нет valid signal→ожидание с численными условиями, не принудительная сделка. Это PASS runtime observation, а не PASS live trade |
| AC-27 | REQ-03, REQ-05, REQ-06, REQ-07, REQ-09, REQ-13 | Естественный live_paper цикл | После согласованного запуска auto сам порождает допустимый сигнал и fill, защита/обычное завершение закрывает позицию, net P&L сверяется; одна и та же позиция видна Telegram/WebUI. Без модификации feed/параметров ради входа. Нет сигнала за окно наблюдения→BLOCKED awaiting_natural_signal |
| AC-28 | REQ-12, REQ-14 | Эксплуатационная поставка | Runbook содержит реальные ports/services/env/mounts, install/build, QA/replay/live, migration/backup/restore/rollback, диагностику zero positions; все команды проверены и все известные блокеры перечислены |

## Обязательные финансовые эталоны

Добавить тесты независимо от формулы implementation. Простой нормализованный контракт: qty 1 BTC, entry/exit quotes заранее определены, slippage 0, fee rate 0.0006, funding 0:

- Long entry 10000, exit 10100: gross 100; entry fee 6; exit fee 6.06; net 87.94 USDT.
- Short entry 10000, exit 9900: gross 100; entry fee 6; exit fee 5.94; net 88.06 USDT.
- Long qty 1, entry 10000; закрытие 0.4 по 10100: gross 40, allocated entry fee 2.4, exit fee 2.424, net закрытой части 35.176; qty остатка 0.6, unallocated entry fee 3.6. Это trade attribution, а не повторное списание entry fee при выходе.
- Funding event при mark 10000, qty 1, rate +0.0001: long cashflow −1, short +1; при −0.0001 знаки обратные. Повтор event не меняет cash.

Эти числа — synthetic fixtures для арифметики, не текущие цены и не параметры действующего HTX-контракта. Добавить отдельные contract multiplier/rounding examples по проверенной metadata.

## Приоритет событий и временная модель

Hermes до реализации фиксирует event ordering: ingestion sequence/source time, command ordering, funding effective time, liquidation/SL/TP trigger, closeout, entry. Одинаковые timestamps разрешаются детерминированным sequence; liquidation имеет приоритет над обычным выходом, когда threshold уже нарушен до его исполнения. Использовать реальные quote/mark events в replay. Если bar-only fixture одновременно пересекает SL и TP и не содержит внутрисвечного пути — результат ambiguous, либо заранее заявленная консервативная модель; нельзя выбирать прибыльный исход задним числом. Bar-only fixture не заменяет AC-14.

## Условия выпуска

1. **Готов к внедрению:** AC-02…AC-19, AC-21…AC-25 и подготовительная часть AC-28 пройдены; discovery AC-01 выполнен или его недоступность явно блокирует выбор cutover. Настоящий Telegram/production/live требуют разрешённой среды, их нельзя отметить заранее.
2. **Runtime подтверждён:** после разрешённого внедрения выполнены AC-01, AC-20, AC-26 и эксплуатационная часть AC-28. Не означает подтверждённую естественную торговлю.
3. **Автоторговля полностью принята:** все AC-01…AC-28 PASS, включая AC-27. В отчёте указывается граница реалистичности расчётной модели и какие параметры HTX проверены.

За 60 минут может не быть сигнала. Это не повод отключать риск и не доказательство неисправности стратегии. Сохранить pending case AC-27 с последней причиной ожидания. Дальнейшее длительное наблюдение согласуется отдельно; не обещать автономный мониторинг после завершения задачи без фактически настроенного механизма.

Недоступный AI при baseline_auto допускает PASS торговли и deferred learning review, если тесты AI/error/learning pipeline выполнены. В ai_plan отсутствие действующего плана блокирует новые входы. Отсутствие live AI допускается как явно обозначенный residual limit; никакой fixture не называется live model success.
