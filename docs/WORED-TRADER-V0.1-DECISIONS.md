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