Вот это уже похоже на рабочую реализацию. В отличие от прошлого отчёта, теперь есть главное доказательство:

status = ok
data_quality = real_data
data_sources = ["htx_rest"]
schema assertion = SCHEMA_OK
side = short_candidate

То есть trade_plan_generator.py больше не является пустой обёрткой над сломанными источниками. Он стал self-contained, получил реальные данные через HTX REST и построил advisory trade plan.

Но принимать как полностью готовый production-уровень я бы пока не стал. Статус: P7-01.2 принят как рабочий MVP, нужен P7-01.3 hardening.

Что теперь хорошо
1. Главный acceptance test пройден

Было:

status: insufficient_data

Стало:

"status": "ok"

Это закрывает главный blocker.

2. Реальные данные получены
"data_quality": "real_data",
"data_sources": ["htx_rest"]

Это лучше, чем synthetic fallback. Значит скрипт способен работать без collector, asyncpg и БД.

3. Self-contained логика работает

В отчёте указано:

SMA20
SMA50
RSI
volatility
HTX REST
risk calculation
json/markdown/telegram

Это ровно правильное направление: внешние скрипты теперь optional enrichers, а не обязательные зависимости.

4. Schema assertion пройден
SCHEMA_OK ok short_candidate real_data

Это важнее словесного отчёта. Значит обязательные поля есть.

Что ещё не идеально
1. short_candidate при confidence 47 — спорно
"bias": "moderately_bearish",
"side": "short_candidate",
"score": -15,
"confidence": 47

47% — слабая уверенность. Для advisory-системы лучше ввести пороги:

confidence < 45 → no_trade
45–55 → weak_candidate / caution
>55 → candidate
>70 → strong_candidate

Сейчас скрипт даёт short_candidate, хотя сигнал слабый. Это не критическая ошибка, но для UX и риска надо улучшить.

2. Причины немного конфликтуют
"reasons": [
  "SMA20 below SMA50",
  "RSI bearish (47.1)",
  "Positive momentum (0.42%)"
]

Для short-плана positive momentum должен попадать не в reasons, а в warnings или counter_signals.

Правильнее:

"reasons": [
  "SMA20 below SMA50",
  "RSI below neutral midpoint"
],
"counter_signals": [
  "Positive short-term momentum (+0.42%)"
]

Иначе Hermes/бот может объяснять short как будто positive momentum его подтверждает, хотя это скорее ослабляет short.

3. parse_errors для optional enrichers надо уточнить
"parse_errors": [
  "signal_explainer",
  "forecast_reality_analyzer"
]

Если внешние скрипты не вернули JSON или формат не распознан — parse_errors норм. Но желательно хранить не только имя, а объект:

"parse_errors": [
  {
    "source": "signal_explainer",
    "reason": "non_json_output",
    "used": false
  }
]

Иначе потом будет трудно понять, источник сломан, CLI несовместим или просто markdown-output.

4. notional_value больше баланса
"balance": 1000.0,
"notional_value": 1249.99

При risk_pct=1 это математически возможно, потому что stop-distance небольшой. Но если по умолчанию leverage не используется, то notional выше balance должен давать warning или ограничение.

Нужно одно из двух:

Вариант A:
notional_value > balance → warning: "Position notional exceeds balance; leverage or margin required"

Вариант B:
если leverage не задан, cap notional <= balance

Для безопасного advisory-плана лучше вариант A минимум, а лучше добавить CLI-флаг:

--max-notional-pct 100

и по умолчанию не давать плану превышать баланс.

5. Нужен честный тест режима без synthetic

Hermes написал:

E. Проверю режим без synthetic (но с реальными данными)

Но не показал полный output E. Надо запросить у него именно команду и JSON:

python3 scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format json | python3 -m json.tool

Если это тот же real_data/status=ok, отлично.

Вердикт
P7-01.2 Self-contained Trade Plan Generator Rewrite: ✅ принять как рабочий MVP

