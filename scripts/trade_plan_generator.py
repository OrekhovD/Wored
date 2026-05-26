#!/usr/bin/env python3
"""
Self-contained Trade Plan Generator for WORED
Generates advisory trading plans based on internal analysis with optional external enrichers
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import math


def fetch_candles_from_local(symbol: str, period: str, lookback_days: int) -> Optional[List[Dict]]:
    """
    Try to fetch candles from local source if available
    """
    # Check if we have any local data source in the project
    # For now, we'll implement this as a placeholder
    return None


def fetch_candles_from_htx_rest(symbol: str, period: str, lookback_days: int) -> Optional[List[Dict]]:
    """
    Fetch candles from HTX REST API using urllib
    """
    try:
        # Calculate start time
        start_time = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
        end_time = int(datetime.now().timestamp())
        
        # HTX REST API endpoint (this is a placeholder - actual endpoint would depend on HTX)
        base_url = "https://api.huobi.pro/market/history/kline"
        params = {
            'symbol': symbol.lower(),
            'period': period,
            'size': min(200, lookback_days * 24)  # Max 200 candles
        }
        
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=10)
        data = json.loads(response.read().decode())
        
        if 'data' in data:
            # Convert HTX format to our internal format
            candles = []
            for candle in data['data']:
                candles.append({
                    'timestamp': candle['id'],
                    'open': float(candle['open']),
                    'high': float(candle['high']),
                    'low': float(candle['low']),
                    'close': float(candle['close']),
                    'volume': float(candle['vol'])
                })
            return candles
        return None
    except Exception as e:
        print(f"HTX API fetch failed: {e}", file=sys.stderr)
        return None


def calculate_sma(closes: List[float], period: int) -> Optional[float]:
    """Calculate Simple Moving Average"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Calculate RSI indicator"""
    if len(closes) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    # Calculate average gain and loss for first period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Calculate RSI
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_volatility(closes: List[float], period: int = 10) -> Optional[float]:
    """Calculate volatility as standard deviation of returns"""
    if len(closes) < period + 1:
        return None
    
    returns = []
    for i in range(1, len(closes)):
        ret = (closes[i] - closes[i-1]) / closes[i-1]
        returns.append(ret)
    
    # Calculate standard deviation
    mean_return = sum(returns[-period:]) / len(returns[-period:])
    variance = sum([(r - mean_return) ** 2 for r in returns[-period:]]) / len(returns[-period:])
    std_dev = variance ** 0.5
    
    return std_dev * 100  # Return as percentage


def calculate_internal_indicators(candles: List[Dict]) -> Dict:
    """Calculate internal technical indicators from candles"""
    if not candles:
        return {}
    
    closes = [float(c['close']) for c in candles]
    highs = [float(c['high']) for c in candles]
    lows = [float(c['low']) for c in candles]
    
    indicators = {}
    
    # Calculate SMAs
    sma20 = calculate_sma(closes, 20)
    sma50 = calculate_sma(closes, 50)
    
    if sma20 is not None:
        indicators['sma20'] = sma20
    if sma50 is not None:
        indicators['sma50'] = sma50
    
    # Calculate RSI
    rsi = calculate_rsi(closes)
    if rsi is not None:
        indicators['rsi'] = rsi
    
    # Calculate volatility
    volatility = calculate_volatility(closes)
    if volatility is not None:
        indicators['volatility_pct'] = volatility
    
    # Calculate recent momentum
    if len(closes) >= 2:
        recent_momentum = ((closes[-1] - closes[-2]) / closes[-2]) * 100
        indicators['momentum_pct'] = recent_momentum
    
    # Calculate recent high/low
    if len(highs) >= 5:
        indicators['recent_high'] = max(highs[-5:])
    if len(lows) >= 5:
        indicators['recent_low'] = min(lows[-5:])
    
    return indicators


