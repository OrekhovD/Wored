# ENV Registry — WORED Configuration Keys

Each key has: type, default, required/optional, service consumers, and secrecy level.

## Core Infrastructure

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `DATABASE_URL` | string (DSN) | — | required | runtime (all services) | **yes** |
| `REDIS_URL` | string (URL) | `redis://redis:6379/0` | required | runtime (all services) | **yes** |
| `POSTGRES_USER` | string | `bot` | required | collector, webui, chatbot | **yes** |
| `POSTGRES_PASSWORD` | string | — | required | collector, webui, chatbot | **yes** |
| `POSTGRES_DB` | string | `wored_qa` | required | postgres container | no |

## WebUI Authentication

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `WEBUI_AUTH_ENABLED` | bool | `true` | required (release) | webui | no |
| `WEBUI_ADMIN_USERNAME` | string | `admin` | optional | webui | no |
| `WEBUI_ADMIN_PASSWORD` | string | пусто (при `WEBUI_AUTH_ENABLED=true` старт невозможен) | required (if auth enabled) | webui; chatbot — как материал для производного секрета | **yes** |
| `WEBUI_SESSION_SECRET` | string | webui: случайный на старт; chatbot: sha256 от username::password::TELEGRAM_ADMIN_ID | required (if auth enabled) | webui, chatbot | **yes** |
| `WEBUI_INTERNAL_TOKEN` | string | auto (32 chars) | required | webui, both bots | **yes** |
| `WEBUI_TELEGRAM_BOT_TOKENS` | JSON array | — | required (if auth enabled) | webui | **yes** |
| `TELEGRAM_ADMIN_IDS` | string (comma-separated) | empty | optional | webui, bots | no |
| `WEBUI_PUBLIC_BASE_URL` | string (URL origin) | — | required (production) | webui, bots | no |
| `WEBUI_COOKIE_SECURE` | bool | `true` (HTTPS) | required | webui | no |
| `WEBUI_PORT` | int | `8080` | optional | compose | no |

Значение `WEBUI_ADMIN_PASSWORD` продублировано в `.env` (строка 88, её читает `webui`) и в
`.env.wored` (строка 93, её читает `chatbot`) — проверено 2026-10-14 построчно, значения не
печатались. Ловушка ротации: `chatbot/integrations/webui_client.py::get_session_secret`
использует пароль как **материал ключа**, когда в контейнере бота не задан
`WEBUI_SESSION_SECRET`; в этом случае ротация пароля молча меняет и производный секрет,
и запись из чата в WebUI отваливается без явной ошибки (тот же класс сбоя, что S12).
Сейчас ловушка дремлет: явный `WEBUI_SESSION_SECRET` задан в обоих живых контейнерах
(проверено 2026-10-14 на присутствие, значения не печатались), явная ветка приоритетна,
и ротация пароля секрет не затрагивает. Требование процедуры: не удалять
`WEBUI_SESSION_SECRET` ни из одного профиля; чек-лист — `docs/OPERATIONAL-PROCESS.md` §7.

## LLM Provider Gateway — ARCHIVED (frozen free-model routing)

> **No-op on the current runtime.** These keys belonged to the free-model gateway
> subsystem (`provider_gateway` / `provider_adapters` / `usage_ledger` /
> `budget_policy`), which is archived under `free_routing_archive/` and is not
> collected or imported by the WORED runtime. They are documented here only for
> the revival procedure; do **not** treat them as active configuration.

| Key | Type | Default | Status | Consumers | Secret |
|-----|------|---------|--------|-----------|--------|
| `LLM_ROUTING_MODE` | enum | `balanced` | **archived / no-op** | archived gateway | no |
| `LLM_PAID_ENABLED` | bool | `false` | **archived / no-op** | archived gateway | no |
| `LLM_REGISTRY_PATH` | string | `/config/provider_registry.json` | **archived / no-op** | archived gateway | no |
| `NVIDIA_NIM_ENABLED` | bool | `false` | **archived / no-op** | webui prediction_engine | no |

## Active Inference (chatbot `ai/models.py` + webui `prediction_engine.py`)

