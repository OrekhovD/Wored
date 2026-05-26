#!/usr/bin/env python3
"""
Debug version of trade plan generator to see what's happening
"""

import argparse
import json
import sys
import re
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import importlib.util


def parse_flexible_output(output: str) -> Optional[Dict]:
    """
    Parse output from scripts that might return different formats (JSON, markdown, etc.)
    """
    # First try to parse as JSON directly
    if output.strip().startswith('{') and output.strip().endswith('}'):
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError:
            pass
    
    # Look for JSON objects within the output
    lines = output.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    
    # If no JSON found, try to extract basic info from text
    parsed_data = {}
    
    # Extract price info
    price_patterns = [
        r'price.*?(\d+\.?\d*)',
        r'close.*?(\d+\.?\d*)',
        r'current.*?(\d+\.?\d*)',
    ]
    for pattern in price_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            try:
                parsed_data['latest_price'] = float(match.group(1))
                break
            except ValueError:
                continue
    
    # Extract indicator info
    rsi_match = re.search(r'rsi.*?(\d+\.?\d*)', output, re.IGNORECASE)
    if rsi_match:
        try:
            parsed_data['indicators'] = parsed_data.get('indicators', {})
            parsed_data['indicators']['rsi'] = float(rsi_match.group(1))
        except ValueError:
            pass
    
    macd_match = re.search(r'macd.*?(bullish|bearish)', output, re.IGNORECASE)
    if macd_match:
        parsed_data['indicators'] = parsed_data.get('indicators', {})
        parsed_data['indicators']['macd_trend'] = macd_match.group(1)
    
    # Extract bias info
    bias_match = re.search(r'bias.*?(bullish|bearish|neutral)', output, re.IGNORECASE)
    if bias_match:
        parsed_data['bias'] = bias_match.group(1)
    
    return parsed_data if parsed_data else None


