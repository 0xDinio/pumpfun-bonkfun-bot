"""
Console reporter for real-time trading verbosity.
Provides ASCII-only console output for trades and portfolio snapshots.
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional
from utils.logger import get_logger

logger = get_logger(__name__)


class ConsoleReporter:
    """Provides real-time ASCII console output for trading events."""
    
    def __init__(self):
        """Initialize console reporter with environment settings."""
        self.verbose_console = os.getenv("VERBOSE_CONSOLE", "false").lower() == "true"
        self.verbose_trade_lines = os.getenv("VERBOSE_TRADE_LINES", "true").lower() == "true" if self.verbose_console else False
        self.verbose_portfolio_interval = int(os.getenv("VERBOSE_PORTFOLIO_INTERVAL_SECONDS", "30")) if self.verbose_console else 30
        self.verbose_include_positions = os.getenv("VERBOSE_INCLUDE_POSITIONS", "false").lower() == "true"
        self.use_logger_only = os.getenv("VERBOSE_USE_LOGGER_ONLY", "false").lower() == "true"
        
        if self.verbose_console:
            # Print startup diagnostics
            dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
            flags_line = (
                f"[VERBOSE] flags: console={self.verbose_console} trades={self.verbose_trade_lines} "
                f"interval={self.verbose_portfolio_interval} include_positions={self.verbose_include_positions} "
                f"dry_run={dry_run}"
            )
            self._console_print(flags_line)
            
            # UTF-8 emoji sanity check
            emoji_test_line = "Console UTF-8 check: 🔥🐶🚀"
            self._console_print(emoji_test_line)
            
            # Test line with PID
            test_line = f"[TEST] ConsoleReporter active (pid={os.getpid()})"
            self._console_print(test_line)
    
    def on_sim_buy(
        self, 
        symbol: str, 
        quantity: float, 
        price: float, 
        value_before: float, 
        value_after: float, 
        delta: float, 
        realized_pnl: float, 
        mode: str
    ) -> None:
        """Print dry-run BUY trade line."""
        if not self.verbose_console or not self.verbose_trade_lines:
            return
            
        # Use raw symbol with emoji support
        safe_symbol = symbol if symbol else "UNKNOWN"
        
        # Format price to show scientific notation for very small numbers
        if price < 0.000001:
            price_str = f"{price:.2e}"
        else:
            price_str = f"{price:.8f}"
            
        line = (
            f"[DRY] BUY {safe_symbol} qty={quantity:.4f} @ {price_str} | "
            f"value: {value_before:.4f} -> {value_after:.4f} (d {delta:+.4f}) | "
            f"realized_pnl_cum={realized_pnl:.4f} | mode={mode}"
        )
        self._console_print(line)
    
    def on_sim_sell(
        self, 
        symbol: str, 
        quantity: float, 
        price: float, 
        value_before: float, 
        value_after: float, 
        delta: float, 
        realized_pnl: float, 
        mode: str
    ) -> None:
        """Print dry-run SELL trade line."""
        if not self.verbose_console or not self.verbose_trade_lines:
            return
            
        # Use raw symbol with emoji support
        safe_symbol = symbol if symbol else "UNKNOWN"
        
        # Format price to show scientific notation for very small numbers
        if price < 0.000001:
            price_str = f"{price:.2e}"
        else:
            price_str = f"{price:.8f}"
            
        line = (
            f"[DRY] SELL {safe_symbol} qty={quantity:.4f} @ {price_str} | "
            f"value: {value_before:.4f} -> {value_after:.4f} (d {delta:+.4f}) | "
            f"realized_pnl_cum={realized_pnl:.4f} | mode={mode}"
        )
        self._console_print(line)
    
    def on_live_buy(
        self, 
        symbol: str, 
        signature: str, 
        quantity: float, 
        price: float
    ) -> None:
        """Print live BUY trade line."""
        if not self.verbose_console or not self.verbose_trade_lines:
            return
            
        # Use raw symbol with emoji support and truncated signature
        safe_symbol = symbol if symbol else "UNKNOWN"
        short_sig = signature[:16] if signature else "unknown"
        
        line = f"[LIVE] BUY {safe_symbol} sig={short_sig} qty={quantity:.4f} @ {price:.8f}"
        self._console_print(line)
    
    def on_live_sell(
        self, 
        symbol: str, 
        signature: str, 
        quantity: float, 
        price: float
    ) -> None:
        """Print live SELL trade line."""
        if not self.verbose_console or not self.verbose_trade_lines:
            return
            
        # Use raw symbol with emoji support and truncated signature
        safe_symbol = symbol if symbol else "UNKNOWN"
        short_sig = signature[:16] if signature else "unknown"
        
        line = f"[LIVE] SELL {safe_symbol} sig={short_sig} qty={quantity:.4f} @ {price:.8f}"
        self._console_print(line)
    
    async def snapshot(
        self, 
        sol_balance: float, 
        total_value: float, 
        realized_pnl: float, 
        positions: Dict[str, Any]
    ) -> None:
        """Print periodic portfolio snapshot."""
        if not self.verbose_console:
            return
            
        timestamp = datetime.utcnow().isoformat()
        num_positions = len(positions)
        
        line = (
            f"[SNAPSHOT] t={timestamp} sol={sol_balance:.4f} value={total_value:.4f} "
            f"realized_pnl={realized_pnl:.4f} positions={num_positions}"
        )
        
        # Add positions summary if requested
        if self.verbose_include_positions and positions:
            pos_parts = []
            for mint_str, pos in positions.items():
                safe_symbol = pos.get("symbol", "UNKNOWN")
                amount = pos.get("amount", 0.0)
                entry_price = pos.get("entry_price", 0.0)
                pos_parts.append(f"{safe_symbol}:{amount:.4f}@{entry_price:.6f}")
            
            # Join and truncate if too long
            pos_str = ";".join(pos_parts)
            if len(pos_str) > 80:  # Leave room for base line
                pos_str = pos_str[:77] + "..."
            
            line += f" pos={pos_str}"
        
        self._console_print(line)
    

    
    @property
    def is_enabled(self) -> bool:
        """Check if console reporter is enabled."""
        return self.verbose_console
    
    def _console_print(self, message: str) -> None:
        """Print message to console with proper handling for Windows multiprocessing."""
        if self.use_logger_only:
            logger.info(message)
        else:
            try:
                print(message, flush=True)
            except Exception:
                # Fallback to logger if print fails
                logger.info(message)
    
    async def startup_test_snapshot(self) -> None:
        """Print immediate test snapshot on startup."""
        if not self.verbose_console:
            return
            
        timestamp = datetime.utcnow().isoformat()
        test_line = (
            f"[SNAPSHOT] t={timestamp} sol=0.0000 value=0.0000 "
            f"realized_pnl=0.0000 positions=0"
        )
        self._console_print(test_line)

    @property
    def snapshot_interval(self) -> int:
        """Get snapshot interval in seconds."""
        return self.verbose_portfolio_interval