def determine_bias(indicators: Dict) -> Tuple[str, int, List[str], List[str]]:
    """Determine market bias based on internal indicators"""
    score = 0
    reasons = []
    counter_signals = []
    
    # SMA relationship
    sma20 = indicators.get('sma20')
    sma50 = indicators.get('sma50')
    
    if sma20 and sma50:
        if sma20 > sma50:
            score += 15
            reasons.append("SMA20 above SMA50")
        else:
            score -= 15
            reasons.append("SMA20 below SMA50")
    
    # RSI
    rsi = indicators.get('rsi')
    if rsi:
        if 30 <= rsi <= 70:
            if rsi > 50:
                score += 10
                reasons.append(f"RSI bullish ({rsi:.1f})")
            else:
                score -= 10
                reasons.append(f"RSI bearish ({rsi:.1f})")
        elif rsi > 70:
            score -= 10
            reasons.append(f"RSI overbought ({rsi:.1f})")
        elif rsi < 30:
            score -= 10
            reasons.append(f"RSI oversold ({rsi:.1f})")
    
    # Momentum - important: check if momentum contradicts bias
    momentum = indicators.get('momentum_pct')
    if momentum is not None:
        if momentum > 0:
            if sma20 and sma50 and sma20 < sma50:  # bearish bias but positive momentum
                counter_signals.append(f"Positive momentum ({momentum:.2f}%) contradicts bearish bias")
            else:
                score += 10
                reasons.append(f"Positive momentum ({momentum:.2f}%)")
        elif momentum < 0:
            if sma20 and sma50 and sma20 > sma50:  # bullish bias but negative momentum
                counter_signals.append(f"Negative momentum ({momentum:.2f}%) contradicts bullish bias")
            else:
                score -= 10
                reasons.append(f"Negative momentum ({momentum:.2f}%)")
    
    # Determine bias
    if score >= 25:
        bias = "bullish"
    elif 10 <= score < 25:
        bias = "moderately_bullish"
    elif -10 <= score < 10:
        bias = "neutral"
    elif -25 <= score < -10:
        bias = "moderately_bearish"
    else:
        bias = "bearish"
    
    return bias, score, reasons, counter_signals


def calculate_trade_levels(latest_price: float, bias: str, volatility_pct: Optional[float]) -> Dict:
    """Calculate entry, stop loss, and take profit levels"""
    if bias == "no_trade":
        return {
            "side": "no_trade",
            "entry_zone": None,
            "stop_loss": None,
            "take_profit": [],
            "position_size": 0.0,
            "risk_reward_tp1": 0.0,
            "risk_reward_tp2": 0.0
        }
    
    # Calculate stop loss distance based on volatility or default
    if volatility_pct:
        stop_distance_pct = max(volatility_pct * 1.5, 0.8)  # At least 0.8%
    else:
        stop_distance_pct = 0.8  # Default 0.8%
    
    stop_distance = latest_price * (stop_distance_pct / 100)
    
    # Entry zone (±0.2% from current price)
    entry_offset = latest_price * 0.002
    entry_zone = [round(latest_price - entry_offset, 2), round(latest_price + entry_offset, 2)]
    
    if bias in ["bullish", "moderately_bullish"]:
        # Long position
        stop_loss = round(latest_price - stop_distance, 2)
        # Take profits: 1.5R and 2.5R targets
        tp1_distance = stop_distance * 1.5
        tp2_distance = stop_distance * 2.5
        tp1 = round(latest_price + tp1_distance, 2)
        tp2 = round(latest_price + tp2_distance, 2)
        side = "long_candidate"
    elif bias in ["bearish", "moderately_bearish"]:
        # Short position
        stop_loss = round(latest_price + stop_distance, 2)
        # Take profits: 1.5R and 2.5R targets
        tp1_distance = stop_distance * 1.5
        tp2_distance = stop_distance * 2.5
        tp1 = round(latest_price - tp1_distance, 2)
        tp2 = round(latest_price - tp2_distance, 2)
        side = "short_candidate"
    else:
        # Neutral - no trade
        return {
            "side": "no_trade",
            "entry_zone": None,
            "stop_loss": None,
            "take_profit": [],
            "position_size": 0.0,
            "risk_reward_tp1": 0.0,
            "risk_reward_tp2": 0.0
        }
    
    return {
        "side": side,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "take_profit": [tp1, tp2],
        "position_size": 0.0,  # Will be calculated later with risk
        "risk_reward_tp1": 1.5,
        "risk_reward_tp2": 2.5
    }