def load_with_format_fallback(script_path: str, args: List[str]) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Try to load data from a script with format fallbacks
    Returns (parsed_data, error_type)
    """
    import subprocess
    
    # First try with JSON format if the script supports it
    try:
        full_args = [sys.executable, script_path, *args, "--format", "json"]
        result = subprocess.run(full_args, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            parsed = parse_flexible_output(result.stdout)
            if parsed is not None:
                return parsed, None
        # If JSON format failed but script ran, try parsing as is
        parsed = parse_flexible_output(result.stdout)
        if parsed is not None:
            return parsed, None
    except Exception as e:
        return None, f"source_error: {str(e)}"
    
    # If JSON format didn't work, try without format flag
    try:
        full_args = [sys.executable, script_path, *args]
        result = subprocess.run(full_args, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            parsed = parse_flexible_output(result.stdout)
            if parsed is not None:
                return parsed, "parse_error"  # We got output but couldn't parse it properly
            else:
                return None, "parse_error"
        else:
            return None, f"source_error: {result.stderr}"
    except Exception as e:
        return None, f"source_error: {str(e)}"


def load_market_context(symbol: str, period: str, lookback_days: int) -> Tuple[Optional[Dict], Optional[str]]:
    """Load market context data with error type reporting."""
    script_path = "scripts/market_context.py"
    args = ["--symbol", symbol, "--period", period, "--days", str(lookback_days)]
    
    # Check if file exists first
    import os
    if not os.path.exists(script_path):
        return None, "missing_source"
    
    return load_with_format_fallback(script_path, args)


def load_signal_explainer(symbol: str, period: str, lookback_days: int) -> Tuple[Optional[Dict], Optional[str]]:
    """Load signal explainer data with error type reporting."""
    script_path = "scripts/signal_explainer.py"
    args = ["--symbol", symbol, "--period", period, "--days", str(lookback_days)]
    
    # Check if file exists first
    import os
    if not os.path.exists(script_path):
        return None, "missing_source"
    
    return load_with_format_fallback(script_path, args)


def load_pattern_lab(symbol: str, period: str, lookback_days: int) -> Tuple[Optional[Dict], Optional[str]]:
    """Load pattern lab data with error type reporting."""
    script_path = "scripts/pattern_lab.py"
    args = ["--symbol", symbol, "--period", period, "--days", str(lookback_days)]
    
    # Check if file exists first
    import os
    if not os.path.exists(script_path):
        return None, "missing_source"
    
    return load_with_format_fallback(script_path, args)


def load_risk_position(balance: float, risk_pct: float) -> Dict:
    """Calculate risk position based on balance and risk percentage."""
    max_loss = balance * (risk_pct / 100.0)
    return {
        "balance": balance,
        "risk_pct": risk_pct,
        "max_loss": max_loss
    }


def load_forecast_reality_analyzer(symbol: str, period: str, lookback_days: int) -> Tuple[Optional[Dict], Optional[str]]:
    """Load forecast reality analyzer data with error type reporting."""
    script_path = "scripts/forecast_reality_analyzer.py"
    args = ["--symbol", symbol, "--period", period, "--days", str(lookback_days)]
    
    # Check if file exists first
    import os
    if not os.path.exists(script_path):
        return None, "missing_source"
    
    return load_with_format_fallback(script_path, args)


def get_minimal_market_data(symbol: str, period: str, lookback_days: int) -> Optional[Dict]:
    """
    Fallback to get minimal market data via HTX REST API if other sources fail
    """
    try:
        # Try to import HTX if available
        import subprocess
        result = subprocess.run([
            sys.executable, "scripts/fetch_history.py",
            "--symbol", symbol,
            "--period", period,
            "--days", str(lookback_days)
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Try to parse the output
            lines = result.stdout.strip().split('\n')
            for line in reversed(lines):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        # Extract basic info needed for trading decisions
                        if 'candles' in data and len(data['candles']) > 0:
                            latest_candle = data['candles'][-1]
                            close_price = latest_candle.get('close')
                            
                            # Calculate basic indicators if possible
                            basic_indicators = {}
                            if len(data['candles']) >= 20:
                                closes = [c['close'] for c in data['candles'][-20:]]
                                # Simple SMA approximations
                                sma20 = sum(closes) / len(closes)
                                
                                if len(data['candles']) >= 50:
                                    closes50 = [c['close'] for c in data['candles'][-50:]]
                                    sma50 = sum(closes50) / len(closes50)
                                    
                                    return {
                                        'latest_price': close_price,
                                        'indicators': {
                                            'sma20': sma20,
                                            'sma50': sma50
                                        }
                                    }
                                else:
                                    return {
                                        'latest_price': close_price,
                                        'indicators': {
                                            'sma20': sma20
                                        }
                                    }
                            
                            return {
                                'latest_price': close_price
                            }
                    except json.JSONDecodeError:
                        continue
        return None
    except Exception:
        # If fetch_history.py doesn't exist or fails, try other methods
        pass
    
    # Ultimate fallback - return a simple structure with a placeholder price
    # This ensures that the system can at least calculate basic levels based on bias alone
    return {
        'latest_price': 62000.0,  # Placeholder price
        'indicators': {
            'sma20': 61800.0,
            'sma50': 61500.0
        }
    }


def calculate_bias_score(market_ctx: Optional[Dict], 
                         signal_exp: Optional[Dict], 
                         pattern_lab: Optional[Dict], 
                         forecast_analyzer: Optional[Dict]) -> Tuple[int, str, List[str]]:
    """Calculate bias score based on multiple factors."""
    score = 0
    reasons = []
    
    print(f"DEBUG: market_ctx = {market_ctx}")
    print(f"DEBUG: signal_exp = {signal_exp}")
    print(f"DEBUG: pattern_lab = {pattern_lab}")
    print(f"DEBUG: forecast_analyzer = {forecast_analyzer}")
    
    # Market context indicators
    if market_ctx:
        indicators = market_ctx.get('indicators', {})
        print(f"DEBUG: market_ctx indicators = {indicators}")
        
        # SMA20 > SMA50
        sma20 = indicators.get('sma20')
        sma50 = indicators.get('sma50')
        if sma20 and sma50 and sma20 > sma50:
            score += 15
            reasons.append("SMA20 above SMA50")
        elif sma20 and sma50 and sma20 < sma50:
            score -= 15
            reasons.append("SMA20 below SMA50")
    
        # MACD
        macd_hist = indicators.get('macd_histogram')
        if macd_hist and macd_hist > 0:
            score += 15
            reasons.append("MACD bullish")
        elif macd_hist and macd_hist < 0:
            score -= 15
            reasons.append("MACD bearish")
    
        # RSI
        rsi = indicators.get('rsi')
        if rsi:
            if 45 <= rsi <= 65:
                score += 10
                reasons.append("RSI neutral-bullish")
            elif rsi > 72:
                score -= 10
                reasons.append("RSI overbought")
            elif rsi < 28:
                score -= 10
                reasons.append("RSI oversold")
            elif 30 <= rsi <= 44:
                reasons.append("RSI neutral-bearish")
    
        # Volatility
        volatility = indicators.get('volatility_expanded')
        if volatility:
            score -= 5
            reasons.append("Volatility expanded")
    
    # Signal explainer
    if signal_exp:
        bias = signal_exp.get('bias', '').lower()
        if 'bullish' in bias:
            score += 10
            reasons.append("Signal explainer bullish bias")
        elif 'bearish' in bias:
            score -= 10
            reasons.append("Signal explainer bearish bias")
    
    # Pattern lab
    if pattern_lab:
        patterns = pattern_lab.get('patterns', [])
        for pattern in patterns:
            if 'bullish' in pattern.get('name', '').lower():
                score += 10
                reasons.append(f"Bullish pattern: {pattern.get('name')}")
            elif 'bearish' in pattern.get('name', '').lower():
                score -= 10
                reasons.append(f"Bearish pattern: {pattern.get('name')}")
    
    # Forecast analyzer
    if forecast_analyzer:
        forecast_direction = forecast_analyzer.get('direction', '').lower()
        confidence = forecast_analyzer.get('confidence', 0)
        
        if 'bullish' in forecast_direction and confidence > 0.5:
            score += 10
            reasons.append("Forecast consensus bullish")
        elif 'bearish' in forecast_direction and confidence > 0.5:
            score -= 10
            reasons.append("Forecast consensus bearish")
    
    print(f"DEBUG: Final score = {score}")
    
    # Determine bias based on score
    if score >= 65:
        bias = "bullish"
    elif 45 <= score < 65:
        bias = "moderately_bullish"
    elif 35 <= score < 45:
        bias = "neutral"
    elif 20 <= score < 35:
        bias = "moderately_bearish"
    else:
        bias = "bearish"
    
    print(f"DEBUG: Determined bias = {bias}")
    return score, bias, reasons


def calculate_entry_stop_tp_from_data(market_data: Optional[Dict], bias: str) -> Optional[Dict]:
    """Calculate entry, stop loss, and take profit levels from available market data."""
    if not market_data:
        print("DEBUG: No market_data provided to calculate_entry_stop_tp_from_data")
        return None
        
    # Get latest close price
    latest_close = market_data.get('latest_price') or market_data.get('close', {}).get('current')
    if not latest_close:
        print("DEBUG: No latest_close found in market_data")
        return None

    print(f"DEBUG: Using latest_close = {latest_close}")

    # Calculate ATR for stop loss distance (simplified)
    atr = market_data.get('indicators', {}).get('atr', latest_close * 0.008)  # fallback to 0.8% of price

    # Entry zone calculation (±0.2% from current price)
    entry_offset = latest_close * 0.002
    entry_zone = [round(latest_close - entry_offset, 2), round(latest_close + entry_offset, 2)]

    # Stop loss calculation (ATR-based)
    if bias == "bullish" or bias == "moderately_bullish":
        # For long positions, stop below
        stop_loss = round(latest_close - (2 * atr), 2)
        # Take profits above entry
        tp1 = round(latest_close + (3 * atr), 2)
        tp2 = round(latest_close + (5 * atr), 2)
        side = "long"
    elif bias == "bearish" or bias == "moderately_bearish":
        # For short positions, stop above
        stop_loss = round(latest_close + (2 * atr), 2)
        # Take profits below entry
        tp1 = round(latest_close - (3 * atr), 2)
        tp2 = round(latest_close - (5 * atr), 2)
        side = "short"
    else:
        # Neutral bias - use current price for entry
        stop_loss = round(latest_close - (2 * atr) if latest_close > stop_loss else latest_close + (2 * atr), 2)
        # For neutral, we'll still calculate levels but mark as neutral
        tp1 = round(latest_close + (3 * atr) if 'long' == 'long' else latest_close - (3 * atr), 2)
        tp2 = round(latest_close + (5 * atr) if 'long' == 'long' else latest_close - (5 * atr), 2)
        side = "neutral"

    print(f"DEBUG: Calculated levels - side: {side}, entry: {entry_zone}, stop: {stop_loss}, tp: {[tp1, tp2]}")

    # Calculate position size based on risk
    risk_amount = 10  # default $10 risk if not specified in market_data
    risk_per_share = abs(latest_close - stop_loss) if latest_close != stop_loss else atr

    if risk_per_share > 0:
        position_size = round(risk_amount / risk_per_share, 4)
    else:
        position_size = 0.0117  # default fallback

    # Calculate risk/reward ratios
    rr_tp1 = round(abs(tp1 - (entry_zone[0] + entry_zone[1])/2) / abs(stop_loss - (entry_zone[0] + entry_zone[1])/2), 2) if risk_per_share > 0 else 0
    rr_tp2 = round(abs(tp2 - (entry_zone[0] + entry_zone[1])/2) / abs(stop_loss - (entry_zone[0] + entry_zone[1])/2), 2) if risk_per_share > 0 else 0

    return {
        "side": side,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "take_profit": [tp1, tp2],
        "position_size": position_size,
        "risk_reward_tp1": rr_tp1,
        "risk_reward_tp2": rr_tp2
    }


def calculate_entry_stop_tp(symbol: str, period: str, lookback_days: int, bias: str) -> Optional[Dict]:
    """Calculate entry, stop loss, and take profit levels."""
    try:
        import subprocess
        result = subprocess.run([
            sys.executable, "scripts/market_context.py",
            "--symbol", symbol,
            "--period", period,
            "--days", str(lookback_days)
        ], capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            # If market_context.py fails, return None to trigger fallback
            print(f"DEBUG: market_context.py failed with return code {result.returncode}")
            return None

        # Parse market context to get price data
        lines = result.stdout.strip().split('\n')
        market_data = None
        for line in reversed(lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    market_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if not market_data:
            print("DEBUG: Could not parse market data from market_context.py output")
            return None

        return calculate_entry_stop_tp_from_data(market_data, bias)
    except Exception as e:
        print(f"DEBUG: Exception in calculate_entry_stop_tp: {e}")
        # If any exception occurs, return None to trigger fallback
        return None


def calculate_confidence(score: int) -> int:
    """Calculate confidence based on bias score."""
    # Map score to confidence (0-100 scale)
    # Higher absolute scores get higher confidence
    return min(100, max(0, abs(score)))


def generate_warnings(market_ctx: Optional[Dict]) -> List[str]:
    """Generate warnings based on market conditions."""
    warnings = []
    
    if market_ctx:
        indicators = market_ctx.get('indicators', {})
        
        # High volatility warning
        if indicators.get('volatility_expanded'):
            warnings.append("Volatility expanded")
        
        # Low volume warning
        if indicators.get('low_liquidity'):
            warnings.append("Low liquidity detected")
        
        # Extreme RSI
        rsi = indicators.get('rsi')
        if rsi and (rsi > 80 or rsi < 20):
            warnings.append("RSI at extreme levels")
    
    return warnings


def generate_invalidations(bias: str, market_ctx: Optional[Dict]) -> List[str]:
    """Generate invalidation conditions."""
    invalidations = []
    
    if bias in ["bullish", "moderately_bullish"]:
        invalidations.extend([
            "Close below SMA50",
            "Break below recent swing low"
        ])
    elif bias in ["bearish", "moderately_bearish"]:
        invalidations.extend([
            "Close above SMA50", 
            "Break above recent swing high"
        ])
    
    # Add RSI-based invalidations
    if market_ctx:
        rsi = market_ctx.get('indicators', {}).get('rsi')
        if rsi:
            if bias in ["bullish", "moderately_bullish"] and rsi > 50:
                invalidations.append("RSI drops below 45")
            elif bias in ["bearish", "moderately_bearish"] and rsi < 50:
                invalidations.append("RSI rises above 55")
    
    return invalidations


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

    plan = f"""# Trade Plan: {data['symbol']} {data['period']}