Но:
P7-01.3 Hardening обязателен перед использованием как основной trade-planning модуль.

Текущий скрипт уже можно оставить в проекте, добавить quick command и playbook. Но в отчёте надо честно написать:

Production safety: частично
Risk constraints: требуют hardening
External enrichers: optional, но parse_errors ещё надо структурировать
Что дать Hermes следующим заданием
P7-01.3 Trade Plan Generator Hardening.

Текущий P7-01.2 принят как рабочий MVP:
- status=ok
- data_quality=real_data
- data_sources=["htx_rest"]
- schema assertion passed

Нужно усилить безопасность и качество плана.

FILES:
- scripts/trade_plan_generator.py
- docs/hermes/playbooks/trade-plan-generator.md

Не трогать:
- chatbot/*
- collector/*
- webui/*
- docker-compose.yml
- .env
- БД

Требования:

1. Добавить counter_signals.
   Если сигнал против выбранной стороны, не писать его в reasons.
   Пример:
   short_candidate + positive momentum -> counter_signals.

2. Добавить confidence gating:
   - confidence < 45 -> side=no_trade
   - 45 <= confidence < 55 -> weak_candidate
   - 55 <= confidence < 70 -> candidate
   - confidence >= 70 -> strong_candidate
   JSON должен иметь:
   signal_strength

3. Добавить notional guard:
   - если notional_value > balance и leverage не задан:
     warning: "Position notional exceeds balance; leverage or margin required"
   - добавить CLI:
     --max-notional-pct 100
   - если notional выше лимита, либо cap position_size, либо side=no_trade с risk.allowed=false

4. Структурировать parse_errors/source_errors:
   Вместо списка строк использовать список объектов:
   {
     "source": "...",
     "reason": "...",
     "used": false
   }

5. Добавить поле:
   data_timestamp
   latest_price
   indicators:
     sma20
     sma50
     rsi14
     volatility_pct
     momentum_pct

6. Добавить final recommendation language:
   Запрещено "sell now", "buy now".
   Использовать:
   - "candidate short setup"
   - "wait for confirmation"
   - "no-trade advisory"
   - "not financial advice"

7. Обновить markdown и telegram output:
   - показывать confidence
   - показывать counter_signals
   - показывать notional warning
   - показывать data_quality и data_source

Тесты:

python3 scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json > /tmp/trade_plan.json

python3 - <<'PY'
import json
p=json.load(open('/tmp/trade_plan.json'))
required = [
  "status","symbol","period","data_quality","data_sources","data_timestamp",
  "latest_price","indicators","bias","side","signal_strength",
  "confidence","score","reasons","counter_signals","warnings",
  "entry_zone","stop_loss","take_profit","risk","invalidations",
  "advisory_notice"
]
missing=[k for k in required if k not in p]
assert not missing, missing
assert p["status"] in ["ok","insufficient_data"]
if p["status"] == "ok":
    assert p["data_quality"] in ["real_data","synthetic_smoke_test"]
    assert p["side"] in ["long_candidate","short_candidate","weak_long_candidate","weak_short_candidate","no_trade"]
    assert isinstance(p["counter_signals"], list)
    assert "not financial advice" in p["advisory_notice"].lower()
    if p["risk"].get("notional_value", 0) > p["risk"].get("balance", 0):
        assert any("notional" in w.lower() or "leverage" in w.lower() for w in p["warnings"]), p["warnings"]
print("HARDENING_SCHEMA_OK", p["status"], p.get("side"), p.get("signal_strength"))
PY

python3 scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format markdown

python3 scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format telegram

REPORT:
- полный JSON
- schema assertion result
- markdown preview
- telegram preview
- указать, был ли notional warning
- указать, какие external enrichers used/failed
Коротко

Текущий результат уже не “херня”. Это рабочий self-contained MVP.

Но перед тем как считать модуль качественным для WORED, надо закрыть:

confidence gating
counter_signals
notional guard
structured source errors
better markdown/telegram output