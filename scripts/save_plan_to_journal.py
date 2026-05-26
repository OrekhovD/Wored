#!/usr/bin/env python3
"""
Integration script to save trade plans to Decision Journal
"""

import json
import sys
import argparse
from datetime import datetime
from pathlib import Path


def save_trade_plan_to_journal(trade_plan: dict, journal_path: str = "data/decision_journal.jsonl"):
    """
    Save a trade plan to the decision journal
    """
    # Import DecisionJournal class
    import importlib.util
    spec = importlib.util.spec_from_file_location("decision_journal", "scripts/decision_journal.py")
    dj_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dj_module)
    DecisionJournal = dj_module.DecisionJournal
    
    journal = DecisionJournal(journal_path)
    
    # Prepare record from trade plan
    record = {
        "symbol": trade_plan.get("symbol"),
        "period": trade_plan.get("period"),
        "bias": trade_plan.get("bias"),
        "confidence": trade_plan.get("confidence"),
        "entry_zone": trade_plan.get("entry_zone"),
        "stop_loss": trade_plan.get("stop_loss"),
        "take_profit": trade_plan.get("take_profit"),
        "risk_pct": trade_plan.get("risk", {}).get("risk_pct", 1.0),
        "position_size": trade_plan.get("risk", {}).get("position_size", 0.0),
        "status": "planned",
        "source": {
            "generator": "trade_plan_generator",
            "data_quality": trade_plan.get("data_quality", "unknown"),
            "side": trade_plan.get("side", "unknown")
        }
    }
    
    # Add additional information
    if "reasons" in trade_plan:
        record["reasons"] = trade_plan["reasons"]
    if "invalidations" in trade_plan:
        record["invalidations"] = trade_plan["invalidations"]
    if "counter_signals" in trade_plan:
        record["counter_signals"] = trade_plan["counter_signals"]
    if "score" in trade_plan:
        record["score"] = trade_plan["score"]
    if "signal_strength" in trade_plan:
        record["signal_strength"] = trade_plan["signal_strength"]
    if "warnings" in trade_plan:
        record["warnings"] = trade_plan["warnings"]
    if "indicators" in trade_plan:
        record["indicators"] = trade_plan["indicators"]
    
    # Save to journal
    record_id = journal.save_record(record)
    return record_id


def main():
    parser = argparse.ArgumentParser(description="Save trade plan to Decision Journal")
    parser.add_argument("--input-file", type=str, help="Input file with trade plan JSON")
    parser.add_argument("--journal-path", type=str, default="data/decision_journal.jsonl",
                       help="Path to the decision journal file")
    parser.add_argument("--stdin", action="store_true", help="Read trade plan from stdin")
    
    args = parser.parse_args()
    
    # Get trade plan data
    if args.stdin:
        # Read from stdin
        trade_plan_str = sys.stdin.read().strip()
        trade_plan = json.loads(trade_plan_str)
    elif args.input_file:
        # Read from file
        with open(args.input_file, 'r') as f:
            trade_plan = json.load(f)
    else:
        print("Error: Either --input-file or --stdin must be specified", file=sys.stderr)
        sys.exit(1)
    
    # Save to journal
    try:
        record_id = save_trade_plan_to_journal(trade_plan, args.journal_path)
        print(f"Trade plan saved to journal with ID: {record_id}")
        
    except Exception as e:
        print(f"Error saving trade plan to journal: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()