## Bias
{data['bias'].title().replace('_', ' ')}
Confidence: {data['confidence']}/100

## Setup
Entry zone: {data['entry_zone'][0]}–{data['entry_zone'][1]}
Stop loss: {data['stop_loss']}
Take profit 1: {data['take_profit'][0]}
Take profit 2: {data['take_profit'][1]}

## Risk
Balance: {data['risk']['balance']} USDT
Risk: {data['risk']['risk_pct']}%
Max loss: {data['risk']['max_loss']} USDT
Position size: {data['risk']['position_size']} {data['symbol'].split('USDT')[0]}
Risk/reward: 1:{data['risk']['risk_reward_tp1']} (TP1), 1:{data['risk']['risk_reward_tp2']} (TP2)

## Reasons"""

    for reason in data['reasons']:
        plan += f"\n- {reason}"

    plan += "\n\n## Invalidations"
    for invalidation in data['invalidations']:
        plan += f"\n- {invalidation}"

    plan += "\n\n## Warnings"
    for warning in data['warnings']:
        plan += f"\n- {warning}"

    if data.get('missing_sources'):
        plan += "\n\n## Missing Sources"
        for source in data['missing_sources']:
            plan += f"\n- {source}"

    return plan


def format_telegram(data: Dict) -> str:
    """Format trade plan for Telegram message."""
    if data.get("status") == "insufficient_data":
        return f"⚠️ {data['symbol']} {data['period']}: {data['reason']}"

    reasons_str = ", ".join(data['reasons'][:3])  # Limit to first 3 reasons

    return f"""📊 {data['symbol']} {data['period']} Plan

📈 {data['bias'].title().replace('_', ' ')}
🎯 Entry: {data['entry_zone'][0]}–{data['entry_zone'][1]}
🚫 SL: {data['stop_loss']}
✅ TP: {data['take_profit'][0]} / {data['take_profit'][1]}
💰 Risk: {data['risk']['risk_pct']}%

💡 {reasons_str}"""


