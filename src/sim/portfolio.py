"""
Portfolio simulator for dry-run trading.
"""

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, Optional

from solders.pubkey import Pubkey
from utils.logger import get_logger
from utils.asserts import assert_approx_equal, approx_equal
from core.fx import get_fx
from .ledger import TradeLedger
from .valuer import TokenValuer, ValueMode

logger = get_logger(__name__)


class TokenPosition:
    """Represents a token position in the simulated portfolio."""
    
    def __init__(self, mint: Pubkey, symbol: str, amount: float, entry_price: float):
        """Initialize token position.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            amount: Amount of tokens held
            entry_price: Entry price in SOL
        """
        self.mint = mint
        self.symbol = symbol
        self.amount = amount
        self.entry_price = entry_price
        self.entry_value = amount * entry_price
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary."""
        return {
            "mint": str(self.mint),
            "symbol": self.symbol,
            "amount": self.amount,
            "entry_price": self.entry_price,
            "entry_value": self.entry_value,
        }


class PortfolioSimulator:
    """Simulates portfolio for dry-run trading without real transactions."""
    
    def __init__(
        self, 
        starting_sol: float = 3.0, 
        value_mode: ValueMode = ValueMode.ENTRY,
        platform_implementations: Any = None,
        ledger_dir: str = "sim_data",
        console_reporter: Any = None
    ):
        """Initialize portfolio simulator.
        
        Args:
            starting_sol: Starting SOL balance
            value_mode: Token valuation mode
            platform_implementations: Platform-specific implementations
            ledger_dir: Directory for simulation data
            console_reporter: Optional console reporter for verbosity
        """
        self.starting_sol = starting_sol
        self.sol_balance = starting_sol
        self.positions: Dict[str, TokenPosition] = {}
        self.realized_pnl = 0.0
        self.console_reporter = console_reporter
        
        self.valuer = TokenValuer(value_mode, platform_implementations)
        self.ledger = TradeLedger(ledger_dir)
        
        # Debug flag
        self.debug_valuation = os.getenv("DEBUG_VALUATION", "false").lower() == "true"
        
        # Portfolio display currency configuration
        self.display_currency = os.getenv("PORTFOLIO_DISPLAY_CCY", "SOL").upper()
        self.fx = get_fx()
        
        # Lock for thread-safe position dictionary access
        self._positions_lock = asyncio.Lock()
        
        # Try to load existing state
        self._load_state()
        
        logger.info(f"PortfolioSimulator initialized: {self.sol_balance:.6f} SOL, mode={value_mode.value}, debug={self.debug_valuation}")
    
    def _load_state(self) -> None:
        """Load portfolio state from disk if available."""
        try:
            state = self.ledger.load_portfolio_state()
            if state:
                self.sol_balance = state.get("sol_balance", self.starting_sol)
                self.realized_pnl = state.get("realized_pnl", 0.0)
                
                # Load positions
                positions_data = state.get("positions", {})
                for mint_str, pos_data in positions_data.items():
                    self.positions[mint_str] = TokenPosition(
                        mint=Pubkey.from_string(mint_str),
                        symbol=pos_data["symbol"],
                        amount=pos_data["amount"],
                        entry_price=pos_data["entry_price"]
                    )
                
                logger.info(f"Loaded portfolio state: {self.sol_balance:.6f} SOL, {len(self.positions)} positions")
            else:
                logger.info("Starting with fresh portfolio state")
                
        except Exception as e:
            logger.exception(f"Failed to load portfolio state: {e}")
    
    def _save_state(self) -> None:
        """Save current portfolio state to disk."""
        try:
            state = {
                "sol_balance": self.sol_balance,
                "realized_pnl": self.realized_pnl,
                "positions": {
                    mint_str: pos.to_dict() 
                    for mint_str, pos in list(self.positions.items())
                },
                "last_updated": datetime.utcnow().isoformat(),
            }
            self.ledger.save_portfolio_state(state)
            
        except Exception as e:
            logger.exception(f"Failed to save portfolio state: {e}")
    
    async def get_portfolio_value(self) -> float:
        """Calculate total portfolio value in SOL.
        
        Returns:
            Total portfolio value in SOL
        """
        async with self._positions_lock:
            total_value = self.sol_balance
            
            # Create snapshot to avoid "dictionary changed size during iteration"
            for position in list(self.positions.values()):
                token_value = await self.valuer.get_token_value_sol(
                    position.mint, 
                    position.amount, 
                    position.entry_price
                )
                total_value += token_value
            
            return total_value
    
    async def simulate_buy(
        self, 
        mint: Pubkey, 
        symbol: str, 
        sol_amount: float, 
        price_per_token: float,
        fee_sol: float = 0.01  # Default fee estimation
    ) -> Dict[str, Any]:
        """Simulate buying a token.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            sol_amount: Amount of SOL to spend
            price_per_token: Price per token in SOL
            fee_sol: Transaction fees in SOL
            
        Returns:
            Trade simulation result
        """
        try:
            # Pre-trade snapshot for debugging
            if self.debug_valuation:
                pre_value = await self.get_portfolio_value()
                async with self._positions_lock:
                    pre_positions = len(self.positions)
                    logger.info(f"[DEBUG] pre: sol={self.sol_balance:.6f} value={pre_value:.6f} positions={pre_positions}")
            
            if sol_amount + fee_sol > self.sol_balance:
                return {
                    "success": False,
                    "error": f"Insufficient SOL balance: {self.sol_balance:.6f} < {sol_amount + fee_sol:.6f}"
                }
            
            # Calculate tokens received
            tokens_received = sol_amount / price_per_token
            
            # Debug trade details
            if self.debug_valuation:
                logger.info(f"[DEBUG] trade: side=BUY symbol={symbol} price={price_per_token:.10f} amount_sol={sol_amount:.6f} qty={tokens_received:.6f} fee_sol={fee_sol:.6f}")
            
            # Calculate portfolio value before trade
            portfolio_value_before = await self.get_portfolio_value()
            
            # CRITICAL SECTION: Protect state mutations
            async with self._positions_lock:
                # Update balances (deduct both buy amount and fees)
                self.sol_balance -= (sol_amount + fee_sol)
                
                # Add to position or create new one
                mint_str = str(mint)
                if mint_str in self.positions:
                    # Average down the position
                    existing = self.positions[mint_str]
                    total_tokens = existing.amount + tokens_received
                    total_value = existing.entry_value + sol_amount
                    new_avg_price = total_value / total_tokens
                    
                    self.positions[mint_str] = TokenPosition(
                        mint=mint,
                        symbol=symbol,
                        amount=total_tokens,
                        entry_price=new_avg_price
                    )
                else:
                    self.positions[mint_str] = TokenPosition(
                        mint=mint,
                        symbol=symbol,
                        amount=tokens_received,
                        entry_price=price_per_token
                    )
            
            # Calculate portfolio value after trade
            portfolio_value_after = await self.get_portfolio_value()
            delta = portfolio_value_after - portfolio_value_before
            
            # Post-trade snapshot and assertions for debugging
            if self.debug_valuation:
                post_positions = len(self.positions)
                logger.info(f"[DEBUG] post: sol={self.sol_balance:.6f} value={portfolio_value_after:.6f} delta={delta:+.6f} realized_pnl_cum={self.realized_pnl:.6f} positions={post_positions}")
                
                # Calculate dual deltas for clearer reporting
                cash_delta = -(sol_amount + fee_sol)  # Cash outflow for BUY
                
                # Assertion checks for BUY invariants (MTM model)
                try:
                    # 1) q == B / p_buy (exact fill)
                    expected_qty = sol_amount / price_per_token
                    assert_approx_equal(tokens_received, expected_qty, rel=1e-6, msg="BUY quantity check")
                    
                    # 2) MTM value delta ≈ -fee_sol (small quote drift allowed)
                    expected_value_delta = -fee_sol
                    tolerance = max(1e-6, fee_sol * 0.5)  # Allow quote drift
                    assert_approx_equal(delta, expected_value_delta, abs_tol=tolerance, msg="BUY MTM value delta check")
                    
                    logger.info(f"[DEBUG] BUY assertions passed: qty={tokens_received:.6f}, cash_delta={cash_delta:.6f}, value_delta={delta:.6f}")
                    
                except AssertionError as e:
                    logger.warning(f"[DEBUG] BUY assertion failed: actual={delta:.6f}, expected≈{expected_value_delta:.6f}, tolerance={tolerance:.6f}: {e}")
            
            # Log the trade (include cash_delta if debug mode)
            if self.debug_valuation:
                cash_delta_for_log = -(sol_amount + fee_sol)
            else:
                cash_delta_for_log = None
                
            await self._log_trade_summary(
                action="BUY",
                symbol=symbol,
                quantity=tokens_received,
                price=price_per_token,
                value_before=portfolio_value_before,
                value_after=portfolio_value_after,
                delta=delta,
                realized_pnl=self.realized_pnl,
                cash_delta=cash_delta_for_log
            )
            
            # Console reporter hook for dry-run BUY
            if self.console_reporter:
                self.console_reporter.on_sim_buy(
                    symbol=symbol,
                    quantity=tokens_received,
                    price=price_per_token,
                    value_before=portfolio_value_before,
                    value_after=portfolio_value_after,
                    delta=delta,
                    realized_pnl=self.realized_pnl,
                    mode=self.valuer.mode.value
                )
            
            # Save state
            self._save_state()
            
            return {
                "success": True,
                "tokens_received": tokens_received,
                "sol_spent": sol_amount,
                "new_sol_balance": self.sol_balance,
                "portfolio_value_before": portfolio_value_before,
                "portfolio_value_after": portfolio_value_after,
                "delta": delta,
            }
            
        except Exception as e:
            logger.exception(f"Error simulating buy: {e}")
            return {"success": False, "error": str(e)}
    
    async def simulate_sell(
        self, 
        mint: Pubkey, 
        symbol: str, 
        price_per_token: float, 
        sell_percentage: float = 1.0,
        fee_sol: float = 0.01  # Default fee estimation
    ) -> Dict[str, Any]:
        """Simulate selling a token.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            price_per_token: Current price per token in SOL
            sell_percentage: Percentage of position to sell (0.0-1.0)
            fee_sol: Transaction fees in SOL
            
        Returns:
            Trade simulation result
        """
        try:
            mint_str = str(mint)
            
            # Pre-trade snapshot for debugging
            if self.debug_valuation:
                pre_value = await self.get_portfolio_value()
                async with self._positions_lock:
                    pre_positions = len(self.positions)
                    logger.info(f"[DEBUG] pre: sol={self.sol_balance:.6f} value={pre_value:.6f} positions={pre_positions}")
            
            async with self._positions_lock:
                if mint_str not in self.positions:
                    return {
                        "success": False,
                        "error": f"No position found for {symbol}"
                    }
                
                position = self.positions[mint_str]
                tokens_to_sell = position.amount * sell_percentage
                sol_received = tokens_to_sell * price_per_token
            
            # Debug trade details
            if self.debug_valuation:
                logger.info(f"[DEBUG] trade: side=SELL symbol={symbol} price={price_per_token:.10f} amount_sol={sol_received:.6f} qty={tokens_to_sell:.6f} fee_sol={fee_sol:.6f}")
            
            # Calculate portfolio value before trade
            portfolio_value_before = await self.get_portfolio_value()
            
            # CRITICAL SECTION: Protect state mutations
            async with self._positions_lock:
                # Calculate PnL for this trade
                cost_basis = tokens_to_sell * position.entry_price
                trade_pnl = sol_received - cost_basis
                self.realized_pnl += trade_pnl
                
                # Update balances (add received SOL minus fees)
                self.sol_balance += (sol_received - fee_sol)
                
                # Update or remove position
                if sell_percentage >= 1.0:
                    # Sell entire position
                    del self.positions[mint_str]
                else:
                    # Partial sell - update remaining position
                    remaining_tokens = position.amount - tokens_to_sell
                    remaining_value = remaining_tokens * position.entry_price
                    
                    self.positions[mint_str] = TokenPosition(
                        mint=mint,
                        symbol=symbol,
                        amount=remaining_tokens,
                        entry_price=position.entry_price
                    )
            
            # Calculate portfolio value after trade
            portfolio_value_after = await self.get_portfolio_value()
            delta = portfolio_value_after - portfolio_value_before
            
            # Post-trade snapshot and assertions for debugging
            if self.debug_valuation:
                post_positions = len(self.positions)
                logger.info(f"[DEBUG] post: sol={self.sol_balance:.6f} value={portfolio_value_after:.6f} delta={delta:+.6f} realized_pnl_cum={self.realized_pnl:.6f} positions={post_positions}")
                
                # Calculate dual deltas for clearer reporting
                cash_delta = sol_received - fee_sol  # Cash inflow for SELL
                
                # Assertion checks for SELL invariants (MTM model)
                try:
                    # MTM model: PnL already included in pre-trade valuation, so delta ≈ -fee_sol
                    expected_value_delta = -fee_sol
                    tolerance = max(1e-6, fee_sol * 0.5)  # Allow quote drift
                    assert_approx_equal(delta, expected_value_delta, abs_tol=tolerance, msg="SELL MTM value delta check")
                    
                    logger.info(f"[DEBUG] SELL assertions passed: qty={tokens_to_sell:.6f}, cash_delta={cash_delta:.6f}, value_delta={delta:.6f}")
                    
                except AssertionError as e:
                    logger.warning(f"[DEBUG] SELL assertion failed: actual={delta:.6f}, expected≈{expected_value_delta:.6f}, tolerance={tolerance:.6f}: {e}")
            
            # Log the trade (include cash_delta if debug mode)
            if self.debug_valuation:
                cash_delta_for_log = sol_received - fee_sol
            else:
                cash_delta_for_log = None
                
            await self._log_trade_summary(
                action="SELL",
                symbol=symbol,
                quantity=tokens_to_sell,
                price=price_per_token,
                value_before=portfolio_value_before,
                value_after=portfolio_value_after,
                delta=delta,
                realized_pnl=self.realized_pnl,
                cash_delta=cash_delta_for_log
            )
            
            # Console reporter hook for dry-run SELL
            if self.console_reporter:
                self.console_reporter.on_sim_sell(
                    symbol=symbol,
                    quantity=tokens_to_sell,
                    price=price_per_token,
                    value_before=portfolio_value_before,
                    value_after=portfolio_value_after,
                    delta=delta,
                    realized_pnl=self.realized_pnl,
                    mode=self.valuer.mode.value
                )
            
            # Save state
            self._save_state()
            
            return {
                "success": True,
                "tokens_sold": tokens_to_sell,
                "sol_received": sol_received,
                "trade_pnl": trade_pnl,
                "new_sol_balance": self.sol_balance,
                "portfolio_value_before": portfolio_value_before,
                "portfolio_value_after": portfolio_value_after,
                "delta": delta,
            }
            
        except Exception as e:
            logger.exception(f"Error simulating sell: {e}")
            return {"success": False, "error": str(e)}
    
    async def log_periodic_snapshot(self) -> None:
        """Log a periodic portfolio snapshot without trade action."""
        try:
            portfolio_value = await self.get_portfolio_value()
            
            # Console summary line for periodic snapshot
            mode = self.valuer.mode.value
            summary = (
                f"[DRY] SNAPSHOT | "
                f"value: {portfolio_value:.4f} SOL | "
                f"positions: {len(self.positions)} | "
                f"realized_pnl_cum={self.realized_pnl:.4f} | "
                f"mode={mode}"
            )
            logger.info(summary)
            
            # Save portfolio snapshot to CSV
            snapshot_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": "snapshot",
                "symbol": "",
                "portfolio_value": portfolio_value,
                "sol_balance": self.sol_balance,
                "realized_pnl": self.realized_pnl,
                "num_positions": len(self.positions),
                "value_mode": mode,
            }
            self.ledger.save_portfolio_snapshot(snapshot_data)
            
        except Exception as e:
            logger.exception(f"Error logging periodic snapshot: {e}")
    
    def get_summary_stats(self) -> Dict[str, Any]:
        """Get portfolio summary statistics."""
        return {
            "starting_sol": self.starting_sol,
            "current_sol_balance": self.sol_balance,
            "realized_pnl": self.realized_pnl,
            "num_positions": len(self.positions),
            "positions": {mint_str: pos.to_dict() for mint_str, pos in list(self.positions.items())},
        }

    async def _log_trade_summary(
        self,
        action: str,
        symbol: str,
        quantity: float,
        price: float,
        value_before: float,
        value_after: float,
        delta: float,
        realized_pnl: float,
        cash_delta: float = None
    ) -> None:
        """Log trade summary to console and files."""
        # Get FX rate for display conversion
        sol_usd_rate = await self.fx.get_sol_usd()
        
        # Console summary line with dual deltas if available
        mode = self.valuer.mode.value
        
        if self.display_currency == "USD":
            # Convert values to USD for display
            value_before_display = self.fx.convert_sol_to_usd(value_before, sol_usd_rate)
            value_after_display = self.fx.convert_sol_to_usd(value_after, sol_usd_rate)
            delta_display = self.fx.convert_sol_to_usd(delta, sol_usd_rate)
            realized_pnl_display = self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate)
            cash_delta_display = self.fx.convert_sol_to_usd(cash_delta, sol_usd_rate) if cash_delta is not None else None
            
            # Format USD values
            currency_suffix = " USD"
            value_before_str = self.fx.format_usd_value(value_before_display)
            value_after_str = self.fx.format_usd_value(value_after_display)
            delta_str = self.fx.format_usd_value(delta_display)
            realized_pnl_str = self.fx.format_usd_value(realized_pnl_display)
        else:
            # Use SOL values directly
            value_before_display = value_before
            value_after_display = value_after
            delta_display = delta
            realized_pnl_display = realized_pnl
            cash_delta_display = cash_delta
            
            currency_suffix = ""
            value_before_str = f"{value_before:.4f}"
            value_after_str = f"{value_after:.4f}"
            delta_str = f"{delta:+.4f}"
            realized_pnl_str = f"{realized_pnl:.4f}"
        
        if cash_delta is not None:
            # Debug mode: show both cash and value deltas
            cash_delta_str = self.fx.format_usd_value(cash_delta_display) if self.display_currency == "USD" else f"{cash_delta:+.4f}"
            summary = (
                f"[DRY] {action} {symbol} qty={quantity:.4f} @ {price:.6f} | "
                f"Δcash={cash_delta_str}{currency_suffix} Δvalue={delta_str}{currency_suffix} | "
                f"value: {value_before_str}{currency_suffix} -> {value_after_str}{currency_suffix} | "
                f"realized_pnl_cum={realized_pnl_str}{currency_suffix} | mode={mode}"
            )
        else:
            # Normal mode: original format
            summary = (
                f"[DRY] {action} {symbol} qty={quantity:.4f} @ {price:.6f} | "
                f"value: {value_before_str}{currency_suffix} -> {value_after_str}{currency_suffix} (d {delta_str}{currency_suffix}) | "
                f"realized_pnl_cum={realized_pnl_str}{currency_suffix} | mode={mode}"
            )
        logger.info(summary)
        
        # Log trade event to NDJSON with both SOL and USD values
        event_data = {
            "action": action.lower(),
            "symbol": symbol,
            "quantity": quantity,
            "token_price_sol": price,
            "token_price_usd": self.fx.convert_sol_to_usd(price, sol_usd_rate),
            "portfolio_value_before_sol": value_before,
            "portfolio_value_after_sol": value_after,
            "delta_sol": delta,
            "realized_pnl_cumulative_sol": realized_pnl,
            "portfolio_value_before_usd": self.fx.convert_sol_to_usd(value_before, sol_usd_rate),
            "portfolio_value_after_usd": self.fx.convert_sol_to_usd(value_after, sol_usd_rate),
            "delta_usd": self.fx.convert_sol_to_usd(delta, sol_usd_rate),
            "realized_pnl_cumulative_usd": self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate),
            "sol_balance": self.sol_balance,
            "sol_usd_rate": sol_usd_rate,
            "display_ccy": self.display_currency,
            "value_mode": mode,
        }
        self.ledger.log_trade_event(event_data)
        
        # Save portfolio snapshot to CSV
        snapshot_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action.lower(),
            "symbol": symbol,
            "portfolio_value_sol": value_after,
            "portfolio_value_usd": self.fx.convert_sol_to_usd(value_after, sol_usd_rate),
            "sol_balance": self.sol_balance,
            "realized_pnl_sol": realized_pnl,
            "realized_pnl_usd": self.fx.convert_sol_to_usd(realized_pnl, sol_usd_rate),
            "num_positions": len(self.positions),
            "sol_usd_rate": sol_usd_rate,
            "display_ccy": self.display_currency,
            "value_mode": mode,
        }
        self.ledger.save_portfolio_snapshot(snapshot_data)
