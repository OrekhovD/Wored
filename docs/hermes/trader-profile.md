# WORED Trader Hermes Profile

**Статус:** подготовлен как reviewable artifact. Установка внешнего profile требует отдельного подтверждения владельца.

## Назначение

Hermes agent подключается к WORED Trader через MCP-инструменты только для:
- read-only digest (часовой, позиции, прогноз, бюджет LLM)
- ограниченного контроля (set_mode, submit_trade_plan)

## Доступные инструменты

| Tool | Тип | Описание |
|---|---|---|
| `get_hourly_digest` | read | Сводка: mode, positions, PnL, reason, feed_status |
| `get_positions` | read | Открытые/закрытые позиции |
| `get_forecast` | read | Последний прогноз run |
| `get_agent_runs` | read | История LLM вызовов + бюджет |
| `set_mode` | write | Смена режима (trade/reduce_only/pause), idempotency key |
| `submit_trade_plan` | write | Отправка плана, idempotency key |
| `run_role` | write | Ручной запуск роли (disabled по умолчанию) |

## Запрещённые инструменты

Следующие инструменты **не реализованы** и не могут быть добавлены:
- `create_order` — создание ордера
- `close_position` — закрытие позиции
- `change_limit` — изменение лимитов
- `adjust_margin_direct` — прямая корректировка маржи
- `reverse_position_direct` — прямой разворот

## Безопасность

- Каждый write требует idempotency_key
- Дубликат ключа → no-op с `applied=false`
- Audit log записывает все вызовы (без idempotency keys)
- Отсутствие Hermes не влияет на paper runner и protection plane
- `run_role` отключён по умолчанию в v0.1

## Подключение

```python
from agents.mcp_server import TraderMCPServer
from agents.role_runner import RoleRunner

runner = RoleRunner()
server = TraderMCPServer(role_runner=runner)

# List available tools
tools = server.list_tools()

# Call a tool
response = server.call_tool("get_hourly_digest", {})
```