def calculate_position_size_and_risk(trade_levels: Dict, balance: float, risk_pct: float, latest_price: float) -> Dict:
    """Calculate position size based on risk parameters"""
    risk_amount = balance * (risk_pct / 100)
    
    if trade_levels["side"] in ["long_candidate", "short_candidate"]:
        entry_price = (trade_levels["entry_zone"][0] + trade_levels["entry_zone"][1]) / 2
        risk_per_unit = abs(entry_price - trade_levels["stop_loss"])
        
        if risk_per_unit > 0:
            position_size = risk_amount / risk_per_unit
            notional_value = position_size * entry_price
        else:
            position_size = 0
            notional_value = 0
    else:
        # No trade case
        position_size = 0
        notional_value = 0
    
    return {
        "balance": balance,
        "risk_pct": risk_pct,
        "max_loss": risk_amount,
        "position_size": round(position_size, 6),
        "notional_value": round(notional_value, 2),
        "allowed": trade_levels["side"] in ["long_candidate", "short_candidate"]
    }


def generate_invalidations(bias: str, indicators: Dict) -> List[str]:
    """Generate invalidation conditions"""
    invalidations = []
    
    if bias in ["bullish", "moderately_bullish"]:
        invalidations.extend([
            "Close below SMA50" if 'sma50' in indicators else "Close below recent support",
            "Break below recent swing low" if 'recent_low' in indicators else "Break below recent low"
        ])
    elif bias in ["bearish", "moderately_bearish"]:
        invalidations.extend([
            "Close above SMA50" if 'sma50' in indicators else "Close above recent resistance",
            "Break above recent swing high" if 'recent_high' in indicators else "Break above recent high"
        ])
    
    # Add RSI-based invalidations
    rsi = indicators.get('rsi')
    if rsi:
        if bias in ["bullish", "moderately_bullish"]:
            invalidations.append("RSI drops below 45")
        elif bias in ["bearish", "moderately_bearish"]:
            invalidations.append("RSI rises above 55")
    
    return invalidations


