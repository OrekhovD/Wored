from __future__ import annotations

import json
import logging
import re
import os
import time
from typing import Optional

from openai import AsyncOpenAI

from ai.resilience import CircuitBreakerError, get_resilience_handler

log = logging.getLogger(__name__)

_clients: dict[str, AsyncOpenAI | None] = {}
_logged_client_messages: set[str] = set()


def _log_client_message_once(key: str, message: str) -> None:
    if key not in _logged_client_messages:
        log.warning(message)
        _logged_client_messages.add(key)


def get_client(tier: str) -> Optional[AsyncOpenAI]:
    from ai.models import MODELS

    if tier not in _clients:
        cfg = MODELS[tier]
        api_key = os.getenv(cfg.api_key_env, "").strip()
        if not api_key:
            _log_client_message_once(
                f"{tier}:missing_key",
                f"AI tier '{tier}' skipped: env var {cfg.api_key_env} is not set.",
            )
            _clients[tier] = None
            return None

        if tier == "minimax" and not api_key.startswith("nvapi-"):
            _log_client_message_once(
                "minimax:unsupported_key",
                "AI tier 'minimax' skipped: current router supports MiniMax only via NVIDIA NIM nvapi- keys.",
            )
            _clients[tier] = None
            return None

        _clients[tier] = AsyncOpenAI(
            api_key=api_key,
            base_url=cfg.endpoint,
            timeout=cfg.timeout,
            max_retries=0,
        )
    return _clients[tier]


def format_badge(tier: str, model_id: str, elapsed: float) -> str:
    badges = {
        "worker": "🤖 Р",
        "analyst": "🧠 А",
        "premium": "🎯 С",
        "minimax": "⚖️ О",
    }
    badge = badges.get(tier, "❓")
    return f"<b>{badge}</b> | <code>{model_id} · {elapsed:.1f}s</code>\n\n"


async def route_request(message: str, context: list[dict] | None = None) -> str:
    from ai.context_builder import build_comparison_context, build_context, build_deep_context
    from ai.dispatcher import classify
    from storage.redis_client import get_redis

    start = time.monotonic()
    intent = await classify(message)
    log.info("Classified intent: %s", intent)

    if intent["intent"] == "price":
        tickers = intent.get("tickers", ["btcusdt"]) or ["btcusdt"]
        ticker = tickers[0]

        redis_client = get_redis()
        data = await redis_client.get(f"ticker:{ticker}")
        elapsed = time.monotonic() - start

        if data:
            payload = json.loads(data)
            return (
                f"⚡ 💰 <b>{ticker.upper()}</b>: "
                f"${payload['price']} ({payload['change_pct']:+.2f}%) "
                f"`[Redis · {elapsed:.3f}s]`"
            )
        return f"❌ Нет данных по {ticker.upper()}"

    if intent["intent"] == "chat":
        return await _call_with_fallback("worker", "worker_chat", message, context)

    if intent["intent"] == "simple":
        return await _call_with_fallback("worker", "worker_quick", message, context)

    if intent["intent"] == "deep_analysis":
        tickers = intent.get("tickers", [])
        ticker = tickers[0] if tickers else "btcusdt"
        enriched = await build_deep_context(ticker)
        enriched += "\nПроведи глубокий пошаговый анализ с использованием chain-of-thought."
        full_context = (context or []) + [{"role": "system", "content": enriched}]
        return await _call_with_fallback("premium", "analyst_deep", message, full_context)

    if intent["intent"] == "comparison":
        tickers = intent.get("tickers", [])
        if len(tickers) >= 2:
            enriched = await build_comparison_context(tickers[0], tickers[1])
            full_context = (context or []) + [{"role": "system", "content": enriched}]
            return await _call_with_fallback("analyst", "analyst_compare", message, full_context)

    if intent["intent"] == "trade_plan":
        return await _route_trade_plan(message, intent, context)

    if intent["intent"] == "trade_sim":
        return await _route_trade_sim(message, intent, context)

    if intent["intent"] == "dual_analysis":
        return await _route_dual_timeframe(message, intent, context)

    tickers = intent.get("tickers", [])
    ticker = tickers[0] if tickers else "btcusdt"
    enriched = await build_context(ticker)
    enriched += "\nОсновываясь только на этих свежих данных и индикаторах, проведи лаконичный анализ."
    full_context = (context or []) + [{"role": "system", "content": enriched}]
    return await _call_with_fallback("analyst", "analyst_single", message, full_context)


