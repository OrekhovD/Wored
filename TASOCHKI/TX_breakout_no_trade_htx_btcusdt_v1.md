# Техническое задание на реализацию breakout no-trade / trade signal
## WORED Daily Session / HTX BTCUSDT

**Дата:** 28.06.2026  
**Статус:** Implementation Spec

---

## 1. Цель

Реализовать правило для daily-session, которое определяет, является ли текущая рыночная ситуация допустимой для входа в сделку или должна быть помечена как no-trade. Правило должно работать на базе цены, объёма и ATR, поддерживать long/short сценарии, и быть пригодным для sim engine и последующего исполнения.

---

## 2. Проблема

Текущее описание условия слишком общее и не позволяет однозначно определить:

- таймфрейм подтверждения;
- сколько свечей должно закрыться над/под уровнем;
- что именно считается повышенным объёмом;
- что именно считается расширением ATR;
- как рассчитываются стоп-лосс, тейк-профит и размер позиции;
- когда применяется no-trade.

Без этих параметров правило не пригодно для автоматизации.

---

## 3. Область применения

Правило используется в:

- `daily-session` планах;
- `plannedentries`;
- `sim_engine`;
- UI preview signal;
- audit log;
- post-session review.

---

## 4. Входные параметры

### 4.1. Обязательные

- `symbol` — например, BTCUSDT.
- `direction` — long / short / both.
- `timeframe` — по умолчанию 5m.
- `breakout_high` — уровень вверх, например 61000.
- `breakout_low` — уровень вниз, например 59800.
- `confirm_closes` — число подтверждающих закрытий, по умолчанию 2.
- `vol_period` — период среднего объёма, по умолчанию 20.
- `vol_factor` — множитель объёма, по умолчанию 1.5.
- `atr_period` — период ATR, по умолчанию 14.
- `atr_factor` — множитель расширения ATR, по умолчанию 1.3.
- `risk_pct` — риск на сделку, по умолчанию 1.0%.
- `sl_atr_mult` — множитель ATR для стоп-лосса, по умолчанию 1.5.
- `tp_rr` — риск/прибыль, по умолчанию 2.0.
- `max_leverage` — максимальное плечо, по умолчанию 20x.

### 4.2. Дополнительные

- `require_retest` — boolean.
- `retest_bars` — число баров на ретест.
- `spread_max_pct` — максимальный спред для допуска.
- `news_block_minutes` — блокировка на новости.
- `session_max_drawdown_pct` — лимит drawdown на сессию.

---

## 5. Логика определения сигнала

### 5.1. Long breakout

Сигнал long разрешён, если выполняются все условия:

1. Цена закрылась выше `breakout_high`.
2. Это произошло `confirm_closes` раз подряд.
3. Текущий объём больше `vol_factor * avg_volume(vol_period)`.
4. Текущий ATR больше `atr_factor * avg_atr(atr_period * 3)`.
5. Спред не превышает `spread_max_pct`.
6. Сессия не нарушает risk limits.
7. Нет активного news block.

### 5.2. Short breakout

Сигнал short разрешён, если:

1. Цена закрылась ниже `breakout_low`.
2. Это произошло `confirm_closes` раз подряд.
3. Текущий объём больше `vol_factor * avg_volume(vol_period)`.
4. Текущий ATR больше `atr_factor * avg_atr(atr_period * 3)`.
5. Спред не превышает `spread_max_pct`.
6. Сессия не нарушает risk limits.
7. Нет активного news block.

### 5.3. No-trade

No-trade устанавливается, если хотя бы одно из условий нарушено:

- нет подтверждения по свечам;
- объём не выше порога;
- ATR не расширен;
- спред слишком большой;
- рынок в news block;
- дневной лимит риска превышен;
- открытые позиции уже нарушают ограничения;
- сигнал конфликтует с активным session plan.

---

## 6. Расчёт входа и риска

### 6.1. Entry

Варианты входа:

- market entry на следующей свече после подтверждения;
- limit entry на ретест уровня, если `require_retest = true`.

### 6.2. Stop loss

Для long:

- `stop_loss = entry_price - (ATR * sl_atr_mult)`

Для short:

- `stop_loss = entry_price + (ATR * sl_atr_mult)`

### 6.3. Take profit

Для long:

- `take_profit = entry_price + (entry_price - stop_loss) * tp_rr`

Для short:

- `take_profit = entry_price - (stop_loss - entry_price) * tp_rr`

### 6.4. Position sizing

- `risk_amount = equity * risk_pct / 100`
- `position_size = risk_amount / abs(entry_price - stop_loss)`
- `notional = position_size * entry_price`
- `leverage = min(max_leverage, floor(notional / margin_available))`

Если расчёт даёт превышение по риску или leverage, сигнал переводится в no-trade.

---

## 7. Требования к данным

### 7.1. Источник данных

- HTX WebSocket для текущих цен и объёмов.
- HTX REST для истории свечей и вычисления ATR.
- Redis как горячий кэш последних snapshot.
- Postgres как источник истории сигналов и результатов.

### 7.2. Минимальные данные

Для работы сигнала должны быть доступны:

- свечи выбранного timeframe;
- объём за `vol_period`;
- ATR за `atr_period`;
- текущий bid/ask спред;
- состояние сессии и risk limits.

---

## 8. Изменения в backend

### 8.1. Новый модуль

Создать модуль, например:

- `collector/signals/breakout_detector.py`
- или `chatbot/services/signal_rules.py`

Функции:

- `detect_breakout_signal(snapshot, config)`;
- `compute_position_size(equity, entry, stop_loss, risk_pct)`;
- `evaluate_no_trade_reasons(...)`.

### 8.2. Интеграция в pipeline

Сигнал должен:

- попадать в `plannedentries`;
- отображаться в UI как `signal status`;
- сохраняться в `executionevents`;
- учитываться в `sim_engine`.

### 8.3. Логирование

Для каждого расчёта сохранять:

- входные параметры;
- reason codes;
- breakout level;
- confirm count;
- volume ratio;
- ATR ratio;
- entry / SL / TP;
- position size;
- final decision.

---

## 9. Изменения в UI

Добавить в daily-session блок `Signal Rules`:

- current symbol;
- direction;
- breakout levels;
- confirm closes;
- volume ratio;
- ATR ratio;
- risk limits;
- decision: trade / no-trade;
- reason if blocked.

Пользователь должен видеть, почему сигнал принят или отклонён.

---

## 10. Acceptance criteria

Функция считается реализованной, если:

1. Сигнал корректно определяется на исторических и live данных.
2. Есть явный trade / no-trade результат.
3. Причина no-trade всегда объясняется.
4. Расчёт entry / SL / TP / size совпадает с конфигом.
5. Сигнал сохраняется в audit trail.
6. UI показывает текущие параметры и итоговое решение.
7. Sim engine может использовать сигнал без ручной доработки.

---

## 11. Риски и ограничения

- На малом таймфрейме будет много ложных пробоев.
- Слишком низкий ATR порог будет давать шум.
- Слишком высокий volume factor может пропускать хорошие входы.
- Без news filter стратегия будет входить в шумные движения.
- Без session risk limits сигнал может конфликтовать с общей логикой сессии.

---

## 12. Рекомендуемые значения по умолчанию

Для первого релиза:

- timeframe = 5m
- confirm_closes = 2
- vol_period = 20
- vol_factor = 1.5
- atr_period = 14
- atr_factor = 1.3
- risk_pct = 1.0
- sl_atr_mult = 1.5
- tp_rr = 2.0
- max_leverage = 20
- require_retest = false

---

## 13. Приоритет реализации

### P0

- детектор breakout;
- no-trade logic;
- расчёт SL/TP;
- position sizing;
- интеграция в audit.

### P1

- UI отображение сигнала;
- ретест-логика;
- news block;
- spread filter.

### P2

- визуальные подсказки;
- расширенные аналитические отчёты;
- оптимизация порогов по истории.

---

## 14. Итог

Это правило нельзя оставлять в виде короткой фразы. Для автоматизации ему нужны формальные параметры, проверка качества сигнала, расчёт риска и ясное решение trade / no-trade. После реализации оно станет пригодным и для sim engine, и для живой сессии.
