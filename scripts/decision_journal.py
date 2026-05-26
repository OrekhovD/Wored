#!/usr/bin/env python3
"""
Decision Journal for WORED
Manages trade plan records and their subsequent evaluation
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any
import argparse


class DecisionJournal:
    def __init__(self, journal_path: str = "data/decision_journal.jsonl"):
        self.journal_path = journal_path
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
    
    def _generate_id(self, symbol: str, timestamp: Optional[str] = None) -> str:
        """Generate unique ID for decision record"""
        if timestamp is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"decision_{timestamp}_{symbol}"
    
    def save_record(self, record: Dict[str, Any]) -> str:
        """Save a decision record to the journal"""
        # Validate required fields
        required_fields = ["symbol", "period", "bias", "confidence", "status"]
        for field in required_fields:
            if field not in record:
                raise ValueError(f"Missing required field: {field}")
        
        # Set default values if not provided
        if "id" not in record:
            record["id"] = self._generate_id(record["symbol"])
        
        if "created_at" not in record:
            record["created_at"] = datetime.utcnow().isoformat() + "Z"
        
        if "source" not in record:
            record["source"] = {}
        
        # Append to JSONL file
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        
        return record["id"]
    
    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific decision record by ID"""
        if not os.path.exists(self.journal_path):
            return None
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if record.get("id") == record_id:
                        return record
        return None
    
    def get_records_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Retrieve all records for a specific symbol"""
        records = []
        if not os.path.exists(self.journal_path):
            return records
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if record.get("symbol") == symbol:
                        records.append(record)
        return records
    
    def get_records_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Retrieve all records with a specific status"""
        records = []
        if not os.path.exists(self.journal_path):
            return records
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if record.get("status") == status:
                        records.append(record)
        return records
    
    def get_all_records(self) -> List[Dict[str, Any]]:
        """Retrieve all records"""
        records = []
        if not os.path.exists(self.journal_path):
            return records
        
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
    
    def update_status(self, record_id: str, new_status: str) -> bool:
        """Update the status of a specific record"""
        # Valid statuses according to specification
        valid_statuses = [
            "planned", "approved", "rejected", "expired", 
            "hit_tp", "hit_sl", "manual_closed", "invalidated"
        ]
        
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status: {new_status}. Valid statuses: {valid_statuses}")
        
        # Read all records
        all_records = self.get_all_records()
        
        # Find and update the target record
        updated = False
        for i, record in enumerate(all_records):
            if record.get("id") == record_id:
                all_records[i]["status"] = new_status
                all_records[i]["updated_at"] = datetime.utcnow().isoformat() + "Z"
                updated = True
                break
        
        if not updated:
            return False
        
        # Write all records back to file
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for record in all_records:
                f.write(json.dumps(record) + "\n")
        
        return True
    
    def update_with_actual_results(self, record_id: str, actual_results: Dict[str, Any]) -> bool:
        """Update a record with actual results after evaluation"""
        # Read all records
        all_records = self.get_all_records()
        
        # Find and update the target record
        updated = False
        for i, record in enumerate(all_records):
            if record.get("id") == record_id:
                # Add actual results to the record
                all_records[i]["actual_results"] = actual_results
                all_records[i]["evaluated_at"] = datetime.utcnow().isoformat() + "Z"
                updated = True
                break
        
        if not updated:
            return False
        
        # Write all records back to file
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for record in all_records:
                f.write(json.dumps(record) + "\n")
        
        return True


