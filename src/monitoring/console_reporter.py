"""
Console reporter for real-time trading verbosity.
Provides ASCII-only console output for trades and portfolio snapshots.
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from core.fx import get_fx
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
        
        # Portfolio display currency configuration
        self.display_currency = os.getenv("PORTFOLIO_DISPLAY_CCY", "SOL").upper()
        self.price_display_mode = os.getenv("PRICE_DISPLAY_MODE", "DUAL").upper()
        self.fx = get_fx()
        
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
    
    def _format_price_display(self, price_sol: float) -> str:
        """Format price according to PRICE_DISPLAY_MODE.
        
        Args:
            price_sol: Token price in SOL
            
        Returns:
            Formatted price string
        """
        if self.price_display_mode == "SOL":
            price_str = self.fx.format_sol_price(price_sol)
            return f"{price_str} SOL"
        
        elif self.price_display_mode == "USD":
            sol_usd_rate = self.fx.get_cached_rate()
            price_usd = self.fx.convert_sol_to_usd(price_sol, sol_usd_rate)
            price_str = self.fx.format_usd_price(price_usd)
            return f"${price_str}"
        
        else:  # DUAL
            sol_usd_rate = self.fx.get_cached_rate()
            price_usd = self.fx.convert_sol_to_usd(price_sol, sol_usd_rate)
            
            price_sol_str = self.fx.format_sol_price(price_sol)
            price_usd_str = self.fx.format_usd_price(price_usd)
            
            return f"{price_sol_str} SOL (~${price_usd_str})"
    
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
        
        # Format price according to PRICE_DISPLAY_MODE
        price_display_str = self._format_price_display(price)
        
        # Format values based on display currency
        if self.display_currency == "USD":
            sol_usd_rate = self.fx.get_cached_rate()
            value_before_display = self.fx.convert_sol_to_usd(value_before, sol_usd_rate)
            value_after_display = self.fx.convert_sol_to_usd(value_after, sol_usd_rate)
            delta_display = self.fx.convert_sol_to_usd(delta, sol_usd_rate)
            realized_pnl_display = self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate)
            
            value_before_str = self.fx.format_usd_value(value_before_display)
            value_after_str = self.fx.format_usd_value(value_after_display)
            delta_str = self.fx.format_usd_value(delta_display)
            realized_pnl_str = self.fx.format_usd_value(realized_pnl_display)
            currency_suffix = " USD"
        else:
            value_before_str = f"{value_before:.4f}"
            value_after_str = f"{value_after:.4f}"
            delta_str = f"{delta:+.4f}"
            realized_pnl_str = f"{realized_pnl:.4f}"
            currency_suffix = ""
            
        line = (
            f"[DRY] BUY {safe_symbol} qty={quantity:.4f} @ {price_display_str} | "
            f"value: {value_before_str}{currency_suffix} -> {value_after_str}{currency_suffix} (d {delta_str}{currency_suffix}) | "
            f"realized_pnl_cum={realized_pnl_str}{currency_suffix} | mode={mode}"
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
        
        # Format price according to PRICE_DISPLAY_MODE
        price_display_str = self._format_price_display(price)
        
        # Format values based on display currency
        if self.display_currency == "USD":
            sol_usd_rate = self.fx.get_cached_rate()
            value_before_display = self.fx.convert_sol_to_usd(value_before, sol_usd_rate)
            value_after_display = self.fx.convert_sol_to_usd(value_after, sol_usd_rate)
            delta_display = self.fx.convert_sol_to_usd(delta, sol_usd_rate)
            realized_pnl_display = self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate)
            
            value_before_str = self.fx.format_usd_value(value_before_display)
            value_after_str = self.fx.format_usd_value(value_after_display)
            delta_str = self.fx.format_usd_value(delta_display)
            realized_pnl_str = self.fx.format_usd_value(realized_pnl_display)
            currency_suffix = " USD"
        else:
            value_before_str = f"{value_before:.4f}"
            value_after_str = f"{value_after:.4f}"
            delta_str = f"{delta:+.4f}"
            realized_pnl_str = f"{realized_pnl:.4f}"
            currency_suffix = ""
            
        line = (
            f"[DRY] SELL {safe_symbol} qty={quantity:.4f} @ {price_display_str} | "
            f"value: {value_before_str}{currency_suffix} -> {value_after_str}{currency_suffix} (d {delta_str}{currency_suffix}) | "
            f"realized_pnl_cum={realized_pnl_str}{currency_suffix} | mode={mode}"
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
        
        # Format values based on display currency
        if self.display_currency == "USD":
            sol_usd_rate = await self.fx.get_sol_usd()
            total_value_display = self.fx.convert_sol_to_usd(total_value, sol_usd_rate)
            realized_pnl_display = self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate)
            
            value_str = self.fx.format_usd_value(total_value_display)
            pnl_str = self.fx.format_usd_value(realized_pnl_display)
            currency_suffix = " USD"
        else:
            value_str = f"{total_value:.4f}"
            pnl_str = f"{realized_pnl:.4f}"
            currency_suffix = ""
        
        line = (
            f"[SNAPSHOT] t={timestamp} sol={sol_balance:.4f} value={value_str}{currency_suffix} "
            f"realized_pnl={pnl_str}{currency_suffix} positions={num_positions}"
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
