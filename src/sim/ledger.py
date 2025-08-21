"""
Trade ledger for dry-run simulation tracking.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from utils.logger import get_logger

logger = get_logger(__name__)


class TradeLedger:
    """Maintains persistent trading ledger for dry-run simulation."""
    
    def __init__(self, ledger_dir: str = "sim_data"):
        """Initialize trade ledger.
        
        Args:
            ledger_dir: Directory to store ledger files
        """
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(exist_ok=True)
        
        self.ndjson_file = self.ledger_dir / "trades.ndjson"
        self.csv_file = self.ledger_dir / "portfolio_snapshots.csv"
        self.state_file = self.ledger_dir / "portfolio_state.json"
        
        logger.info(f"TradeLedger initialized with directory: {self.ledger_dir}")
    
    def log_trade_event(self, event_data: Dict[str, Any]) -> None:
        """Log a trade event to NDJSON file.
        
        Args:
            event_data: Trade event data dictionary
        """
        try:
            # Add timestamp if not present
            if "timestamp" not in event_data:
                event_data["timestamp"] = datetime.utcnow().isoformat()
            
            # Append to NDJSON file
            with self.ndjson_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event_data) + "\n")
                
            logger.debug(f"Logged trade event: {event_data.get('action', 'unknown')}")
            
        except Exception as e:
            logger.exception(f"Failed to log trade event: {e}")
    
    def save_portfolio_snapshot(self, snapshot_data: Dict[str, Any]) -> None:
        """Save portfolio snapshot to CSV file.
        
        Args:
            snapshot_data: Portfolio snapshot data
        """
        try:
            # Add timestamp if not present
            if "timestamp" not in snapshot_data:
                snapshot_data["timestamp"] = datetime.utcnow().isoformat()
            
            # Check if CSV file exists and write header if needed
            file_exists = self.csv_file.exists()
            
            with self.csv_file.open("a", newline="", encoding="utf-8") as f:
                fieldnames = list(snapshot_data.keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                writer.writerow(snapshot_data)
            
            logger.debug("Saved portfolio snapshot to CSV")
            
        except Exception as e:
            logger.exception(f"Failed to save portfolio snapshot: {e}")
    
    def save_portfolio_state(self, state_data: Dict[str, Any]) -> None:
        """Save current portfolio state to JSON file.
        
        Args:
            state_data: Current portfolio state
        """
        try:
            with self.state_file.open("w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, default=str)
            
            logger.debug("Saved portfolio state to JSON")
            
        except Exception as e:
            logger.exception(f"Failed to save portfolio state: {e}")
    
    def load_portfolio_state(self) -> Dict[str, Any]:
        """Load portfolio state from JSON file.
        
        Returns:
            Portfolio state data or empty dict if file doesn't exist
        """
        try:
            if self.state_file.exists():
                with self.state_file.open("r", encoding="utf-8") as f:
                    state = json.load(f)
                logger.info("Loaded existing portfolio state")
                return state
            else:
                logger.info("No existing portfolio state found")
                return {}
                
        except Exception as e:
            logger.exception(f"Failed to load portfolio state: {e}")
            return {}
    
    def get_trade_history(self) -> List[Dict[str, Any]]:
        """Get all trade events from NDJSON file.
        
        Returns:
            List of trade events
        """
        try:
            if not self.ndjson_file.exists():
                return []
            
            events = []
            with self.ndjson_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            
            return events
            
        except Exception as e:
            logger.exception(f"Failed to read trade history: {e}")
            return []
