# rachello-hermes-bridge.md

## Цель

Безопасный мост между Telegram-ботом RACHELLO и Hermes Agent — без второго polling, без shell passthrough, с whitelist команд.

## Архитектура

```
Telegram → RACHELLO (aiogram 3) → hermes_admin.py → hermes_bridge.py → scripts/
```

## Правила маршрутизации

| Тип сообщения | Отправитель | Обработка |
|----------------|--------------|-------------|
| Кнопка / callback_query | Любой | Криптобот WORED |
| Slash-command `/market` | Любой | Криптобот WORED |
| Slash-command `/hermes_status` | Только admin | Hermes Bridge |
| Обычный текст | Только admin | Hermes Bridge |
| Обычный текст | Не-admin | Криптобот WORED |

## Whitelist команд

| Команда | CLI |
|----------|-----|
| `status` | `hermes status` |
| `brief` | `python scripts/intelligence_brief.py --mode hourly --symbols BTCUSDT,ETHUSDT --format markdown` |
| `risk-position` | `python scripts/risk_position.py --balance 1000 --risk-pct 1 --entry 62000 --stop 61000 --take 65000` |
| `signal-explainer` | `python scripts/signal_explainer.py --symbol BTCUSDT --period 60min --lookback-days 7 --format markdown` |
| `webui-check` | `curl -fsS http://localhost:8080/api/health || echo "WebUI offline"` |
| `runtime-snapshot` | `bash scripts/runtime_snapshot.sh` |
| `git-status` | `cd /mnt/d/WORED && git status --short && git branch --show-current` |
| `help` | `Список доступных команд` |

## Формат отчёта в Telegram

Каждый ответ начинается с заголовка:

```
#HERMES_TASK_20260501_174500
Status: OK
Command: brief
Result:
# WORED Intelligence Brief — Hourly (17:00 → 18:00)

## Market Context
- `BTCUSDT`: bullish • RSI 62.3 • Volatility 2.1%
- `ETHUSDT`: neutral • RSI 53.1 • Volatility 3.4%

## Alerts (2 total)
- [volume_spike] BTCUSDT — high (2.3x)
- [rsi_overbought] ETHUSDT — medium (72.1)

## Forecasts (1 total)
- Accuracy score: 64.2%
- `qwen-plus`: 1

## Open Risks (1 total)
- `position_size_mismatch`: Position size exceeds risk budget on ETHUSDT

## AI Journal Summary
```
AI Journal (17:00 → 18:00):
• 3 new forecasts generated
• 2 volume spike alerts triggered
• 1 RSI overbought alert
• No errors
```

## Top Patterns
- `BTCUSDT` volume_spike: 4x, win rate ↑58.3%
```

## Безопасность

- Нет `exec`, `shell`, `bash`, `docker`, `rm`, `cat ~/.env`.
- Все секреты маскируются: `nvapi-*`, `Bearer ...`, `API_KEY=...`, `TOKEN=...`, `PASSWORD=...`
- Макс. длина ответа — 3500 символов. При превышении — выводится summary + `(truncated)`.
- Отчёт всегда начинается с `#HERMES_TASK_<timestamp>`.

## Тестирование

TEST-8: shell injection заблокирован

От admin:

```
/task rm -rf /
/task cat ~/.hermes/.env
/task docker compose down -v
/task bash -c "env"
```

Ожидание:

```
#HERMES_TASK_20260501_174800
Status: REJECTED
Reason: command 'rm' not allowed
Command: rm
Result: (no output)
```

или

```
#HERMES_TASK_20260501_174800
Status: REJECTED
Reason: command 'cat' not allowed
Command: cat
Result: (no output)
```