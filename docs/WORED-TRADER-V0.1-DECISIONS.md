# WORED Trader V0.1 — Decision Record

**Дата:** 18 сентября 2026.  
**Назначение:** зафиксировать проектные решения до изменения runtime-кода.

## 1. Active live profile

P0 — активный кандидат. P1 включается только после QA и явной активации владельцем.  
P2/P3 отключены. `PAPER_ALLOW_P3=false` — неизменяемое правило.

## 2. Счета

Два рабочих счёта владельца: `manual` и `auto`.  
Четыре авто-счёта (`auto_p0…auto_p3`) **не создаются**.  
`auto` работает с профилем P0 (или P1 после активации).  
P1–P3 сравниваются offline/replay как shadow results — без счетов и проводок в production.

## 3. Starting balances

Используются существующие сохранённые депозиты.  
Новые балансные числа не изобретаются.

## 4. Runtime MMR

Runtime MMR = validated V5 tier с временем и источником.  
Жёстко закодированный `0.0028` используется только в fixture-тестах.  
Missing/stale/malformed tier → `risk_tier_missing` → no new auto entry.

## 5. adjust_margin и reverse

`adjust_margin` и `reverse` — command-capable и audited.  
Автоматический top-up/reversal отключён по умолчанию.

## 6. Forecast gate

B1 должен превзойти B0 на walk-forward статистике.  
До этого auto = `no_trade:model_below_baseline`.  
M1/M2/ensemble — offline experiments, не блокируют functional release.

## 7. Telegram delivery

Один daily summary + severity alerts.  
Сообщение на каждую сделку отключено по умолчанию.

## 8. Hermes profile

Текущий Hermes profile не меняется.  
Отдельный profile готовится только после готовности in-repo core behavior.

## 9. LLM budget

`PAPER_AI_MAX_REQUESTS_PER_DAY=48` и `PAPER_AI_MAX_TOKENS_PER_DAY=200000` — сохраняются.  
Entry-Gate LLM отключён в v0.1.  
Planner: ≤12/day, ≤1500 input / ≤300 output tokens.  
Critic: ≤12/day, ≤800 / ≤160.  
Coach: ≤4/day, ≤1000 / ≤220.  
Daily/Weekly: deterministic по умолчанию.  
Target: ≤$5/month.

## 10. Источник истины

`paper_v2_*` — единственный источник account/day/order/fill/position/posting.  
`trader_v1_*` — только candles, forecasts, plans, agent runs, reviews с FK на `paper_v2_*`.

## 11. UI

`/trader` — инкрементальная надстройка над Command Deck.  
Существующие routes, charts, `app.js`, `styles.css` сохраняются.  
Мокап — визуальная ссылка, не production UI.

## ADR-05 — Унификация формулы ликвидации в `trading_math` (блок B)

**Контекст.** ADR-01 зафиксировал деление на `(1 − MM)` эталоном, но предвидел,
что часть ожидаемых значений тестов `paper_trading` сместится. Фактически
сместились 3 теста в `tests/paper_trading/test_risk_v5.py` (`L=100`, `L=200`,
`L=8`), потому что `paper_trading/risk.py` больше не держит локальную
аддитивную аппроксимацию.

**Решение.** Единое ядро `trading_math.core.liquidation_price`
(long: `E·(1+f−M/N)/(1−m)`, short: `E·(M/N+1−f)/(1+m)`, `M/N=1/L` по умолчанию).
`risk.py` и `sim_math.py` (ветка `calculation_version != 1`) делегируют в ядро;
локальных копий делительной формы в дереве не остаётся (CI-проверка
`test_exactly_one_liquidation_price_implementation`). Cross-маржа отвергается
явным `ValueError` (D10 / ТЗ §6.2).

**Обоснование.** Делительная форма — алгебраическое решение `equity = maintenance`
и верна при любом плече; аддитивная была точна лишь при `MM → 0`. Одна
реализация убирает расхождение D3 между контурами.

**Последствия.** Ожидаемые значения 3 тестов пересчитаны под делительную форму
(смещения задокументированы здесь, как того требует приёмка блока B). Ветка
`calculation_version == 1` в `sim_math` сохранена как legacy-реконструкция —
эпизоды с разным `math_version` не смешиваются (ТЗ §7.3).