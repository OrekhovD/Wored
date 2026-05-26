#!/usr/bin/env python3
"""
Trade Plan Outcome Evaluator for WORED
Evaluates trade plans against actual market movements after a time period
"""

import json
import sys
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path


class TradePlanEvaluator:
    def __init__(self, journal_path: str = "data/decision_journal.jsonl"):
        self.journal_path = journal_path
    
    def get_completed_plans(self, lookback_days: int = 14) -> List[Dict[str, Any]]:
        """Get trade plans that were made N days ago and haven't been evaluated yet"""
        cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
        
        completed_plans = []
        if not Path(self.journal_path).exists():
            return completed_plans
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    
                    # Only evaluate plans that are older than lookback_days
                    created_at = datetime.fromisoformat(record.get("created_at").replace("Z", "+00:00"))
                    if created_at < cutoff_date:
                        # Only evaluate if it hasn't been evaluated yet
                        if "actual_results" not in record:
                            completed_plans.append(record)
        
        return completed_plans
    
    def evaluate_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a trade plan against actual market movements"""
        # This is a simplified evaluator - in real implementation would fetch actual prices
        # For demo purposes, we'll simulate the evaluation
        
        # Get entry zone and stop loss
        entry_zone = plan.get("entry_zone")
        stop_loss = plan.get("stop_loss")
        take_profits = plan.get("take_profit", [])
        
        if not entry_zone or not stop_loss:
            return {
                "status": "insufficient_data",
                "reason": "Missing entry_zone or stop_loss"
            }
        
        # Simulate actual market movement based on historical data
        # In real implementation, this would fetch actual prices after the plan was made
        actual_entry = (entry_zone[0] + entry_zone[1]) / 2  # Midpoint of entry zone
        
        # For simulation, let's assume some market movement
        import random
        movement_factor = random.uniform(-0.05, 0.05)  # -5% to +5% movement
        actual_exit = actual_entry * (1 + movement_factor)
        
        # Determine outcome based on movement
        outcome = self._determine_outcome(actual_entry, actual_exit, stop_loss, take_profits)
        
        # Calculate profit/loss
        position_size = plan.get("position_size", 0)
        profit_loss = (actual_exit - actual_entry) * position_size if position_size > 0 else 0
        
        return {
            "actual_entry": actual_entry,
            "actual_exit": actual_exit,
            "profit_loss": profit_loss,
            "outcome": outcome,
            "evaluation_date": datetime.utcnow().isoformat() + "Z"
        }
    
    def _determine_outcome(self, actual_entry: float, actual_exit: float, stop_loss: float, take_profits: List[float]) -> str:
        """Determine the outcome of a trade plan"""
        if actual_exit <= stop_loss:
            return "hit_sl"  # Hit stop loss
        elif take_profits and actual_exit >= max(take_profits):
            return "hit_tp"  # Hit take profit
        else:
            # Determine if it was a valid trade based on direction
            if actual_exit > actual_entry:
                # If exit was higher than entry, it was a good long trade or bad short trade
                return "profitable" if "long" in self._get_trade_direction() else "loss"
            else:
                # If exit was lower than entry, it was a good short trade or bad long trade
                return "profitable" if "short" in self._get_trade_direction() else "loss"
    
    def _get_trade_direction(self) -> str:
        """Helper to determine trade direction - in real implementation would be based on plan data"""
        # This is a placeholder - would be determined from the plan's bias and side
        return "long"  # Default assumption for demo
    
    def update_plan_evaluation(self, plan_id: str, evaluation_result: Dict[str, Any]) -> bool:
        """Update a plan record with evaluation results"""
        # Read all records
        all_records = []
        target_found = False
        
        if Path(self.journal_path).exists():
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        if record.get("id") == plan_id:
                            # Update this record with evaluation results
                            record["actual_results"] = evaluation_result
                            record["evaluated_at"] = evaluation_result.get("evaluation_date")
                            
                            # Update status based on outcome
                            outcome = evaluation_result.get("outcome", "")
                            if outcome == "hit_tp":
                                record["status"] = "hit_tp"
                            elif outcome == "hit_sl":
                                record["status"] = "hit_sl"
                            else:
                                record["status"] = "expired"  # Plan expired without hitting targets
                        
                        all_records.append(record)
                        if record.get("id") == plan_id:
                            target_found = True
        else:
            return False
        
        if not target_found:
            return False
        
        # Write all records back to file
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for record in all_records:
                f.write(json.dumps(record) + "\n")
        
        return True
    
    def generate_scorecard(self, lookback_days: int = 90) -> Dict[str, Any]:
        """Generate a scorecard for evaluated plans"""
        cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
        
        evaluated_plans = []
        if not Path(self.journal_path).exists():
            return {
                "evaluated_plans": 0,
                "win_rate": 0,
                "avg_r_multiple": 0,
                "total_pnl": 0
            }
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    
                    # Only include plans that have been evaluated
                    if "actual_results" in record:
                        created_at = datetime.fromisoformat(record.get("created_at").replace("Z", "+00:00"))
                        if created_at.replace(tzinfo=None) > cutoff_date:
                            evaluated_plans.append(record)
        
        # Calculate metrics
        if not evaluated_plans:
            return {
                "evaluated_plans": 0,
                "win_rate": 0,
                "avg_r_multiple": 0,
                "total_pnl": 0,
                "plans": []
            }
        
        wins = 0
        total_pnl = 0
        r_multiples = []
        
        for plan in evaluated_plans:
            actual_results = plan.get("actual_results", {})
            outcome = actual_results.get("outcome", "")
            pnl = actual_results.get("profit_loss", 0)
            
            if outcome in ["hit_tp", "profitable"]:
                wins += 1
            
            total_pnl += pnl
            
            # Calculate R multiple if possible
            if "risk" in plan and plan["risk"].get("max_loss"):
                max_loss = plan["risk"]["max_loss"]
                if max_loss != 0:
                    r_multiple = pnl / max_loss
                    r_multiples.append(r_multiple)
        
        win_rate = wins / len(evaluated_plans) if evaluated_plans else 0
        avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        
        return {
            "evaluated_plans": len(evaluated_plans),
            "win_rate": round(win_rate * 100, 2),
            "avg_r_multiple": round(avg_r_multiple, 2),
            "total_pnl": round(total_pnl, 2),
            "plans": evaluated_plans
        }


def main():
    parser = argparse.ArgumentParser(description="Trade Plan Outcome Evaluator for WORED")
    parser.add_argument("--journal-path", type=str, default="data/decision_journal.jsonl",
                       help="Path to the decision journal file")
    parser.add_argument("--lookback-days", type=int, default=14,
                       help="Number of days to look back for evaluation")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate trade plans")
    eval_parser.add_argument("--plan-id", type=str, help="Specific plan ID to evaluate (optional - if not provided, evaluates all eligible plans)")
    
    # Scorecard command
    score_parser = subparsers.add_parser("scorecard", help="Generate performance scorecard")
    score_parser.add_argument("--days", type=int, default=90, help="Number of days for scorecard")
    
    args = parser.parse_args()
    
    evaluator = TradePlanEvaluator(args.journal_path)
    
    if args.command == "evaluate":
        if args.plan_id:
            # Evaluate specific plan
            plan = None
            if Path(args.journal_path).exists():
                with open(args.journal_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            if record.get("id") == args.plan_id:
                                plan = record
                                break
            
            if plan:
                evaluation = evaluator.evaluate_plan(plan)
                if evaluator.update_plan_evaluation(args.plan_id, evaluation):
                    print(f"Evaluation completed for plan {args.plan_id}")
                    print(json.dumps(evaluation, indent=2))
                else:
                    print(f"Failed to update evaluation for plan {args.plan_id}")
            else:
                print(f"Plan with ID {args.plan_id} not found")
        else:
            # Evaluate all eligible plans
            plans = evaluator.get_completed_plans(args.lookback_days)
            print(f"Found {len(plans)} plans to evaluate")
            
            for plan in plans:
                evaluation = evaluator.evaluate_plan(plan)
                if evaluator.update_plan_evaluation(plan["id"], evaluation):
                    print(f"Evaluation completed for plan {plan['id']}: {evaluation['outcome']}")
                else:
                    print(f"Failed to update evaluation for plan {plan['id']}")
    
    elif args.command == "scorecard":
        scorecard = evaluator.generate_scorecard(args.days)
        print(json.dumps(scorecard, indent=2))
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()