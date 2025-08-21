"""
Quote failure logging for DRY_RUN mode.

This module provides structured logging for quote failures to help diagnose
when and why quote fetching fails for specific tokens.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class QuoteFailureLogger:
    """Logger for quote failures with structured NDJSON output."""

    def __init__(self, log_dir: str = "sim_data"):
        """Initialize the quote failure logger.
        
        Args:
            log_dir: Directory to write quote failure logs
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.log_file = self.log_dir / "quote_failures.ndjson"

    def log_failure(
        self,
        mint: str,
        derived_addr: str,
        commitment: str,
        rpc_url: str,
        error: str,
        platform: str = "pump_fun",
        program_id: str = "",
        attempts: int = 1
    ) -> None:
        """Log a quote failure with structured data.
        
        Args:
            mint: Token mint address
            derived_addr: Derived pool/bonding curve address
            commitment: Commitment level used
            rpc_url: RPC endpoint URL
            error: Error message from failed quote attempt
            platform: Platform name
            program_id: Program ID used for derivation
            attempts: Number of retry attempts made
        """
        try:
            failure_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "mint": mint,
                "derived_addr": derived_addr,
                "commitment": commitment,
                "rpc_url": rpc_url,
                "error": error,
                "platform": platform,
                "program_id": program_id,
                "attempts": attempts
            }
            
            # Write to NDJSON file
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(failure_data) + "\n")
                
            logger.debug(f"Logged quote failure for {mint}: {error}")
            
        except Exception as e:
            logger.error(f"Failed to log quote failure: {e}")

    def get_recent_failures(self, hours: int = 1) -> list[dict[str, Any]]:
        """Get recent quote failures within the specified time window.
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            List of recent failure records
        """
        if not self.log_file.exists():
            return []
            
        recent_failures = []
        cutoff_time = datetime.utcnow().timestamp() - (hours * 3600)
        
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line.strip())
                        timestamp = datetime.fromisoformat(data["timestamp"]).timestamp()
                        if timestamp >= cutoff_time:
                            recent_failures.append(data)
        except Exception as e:
            logger.error(f"Failed to read quote failures: {e}")
            
        return recent_failures
