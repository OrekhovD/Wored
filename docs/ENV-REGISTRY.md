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
| `WEBUI_URL` | string (URL origin, no path) | `http://localhost:8080` | optional | chatbot (handlers/now.py, integrations/webui_client.py) | no |
| `TG_MINIAPP_URL` | string (URL with path) | `http://localhost:8080/command-deck` | optional | chatbot (main.py Menu Button, handlers/start.py, handlers/pipeline.py) | no |
| `WEBUI_COOKIE_SECURE` | bool | `true` (HTTPS) | required | webui | no |
| `WEBUI_PORT` | int | `8080` | optional | compose | no |

**Telegram Mini App HTTPS (2026-10-22).** The Mini App opens in Telegram's
in-app browser and therefore needs a public HTTPS origin with a valid TLS
certificate; `localhost` fallbacks in `chatbot/main.py`,
`chatbot/handlers/start.py` and `chatbot/handlers/pipeline.py` are unreachable
from a phone. The three env knobs that must agree are:

* `TG_MINIAPP_URL` — the URL Telegram's Menu Button and `web_app` keyboard
  open. Consumers paste this value verbatim, so the path suffix
  (`/command-deck`, `/dashboard`, `/daily-session`) is part of the
  contract, and the same variable serves three different pages today.
* `WEBUI_URL` — the bare origin, no path. Used by
  `chatbot/handlers/now.py`'s `_miniapp_button` (which builds
  `f"{base}/command-deck"`) and by `chatbot/integrations/webui_client.py`.
* `WEBUI_PUBLIC_BASE_URL` — the bare origin used by `webui/app.py` CSRF
  checks (`allowed_origin(origin, str(request.url), ...)` at `webui/app.py:2116`)
  and by `webui_client.py`. A POST with a `Origin:` header that does not
  match either the current request host or this value is rejected with
  HTTP 403.

`TELEGRAM_ADMIN_IDS` is the owner allowlist that `webui/principal.py:90` uses
inside `create_from_telegram`: an empty allowlist returns `None` on every
`initData`, so Mini App auth fails silently with a redirect to the password
login (RFC D-04). The value must include the operator's Telegram numeric id
(the same one `PAPER_TRADING_OWNER_TELEGRAM_ID` derives the runner owner
scope from).

**Quick-tunnel (default dev setup).** The deployed `.env` uses
`cloudflare/cloudflared` in a `docker run` container attached to
`wored_default`, launched with `tunnel --no-autoupdate --url http://webui:8000`.
The command assigns an ephemeral
`https://<words>.trycloudflare.com` subdomain on every start and prints it to
the container log. Recreating the container issues a new subdomain, so BotFather
and all three HTTPS env keys must be updated together. Because env_file values
apply only on `docker compose up -d --force-recreate` (not on `restart`), the
update sequence is: (1) `docker rm -f wored_cloudflared` and rerun the container;
(2) copy the new subdomain from `docker logs wored_cloudflared`; (3) patch
`TG_MINIAPP_URL` / `WEBUI_URL` / `WEBUI_PUBLIC_BASE_URL` in both `.env` and
`.env.wored` (chatbot uses the second file per `docker-compose.yml:70`);
(4) `docker compose up -d --force-recreate webui chatbot`; (5) re-point the
BotFather Mini App URL to the new origin; (6) probe with
`curl -sSI https://<new-origin>/command-deck` — expect HTTP 303 → `/login?next=/command-deck`
when `WEBUI_AUTH_ENABLED=true`, HTTP 200 when auth is off. The `WEBUI_COOKIE_SECURE=true`
setting makes the session cookie HTTPS-only, so a mixed HTTP/HTTPS contour
silently loses login.

**Stable origin (production candidate).** A Cloudflare named tunnel
(`cloudflared tunnel login` + `tunnel create` + DNS CNAME) or a personal
domain behind Caddy/nginx with Let's Encrypt removes the ephemeral-subdomain
renewal loop. That is a deployment choice outside the WORED codebase; every env
key above keeps the same meaning either way.

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

