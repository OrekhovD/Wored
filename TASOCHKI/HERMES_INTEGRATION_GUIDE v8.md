P7-01: Trade Plan Generator

Это главный следующий модуль.

Сейчас у тебя есть:

market_context
pattern_lab
signal_explainer
risk_position
forecast analyzer
alert outcome tracker

Нужно объединить это в один понятный торговый план.

Команда
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --balance 1000 \
  --risk-pct 1 \
  --format markdown
Выход
# Trade Plan: BTCUSDT 60min

Bias: Moderately Bullish
Confidence: 64/100

## Setup
Entry zone: 62100–62400
Stop loss: 61250
Take profit 1: 63600
Take profit 2: 64800

## Risk
Balance: 1000 USDT
Risk: 1%
Max loss: 10 USDT
Position size: 0.0117 BTC
Risk/reward: 1:2.1

## Reasons
- SMA20 > SMA50
- MACD positive
- RSI 61, not overbought
- Volume spike had continuation in 58% of similar cases

## Invalidations
- Close below SMA50
- RSI drops below 45
- BTC loses previous local low

## Warnings
- Volatility expanded
- Do not enter if spread/liquidity abnormal
Форматы
json
markdown
telegram

telegram нужен для компактного сообщения в бота.

P7-02: Decision Journal

Сейчас есть ai_journal, но нужен отдельный журнал торговых решений, даже если сделки не исполнялись.

Таблица / файл

Лучше начать без миграции БД, через JSONL:

data/decision_journal.jsonl

Потом можно перенести в Postgres.

Запись
{
  "id": "decision_20260501_120000_BTCUSDT",
  "created_at": "2026-05-01T12:00:00Z",
  "symbol": "BTCUSDT",
  "period": "60min",
  "bias": "bullish",
  "confidence": 64,
  "entry_zone": [62100, 62400],
  "stop_loss": 61250,
  "take_profit": [63600, 64800],
  "risk_pct": 1,
  "position_size": 0.0117,
  "source": {
    "signal_explainer": true,
    "pattern_lab": true,
    "forecast_analyzer": true,
    "risk_calculator": true
  },
  "status": "planned"
}
Статусы
planned
approved
rejected
expired
hit_tp
hit_sl
manual_closed
invalidated
P7-03: Trade Plan Outcome Evaluator

После генерации плана WORED должен уметь проверить: идея сработала или нет.

Команда
python scripts/trade_plan_evaluator.py \
  --journal data/decision_journal.jsonl \
  --lookback-days 14
Проверяет
дошла ли цена до entry zone
дошла ли до TP
дошла ли до SL
сколько времени заняло движение
была ли идея invalidated
какой был max favorable excursion
какой был max adverse excursion
Выход
{
  "evaluated": 12,
  "hit_tp": 5,
  "hit_sl": 3,
  "expired": 4,
  "win_rate": 0.625,
  "avg_r_multiple": 0.74,
  "best_symbol": "BTCUSDT",
  "worst_pattern": "rsi_overbought_reversal"
}
P7-04: Strategy Scoreboard

Нужен единый рейтинг стратегий и сигналов.

Что оценивать
signal_explainer bias
volume_spike_continuation
rsi_oversold_rebound
macd_bull_cross
sma_cross
forecast_consensus
alert_follow
Метрики
sample_count
win_rate
avg_r
median_r
max_drawdown
false_positive_rate
best_time_of_day
best_period
best_symbol
Команда
python scripts/strategy_scoreboard.py --days 90 --format markdown
Выход
# Strategy Scoreboard

| Strategy | Samples | Win Rate | Avg R | Best Period |
|---|---:|---:|---:|---|
| volume_spike_continuation | 42 | 61% | 0.72 | 60min |
| macd_bull_cross | 31 | 54% | 0.31 | 4hour |
| rsi_oversold_rebound | 18 | 67% | 0.88 | 15min |
P7-05: Approval Workflow для Telegram/WebUI

До автоторговли нужен режим “план → подтверждение”.

Telegram flow
WORED:
BTCUSDT 60m trade plan ready.

Bias: bullish
Entry: 62100–62400
SL: 61250
TP: 63600 / 64800
Risk: 1%, size: 0.0117 BTC

[Approve] [Reject] [Edit Risk] [Open WebUI]
WebUI flow

В Dashboard или отдельной странице:

