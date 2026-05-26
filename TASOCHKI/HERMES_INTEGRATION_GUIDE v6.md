P6 — WORED Decision Intelligence

Теперь можно расширять уже не инфраструктуру, а пользу для торговли и анализа.

Я бы пошёл так:

P6-01 Signal Explainer
P6-02 Forecast vs Reality Analyzer
P6-03 Alert Outcome Tracker
P6-04 Strategy Backtest Mini-Lab
P6-05 Risk Position Calculator API
P6-06 Daily/Hourly Intelligence Brief
P6-01 Signal Explainer

Цель: Hermes получает symbol/period и объясняет, почему рынок выглядит bullish/bearish/neutral.

Команда:

python scripts/signal_explainer.py --symbol BTCUSDT --period 60min --lookback-days 14 --format markdown

Выход:

BTCUSDT 60min: moderately bullish

Reasons:
- SMA20 above SMA50
- RSI 62, not overbought
- MACD positive
- volume spike confirmed continuation
- volatility expanded after breakout

Risks:
- RSI approaching overbought
- latest candle has upper wick
P6-02 Forecast vs Reality Analyzer

Цель: понять, какие AI-модели реально угадывают.

Показывать:

direction accuracy
avg change_pct error
best horizon
worst horizon
provider latency
fallback impact

Команда:

python scripts/forecast_reality_analyzer.py --symbol BTCUSDT --days 30
P6-03 Alert Outcome Tracker

Цель: не просто открывать alert, а понимать, был ли он полезен.

Например:

volume_spike alert:
samples: 48
avg move after 4h: +0.62%
win rate up: 58%
false positive rate: 31%

Команда:

python scripts/alert_outcome_tracker.py --symbol BTCUSDT --days 60
P6-04 Strategy Backtest Mini-Lab

Цель: простой бэктест без тяжёлого фреймворка.

Примеры стратегий:

RSI oversold rebound
MACD bull cross
SMA20/SMA50 cross
Volume spike continuation
Alert-follow strategy

Команда:

python scripts/backtest_mini_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --strategy volume_spike_continuation \
  --lookback-days 90
P6-05 Risk Position Calculator API

Это уже продуктовая функция для WebUI/Telegram.

Вход:

balance
risk_pct
entry
stop_loss
take_profit

Выход:

position_size
max_loss
risk_reward
invalid_stop warning

Можно сделать endpoint:

POST /api/risk/position

И CLI:

python scripts/risk_position.py --balance 1000 --risk-pct 1 --entry 62000 --stop 61000 --take 65000
P6-06 Daily/Hourly Intelligence Brief

Hermes собирает:

market context
alerts
forecast quality
open risks
AI journal summary
top patterns

И выдаёт brief:

python scripts/intelligence_brief.py --mode hourly --symbols BTCUSDT,ETHUSDT
Рекомендованный порядок P6
1. P6-01 Signal Explainer
2. P6-03 Alert Outcome Tracker
3. P6-02 Forecast vs Reality Analyzer
4. P6-04 Strategy Backtest Mini-Lab
5. P6-05 Risk Position Calculator API
6. P6-06 Intelligence Brief

Почему так:

Signal Explainer использует уже готовый market_context.
Alert Outcome Tracker использует alerts + on-demand history.
Forecast Analyzer использует forecast tables.
Backtest Mini-Lab строится поверх pattern/history.
Risk API можно потом встроить в WebUI/Telegram.
Brief объединит всё в один продуктовый слой.
Готовый следующий промпт для Hermes
Задача: P6-01 Signal Explainer.

Цель:
создать scripts/signal_explainer.py, который использует существующий market_context/fetch_history подход и объясняет состояние рынка по symbol/period.

Ограничения:
- не писать в БД;
- не требовать collector;
- не менять runtime;
- не печатать секреты;
- работать в minimal Python environment;
- output: json и markdown;
- при недостатке данных возвращать insufficient_data.

Файлы:
- scripts/signal_explainer.py
- docs/hermes/playbooks/signal-explainer.md

Перед patch:
PLAN
FILES
RISK
TESTS

После patch проверить:
python scripts/signal_explainer.py --symbol BTCUSDT --period 60min --lookback-days 7 --format json
python scripts/signal_explainer.py --symbol BTCUSDT --period 60min --lookback-days 7 --format markdown