## RACHello personal bot (`bot_rachello`, `RACHELLO_*`)

Independent personal-assistant service. Secrets are injected via `${VAR}`
interpolation from the repo-root `.env` / shell — there is **no** committed
`.env.rachello` (git-ignored). `LOCAL_LLM_*` and `OLLAMA_CLOUD_*` are reused
from the root `.env`. Read by `bot_rachello/config.py`.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `TELEGRAM_RACHELLO_TOKEN` | string | — | required | bot_rachello | **yes** |
| `RACHELLO_ADMIN_IDS` | string (csv ids) | empty = first-user claim | optional | bot_rachello | no |
| `RACHELLO_CLOUD_MODEL` | string | `glm-5.3:cloud` | optional | bot_rachello | no |
| `RACHELLO_CTX_TOKENS` | int | `8192` | optional | bot_rachello | no |
| `RACHELLO_MAX_OUT_TOKENS` | int | `1024` | optional | bot_rachello | no |
| `RACHELLO_HISTORY_TURNS` | int | `8` | optional | bot_rachello | no |
| `RACHELLO_HISTORY_TTL_SEC` | int | `21600` | optional | bot_rachello | no |
| `RACHELLO_STREAM_EDIT_MS` | int | `1200` | optional | bot_rachello | no |
| `RACHELLO_BREAKER_FAILS` | int | `5` | optional | bot_rachello | no |
| `RACHELLO_BREAKER_WINDOW_SEC` | int | `60` | optional | bot_rachello | no |
| `RACHELLO_BREAKER_OPEN_SEC` | int | `300` | optional | bot_rachello | no |
| `RACHELLO_MAX_CALLS_PER_DAY` | int | `200` | optional | bot_rachello | no |
| `RACHELLO_MAX_TOKENS_PER_DAY` | int | `200000` | optional | bot_rachello | no |
| `RACHELLO_REDIS_URL` | string | `redis://redis:6379/3` | optional | bot_rachello | no |
| `RACHELLO_METRICS_TTL_DAYS` | int | `45` | optional | bot_rachello | no |
| `RACHELLO_LOCAL_LLM_THINK` | bool | `false` | optional | bot_rachello | no |

### Self-learning / reflection (`bot_rachello/selflearn/*` — new 2026-10)

Prompt self-evolution: idle-gated background reflector extracts `{rules, facts}`
from recent history via bonsai (strict JSON), merges into
`bot_rachello/learning/{rules,facts}.json` (source of truth; Redis db3 is cache
only) and injects a bounded overlay into the system prompt. Controlled from
Telegram via `/memory` `/reflect` `/undo` `/learn on|off`.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `RACHELLO_LEARN_ENABLED` | bool | `true` | optional | bot_rachello | no |
| `RACHELLO_LEARN_DIR` | string | `learning` | optional | bot_rachello | no |
| `RACHELLO_REFLECT_INTERVAL_SEC` | int | `600` | optional | bot_rachello | no |
| `RACHELLO_IDLE_SEC` | int | `120` | optional | bot_rachello | no |
| `RACHELLO_LEARN_MIN_TURNS` | int | `4` | optional | bot_rachello | no |
| `RACHELLO_LEARN_MAX_RULES` | int | `12` | optional | bot_rachello | no |
| `RACHELLO_LEARN_MAX_CHARS` | int | `1200` | optional | bot_rachello | no |

