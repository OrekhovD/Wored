#!/usr/bin/env python3

import argparse
import json
import sys
from typing import Optional, Dict, Any

# --- CONFIG ---
DEFAULT_BALANCE = 1000.0
DEFAULT_RISK_PCT = 1.0
DEFAULT_ENTRY = 62000.0
DEFAULT_STOP_LOSS = 61000.0
DEFAULT_TAKE_PROFIT = 65000.0


def validate_inputs(
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
) -> Optional[str]:
    if balance <= 0:
        return "balance must be > 0"
    if risk_pct <= 0 or risk_pct > 100:
        return "risk_pct must be between 0.01 and 100"
    if entry <= 0:
        return "entry must be > 0"
    if stop_loss <= 0:
        return "stop_loss must be > 0"
    if take_profit <= 0:
        return "take_profit must be > 0"
    if stop_loss >= entry:
        return "stop_loss must be < entry"
    if take_profit <= entry:
        return "take_profit must be > entry"
    return None


def calculate_risk_position(
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
) -> Dict[str, Any]:
    # Position size
    risk_amount = balance * (risk_pct / 100)
    loss_per_unit = abs(entry - stop_loss)
    position_size = risk_amount / loss_per_unit if loss_per_unit != 0 else 0.0

    # Max loss
    max_loss = risk_amount

    # Risk/reward ratio
    gain_per_unit = abs(take_profit - entry)
    risk_reward = gain_per_unit / loss_per_unit if loss_per_unit != 0 else None

    # Warnings
    warnings = []
    if loss_per_unit == 0:
        warnings.append("invalid_stop")

    return {
        "position_size": round(position_size, 4),
        "max_loss": round(max_loss, 2),
        "risk_reward": round(risk_reward, 2) if risk_reward is not None else None,
        "warnings": warnings,
        "secrets_printed": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Risk Position Calculator")
    parser.add_argument("--balance", type=float, default=DEFAULT_BALANCE, help="Account balance (USD)")
    parser.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT, help="Risk per trade (%)")
    parser.add_argument("--entry", type=float, default=DEFAULT_ENTRY, help="Entry price")
    parser.add_argument("--stop", type=float, default=DEFAULT_STOP_LOSS, help="Stop loss price")
    parser.add_argument("--take", type=float, default=DEFAULT_TAKE_PROFIT, help="Take profit price")
    args = parser.parse_args()

    # Validate inputs
    error = validate_inputs(
        args.balance,
        args.risk_pct,
        args.entry,
        args.stop,
        args.take,
    )
    if error:
        result = {
            "error": error,
            "secrets_printed": False,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1)

    # Calculate
    result = calculate_risk_position(
        args.balance,
        args.risk_pct,
        args.entry,
        args.stop,
        args.take,
    )

    # Output
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
