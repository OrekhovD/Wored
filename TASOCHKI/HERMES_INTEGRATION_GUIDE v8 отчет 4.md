Отчёт стал намного лучше, но финально принимать P7-01.3 пока нельзя. Есть три несоответствия, которые надо добить перед закрытием.

Вердикт
P7-01.3: частично принято
Статус: рабочий hardening-кандидат
Финальный статус: HOLD до проверки raw JSON и notional guard

Главное улучшение есть:

✅ status=ok
✅ real_data через htx_rest
✅ weak_short_candidate вместо агрессивного short_candidate
✅ counter_signals вынесены отдельно
✅ notional warning появился
✅ schema assertion якобы пройден

Но в отчёте есть проблемы.

Проблема 1. Вставленный JSON невалидный

В блоке missing_sources у тебя явная поломка структуры:

{
  "source": "market_context",
  "reason": "missing",
  "used": false
  "reason": "missing",
  "used": false
},

Там нет запятой, дублируются поля, повторяется market_context, ломается pattern_lab, лишние скобки. Такой JSON не может пройти:

python3 -m json.tool

Значит одно из двух:

1. Hermes вставил в отчёт повреждённый/склеенный JSON;
2. schema assertion проверял другой файл, а не тот JSON, который показан.

Это надо проверить напрямую.

Дай Hermes команду:

Покажи raw JSON без пересказа. Выполни:

cd /mnt/d/WORED

python3 scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format json > /tmp/trade_plan_p7_hardening.json

python3 -m json.tool /tmp/trade_plan_p7_hardening.json

python3 - <<'PY'
import json
p=json.load(open('/tmp/trade_plan_p7_hardening.json'))
print("JSON_VALID")
print("status=", p.get("status"))
print("side=", p.get("side"))
print("signal_strength=", p.get("signal_strength"))
print("missing_sources=", p.get("missing_sources"))
print("warnings=", p.get("warnings"))
print("risk=", p.get("risk"))
PY

Пока этого нет — JSON-часть не засчитана.

Проблема 2. risk.allowed=true при notional выше balance

Сейчас:

"balance": 1000.0,
"notional_value": 1249.99,
"allowed": true

и только warning:

Position notional exceeds balance; leverage or margin required

Это допустимо только если скрипт явно разрешает margin/leverage. Но по безопасной логике WORED по умолчанию лучше так:

если notional_value > balance и --allow-margin не задан:
  risk.allowed = false
  side = no_trade или position_size capped

Сейчас получается: “предупредил, но всё равно разрешил позицию больше депозита”. Для risk-модуля это слабое место.

Нужно требование:

Добавить --allow-margin.
По умолчанию margin/leverage запрещены.
Если notional_value > balance и --allow-margin не задан:
  risk.allowed=false
  risk.reason="notional exceeds balance; margin disabled"
  warning сохраняется.
Если --allow-margin задан:
  allowed=true, но warning остаётся.

Мини-тест:

python3 scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --max-notional-pct 100 \
  --format json > /tmp/tp_no_margin.json

python3 - <<'PY'
import json
p=json.load(open('/tmp/tp_no_margin.json'))
r=p["risk"]
if r.get("notional_value", 0) > r.get("balance", 0):
    assert r["allowed"] is False, r
print("NO_MARGIN_GUARD_OK")
PY
Проблема 3. missing_sources выглядит неправдиво

Hermes пишет:

External enrichers: Все помечены как missing_sources

Но ранее в проекте уже были:

scripts/signal_explainer.py
scripts/forecast_reality_analyzer.py
scripts/alert_outcome_tracker.py
scripts/backtest_mini_lab.py
scripts/risk_position.py

Если файл существует, он не должен быть missing. Тогда статус должен быть:

source_error
parse_error
unsupported_cli
disabled_optional
insufficient_source_data

А missing — только если файла реально нет.

Нужно проверить:

cd /mnt/d/WORED

ls -la scripts | grep -E "signal_explainer|forecast_reality|pattern_lab|market_context|risk_position|trade_plan"

Если signal_explainer.py существует, то запись:

{"source": "signal_explainer", "reason": "missing"}

неверна.

Что принять сейчас

Можно принять как факт:

✅ self-contained HTX REST path работает
✅ внутренние индикаторы работают
✅ trade plan строится
✅ weak gating и counter_signals добавлены
✅ markdown/telegram форматы есть

Нельзя пока принять:

❌ JSON в отчёте валиден — не доказано
❌ notional guard безопасен — нет
❌ source classification корректна — нет
Короткая команда Hermes

Дай ему вот это:

P7-01.3 не закрыт. Есть 3 blocker:

1. Показанный JSON невалидный в missing_sources. Покажи raw output через python3 -m json.tool из файла /tmp/trade_plan_p7_hardening.json.
2. risk.allowed=true при notional_value > balance. Добавь --allow-margin. По умолчанию, если notional > balance и margin не разрешён, risk.allowed=false или position capped.
3. Если source script существует, не помечай его missing. Используй source_error/parse_error/unsupported_cli/disabled_optional.

FILES:
- scripts/trade_plan_generator.py
- docs/hermes/playbooks/trade-plan-generator.md

Не трогать runtime.

После patch выполни:
- json.tool validation
- NO_MARGIN_GUARD_OK assertion
- проверку ls scripts для source classification
- markdown и telegram smoke.