Note: `RACHELLO_LOCAL_LLM_THINK` is rachello-scoped on purpose — the global
`LOCAL_LLM_THINK=true` in root `.env` is required by chatbot, and bonsai with
`think=true` leaves `message.content` empty (blank replies).

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
| `PAPER_SIM_FEE_RATE` | decimal | `0.0005` | optional | collector runner (gate + fill) | no |
| `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION` | decimal | `0.1` | optional | collector | no |
| `PAPER_AI_MIN_INTERVAL_SECONDS` | int | `300` | optional | collector | no |
| `PAPER_AI_PLAN_TTL_SECONDS` | int | `3600` | optional | collector | no |
| `PAPER_AI_MAX_REQUESTS_PER_DAY` | int | `48` | optional | collector | no |
| `PAPER_AI_MAX_TOKENS_PER_DAY` | int | `200000` | optional | collector | no |
| `PAPER_TRADING_OWNER_TELEGRAM_ID` | digits (Telegram user id) | none — unset means unscoped | optional | collector runner recovery | no |
| `PAPER_RISK_TIER_DECLARED` | bool | `false` | optional | collector HTX publisher | no |
| `PAPER_RISK_TIER_TIER` | int | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_RISK_TIER_MAX_LEVERAGE` | int | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_RISK_TIER_MAINTENANCE_MARGIN_RATE` | decimal | none — required when enabled | conditional | collector HTX publisher | no |
| `PAPER_STRATEGY_COOLDOWN_SECONDS` | float | `600` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_MIN_NET_RR` | decimal | `1.2` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_SL_ATR_FRACTION` | decimal | `0.25` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_TP_RR` | decimal | `2` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_TRIGGER_MODE` | `breakout` \| `ema_cross` \| `trend_side` \| `ema_only` | `breakout` | optional | collector runner (BaselineV1) | no |
| `PAPER_STRATEGY_FORECAST_GATE` | `off` \| `on` | `off` | optional | collector runner (forecast gate) | no |
| `PAPER_STRATEGY_FORECAST_MODEL` | `B0_NAIVE` \| `B1_QUANTILE` | `B1_QUANTILE` | optional | collector runner (forecast gate) | no |
| `PAPER_STRATEGY_FORECAST_HORIZON_STEPS` | int `1..48` | `3` | optional | collector runner (forecast gate) | no |
| `PAPER_STRATEGY_FORECAST_MIN_EV` | decimal | `0` | optional | collector runner (forecast gate) | no |

**Trigger mode (2026-10-15).** `breakout` is the original BaselineV1 gate: a 1m
close crossing the EMA20 *and* pushing beyond the previous bar's extreme (a real
swing). In a flat market that second clause never aligns and the pipeline prints
no entries, which is correct for the strategy but useless when an operator is
verifying the signal→order→fill→ledger chain. `ema_cross` drops only the swing
clause and keeps the cross, so a choppy tape still trades — but a cross detector
stays silent in a *sustained* trend that has already moved past the EMA (every
bar closes on the same side, so no fresh cross prints). `trend_side` drops the
cross event too and fires whenever the 1m close sits on the trend side of the
EMA20 (bearish + below → short, bullish + above → long). `breakout`, `ema_cross`
and `trend_side` keep the 1h regime and the 15m confirm, so a loosened trigger
cannot fight the higher timeframe. `ema_only` is the deliberate stress knob that
also drops that alignment and trades the 1m EMA20 side in any regime — added
because `trend_side` correctly goes idle when price rebounds above the EMA inside a
bearish 1h regime (no short above the EMA, no long against the regime), which is
right for the strategy but stalls a pipeline test. In `ema_only` that bounce opens a
long. Cooldown, one-position-per-instrument and the daily loss caps stay untouched in
every mode, so even `ema_only` cannot spam. Unset means `breakout` — the running
contour is unchanged byte for byte unless an operator opts in; an unknown value
raises at startup by name.

**Forecast-gated entry (2026-10-16).** `PAPER_STRATEGY_FORECAST_GATE` turns the
mechanical trigger into a *candidate* and asks the calibrated forecast whether the
round trip still has positive expected value once the taker fee is charged. When
`on`, `paper_trading.forecast_gate` runs `forecast_engine.models.predict` over the
same 1m bars, extracts a per-bar return drift and volatility from the step-1
prediction, and derives a Brownian **TP-before-SL hitting probability** (closed
form, `θ = 2μ/σ²`; reduces to the fair `risk/(reward+risk)` at zero drift). The
signal executes only when
`p_hit · reward − (1 − p_hit) · risk − fee_cost ≥ PAPER_STRATEGY_FORECAST_MIN_EV`.
With `MIN_EV = 0` this is the project rule *a winning trade must exceed the
round-trip commission*. The gate is **fail-closed**: an unknown model, a forecast
exception, fewer than two bars, or a degenerate zero-width band (which is what
`B0_NAIVE` produces) all reject rather than pass. The flag, model and steps are
validated by name and raise at startup like the other entry knobs; unset means
`off`, so the contour is byte-for-byte unchanged until an operator opts in.

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