Trade Plans
- planned
- approved
- rejected
- expired
- evaluated
Runtime path
chatbot/handlers/*
webui/templates/*
webui/app.py
scripts/trade_plan_generator.py

Но начинать лучше только со scripts + WebUI read-only.

P7-06: Scenario Simulator

Перед входом в сделку WORED должен уметь показать сценарии.

Команда
python scripts/scenario_simulator.py \
  --symbol BTCUSDT \
  --entry 62300 \
  --stop 61250 \
  --take 64800 \
  --period 60min
Выход
Scenario A: bullish continuation
Probability estimate: 42%
Expected move: +2.1%

Scenario B: range chop
Probability estimate: 35%
Risk: entry noise, no follow-through

Scenario C: breakdown
Probability estimate: 23%
Invalidation: close below 61250

Это не “точные вероятности”, а scoring на основе исторических похожих паттернов.

P7-07: WORED Commander Mode для Hermes

Hermes должен иметь командный режим по одной фразе:

Собери план по BTCUSDT на 60min

Hermes должен выполнить:

1. market_context
2. signal_explainer
3. pattern_lab
4. forecast_reality_analyzer
5. risk_position
6. trade_plan_generator
7. сохранить decision_journal
8. отдать markdown summary

Для этого нужен playbook:

docs/hermes/playbooks/trade-plan-workflow.md
Приоритет P7

Я бы шёл так:

P7-01 Trade Plan Generator
P7-02 Decision Journal
P7-03 Trade Plan Outcome Evaluator
P7-04 Strategy Scoreboard
P7-07 Hermes Commander Mode
P7-05 Approval Workflow
P7-06 Scenario Simulator

Почему:

Сначала генерируем планы.
Потом сохраняем планы.
Потом оцениваем результат.
Потом строим рейтинг стратегий.
Потом автоматизируем workflow Hermes.
Потом добавляем Telegram/WebUI approval.
Потом сценарный симулятор.
ТЗ для первого шага: P7-01 Trade Plan Generator
Цель

Создать модуль, который собирает готовый торговый план на основе уже существующих аналитических скриптов.

Файлы
scripts/trade_plan_generator.py
docs/hermes/playbooks/trade-plan-generator.md
Входные параметры
--symbol BTCUSDT
--period 60min
--lookback-days 14
--balance 1000
--risk-pct 1
--format json|markdown|telegram
Источники данных

Использовать, если доступны:

scripts/market_context.py
scripts/signal_explainer.py
scripts/pattern_lab.py
scripts/risk_position.py
scripts/forecast_reality_analyzer.py

Если какой-то модуль недоступен, не падать. Вернуть:

"missing_sources": ["forecast_reality_analyzer"]
Логика bias

Пример scoring:

SMA20 > SMA50                  +15
MACD bullish                   +15
RSI 45–65                      +10
RSI > 72                       -10
volume spike continuation      +10
forecast consensus bullish     +10
recent alerts bearish          -10
high volatility                -5

Результат:

score >= 65 bullish
score 45–64 moderately_bullish
score 35–44 neutral
score 20–34 moderately_bearish
score < 20 bearish
Entry/SL/TP

На первом этапе простая логика:

long setup:
entry = latest close или зона ±0.2%
stop = recent swing low или ATR-based stop
tp1 = entry + 1.5R
tp2 = entry + 2.5R

short setup:
entry = latest close или зона ±0.2%
stop = recent swing high или ATR-based stop
tp1 = entry - 1.5R
tp2 = entry - 2.5R

Если данных недостаточно:

{
  "status": "insufficient_data",
  "reason": "not enough candles to calculate stop/take-profit"
}
Output JSON
{
  "status": "ok",
  "symbol": "BTCUSDT",
  "period": "60min",
  "bias": "moderately_bullish",
  "score": 64,
  "confidence": 61,
  "side": "long",
  "entry_zone": [62100, 62400],
  "stop_loss": 61250,
  "take_profit": [63600, 64800],
  "risk": {
    "balance": 1000,
    "risk_pct": 1,
    "max_loss": 10,
    "position_size": 0.0117,
    "risk_reward_tp1": 1.5,
    "risk_reward_tp2": 2.5
  },
  "reasons": [
    "SMA20 above SMA50",
    "MACD bullish",
    "RSI neutral-bullish",
    "Volume spike continuation historically positive"
  ],
  "warnings": [
    "Volatility expanded"
  ],
  "invalidations": [
    "Close below SMA50",
    "Break below recent swing low"
  ],
  "missing_sources": []
}
Tests
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format json | python3 -m json.tool
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format markdown
python scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format telegram
Acceptance criteria
1. Не пишет в БД.
2. Не требует collector.
3. Не меняет runtime.
4. Работает в minimal Python environment.
5. Возвращает JSON/Markdown/Telegram.
6. При нехватке данных возвращает insufficient_data.
7. Не печатает секреты.
8. Не даёт команду “покупай/продавай”, а формирует advisory trade plan.
Готовый промпт для Hermes
Задача: P7-01 Trade Plan Generator.

Цель:
создать advisory trade plan generator для WORED.

Файлы:
- scripts/trade_plan_generator.py
- docs/hermes/playbooks/trade-plan-generator.md

Ограничения:
- не писать в БД;
- не требовать collector;
- не менять runtime;
- не менять chatbot/webui;
- не печатать секреты;
- работать в minimal Python environment;
- использовать существующие scripts/market_context.py, signal_explainer.py, pattern_lab.py, risk_position.py, если они доступны;
- если источник недоступен, не падать, а добавить в missing_sources;
- output: json, markdown, telegram;
- advisory only, без автоторговли.

Сначала дай:
PLAN
FILES
RISK
TESTS

После patch выполни:
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json | python3 -m json.tool
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format markdown
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format telegram

REPORT:
- какие источники использованы;
- какие sources missing;
- статус тестов;
- подтверждение, что runtime не менялся.
Следующий слой после P7

Когда P7 будет закрыт, следующий большой блок — P8: Execution Safety Layer.

Но туда идти рано. Сначала нужно, чтобы WORED научился стабильно генерировать, сохранять и оценивать advisory trade plans. Только после этого можно обсуждать любые интеграции с реальными ордерами.