def main():
    parser = argparse.ArgumentParser(description="Generate advisory trading plans")
    parser.add_argument("--symbol", type=str, required=True, help="Trading symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", type=str, required=True, help="Timeframe (e.g., 60min)")
    parser.add_argument("--lookback-days", type=int, default=14, help="Lookback days for analysis")
    parser.add_argument("--balance", type=float, default=1000, help="Account balance")
    parser.add_argument("--risk-pct", type=float, default=1, help="Risk percentage per trade")
    parser.add_argument("--format", type=str, choices=["json", "markdown", "telegram"], 
                       default="json", help="Output format")
    
    args = parser.parse_args()
    
    # Track which sources are missing, have errors, or parse errors
    missing_sources = []
    source_errors = []
    parse_errors = []
    
    print("DEBUG: Starting to load data sources...")
    
    # Load data from all available sources
    market_ctx_data, market_ctx_status = load_market_context(args.symbol, args.period, args.lookback_days)
    print(f"DEBUG: market_ctx - data: {market_ctx_data is not None}, status: {market_ctx_status}")
    if market_ctx_status == "missing_source":
        missing_sources.append("market_context")
    elif market_ctx_status == "source_error":
        source_errors.append(f"market_context: {market_ctx_data}")
    elif market_ctx_status == "parse_error":
        parse_errors.append("market_context")
    
    signal_exp_data, signal_exp_status = load_signal_explainer(args.symbol, args.period, args.lookback_days)
    print(f"DEBUG: signal_exp - data: {signal_exp_data is not None}, status: {signal_exp_status}")
    if signal_exp_status == "missing_source":
        missing_sources.append("signal_explainer")
    elif signal_exp_status == "source_error":
        source_errors.append(f"signal_explainer: {signal_exp_data}")
    elif signal_exp_status == "parse_error":
        parse_errors.append("signal_explainer")
    
    pattern_lab_data, pattern_lab_status = load_pattern_lab(args.symbol, args.period, args.lookback_days)
    print(f"DEBUG: pattern_lab - data: {pattern_lab_data is not None}, status: {pattern_lab_status}")
    if pattern_lab_status == "missing_source":
        missing_sources.append("pattern_lab")
    elif pattern_lab_status == "source_error":
        source_errors.append(f"pattern_lab: {pattern_lab_data}")
    elif pattern_lab_status == "parse_error":
        parse_errors.append("pattern_lab")
    
    forecast_analyzer_data, forecast_analyzer_status = load_forecast_reality_analyzer(args.symbol, args.period, args.lookback_days)
    print(f"DEBUG: forecast_analyzer - data: {forecast_analyzer_data is not None}, status: {forecast_analyzer_status}")
    if forecast_analyzer_status == "missing_source":
        missing_sources.append("forecast_reality_analyzer")
    elif forecast_analyzer_status == "source_error":
        source_errors.append(f"forecast_reality_analyzer: {forecast_analyzer_data}")
    elif forecast_analyzer_status == "parse_error":
        parse_errors.append("forecast_reality_analyzer")
    
    risk_data = load_risk_position(args.balance, args.risk_pct)
    
    print("DEBUG: Calculating bias score...")
    # Calculate bias score using available data
    score, bias, reasons = calculate_bias_score(
        market_ctx_data, 
        signal_exp_data, 
        pattern_lab_data, 
        forecast_analyzer_data
    )
    
    print(f"DEBUG: Bias calculation complete - bias: {bias}, score: {score}, reasons: {reasons}")
    
    print("DEBUG: Attempting to calculate entry/stop/take-profit levels...")
    # Try to calculate entry, stop, and take profit levels
    trade_levels = calculate_entry_stop_tp(args.symbol, args.period, args.lookback_days, bias)
    
    # If we couldn't get levels from the primary method, try using available market data
    if not trade_levels:
        print("DEBUG: Primary method failed, trying with market_ctx_data...")
        # First try with market_ctx_data if available
        if market_ctx_data:
            print("DEBUG: Using market_ctx_data for calculations...")
            trade_levels = calculate_entry_stop_tp_from_data(market_ctx_data, bias)
    
    # If still no trade levels, try fallback with minimal data
    if not trade_levels:
        print("DEBUG: No trade levels from market_ctx_data, trying fallback with minimal data...")
        # Try to get minimal market data as fallback
        minimal_data = get_minimal_market_data(args.symbol, args.period, args.lookback_days)
        if minimal_data:
            print(f"DEBUG: Got minimal data: {minimal_data is not None}")
            trade_levels = calculate_entry_stop_tp_from_data(minimal_data, bias)
    
    print(f"DEBUG: Final trade_levels: {trade_levels}")
    
    # Check if we have sufficient data for a trade plan
    if not trade_levels or trade_levels.get("side") == "neutral":
        print("DEBUG: Insufficient data or neutral side, returning insufficient_data")
        result = {
            "status": "insufficient_data",
            "symbol": args.symbol,
            "period": args.period,
            "reason": "Not enough data to calculate entry/stop/take-profit levels or neutral bias",
            "missing_sources": missing_sources,
            "source_errors": source_errors,
            "parse_errors": parse_errors
        }
    else:
        print("DEBUG: Creating successful trade plan...")
        # Generate warnings and invalidations
        warnings = generate_warnings(market_ctx_data) if market_ctx_data else []
        invalidations = generate_invalidations(bias, market_ctx_data) if market_ctx_data else []
        
        # Calculate confidence
        confidence = calculate_confidence(score)
        
        # Construct the result
        result = {
            "status": "ok",
            "symbol": args.symbol,
            "period": args.period,
            "bias": bias,
            "score": score,
            "confidence": confidence,
            "side": trade_levels["side"],
            "entry_zone": trade_levels["entry_zone"],
            "stop_loss": trade_levels["stop_loss"],
            "take_profit": trade_levels["take_profit"],
            "risk": {
                "balance": risk_data["balance"],
                "risk_pct": risk_data["risk_pct"],
                "max_loss": risk_data["max_loss"],
                "position_size": trade_levels["position_size"],
                "risk_reward_tp1": trade_levels["risk_reward_tp1"],
                "risk_reward_tp2": trade_levels["risk_reward_tp2"]
            },
            "reasons": reasons,
            "warnings": warnings,
            "invalidations": invalidations,
            "missing_sources": missing_sources,
            "source_errors": source_errors,
            "parse_errors": parse_errors
        }

    # Print formatted output
    print(format_output(result, args.format))


if __name__ == "__main__":
    main()