The deployed value `min_net_rr=0.5` **matches** the `0.5` floor
`paper_trading/rules.py` sets for self-learn candidates — the previous `0.3` was
an operator choice during the pipeline-verification phase and sat below that
floor, so any future ruleset overlay would have silently clamped it back up.
Now that the forecast EV-gate is the primary entry-quality filter, the operator
raised `min_net_rr` to the rules floor so gate-accepted signals also carry
enough gross reward per unit risk to remain above the fee cushion. All four
keys fail fast at startup on a non-numeric, negative (or, for the two shape
knobs, zero) value; the effective numbers are logged (`strategy config: …`) so
the running policy is readable from the container log.

**Runner owner scope (2026-10-15).** Recovery binds exactly one auto account, and
the candidate query asked the database for *every* owner holding a live day. With
two owners present — the operator's day plus a leftover test owner — the result
was two accounts, which the runner reads as ambiguous: `entries_blocked_reason =
ambiguous_active_auto_accounts`, entries dead after every restart until a manual
`start_day`. `PAPER_TRADING_OWNER_TELEGRAM_ID` names the operator the runner
serves; the id goes through the canonical `owner_id_from_telegram` uuid5
derivation, the same one the Telegram console and the WebUI write, so a mismatch
between the runner and the consoles is impossible. Unreadable values fail fast at
startup; **unset keeps the old all-owners behaviour exactly** — the scope is an
opt-in, not a hidden default, because guessing between owners would mean trading
someone else's book. The recovery report now carries `auto_owner_scope` (the
resolved owner uuid, or `unscoped`).

One related correction is *not* configuration: `paper_v2_fills.fee_rate` recorded
`0.0006` on every row while the engine charged `paper_trading/execution.py:FEE_RATE`
= `0.0005` (measured live: fee 2.04678428 on a 4093.5686 notional). The fill now
stores the rate carried by its own `FillResult`/`CloseResult`, and the `Fill`
dataclass default is zero — visibly unset instead of plausibly wrong.

**Fee wiring (2026-10-15).** `PAPER_SIM_FEE_RATE` previously had **no code
consumer**; it has one now. It is read once into `BaselineV1Config.fee_rate`
(`runner._strategy_config_from_env`) and that single value is used by *both* sides
of the round trip: the net-RR emit gate in `strategy._build_signal` (so the reward
the strategy accepted already has this fee subtracted) and the booked
`entry_fee`/`close_fee` in `execution.execute_market_order`/`execute_close` (the
runner passes `cfg.fee_rate` into the call). There is no second fee to drift. The
code default is `0.0005` (HTX USDT-M Prime-0) so an unset contour is byte-for-byte
the old behaviour; the operator's `.env` sets `0.0006` as a **deliberate
conservative buffer** above the Prime-0 venue rate (0.0005), so the strategy
gate charges slightly more per round trip than the venue actually takes. That
buffer is an intentional choice, not a stale literal: the fill now carries the
same `cfg.fee_rate` the strategy used, and a ruleset overlay that ever wants
venue-exact modelling has only to drop `PAPER_SIM_FEE_RATE` to `0.0005`.
`PAPER_SIM_SLIPPAGE_BPS`,
`PAPER_ORDER_MAX_SPREAD_BPS` and `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION` were
likewise inert and are now read via `runner._sim_settings_from_env` into the fill
and the risk gate (defaults equal the module constants). The former
`PAPER_SIM_LATENCY_MS` key has been **removed**: `market.apply_latency` is never
called, so a latency knob would have been config that lies — re-add it only
together with a real call site.

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
