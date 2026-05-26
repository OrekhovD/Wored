#!/usr/bin/env python3
"""
Trade Plan Outcome Evaluator for WORED
Evaluates trade plans against actual market movements after a specified time period
"""

import json
import sys
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path


class PlanEvaluator:
    def __init__(self, journal_path: str = "data/decision_journal.jsonl"):
        self.journal_path = journal_path
    
    def evaluate_plan(self, plan_id: str, hours_after: int = 24) -> Dict[str, Any]:
        """
        Evaluate a trade plan against actual market movements after N hours
        """
        # Load the plan from journal
        plan = self.get_plan_from_journal(plan_id)
        if not plan:
            raise ValueError(f"Plan with ID {plan_id} not found in journal")
        
        # In a real implementation, we would:
        # 1. Get the market data at the time of plan creation
        # 2. Get the market data after N hours
        # 3. Compare actual movements to the plan's predictions
        
        # For simulation, we'll create a mock evaluation
        evaluation = {
            "plan_id": plan_id,
            "evaluated_at": datetime.utcnow().isoformat() + "Z",
            "hours_after": hours_after,
            "original_plan": plan,
            "actual_movement": {
                "entry_reached": True,  # Whether the entry zone was reached
                "stop_loss_hit": False,  # Whether stop loss was triggered
                "take_profit_1_hit": True,  # Whether TP1 was reached
                "take_profit_2_hit": False,  # Whether TP2 was reached
                "final_price": 63500.0,  # Final price after N hours
                "max_favorable_excursion": 0.02,  # Max favorable movement
                "max_adverse_excursion": -0.01   # Max adverse movement
            },
            "result": {
                "outcome": "hit_tp",  # hit_tp, hit_sl, expired, invalidated
                "profit_loss": 150.0,  # Profit/loss in USD
                "return_pct": 2.5,  # Return percentage
                "r_multiple": 1.5   # Risk-reward multiple achieved
            }
        }
        
        return evaluation
    
    def get_plan_from_journal(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific plan from the journal"""
        if not Path(self.journal_path).exists():
            return None
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if record.get("id") == plan_id:
                        return record
        return None
    
    def batch_evaluate(self, lookback_days: int = 14) -> Dict[str, Any]:
        """
        Evaluate all eligible plans from the last N days
        """
        if not Path(self.journal_path).exists():
            return {
                "evaluated": 0,
                "hit_tp": 0,
                "hit_sl": 0,
                "expired": 0,
                "win_rate": 0.0,
                "avg_r_multiple": 0.0,
                "best_symbol": "",
                "worst_pattern": ""
            }
        
        # Load all plans from journal
        all_plans = []
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    all_plans.append(record)
        
        # Filter plans that are old enough to evaluate
        cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
        evaluable_plans = []
        
        for plan in all_plans:
            created_at = plan.get("created_at", "")
            if created_at:
                try:
                    # Parse date string (format: 2026-05-02T14:13:28.250182Z)
                    plan_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if plan_date < cutoff_date:
                        evaluable_plans.append(plan)
                except ValueError:
                    continue  # Skip if date format is invalid
        
        # Simulate evaluation results
        results = {
            "evaluated": len(evaluable_plans),
            "hit_tp": 0,
            "hit_sl": 0,
            "expired": len(evaluable_plans),  # In this simulation, most plans expire
            "win_rate": 0.0,
            "avg_r_multiple": 0.0,
            "best_symbol": "",
            "worst_pattern": ""
        }
        
        return results


def main():
    parser = argparse.ArgumentParser(description="Trade Plan Outcome Evaluator for WORED")
    parser.add_argument("--journal-path", type=str, default="data/decision_journal.jsonl",
                       help="Path to the decision journal file")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Evaluate single plan command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a single trade plan")
    eval_parser.add_argument("--id", type=str, required=True, help="Plan ID to evaluate")
    eval_parser.add_argument("--hours-after", type=int, default=24, help="Hours after plan creation to evaluate")
    
    # Batch evaluate command
    batch_parser = subparsers.add_parser("batch-evaluate", help="Evaluate all eligible plans")
    batch_parser.add_argument("--lookback-days", type=int, default=14, help="Days to look back for plans")
    
    args = parser.parse_args()
    
    evaluator = PlanEvaluator(args.journal_path)
    
    if args.command == "evaluate":
        try:
            evaluation = evaluator.evaluate_plan(args.id, args.hours_after)
            print(json.dumps(evaluation, indent=2))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    
    elif args.command == "batch-evaluate":
        results = evaluator.batch_evaluate(args.lookback_days)
        print(json.dumps(results, indent=2))
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()