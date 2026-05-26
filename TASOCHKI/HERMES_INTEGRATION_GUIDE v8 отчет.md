Оценка отчёта Hermes
Что сделано нормально

Формально он создал нужные файлы:

scripts/trade_plan_generator.py
docs/hermes/playbooks/trade-plan-generator.md

Это соответствует ТЗ. Также хорошо, что он не менял runtime, не писал в БД, не требовал collector и сделал три формата вывода. Эти пункты совпадают с acceptance criteria P7-01.

Главная проблема

Он пишет:

Все источники данных отмечаются как недоступные

Это означает, что генератор сейчас, скорее всего, не генерирует торговый план, а только корректно сообщает:

{
  "status": "insufficient_data",
  "missing_sources": [...]
}

Это полезный fallback, но не полноценная реализация P7-01.

P7-01 нужен не как “обёртка, которая сообщает, что всё missing”, а как агрегатор, который хотя бы при наличии минимальных данных способен построить:

Bias
Confidence
Entry zone
Stop loss
Take profit
Risk
Reasons
Invalidations
Warnings
В отчёте есть противоречие

Hermes одновременно пишет:

Использует все указанные источники данных

и:

Пока не удалось получить данные от каких-либо источников

Это нельзя принять как “источники используются”. Правильнее статус:

Источник-адаптеры добавлены, но фактическое получение данных не работает.

То есть код, возможно, пытается вызвать источники, но интеграция с ними невалидна.

Несоответствия ТЗ
1. Нет подтверждения валидного status: ok

В ТЗ пример результата — полноценный status: ok с bias, score, entry zone, SL/TP и risk. insufficient_data допустим только при нехватке данных, а не как единственный сценарий.

Нужно потребовать тест, где генератор возвращает именно:

{
  "status": "ok",
  "symbol": "BTCUSDT",
  "period": "60min",
  "bias": "...",
  "entry_zone": [...],
  "stop_loss": ...,
  "take_profit": [...]
}
2. Источники не должны считаться missing только из-за формата

Если signal_explainer.py или другие скрипты возвращают markdown, telegram или нестандартный JSON, trade_plan_generator.py должен иметь адаптеры:

- сначала пробовать --format json;
- если JSON не поддержан — читать markdown и извлекать минимум;
- если скрипт отсутствует или упал — только тогда missing_source;
- если источник есть, но формат неподдержан — source_error или parse_error, а не missing.

Сейчас Hermes смешал разные состояния:

missing source
source exists but returned non-JSON
source failed
source returned insufficient_data

Для диагностики это плохо.

3. Не указан фактический output JSON

В отчёте нет примера реального вывода команды:

python scripts/trade_plan_generator.py ... --format json | python3 -m json.tool

Без этого нельзя проверить структуру. Нужно увидеть полный JSON, хотя бы с insufficient_data.

4. Не подтверждена работа risk_position

ТЗ явно включает risk_position.py как источник расчёта позиции. Даже если market/signal данные отсутствуют, при переданных --balance и --risk-pct генератор должен уметь рассчитать риск, когда есть entry/stop. Если entry/stop нет — это нормально, но тогда отчёт должен явно сказать:

risk_position skipped because entry/stop unavailable

А не просто “все источники missing”.

5. Нет fallback на минимальный market data

В ТЗ сказано “не требует collector”, но это не значит “не умеет брать данные вообще”. Для P7-01 лучше иметь хотя бы один fallback:

1. пробовать существующие scripts;
2. если они не дают данных — пробовать HTX REST candles;
3. если REST недоступен — insufficient_data.

Иначе модуль будет почти всегда бесполезен в minimal environment.

Вердикт

Статус я бы поставил так:

P7-01 каркас: ✅ выполнен
P7-01 production usable: ❌ не выполнен
P7-01 acceptance criteria: частично
Главный blocker: все источники фактически missing, нет доказанного status: ok
Что дать Hermes следующим заданием

Нужно не переписывать всё, а сделать P7-01.1 Source Adapter Patch.

Готовый текст для Hermes:

Задача: P7-01.1 Trade Plan Generator Source Adapter Patch.

Текущий статус:
scripts/trade_plan_generator.py создан, но все источники данных возвращаются как missing.
Это не закрывает P7-01 полностью.

Цель:
сделать так, чтобы trade_plan_generator мог вернуть status=ok при наличии хотя бы минимальных рыночных данных.

FILES:
- scripts/trade_plan_generator.py
- docs/hermes/playbooks/trade-plan-generator.md

Не трогать:
- chatbot/*
- webui/*
- collector/*
- docker-compose.yml
- .env
- БД

Требования:
1. Разделить состояния источников:
   - missing_source: файл отсутствует
   - source_error: скрипт упал
   - parse_error: источник вернул данные, но формат не распознан
   - insufficient_source_data: источник работает, но данных мало

2. Для каждого источника сначала пробовать:
   python scripts/<source>.py ... --format json

3. Если источник не поддерживает JSON, не считать его missing.
   Добавить tolerant parser для markdown/telegram хотя бы для:
   - bias
   - RSI
   - MACD
   - SMA20/SMA50
   - latest price / close
   - warnings

4. Добавить fallback market candles:
   - сначала Postgres, если доступен через env/localhost;
   - если Postgres недоступен — HTX REST;
   - если оба недоступны — insufficient_data.

5. Если есть latest_close и минимальный candle range:
   - long setup при bullish/moderately_bullish;
   - short setup при bearish/moderately_bearish;
   - neutral -> no_trade или insufficient_signal, но не crash.

6. Если нет swing low/high, использовать fallback stop:
   - stop distance = max(0.8%, recent volatility estimate)
   - entry zone = latest_close ±0.2%
   - TP1 = 1.5R
   - TP2 = 2.5R

7. Output JSON должен всегда иметь:
   status
   symbol
   period
   missing_sources
   source_errors
   parse_errors
   warnings

8. При status=ok должен иметь:
   bias
   score
   confidence
   side
   entry_zone
   stop_loss
   take_profit
   risk
   reasons
   invalidations

9. Advisory only:
   не писать “buy/sell now”.
   Использовать “advisory setup”, “candidate plan”, “not financial advice”.

После patch выполнить:
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json | python3 -m json.tool
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format markdown
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format telegram

Дополнительный тест:
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json > /tmp/trade_plan.json
python3 - <<'PY'
import json
p=json.load(open('/tmp/trade_plan.json'))
print("status=", p.get("status"))
print("missing=", p.get("missing_sources"))
print("source_errors=", p.get("source_errors"))
print("parse_errors=", p.get("parse_errors"))
assert "status" in p
assert "missing_sources" in p
if p["status"] == "ok":
    for k in ["bias","score","confidence","side","entry_zone","stop_loss","take_profit","risk","reasons","invalidations"]:
        assert k in p, k
PY

REPORT:
- полный JSON status
- какие источники реально использованы
- какие missing/source_error/parse_error
- удалось ли получить status=ok
- если нет status=ok, точная причина
Что запросить у него сейчас

Пусть пришлёт не отчёт словами, а факты:

cd /mnt/d/WORED

sed -n '1,260p' scripts/trade_plan_generator.py
echo '--- PLAYBOOK ---'
sed -n '1,220p' docs/hermes/playbooks/trade-plan-generator.md

echo '--- JSON TEST ---'
python scripts/trade_plan_generator.py --symbol BTCUSDT --period 60min --lookback-days 7 --balance 1000 --risk-pct 1 --format json | python3 -m json.tool