async def _call_with_fallback(
    preferred: str,
    prompt_skill: str,
    message: str,
    context: list[dict] | None = None,
) -> str:
    from ai.models import MODELS, expand_fallback_tiers
    from ai.prompts import get_prompt

    last_error: Exception | None = None

    for tier in expand_fallback_tiers(preferred):
        if tier not in MODELS:
            continue

        cfg = MODELS[tier]
        client = get_client(tier)
        if client is None:
            continue

        handler = get_resilience_handler(tier)
        if not await handler.circuit_breaker.can_execute():
            log.info("Circuit OPEN for '%s', skipping. Stats: %s", tier, handler.get_circuit_stats())
            continue

        async def _do_call():
            messages = [{"role": "system", "content": get_prompt(prompt_skill)}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": message})
            request_kwargs = {
                "model": cfg.model_id,
                "messages": messages,
                "max_tokens": cfg.max_tokens,
                "temperature": 0.7,
            }
            if cfg.tier == "worker" and "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                request_kwargs["extra_body"] = {"enable_thinking": False}
            return await client.chat.completions.create(
                **request_kwargs,
            )

        try:
            started = time.monotonic()
            response = await handler.execute(_do_call)
            elapsed = time.monotonic() - started
            text = response.choices[0].message.content
            badge = format_badge(cfg.tier, cfg.model_id, elapsed)
            log.info("AI '%s' responded in %.2fs", tier, elapsed)
            return f"{badge}{text}"
        except CircuitBreakerError:
            log.warning("Circuit breaker rejected '%s'", tier)
            continue
        except Exception as exc:
            last_error = exc
            log.warning("AI tier '%s' (%s) failed: %s. Trying next...", tier, cfg.model_id, exc)
            continue

    error_detail = f" Последняя ошибка: {last_error}" if last_error else ""
    return f"❌ Все AI-модули сейчас недоступны.{error_detail} Попробуйте позже."


async def _route_trade_plan(message: str, intent: dict, context: list[dict] | None = None) -> str:
    """Two-tier trade plan: flash normalizes request → pro generates trade plan."""
    from ai.models import MODELS, WORKER_MODEL_CHAIN, PREMIUM_MODEL_CHAIN
    from ai.prompts import get_prompt
    from ai.context_builder import build_deep_context

    start = time.monotonic()

    # Tier 1: Flash normalizes user request → JSON
    plan_request = None
    for tier in WORKER_MODEL_CHAIN:
        cfg = MODELS[tier]
        client = get_client(tier)
        if client is None:
            continue

        try:
            async def _normalize():
                request_kwargs = {
                    "model": cfg.model_id,
                    "messages": [
                        {"role": "system", "content": get_prompt("trade_plan_flash")},
                        {"role": "user", "content": message},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.1,
                }
                if "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                    request_kwargs["extra_body"] = {"enable_thinking": False}
                return await client.chat.completions.create(**request_kwargs)

            response = await _normalize()
            raw = (response.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            plan_request = json.loads(raw)
            log.info("Trade plan normalized by %s: %s", cfg.model_id, plan_request)
            break
        except Exception as exc:
            log.warning("Trade plan normalization failed on %s: %s", cfg.model_id, exc)
            continue

    if not plan_request:
        plan_request = {
            "symbol": (intent.get("tickers") or ["btcusdt"])[0],
            "direction": "any",
            "timeframe": "1h",
            "risk": "any",
            "question": message,
            "extra_context": "",
        }

    symbol = plan_request.get("symbol", "btcusdt")
    normalized_text = json.dumps(plan_request, ensure_ascii=False, indent=2)

    # Build market context
    enriched = await build_deep_context(symbol)
    enriched += f"\n\nПАРАМЕТРЫ ЗАПРОСА ПОЛЬЗОВАТЕЛЯ:\n{normalized_text}"
    full_context = (context or []) + [{"role": "system", "content": enriched}]

    # Tier 2: Pro/GLM generates trade plan
    return await _call_with_fallback("premium", "trade_plan_analyst", message, full_context)



def _parse_trade_sim_regex(raw: str) -> dict:
    """Fallback regex parser for trade sim commands when JSON is malformed."""
    import re
    result = {
        "action": "open", "symbol": "btcusdt", "direction": "long",
        "order_type": "market", "margin_mode": "cross", "leverage": 10,
        "margin": 10.0, "ai_managed": False, "position_id": None,
    }

    raw_lower = raw.lower()

    # Action
    if any(w in raw_lower for w in ["закрой", "закрыть", "close"]):
        result["action"] = "close"
    elif any(w in raw_lower for w in ["позици", "position", "status", "мои"]):
        result["action"] = "status"
    elif any(w in raw_lower for w in ["истори", "history"]):
        result["action"] = "history"

    # Symbol
    sym_map = {"btc": "btcusdt", "eth": "ethusdt", "sol": "solusdt",
               "doge": "dogeusdt", "xrp": "xrpusdt", "ltc": "ltcusdt"}
    for key, val in sym_map.items():
        if key in raw_lower:
            result["symbol"] = val
            break

    # Direction
    if any(w in raw_lower for w in ["шорт", "short", "sell", "прода"]):
        result["direction"] = "short"

    # Order type
    if any(w in raw_lower for w in ["лимит", "limit"]):
        result["order_type"] = "limit"

    # Margin mode
    if any(w in raw_lower for w in ["изолир", "isolated"]):
        result["margin_mode"] = "isolated"

    # AI managed
    if any(w in raw_lower for w in ["торгуй", "ai", "авто"]):
        result["ai_managed"] = True

    # Leverage: find number followed by 'x' or 'х'
    lev_match = re.search(r'(\d+)\s*[xх]', raw_lower)
    if lev_match:
        result["leverage"] = int(lev_match.group(1))

    # Margin: find number followed by '$' or 'usdt' or 'дол' or 'бакс'
    margin_match = re.search(r'(\d+(?:\.\d+)?)\s*[\$]|(\d+(?:\.\d+)?)\s*(?:usdt|дол|бакс)', raw_lower)
    if margin_match:
        val = margin_match.group(1) or margin_match.group(2)
        if val:
            result["margin"] = float(val)

    # Position ID for close
    pos_match = re.search(r'(?:позици[юя]|position)\s*(?:#|id)?\s*(\d+)', raw_lower)
    if pos_match:
        result["position_id"] = int(pos_match.group(1))

    return result


async def _route_trade_sim(message: str, intent: dict, context: list[dict] | None = None) -> str:
    """Trade simulation: flash parses command → sim_engine executes."""
    from ai.models import MODELS, WORKER_MODEL_CHAIN
    from ai.prompts import get_prompt
    from services.sim_engine import (
        open_position, close_position, get_open_positions,
        get_user_history, calculate_unrealized_pnl, format_position_card,
    )
    from storage.redis_client import get_redis

    start = time.monotonic()

    # Tier 1: Regex parser (reliable, no API dependency)
    sim_params = _parse_trade_sim_regex(message)
    if sim_params:
        log.info("Trade sim parsed by regex: %s", sim_params)
    else:
        # Tier 2: Flash parses trade command → JSON (fallback)
        for tier in WORKER_MODEL_CHAIN:
            cfg = MODELS[tier]
            client = get_client(tier)
            if client is None:
                continue

            try:
                async def _parse_sim():
                    request_kwargs = {
                        "model": cfg.model_id,
                        "messages": [
                            {"role": "system", "content": get_prompt("trade_sim_flash")},
                            {"role": "user", "content": message},
                        ],
                        "max_tokens": 250,
                        "temperature": 0.1,
                    }
                    if "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                        request_kwargs["extra_body"] = {"enable_thinking": False}
                    return await client.chat.completions.create(**request_kwargs)

                response = await _parse_sim()
                raw = (response.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    lines = raw.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw = "\n".join(lines).strip()
                try:
                    sim_params = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    sim_params = _parse_trade_sim_regex(raw)
                log.info("Trade sim parsed by %s: %s", cfg.model_id, sim_params)
                break
            except Exception as exc:
                log.warning("Trade sim parsing failed on %s: %s", cfg.model_id, exc)
                continue

    if not sim_params:
        return "❌ Не удалось разобрать команду. Пример: <code>фьючерсы кросс 200x лонг btc на 30$</code>"

    action = sim_params.get("action", "open")
    user_id = 5249526259  # TODO: extract from message context

    # Get current price from Redis
    symbol = sim_params.get("symbol", "btcusdt")
    redis_client = get_redis()
    ticker_data = await redis_client.get(f"ticker:{symbol}")
    if ticker_data:
        current_price = json.loads(ticker_data)["price"]
    else:
        return f"❌ Нет данных по {symbol.upper()}"

    if action == "open":
        result = await open_position(
            user_id=user_id,
            symbol=symbol,
            direction=sim_params.get("direction", "long"),
            order_type=sim_params.get("order_type", "market"),
            margin_mode=sim_params.get("margin_mode", "cross"),
            leverage=int(sim_params.get("leverage", 10)),
            margin=float(sim_params.get("margin", 10)),
            entry_price=current_price,
            ai_managed=bool(sim_params.get("ai_managed", False)),
        )

        if "error" in result:
            return f"❌ {result['error']}"

        elapsed = time.monotonic() - start
        badge = format_badge("worker", cfg.model_id if 'cfg' in dir() else "flash", elapsed)

        # Build response
        pos = {
            "id": result["id"],
            "symbol": result["symbol"],
            "direction": result["direction"],
            "order_type": result["order_type"],
            "margin_mode": result["margin_mode"],
            "leverage": result["leverage"],
            "margin": result["margin"],
            "entry_price": result["entry_price"],
            "ai_managed": result["ai_managed"],
            "status": "open",
        }
        pnl = calculate_unrealized_pnl(pos, current_price)
        card = format_position_card(pos, pnl)

        fee_info = f"\n💰 Комиссия входа: {result['entry_fee']:.4f} USDT ({'maker' if result['order_type']=='limit' else 'taker'})"
        delay_info = f"\n⏱ Задержка исполнения: {result['execution_delay_ms']:.0f}ms"
        ai_info = "\n🤖 AI-управление: включено (агент сам закроет)" if result["ai_managed"] else ""

        return f"{badge}{card}{fee_info}{delay_info}{ai_info}"

    elif action == "close":
        position_id = sim_params.get("position_id")
        if not position_id:
            # Close all open positions for user
            positions = await get_open_positions(user_id)
            if not positions:
                return "📭 Нет открытых позиций"
            results = []
            for pos in positions:
                ticker_data = await redis_client.get(f"ticker:{pos['symbol']}")
                if ticker_data:
                    price = json.loads(ticker_data)["price"]
                else:
                    continue
                r = await close_position(pos["id"], price, reason="manual")
                if "error" not in r:
                    results.append(format_position_card(r))
            if results:
                return "\n\n".join(results)
            return "❌ Не удалось закрыть позиции"

        result = await close_position(int(position_id), current_price, reason="manual")
        if "error" in result:
            return f"❌ {result['error']}"

        elapsed = time.monotonic() - start
        badge = format_badge("worker", "flash", elapsed)
        card = format_position_card(result)
        fee_info = f"\n💰 Комиссия закрытия: {result['close_fee']:.4f} USDT (taker)"
        delay_info = f"\n⏱ Задержка исполнения: {result['execution_delay_ms']:.0f}ms"

        return f"{badge}{card}{fee_info}{delay_info}"

    elif action == "status":
        positions = await get_open_positions(user_id)
        if not positions:
            return "📭 Нет открытых позиций"

        lines = []
        for pos in positions:
            ticker_data = await redis_client.get(f"ticker:{pos['symbol']}")
            if ticker_data:
                price = json.loads(ticker_data)["price"]
                pnl = calculate_unrealized_pnl(pos, price)
                lines.append(format_position_card(pos, pnl))
            else:
                lines.append(format_position_card(pos))

        return "\n\n".join(lines)

    elif action == "history":
        history = await get_user_history(user_id, limit=10)
        if not history:
            return "📭 История пуста"

        lines = []
        total_pnl = 0
        for pos in history:
            lines.append(format_position_card(pos))
            if pos.get("realized_pnl"):
                total_pnl += float(pos["realized_pnl"])

        summary = f"\n\n📊 Всего закрыто: {len(history)} | Суммарный PnL: {total_pnl:+.4f} USDT"
        return "\n\n".join(lines) + summary

    return f"❌ Неизвестное действие: {action}"


async def _route_trade_sim(message: str, intent: dict, context: list[dict] | None = None) -> str:
    """Trade simulation: flash parses command -> sim_engine executes."""
    from ai.models import MODELS, WORKER_MODEL_CHAIN
    from ai.prompts import get_prompt
    from services.sim_engine import (
        open_position, close_position, get_open_positions,
        get_user_history, calculate_unrealized_pnl, format_position_card,
    )
    from storage.redis_client import get_redis

    start = time.monotonic()

    # Tier 1: Flash parses trade command -> JSON
    sim_params = None
    cfg = None
    for tier in WORKER_MODEL_CHAIN:
        cfg = MODELS[tier]
        client = get_client(tier)
        if client is None:
            continue

        try:
            async def _parse_sim():
                request_kwargs = {
                    'model': cfg.model_id,
                    'messages': [
                        {'role': 'system', 'content': get_prompt('trade_sim_flash')},
                        {'role': 'user', 'content': message},
                    ],
                    'max_tokens': 250,
                    'temperature': 0.1,
                }
                if 'dashscope-intl.aliyuncs.com' in cfg.endpoint:
                    request_kwargs['extra_body'] = {'enable_thinking': False}
                return await client.chat.completions.create(**request_kwargs)

            response = await _parse_sim()
            raw = (response.choices[0].message.content or '').strip()
            if raw.startswith('```'):
                lines = raw.splitlines()
                if lines and lines[0].startswith('```'):
                    lines = lines[1:]
                if lines and lines[-1].startswith('```'):
                    lines = lines[:-1]
                raw = '\n'.join(lines).strip()
            try:
                sim_params = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # Regex fallback for broken JSON from flash models
                sim_params = _parse_trade_sim_regex(raw)
            log.info('Trade sim parsed by %s: %s', cfg.model_id, sim_params)
            break
        except Exception as exc:
            log.warning('Trade sim parsing failed on %s: %s', cfg.model_id, exc)
            continue

    if not sim_params:
        # Last resort: regex fallback when all AI models are dead
        sim_params = _parse_trade_sim_regex(message)
        if sim_params:
            log.info('Trade sim parsed by regex fallback: %s', sim_params)
        else:
            return '❌ Не удалось разобрать команду. Пример: <code>фьючерсы кросс 200x лонг btc на 30$</code>'

    action = sim_params.get('action', 'open')
    user_id = 5249526259  # TODO: extract from message context

    # Get current price from Redis (skip for status/history actions)
    symbol = sim_params.get('symbol', 'btcusdt')
    redis_client = get_redis()
    current_price = 0.0
    if action in ('open', 'close'):
        ticker_data = await redis_client.get(f'ticker:{symbol}')
        if ticker_data:
            current_price = json.loads(ticker_data)['price']
        else:
            return f'❌ Нет данных по {symbol.upper()}'

    if action == 'open':
        result = await open_position(
            user_id=user_id,
            symbol=symbol,
            direction=sim_params.get('direction', 'long'),
            order_type=sim_params.get('order_type', 'market'),
            margin_mode=sim_params.get('margin_mode', 'cross'),
            leverage=int(sim_params.get('leverage', 10)),
            margin=float(sim_params.get('margin', 10)),
            entry_price=current_price,
            ai_managed=bool(sim_params.get('ai_managed', False)),
        )

        if 'error' in result:
            return f'❌ {result["error"]}'

        elapsed = time.monotonic() - start
        model_id = cfg.model_id if cfg else 'flash'
        badge = format_badge('worker', model_id, elapsed)

        pos = {
            'id': result['id'],
            'symbol': result['symbol'],
            'direction': result['direction'],
            'order_type': result['order_type'],
            'margin_mode': result['margin_mode'],
            'leverage': result['leverage'],
            'margin': result['margin'],
            'entry_price': result['entry_price'],
            'size': result.get('size', 0),
            'entry_fee': result.get('entry_fee', 0),
            'funding_paid': result.get('funding_paid', 0),
            'ai_managed': result['ai_managed'],
            'status': 'open',
        }
        pnl = calculate_unrealized_pnl(pos, current_price)
        card = format_position_card(pos, pnl)

        fee_info = f'\n💰 Комиссия входа: {result["entry_fee"]:.4f} USDT ({"maker" if result["order_type"]=="limit" else "taker"})'
        delay_info = f'\n⏱ Задержка исполнения: {result["execution_delay_ms"]:.0f}ms'
        ai_info = '\n🤖 AI-управление: включено (агент сам закроет)' if result['ai_managed'] else ''

        return f'{badge}{card}{fee_info}{delay_info}{ai_info}'

    elif action == 'close':
        position_id = sim_params.get('position_id')
        if not position_id:
            positions = await get_open_positions(user_id)
            if not positions:
                return '📭 Нет открытых позиций'
            results = []
            for pos in positions:
                ticker_data = await redis_client.get(f'ticker:{pos["symbol"]}')
                if ticker_data:
                    price = json.loads(ticker_data)['price']
                else:
                    continue
                r = await close_position(pos['id'], price, reason='manual')
                if 'error' not in r:
                    results.append(format_position_card(r))
            if results:
                return '\n\n'.join(results)
            return '❌ Не удалось закрыть позиции'

        result = await close_position(int(position_id), current_price, reason='manual')
        if 'error' in result:
            return f'❌ {result["error"]}'

        elapsed = time.monotonic() - start
        badge = format_badge('worker', 'flash', elapsed)
        card = format_position_card(result)
        fee_info = f'\n💰 Комиссия закрытия: {result["close_fee"]:.4f} USDT (taker)'
        delay_info = f'\n⏱ Задержка исполнения: {result["execution_delay_ms"]:.0f}ms'

        return f'{badge}{card}{fee_info}{delay_info}'

    elif action == 'status':
        positions = await get_open_positions(user_id)
        if not positions:
            return '📭 Нет открытых позиций'

        lines = []
        for pos in positions:
            ticker_data = await redis_client.get(f'ticker:{pos["symbol"]}')
            if ticker_data:
                price = json.loads(ticker_data)['price']
                pnl = calculate_unrealized_pnl(pos, price)
                lines.append(format_position_card(pos, pnl))
            else:
                lines.append(format_position_card(pos))

        return '\n\n'.join(lines)

    elif action == 'history':
        history = await get_user_history(user_id, limit=10)
        if not history:
            return '📭 История пуста'

        lines = []
        total_pnl = 0
        for pos in history:
            lines.append(format_position_card(pos))
            if pos.get('realized_pnl'):
                total_pnl += float(pos['realized_pnl'])

        summary = f'\n\n📊 Всего закрыто: {len(history)} | Суммарный PnL: {total_pnl:+.4f} USDT'
        return '\n\n'.join(lines) + summary

    return f'❌ Неизвестное действие: {action}'


async def _route_dual_timeframe(message: str, intent: dict, context: list[dict] | None = None) -> str:
    """Dual timeframe analysis: fetch 15m+1h+4h indicators → AI dual horizon."""
    from ai.knowledge_base import build_dual_timeframe_context

    tickers = intent.get("tickers", [])
    ticker = tickers[0] if tickers else "btcusdt"

    enriched = await build_dual_timeframe_context(ticker)
    full_context = (context or []) + [{"role": "system", "content": enriched}]
    return await _call_with_fallback("analyst", "analyst_dual_timeframe", message, full_context)