The active stack is Ollama Cloud Pro (`:cloud`) as primary and the workstation-local
Bonsai server as failover. The chatbot and the WebUI resolve **different chain
orders and use different env names for the same cloud endpoint**: the chatbot uses
`OLLAMA_CLOUD_BASE_URL` / `OLLAMA_CLOUD_API_KEY` and tier chains
(worker/analyst/premium, cloud→local) via `OLLAMA_CHATBOT_*_MODEL`, while the WebUI
forecast engine uses `OLLAMA_BASE_URL` / `OLLAMA_API_KEY` and its own per-role
candidate order (`OLLAMA_WORKER_MODEL` / `OLLAMA_ANALYST_MODEL` /
`OLLAMA_PREMIUM_MODEL` / `OLLAMA_ORACLE_MODEL` + `*_FALLBACK_MODEL`). See
`chatbot/ai/models.py` versus `webui/prediction_engine.py`.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `OLLAMA_CLOUD_API_KEY` | string | — | required | chatbot (services, admin, sentiment) | **yes** |
| `OLLAMA_CLOUD_BASE_URL` | string | `https://ollama.com/v1` | optional | chatbot | no |
| `OLLAMA_API_KEY` | string | — | required | webui prediction_engine | **yes** |
| `OLLAMA_BASE_URL` | string | `https://ollama.com/v1` | optional | webui prediction_engine | no |
| `OLLAMA_CHATBOT_WORKER_MODEL` | string | `deepseek-v4.1-flash:cloud` | optional | chatbot | no |
| `OLLAMA_CHATBOT_ANALYST_MODEL` | string | `glm-5.3:cloud` | optional | chatbot | no |
| `OLLAMA_CHATBOT_PREMIUM_MODEL` | string | `glm-5.3:cloud` | optional | chatbot | no |
| `OLLAMA_WORKER_MODEL` | string | `deepseek-v4.1-flash:cloud` | optional | webui prediction_engine | no |
| `OLLAMA_ANALYST_MODEL` | string | `glm-5.3:cloud` | optional | webui prediction_engine | no |
| `OLLAMA_PREMIUM_MODEL` | string | `glm-5.3:cloud` | optional | webui prediction_engine | no |
| `OLLAMA_ORACLE_MODEL` | string | `glm-5.3-flash:cloud` | optional | webui prediction_engine | no |
| `LOCAL_LLM_ROLES` | string (comma-separated model keys, or `all`) | empty (disabled) | optional | webui prediction_engine | no |
| `LOCAL_LLM_MODEL` | string | `bonsai-27b` | optional | chatbot, webui prediction_engine | no |
| `LOCAL_LLM_BASE_URL` | string (URL origin) | `http://127.0.0.1:8088` | optional | chatbot, webui prediction_engine | no |
| `LOCAL_LLM_TIMEOUT` | float (seconds) | `120` | optional | webui prediction_engine | no |

`NVIDIA_NIM_ENABLED` used to gate the NVIDIA NIM tail of every prediction chain.
That free-model tier was retired with the routing subsystem archived to
`free_routing_archive/`, so the flag is now **inert**: `_nvidia_candidate` and the
`nvidia` chain tail were removed from `webui/prediction_engine.py`, and setting
the flag to `true` no longer appends any NVIDIA candidate. Forecast chains resolve
to Ollama Cloud Pro (`:cloud`) and the local Bonsai server only. The historical
rationale (permanent `410 Gone` from `integrate.api.nvidia.com`, 24/24 failures)
is preserved in `free_routing_archive/README.md`.

`LOCAL_LLM_*` point at a workstation `ollama serve` (port 8088), not at Ollama
Cloud. A local candidate leads the chain of the named role and the cloud chain
stays behind it as fallback. Inside Docker `127.0.0.1` is the container itself:
use `http://host.docker.internal:8088` and start the server on a routable
interface, which publishes an unauthenticated endpoint to the local network.

## Telegram Bots

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `TELEGRAM_TOKEN` | string | — | required | chatbot (legacy) | **yes** |
| `TELEGRAM_BOT_TOKEN` | string | — | required | chatbot | **yes** |
| `TELEGRAM_WORED_TOKEN` | string | — | required | chatbot_wored | **yes** |

