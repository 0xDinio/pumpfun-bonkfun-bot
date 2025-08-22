"""
Token valuation for dry-run simulation.
"""

from enum import Enum
from typing import Any

from solders.pubkey import Pubkey
from utils.logger import get_logger

logger = get_logger(__name__)


class ValueMode(Enum):
    """Token valuation modes for dry-run simulation."""
    ENTRY = "entry"      # Use entry price (simple)
    QUOTE = "quote"      # Use quote price from curve/pool
    RPC = "rpc"          # Use RPC price lookup (slow but accurate)


class TokenValuer:
    """Handles token valuation for portfolio simulation."""
    
    def __init__(self, mode: ValueMode = ValueMode.ENTRY, platform_implementations: Any = None):
        """Initialize the token valuer.
        
        Args:
            mode: Valuation mode to use
            platform_implementations: Platform-specific implementations for pricing
        """
        self.mode = mode
        self.platform_implementations = platform_implementations
        logger.info(f"TokenValuer initialized with mode: {mode.value}")
    
    async def get_token_value_sol(self, mint: Pubkey, amount: float, entry_price: float = None) -> float:
        """Get the current SOL value of a token amount.
        
        Args:
            mint: Token mint address
            amount: Amount of tokens
            entry_price: Entry price in SOL (for ENTRY mode)
            
        Returns:
            Current value in SOL
        """
        try:
            if self.mode == ValueMode.ENTRY:
                # Use entry price (simple simulation)
                if entry_price is None:
                    logger.warning(f"No entry price provided for {mint}, using 0")
                    return 0.0
                value = amount * entry_price
                logger.debug(f"ENTRY mode: {amount} tokens * {entry_price} SOL = {value} SOL")
                return value
                
            elif self.mode == ValueMode.QUOTE:
                # Use quote price from curve/pool (more realistic)
                logger.info(f"QUOTE mode: platform_implementations={self.platform_implementations}, hasattr={hasattr(self.platform_implementations, 'curve_manager') if self.platform_implementations else 'None'}")
                if self.platform_implementations and hasattr(self.platform_implementations, 'curve_manager'):
                    try:
                        # Get pool/curve address for pricing
                        pool_address = self._get_pool_address(mint)
                        current_price = await self.platform_implementations.curve_manager.calculate_price(pool_address)
                        value = amount * current_price
                        logger.info(f"QUOTE mode: {amount} tokens * {current_price:.8f} SOL = {value:.8f} SOL")
                        return value
                    except Exception as e:
                        # Enhanced error logging for quote failures
                        platform_name = type(self.platform_implementations).__name__
                        logger.warning(f"[QUOTE-DEBUG] platform={platform_name} pool_addr={pool_address} source=curve_manager.calculate_price reason=\"{e}\"")
                        logger.warning(f"Failed to get quote price for {mint}: {e}, falling back to entry price")
                        return amount * (entry_price or 0.0)
                else:
                    logger.warning(f"No platform implementations available for QUOTE mode (impl={self.platform_implementations}), using entry price")
                    return amount * (entry_price or 0.0)
                    
            elif self.mode == ValueMode.RPC:
                # Use RPC price lookup (most accurate but slowest)
                logger.warning("RPC mode not yet implemented, using entry price")
                return amount * (entry_price or 0.0)
                
            else:
                logger.error(f"Unknown value mode: {self.mode}")
                return 0.0
                
        except Exception as e:
            logger.exception(f"Error valuing token {mint}: {e}")
            return 0.0
    
    def _get_pool_address(self, mint: Pubkey) -> Pubkey:
        """Get the pool/curve address for a token."""
        if self.platform_implementations and hasattr(self.platform_implementations, 'address_provider'):
            return self.platform_implementations.address_provider.derive_pool_address(mint)
        else:
            # Fallback - just return the mint (will likely fail)
            return mint