def main():
    parser = argparse.ArgumentParser(description="Decision Journal Manager for WORED")
    parser.add_argument("--journal-path", type=str, default="data/decision_journal.jsonl",
                       help="Path to the decision journal file")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Save command
    save_parser = subparsers.add_parser("save", help="Save a new decision record")
    save_parser.add_argument("--symbol", type=str, required=True, help="Trading symbol (e.g. BTCUSDT)")
    save_parser.add_argument("--period", type=str, required=True, help="Timeframe (e.g. 60min)")
    save_parser.add_argument("--bias", type=str, required=True, help="Market bias (e.g. bullish)")
    save_parser.add_argument("--confidence", type=int, required=True, help="Confidence level (0-100)")
    save_parser.add_argument("--entry-zone", type=str, required=True, help="Entry zone as 'low,high'")
    save_parser.add_argument("--stop-loss", type=float, required=True, help="Stop loss price")
    save_parser.add_argument("--take-profit", type=str, required=True, help="Take profit as 'tp1,tp2'")
    save_parser.add_argument("--risk-pct", type=float, required=True, help="Risk percentage")
    save_parser.add_argument("--position-size", type=float, required=True, help="Position size")
    save_parser.add_argument("--status", type=str, default="planned", help="Initial status (default: planned)")
    save_parser.add_argument("--id", type=str, help="Record ID (auto-generated if not provided)")
    
    # Get command
    get_parser = subparsers.add_parser("get", help="Get a specific decision record")
    get_parser.add_argument("--id", type=str, required=True, help="Record ID")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List decision records")
    list_parser.add_argument("--symbol", type=str, help="Filter by symbol")
    list_parser.add_argument("--status", type=str, help="Filter by status")
    
    # Update status command
    update_parser = subparsers.add_parser("update-status", help="Update record status")
    update_parser.add_argument("--id", type=str, required=True, help="Record ID")
    update_parser.add_argument("--new-status", type=str, required=True, help="New status")
    
    # Update with results command
    results_parser = subparsers.add_parser("update-results", help="Update record with actual results")
    results_parser.add_argument("--id", type=str, required=True, help="Record ID")
    results_parser.add_argument("--actual-entry", type=float, help="Actual entry price")
    results_parser.add_argument("--actual-exit", type=float, help="Actual exit price")
    results_parser.add_argument("--profit-loss", type=float, help="Profit/loss amount")
    results_parser.add_argument("--result-status", type=str, help="Final result (hit_tp, hit_sl, etc.)")
    
    args = parser.parse_args()
    
    journal = DecisionJournal(args.journal_path)
    
    if args.command == "save":
        # Parse entry zone and take profit
        try:
            entry_zone_parts = args.entry_zone.split(',')
            entry_zone = [float(x.strip()) for x in entry_zone_parts]
            
            take_profit_parts = args.take_profit.split(',')
            take_profit = [float(x.strip()) for x in take_profit_parts]
        except ValueError:
            print("Error: Entry zone and take profit must be in format 'low,high'")
            sys.exit(1)
        
        record = {
            "symbol": args.symbol,
            "period": args.period,
            "bias": args.bias,
            "confidence": args.confidence,
            "entry_zone": entry_zone,
            "stop_loss": args.stop_loss,
            "take_profit": take_profit,
            "risk_pct": args.risk_pct,
            "position_size": args.position_size,
            "status": args.status
        }
        
        if args.id:
            record["id"] = args.id
        
        record_id = journal.save_record(record)
        print(f"Record saved with ID: {record_id}")
        
    elif args.command == "get":
        record = journal.get_record(args.id)
        if record:
            print(json.dumps(record, indent=2))
        else:
            print(f"No record found with ID: {args.id}")
            sys.exit(1)
            
    elif args.command == "list":
        if args.symbol:
            records = journal.get_records_by_symbol(args.symbol)
        elif args.status:
            records = journal.get_records_by_status(args.status)
        else:
            records = journal.get_all_records()
        
        if records:
            for record in records:
                print(json.dumps(record, indent=2))
        else:
            print("No records found.")
            
    elif args.command == "update-status":
        success = journal.update_status(args.id, args.new_status)
        if success:
            print(f"Status updated for record {args.id} to {args.new_status}")
        else:
            print(f"Failed to update status for record {args.id}")
            sys.exit(1)
            
    elif args.command == "update-results":
        actual_results = {}
        if args.actual_entry is not None:
            actual_results["actual_entry"] = args.actual_entry
        if args.actual_exit is not None:
            actual_results["actual_exit"] = args.actual_exit
        if args.profit_loss is not None:
            actual_results["profit_loss"] = args.profit_loss
        if args.result_status is not None:
            actual_results["result_status"] = args.result_status
        
        success = journal.update_with_actual_results(args.id, actual_results)
        if success:
            print(f"Results updated for record {args.id}")
        else:
            print(f"Failed to update results for record {args.id}")
            sys.exit(1)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()