## Telegram Proposal Agent (`BOT_PROPOSAL_*` — new in P3d)

Read by `chatbot/services/simulation_proposals.py` and `chatbot/ai/proposal_source.py`.
None of these keys is in `.env` today; the shipped default is the free path, and
no `.env` change was needed (or made) to land P3d.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `BOT_PROPOSAL_SOURCE` | enum (`rules`, `llm`) | anything except `llm` ⇒ deterministic rule engine | optional (paid agent opt-in) | chatbot | no |
| `BOT_PROPOSAL_TIER` | string (model key, e.g. `analyst_ollama`) | `analyst` tier | optional | chatbot (only when `BOT_PROPOSAL_SOURCE=llm`) | no |
| `BOT_PROPOSAL_TIMEOUT` | float seconds, `> 0` | `PROPOSAL_TIMEOUT_SECONDS` = `20.0` | optional | chatbot | no |

`BOT_PROPOSAL_SOURCE` is the only door to a billable call: the `ai.proposal_source`
import sits inside that branch, so a default installation never constructs a
provider client, and a quota check runs before the first attempt. The dashboard
has no equivalent flag — its source stays injectable only through
`app.state.simulation_proposal_source` (`webui/simulation_proposal.py:119`).

Sibling `BOT_*` console keys already in code and unchanged by P3d:
`BOT_OWNER_IDS`, `BOT_DEFAULT_TIMEZONE`, `BOT_DEFAULT_INSTRUMENT`,
`BOT_ALLOW_GROUPS`, `BOT_WIZARD_TTL_HOURS`, `BOT_MARKET_REFRESH_MIN_SECONDS`,
`BOT_SIM_MONITOR_ENABLED`.

## Execution / Simulation

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `SIM_ALLOWED_SYMBOLS` | string | `btcusdt` | required | execution_engine | no |
| `PAPER_MARKET_MODE` | enum (`demo`, `live`) | `live` | required for paper trading | webui | no |
| `PAPER_MARKET_MAX_AGE_SECONDS` | number, `0 < value <= 60` | `5` | required in `live` mode | webui | no |
| `HTX_LINEAR_SWAP_BASE_URL` | URL | `https://api.hbdm.com` | required for live paper trading | collector | no |
| `HTX_PERPETUAL_POLL_SECONDS` | number, `0.5..30` | `2` | required for live paper trading | collector | no |
| `PAPER_CONTRACTS` | comma-separated contract codes | `BTC-USDT` | required for live paper trading | collector | no |

`PAPER_MARKET_MODE=live` requires Redis keys in the form
`market:perpetual:htx:BTC-USDT`. A missing, malformed or stale snapshot blocks
preview, fill and close. The execution path never falls back to the demo price.
The collector publishes the keys from public HTX USDT-M endpoints without
using account credentials or order endpoints.

## Secrets Management

- Secrets are generated programmatically via `secrets.token_urlsafe()` and written directly to `.env` files
- Only the key name and present/updated status are shown to the administrator, never the value
- `test-password` / `test-token` / `disposable-qa-only` values are **never** used in production
- Environment changes require `docker compose up -d --force-recreate` (restart does not re-read env)

## Model Registry — ARCHIVED

> The `provider_registry.json` mount (`./config:/config:ro`) and
> `LLM_REGISTRY_PATH` belong to the frozen free-model gateway. The active runtime
> does not read them; model selection lives in `chatbot/ai/models.py` and
> `webui/prediction_engine.py`. Kept only for the `free_routing_archive/` revival
> procedure. For a host revival run, pass the archived path:
> `LLM_REGISTRY_PATH=D:/WORED/free_routing_archive/config/provider_registry.json`

Registry entries include: `registry_version`, `verified_at`, `provider`, `model_id`, `endpoint_type`, `enabled`, `cost_class`, `capabilities`, `context_tokens`, `max_output_tokens`, `pricing`, `validation_gate_id`.

