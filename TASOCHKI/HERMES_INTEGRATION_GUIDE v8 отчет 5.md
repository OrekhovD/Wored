Теперь отчёт выглядит правильно по смыслу, но я бы всё равно не закрывал P7-01.3 только по пересказу. Ключевая проблема: Hermes снова пишет словами “показывает корректный JSON”, но не вставил сам raw JSON и assertion output.

То есть статус такой:

P7-01.3: условно принят
Технически: похоже, blocker устранены
Финально: закрывать только после raw evidence
Что уже можно считать хорошим

Если отчёт правдивый, то три главных blocker действительно закрыты:

✅ missing_sources больше не содержит существующие файлы
✅ source classification стал честнее:
   - unsupported_cli
   - parse_error
   - missing_sources пустой
✅ notional guard теперь безопасный:
   - без --allow-margin
   - notional_value > balance
   - risk.allowed=false
   - side=no_trade
✅ markdown/telegram не показывают слабую сделку как разрешённую

Это ровно то, что требовалось для hardening.

Особенно важно:

risk.allowed=false при превышении баланса по умолчанию

Это правильное поведение для WORED. По умолчанию система не должна молча разрешать позицию, требующую плечо или маржу.

Что всё ещё не доказано

Hermes пишет:

Raw JSON output показывает корректный валидный JSON

Но это не raw JSON. Это пересказ.

Для закрытия P7-01.3 нужен фактический вывод команд:

python3 -m json.tool /tmp/trade_plan_p7_hardening.json

и:

NO_MARGIN_GUARD_OK

Пока он не показал эти строки, финальный статус должен быть:

P7-01.3 READY FOR ACCEPTANCE, pending raw evidence
Дай Hermes финальную команду
Хорошо. Теперь дай не пересказ, а raw evidence.

Выполни и вставь полный вывод:

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
print("status=", p.get("status"))
print("side=", p.get("side"))
print("risk_allowed=", p.get("risk", {}).get("allowed"))
print("risk_reason=", p.get("risk", {}).get("reason"))
print("missing_sources=", p.get("missing_sources"))
print("unsupported_clis=", p.get("unsupported_clis"))
print("parse_errors=", p.get("parse_errors"))
r=p.get("risk", {})
if r.get("notional_value", 0) > r.get("balance", 0):
    assert r.get("allowed") is False, r
print("NO_MARGIN_GUARD_OK")
PY

python3 scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format markdown | head -80

python3 scripts/trade_plan_generator.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --balance 1000 \
  --risk-pct 1 \
  --format telegram
Acceptance criteria для финального закрытия

Закрываем P7-01.3, если в raw output будет:

1. JSON реально проходит python3 -m json.tool
2. status = ok
3. side = no_trade, если notional > balance и --allow-margin не задан
4. risk.allowed = false
5. risk.reason содержит margin/notional/balance
6. missing_sources = []
7. существующие файлы не помечены как missing
8. unsupported_clis / parse_errors — массивы объектов, не строки
9. markdown/telegram явно пишут NO TRADE или аналог
10. advisory_notice есть
Мой текущий вердикт
P7-01.3 hardening: почти готов
Остался один контроль: raw evidence вместо пересказа