def call_external_enricher(script_name: str, args: List[str]) -> Tuple[Optional[Dict], Optional[str]]:
    """Call external enricher script if available"""
    try:
        import subprocess
        import os
        
        script_path = f"scripts/{script_name}"
        if not os.path.exists(script_path):
            return None, "missing_source"
        
        # Try to call the script with proper arguments based on what we know
        if script_name == "signal_explainer.py":
            # Use the arguments that the script actually supports
            actual_args = [sys.executable, script_path, "--symbol", args[0], "--period", args[1]]
        elif script_name == "market_context.py":
            actual_args = [sys.executable, script_path, "--symbol", args[0], "--period", args[1]]
        elif script_name == "pattern_lab.py":
            actual_args = [sys.executable, script_path, "--symbol", args[0], "--period", args[1]]
        elif script_name == "forecast_reality_analyzer.py":
            actual_args = [sys.executable, script_path, "--symbol", args[0]]
        elif script_name == "risk_position.py":
            # For risk_position, just call with symbol
            actual_args = [sys.executable, script_path, args[0]]
        else:
            actual_args = [sys.executable, script_path] + args
        
        result = subprocess.run(actual_args, capture_output=True, text=True, timeout=15)
        
        if result.returncode != 0:
            # Check if it's an argument error or other error
            stderr_output = result.stderr.lower()
            if "unrecognized arguments" in stderr_output or "error:" in stderr_output:
                return None, "unsupported_cli"
            else:
                return None, f"source_error: {result.stderr.strip()}"
        
        # Try to parse the output
        lines = result.stdout.strip().split('\n')
        for line in reversed(lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    return json.loads(line), "used"
                except json.JSONDecodeError:
                    continue
        
        # If no JSON found, return as parse_error
        return None, "parse_error"
    
    except FileNotFoundError:
        return None, "missing_source"
    except Exception as e:
        return None, f"source_error: {str(e)}"


def get_market_data_with_fallback(symbol: str, period: str, lookback_days: int, allow_synthetic: bool) -> Tuple[Optional[List[Dict]], str, List[str]]:
    """Get market data with fallback chain"""
    data_sources = []
    candles = None
    
    # 1. Try local source
    candles = fetch_candles_from_local(symbol, period, lookback_days)
    if candles:
        data_sources.append("local")
        return candles, "real_data", data_sources
    
    # 2. Try HTX REST API
    candles = fetch_candles_from_htx_rest(symbol, period, lookback_days)
    if candles:
        data_sources.append("htx_rest")
        return candles, "real_data", data_sources
    
    # 3. Use synthetic data if allowed
    if allow_synthetic:
        # Generate synthetic candles for testing purposes
        import random
        synthetic_candles = []
        base_price = 62000.0
        for i in range(lookback_days * 24):  # Hourly candles for the lookback period
            # Random walk with slight upward drift
            change = random.uniform(-0.02, 0.03)  # -2% to +3% daily variation
            new_price = base_price * (1 + change)
            synthetic_candles.append({
                'timestamp': int((datetime.now() - timedelta(hours=i)).timestamp()),
                'open': round(new_price * random.uniform(0.995, 1.005), 2),
                'high': round(new_price * random.uniform(1.001, 1.02), 2),
                'low': round(new_price * random.uniform(0.98, 0.999), 2),
                'close': round(new_price, 2),
                'volume': round(random.uniform(100, 1000), 2)
            })
            base_price = new_price
        
        data_sources.append("synthetic_smoke_test")
        return synthetic_candles, "synthetic_data", data_sources
    
    # 4. No data available
    return None, "no_data", []


def format_output(data: Dict, output_format: str) -> str:
    """Format the trade plan output according to specified format."""
    if output_format == "json":
        return json.dumps(data, indent=2)
    elif output_format == "markdown":
        return format_markdown(data)
    elif output_format == "telegram":
        return format_telegram(data)
    else:
        return json.dumps(data, indent=2)


def format_markdown(data: Dict) -> str:
    """Format trade plan as markdown."""
    if data.get("status") == "insufficient_data":
        return f"# Trade Plan: {data['symbol']} {data['period']}\n\n{data['reason']}"
    
    md = f"""# Trade Plan: {data['symbol']} {data['period']}

## Analysis Status
Data Quality: {data['data_quality']}
Sources: {', '.join(data['data_sources']) if data['data_sources'] else 'None'}
Latest Price: {data.get('latest_price', 'N/A')}
Signal Strength: {data.get('signal_strength', 'N/A')}
Confidence: {data['confidence']}/100

## Market Bias
{data['bias'].title().replace('_', ' ')}
Score: {data['score']}

## Trade Setup"""
    
    if data['side'] == 'no_trade':
        md += f"\n\n**NO TRADE RECOMMENDED**\n\n"
        md += f"Reason: {data['risk'].get('reason', 'Insufficient directional edge')}"
    else:
        md += f"""
Entry zone: {data['entry_zone'][0]}–{data['entry_zone'][1]}
Stop loss: {data['stop_loss']}
Take profit 1: {data['take_profit'][0]}
Take profit 2: {data['take_profit'][1]}

## Risk Management
Balance: {data['risk']['balance']} USDT
Risk: {data['risk']['risk_pct']}%
Max loss: {data['risk']['max_loss']} USDT
Position size: {data['risk']['position_size']} {data['symbol'].split('USDT')[0]}
Notional value: {data['risk']['notional_value']} USDT
Risk/reward: 1:{data['risk']['risk_reward_tp1']} (TP1), 1:{data['risk']['risk_reward_tp2']} (TP2)
"""
    
    md += "\n## Supporting Reasons"
    for reason in data['reasons']:
        md += f"\n- {reason}"
    
    if data.get('counter_signals'):
        md += "\n\n## Counter Signals"
        for counter in data['counter_signals']:
            md += f"\n- {counter}"
    
    md += "\n\n## Invalidations"
    for invalidation in data['invalidations']:
        md += f"\n- {invalidation}"
    
    md += "\n\n## Warnings"
    for warning in data['warnings']:
        md += f"\n- {warning}"
    
    if data.get('missing_sources'):
        md += "\n\n## Missing Sources"
        for source in data['missing_sources']:
            if isinstance(source, dict):
                md += f"\n- {source.get('source', 'Unknown')}: {source.get('reason', 'Unknown')}"
            else:
                md += f"\n- {source}"
    
    if data.get('advisory_notice'):
        md += f"\n\n## Advisory Notice\n{data['advisory_notice']}"
    
    return md


def format_telegram(data: Dict) -> str:
    """Format trade plan for Telegram message."""
    if data.get("status") == "insufficient_data":
        return f"⚠️ {data['symbol']} {data['period']}: {data['reason']}"
    
    if data['side'] == 'no_trade':
        return f"""📊 {data['symbol']} {data['period']} Analysis

📈 {data['bias'].title().replace('_', ' ')}
📊 Conf: {data['confidence']}/100
🔍 Signal: {data.get('signal_strength', 'N/A')}

❌ NO TRADE RECOMMENDED
💡 Reason: {data['risk'].get('reason', 'Insufficient signal strength')}

📋 Sources: {', '.join(data['data_sources']) if data['data_sources'] else 'None'}"""
    else:
        reasons_str = ", ".join(data['reasons'][:3])  # Limit to first 3 reasons
        counter_str = ""
        if data.get('counter_signals'):
            counter_str = f"\n⚠️ Counters: {', '.join(data['counter_signals'][:2])}"
        
        return f"""📊 {data['symbol']} {data['period']} Plan

📈 {data['bias'].title().replace('_', ' ')}
📊 Conf: {data['confidence']}/100
🔍 Signal: {data.get('signal_strength', 'N/A')}
🎯 Entry: {data['entry_zone'][0]}–{data['entry_zone'][1]}
🚫 SL: {data['stop_loss']}
✅ TP: {data['take_profit'][0]} / {data['take_profit'][1]}
💰 Risk: {data['risk']['risk_pct']}%

💡 {reasons_str}{counter_str}

📋 Sources: {', '.join(data['data_sources']) if data['data_sources'] else 'None'}"""


def main():
    parser = argparse.ArgumentParser(description="Generate advisory trading plans (self-contained)")
    parser.add_argument("--symbol", type=str, required=True, help="Trading symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", type=str, required=True, help="Timeframe (e.g., 60min)")
    parser.add_argument("--lookback-days", type=int, default=14, help="Lookback days for analysis")
    parser.add_argument("--balance", type=float, default=1000, help="Account balance")
    parser.add_argument("--risk-pct", type=float, default=1, help="Risk percentage per trade")
    parser.add_argument("--format", type=str, choices=["json", "markdown", "telegram"], 
                       default="json", help="Output format")
    parser.add_argument("--allow-synthetic-fallback", action="store_true",
                       help="Allow synthetic data for smoke tests (not for real trading)")
    parser.add_argument("--max-notional-pct", type=float, default=100, 
                       help="Maximum notional value as percentage of balance (default 100%)")
    parser.add_argument("--allow-margin", action="store_true",
                       help="Allow positions where notional value exceeds balance (requires margin/leverage)")
    parser.add_argument("--save-to-journal", action="store_true",
                       help="Save the trade plan to decision journal")
    parser.add_argument("--journal-path", type=str, default="data/decision_journal.jsonl",
                       help="Path to the decision journal file")
    
    args = parser.parse_args()
    
    # Initialize tracking variables
    missing_sources = []
    source_errors = []
    parse_errors = []
    unsupported_clis = []
    data_sources = []
    
    # Get market data with fallback chain
    candles, data_quality, data_sources = get_market_data_with_fallback(
        args.symbol, args.period, args.lookback_days, args.allow_synthetic_fallback
    )
    
    # If no candles available, return insufficient_data
    if not candles:
        result = {
            "status": "insufficient_data",
            "symbol": args.symbol,
            "period": args.period,
            "reason": "No market data available",
            "data_quality": "no_data",
            "data_sources": [],
            "missing_sources": missing_sources,
            "source_errors": source_errors,
            "parse_errors": parse_errors,
            "unsupported_clis": unsupported_clis,
            "warnings": ["No market data available to generate trade plan"]
        }
        print(format_output(result, args.format))
        return
    
    # Calculate internal indicators
    indicators = calculate_internal_indicators(candles)
    
    # Determine bias with counter_signals
    latest_price = candles[-1]['close']
    bias, score, reasons, counter_signals = determine_bias(indicators)
    
    # Apply confidence gating
    raw_confidence = min(95, max(20, 40 + abs(score) // 2))
    
    # Determine signal strength based on confidence
    if raw_confidence < 45:
        effective_side = "no_trade"
        signal_strength = "none"
    elif 45 <= raw_confidence < 55:
        if bias in ["bullish", "moderately_bullish"]:
            effective_side = "weak_long_candidate"
        elif bias in ["bearish", "moderately_bearish"]:
            effective_side = "weak_short_candidate"
        else:
            effective_side = "no_trade"
        signal_strength = "weak"
    elif 55 <= raw_confidence < 70:
        if bias in ["bullish", "moderately_bullish"]:
            effective_side = "long_candidate"
        elif bias in ["bearish", "moderately_bearish"]:
            effective_side = "short_candidate"
        else:
            effective_side = "no_trade"
        signal_strength = "moderate"
    else:  # raw_confidence >= 70
        if bias in ["bullish", "moderately_bullish"]:
            effective_side = "strong_long_candidate"
        elif bias in ["bearish", "moderately_bearish"]:
            effective_side = "strong_short_candidate"
        else:
            effective_side = "no_trade"
        signal_strength = "strong"
    
    # Calculate trade levels based on effective side, not original bias
    volatility_pct = indicators.get('volatility_pct')
    
    if effective_side in ["long_candidate", "weak_long_candidate", "strong_long_candidate", 
                          "short_candidate", "weak_short_candidate", "strong_short_candidate"]:
        trade_levels = calculate_trade_levels(latest_price, bias, volatility_pct)
    else:
        # For no_trade, create empty trade levels
        trade_levels = {
            "side": "no_trade",
            "entry_zone": None,
            "stop_loss": None,
            "take_profit": [],
            "position_size": 0.0,
            "risk_reward_tp1": 0.0,
            "risk_reward_tp2": 0.0
        }
    
    # Calculate risk and position size
    risk_data = calculate_position_size_and_risk(
        trade_levels, args.balance, args.risk_pct, latest_price
    )
    
    # Apply notional guard
    warnings = []
    notional_exceeds_balance = risk_data["notional_value"] > args.balance
    
    if notional_exceeds_balance:
        if not args.allow_margin:
            # If margin is not allowed and notional exceeds balance, disallow the trade
            risk_data["allowed"] = False
            risk_data["reason"] = "notional exceeds balance; margin disabled"
            effective_side = "no_trade"
            warnings.append(f"Position notional ({risk_data['notional_value']}) exceeds balance ({args.balance}); margin disabled by default")
        else:
            # If margin is allowed, show warning but allow the trade
            warnings.append(f"Position notional ({risk_data['notional_value']}) exceeds balance ({args.balance}); margin/leverage enabled")
    
    if data_quality == "synthetic_data":
        warnings.append("Synthetic fallback data used; not valid for trading decisions")
    
    if args.risk_pct > 3:
        warnings.append(f"Risk percentage ({args.risk_pct}%) is higher than recommended (<3%)")
    
    # Generate invalidations
    invalidations = generate_invalidations(bias, indicators)
    
    # Handle external enrichers (optional)
    enricher_args = [args.symbol, args.period, str(args.lookback_days)]
    
    # Try signal_explainer
    signal_data, signal_status = call_external_enricher("signal_explainer.py", enricher_args)
    if signal_status == "missing_source":
        missing_sources.append({"source": "signal_explainer", "reason": "missing", "used": False})
    elif signal_status == "unsupported_cli":
        unsupported_clis.append({"source": "signal_explainer", "reason": "unsupported_cli", "used": False})
    elif "source_error" in signal_status:
        source_errors.append({"source": "signal_explainer", "reason": signal_status, "used": False})
    elif signal_status == "parse_error":
        parse_errors.append({"source": "signal_explainer", "reason": "non_json_output", "used": False})
    elif signal_status == "used" and signal_data:
        # Integrate signal data if available
        if 'reasons' in signal_data:
            reasons.extend(signal_data['reasons'])
    
    # market_context
    mc_data, mc_status = call_external_enricher("market_context.py", enricher_args)
    if mc_status == "missing_source":
        missing_sources.append({"source": "market_context", "reason": "missing", "used": False})
    elif mc_status == "unsupported_cli":
        unsupported_clis.append({"source": "market_context", "reason": "unsupported_cli", "used": False})
    elif "source_error" in mc_status:
        source_errors.append({"source": "market_context", "reason": mc_status, "used": False})
    elif mc_status == "parse_error":
        parse_errors.append({"source": "market_context", "reason": "non_json_output", "used": False})
    elif mc_status == "used" and mc_data:
        # Integrate market context data
        pass
    
    # pattern_lab
    pl_data, pl_status = call_external_enricher("pattern_lab.py", enricher_args)
    if pl_status == "missing_source":
        missing_sources.append({"source": "pattern_lab", "reason": "missing", "used": False})
    elif pl_status == "unsupported_cli":
        unsupported_clis.append({"source": "pattern_lab", "reason": "unsupported_cli", "used": False})
    elif "source_error" in pl_status:
        source_errors.append({"source": "pattern_lab", "reason": pl_status, "used": False})
    elif pl_status == "parse_error":
        parse_errors.append({"source": "pattern_lab", "reason": "non_json_output", "used": False})
    elif pl_status == "used" and pl_data:
        # Integrate pattern data
        pass
    
    # forecast_analyzer
    fa_data, fa_status = call_external_enricher("forecast_reality_analyzer.py", [args.symbol])
    if fa_status == "missing_source":
        missing_sources.append({"source": "forecast_reality_analyzer", "reason": "missing", "used": False})
    elif fa_status == "unsupported_cli":
        unsupported_clis.append({"source": "forecast_reality_analyzer", "reason": "unsupported_cli", "used": False})
    elif "source_error" in fa_status:
        source_errors.append({"source": "forecast_reality_analyzer", "reason": fa_status, "used": False})
    elif fa_status == "parse_error":
        parse_errors.append({"source": "forecast_reality_analyzer", "reason": "non_json_output", "used": False})
    elif fa_status == "used" and fa_data:
        # Integrate forecast data
        pass
    
    # risk_position
    rp_data, rp_status = call_external_enricher("risk_position.py", [args.symbol])
    if rp_status == "missing_source":
        missing_sources.append({"source": "risk_position", "reason": "missing", "used": False})
    elif rp_status == "unsupported_cli":
        unsupported_clis.append({"source": "risk_position", "reason": "unsupported_cli", "used": False})
    elif "source_error" in rp_status:
        source_errors.append({"source": "risk_position", "reason": rp_status, "used": False})
    elif rp_status == "parse_error":
        parse_errors.append({"source": "risk_position", "reason": "non_json_output", "used": False})
    elif rp_status == "used" and rp_data:
        # Integrate risk data
        pass
    
    # Prepare the final result
    result = {
        "status": "ok",
        "symbol": args.symbol,
        "period": args.period,
        "data_quality": data_quality,
        "data_sources": data_sources,
        "data_timestamp": datetime.now().isoformat(),
        "latest_price": latest_price,
        "indicators": {
            "sma20": indicators.get('sma20'),
            "sma50": indicators.get('sma50'),
            "rsi14": indicators.get('rsi'),
            "volatility_pct": indicators.get('volatility_pct'),
            "momentum_pct": indicators.get('momentum_pct')
        },
        "missing_sources": missing_sources,
        "source_errors": source_errors,
        "parse_errors": parse_errors,
        "unsupported_clis": unsupported_clis,
        "warnings": warnings,
        "bias": bias,
        "side": effective_side,
        "signal_strength": signal_strength,
        "score": score,
        "confidence": raw_confidence,
        "entry_zone": trade_levels["entry_zone"],
        "stop_loss": trade_levels["stop_loss"],
        "take_profit": trade_levels["take_profit"],
        "risk": {
            "balance": risk_data["balance"],
            "risk_pct": risk_data["risk_pct"],
            "max_loss": risk_data["max_loss"],
            "position_size": risk_data["position_size"],
            "notional_value": risk_data["notional_value"],
            "allowed": risk_data["allowed"],
            "risk_reward_tp1": trade_levels["risk_reward_tp1"],
            "risk_reward_tp2": trade_levels["risk_reward_tp2"],
            "reason": risk_data.get("reason", "insufficient signal strength" if effective_side == "no_trade" else None)
        },
        "reasons": reasons,
        "counter_signals": counter_signals,
        "invalidations": invalidations,
        "advisory_notice": "This is an advisory plan, not financial advice. Trade at your own risk."
    }
    
    # Optionally save to decision journal
    if args.save_to_journal:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("save_plan_to_journal", "scripts/save_plan_to_journal.py")
            spj_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(spj_module)
            save_trade_plan_to_journal = spj_module.save_trade_plan_to_journal
            
            record_id = save_trade_plan_to_journal(result, args.journal_path)
            print(f"[INFO] Trade plan saved to journal with ID: {record_id}", file=sys.stderr)
        except ImportError:
            print("[WARNING] Could not save to journal - save_plan_to_journal module not available", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Failed to save to journal: {e}", file=sys.stderr)
    
    # Print formatted output
    print(format_output(result, args.format))


if __name__ == "__main__":
    main()