## Session Forecast Auto-Refresh (FORECAST_AUTO_REFRESH_* — Trader Deck)

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `FORECAST_AUTO_REFRESH` | bool | `true` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_SYMBOL` | string | `btcusdt` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_HOURS` | int | `4` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_TIMEFRAME` | string | `60min` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_DEPTH` | int | `3` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_INTERVAL` | int (seconds) | `60` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_MAX_PER_DAY` | int | `6` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_COOLDOWN` | int (seconds) | `900` | optional | webui | no |

The loop in `webui/forecast_refresh.py` enqueues a forecast only when the newest
completed one no longer covers a future step, and it is bounded by the per-day
budget plus cooldown. Requests it creates carry `source='auto-trader-session'`,
which is what the budget guard counts — operator-triggered forecasts never
consume that budget. Set `FORECAST_AUTO_REFRESH=false` to stop AI spend entirely
(the Trader Deck then shows `прогноз просрочен` instead of inventing data).

## Paper Trading Engine (PAPER_* keys — new in HERMES-ACTIVE-PAPER-TRADING-V1)

These are technical limits, not user-facing risk settings. User risk is stored in DB settings.
More restrictive user limits always take precedence.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `PAPER_ENGINE_ENABLED` | bool | `false` | required (cutover) | collector | no |
| `PAPER_ENGINE_INTERVAL_SECONDS` | int | `2` | optional | collector runner | no |
| `PAPER_HEARTBEAT_INTERVAL_SECONDS` | int | `5` | optional | collector runner | no |
| `PAPER_HEARTBEAT_STALE_SECONDS` | int | `30` | optional | webui, chatbot | no |
| `PAPER_MARKET_MAX_AGE_SECONDS` | int | `5` | optional | collector, webui | no |
| `PAPER_ORDER_MAX_SPREAD_BPS` | int | `10` | optional | collector, webui | no |
| `PAPER_SIM_SLIPPAGE_BPS` | int | `2` | optional | collector | no |
| `PAPER_SIM_LATENCY_MS` | int | `250` | optional | collector | no |
| `PAPER_SIM_FEE_RATE` | decimal | `0.0006` | optional | collector | no |
| `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION` | decimal | `0.1` | optional | collector | no |
| `PAPER_AI_MIN_INTERVAL_SECONDS` | int | `300` | optional | collector | no |
| `PAPER_AI_PLAN_TTL_SECONDS` | int | `3600` | optional | collector | no |
| `PAPER_AI_MAX_REQUESTS_PER_DAY` | int | `48` | optional | collector | no |
| `PAPER_AI_MAX_TOKENS_PER_DAY` | int | `200000` | optional | collector | no |
| `PAPER_RISK_TIER_DECLARED` | bool | `false` | optional | collector HTX publisher | no |
| `PAPER_RISK_TIER_TIER` | int | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_RISK_TIER_MAX_LEVERAGE` | int | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_RISK_TIER_MAINTENANCE_MARGIN_RATE` | decimal | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_STRATEGY_COOLDOWN_SECONDS` | float | `600` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_MIN_NET_RR` | decimal | `1.2` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_SL_ATR_FRACTION` | decimal | `0.25` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_TP_RR` | decimal | `2` | optional | collector runner (BaselineV1) | no |

**Declared risk tier (2026-10-15).** The four `PAPER_RISK_TIER_*` keys are not a
risk limit — they are a manual substitute for a *market-data* field the venue
does not publish. Measured on the live endpoint,
`/linear-swap-api/v1/swap_contract_info` returns neither `max_leverage` nor
`maintenance_margin_rate`, and the documented alternatives are either an
insurance-fund view (`swap_risk_info`) or an authenticated per-position limit
(`swap_lever_position_limit`), so no public source can build a tier. Without one
the executor's `require_risk_tier` gate refuses every automatic entry
(`risk_tier_unavailable`) — correct for a real account, fatal for a simulation.
The declaration is all-or-nothing (`collector/htx/perpetual_market.py:
declared_risk_tier`): with the switch on, a missing or unparsable value is an
error that is logged and publishes the snapshot **without** a tier, never with a
guessed one; the published tier carries `source="operator-declared"` so the deal
ticket and the audit can tell it apart from venue data. `false` (or removing the
block) restores the previous behaviour exactly.

`PAPER_STRATEGY_*` are the four entry parameters of `BaselineV1Config`
(`cooldown_seconds`, `min_net_rr`, `sl_atr_fraction`, `tp_rr`). They decide
*whether the strategy acts*, not what an order costs, and they are separate from
the risk layer: the gate's own `min_net_rr` stays a day limit in
`paper_v2_days.settings_snapshot`. Two of them exist because of a measured
property of the live feed: one round trip of taker fee costs about `2 × 0.05% ×
price` ≈ 82 USDT of BTC near 81 900, so with `tp_rr=2` any stop narrower than
~41 USDT has non-positive net reward:risk — and the 1m ATR was ~20 USDT, i.e.
the default `sl_atr_fraction=0.25` produced stops around 15-25 USDT. Every
trigger was cancelled on cost before the risk gate saw it. Widening the stop and
raising the target is what makes a quiet market tradeable at all.

The deployed value `min_net_rr=0.3` is **below** the `0.5` floor
`paper_trading/rules.py` sets for self-learn candidates — an operator decision
for the simulated account, not a production policy, and any future ruleset
overlay will clamp it back to 0.5. All four keys fail fast at startup on a
non-numeric, negative (or, for the two shape knobs, zero) value; the effective
numbers are logged (`strategy config: …`) so the running policy is readable from
the container log.

Invalid configuration causes explicit readiness failure for the trading subsystem.
`PAPER_ENGINE_ENABLED=false` keeps the new runner off until cutover; existing
legacy sessions continue to work unchanged.

**Live state after P4:** the collector container runs `PAPER_ENGINE_ENABLED=true`
(set in `.env.wored`, line 99) — the row default above describes the pre-cutover
value, not the deployed one. The P4 gate was proven without flipping it, and the
engine stays `true`.

## Telegram Manual Deal (P4 — no new keys)

P4 added the `/deal` slice (chat preview → confirm → status → positions) and the
single domain ticket gate. It introduces **zero** configuration keys:

* `chatbot/services/paper_orders.py` and `chatbot/handlers/paper_deal.py` call no
  `os.getenv` at all — verified by grep over the whole of `chatbot/`, neither file
  appears in the result;
* the ticket lifetime is the domain constant `TICKET_TTL_SECONDS = 30.0`
  (`paper_trading/ticket.py:62`), deliberately not a knob: WebUI and chat must
  publish the same `expires_at`, and a per-client override would reintroduce the
  two-implementation bug this phase removed (RFC Q-21);
* risk limits are not env either — they come from `paper_v2_days.settings_snapshot`
  of the active day, and a key missing from (or unparsable in) that snapshot is
  named in the ticket's `settings_notes` as the bare snapshot key (`min_net_rr`,
  `max_leverage`, …) plus the marker `risk_tier_absent`, never substituted
  silently. Note the shape: the plan asked for a `settings_defaulted:<key>` entry
  in `reasons`; the implementation carries the bare key in `settings_notes`
  instead — same guarantee, different field (deviation recorded in the P4 report).

Keys the slice reads that already existed in this registry, unchanged in type,
default and consumers: `PAPER_MARKET_MODE`, `PAPER_MARKET_MAX_AGE_SECONDS`,
`PAPER_CONTRACTS`, `BOT_DEFAULT_INSTRUMENT` (all via
`chatbot/integrations/wored_api.py`), plus the principal lists resolved by
`chatbot/security/principal.py` in precedence order
`BOT_OWNER_IDS` → `TELEGRAM_ADMIN_IDS` → `TELEGRAM_ADMIN_ID`. `PAPER_ENGINE_ENABLED`
and `PAPER_ENGINE_INTERVAL_SECONDS` belong to the runner and were not touched.

One development-only name appears in P4 files and is **not** configuration:
`P4_PROBE_TELEGRAM_ID`, read solely by `output/p4_ticket_parity.py` to identify
which owner the read-only live parity probe should use. No service reads it, it is
not present in any `.env`, and the probe accepts the same value on stdin instead.
Deploying P4 is therefore a restart of `chatbot` and `webui` with the existing env
files — nothing for the operator to add.
