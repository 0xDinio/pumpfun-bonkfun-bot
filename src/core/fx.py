"""
Foreign exchange rate adapter for SOL->USD conversion.
Provides simple, robust FX conversion for portfolio display purposes.
"""

import asyncio
import json
import os
import time
from typing import Optional

import aiohttp

from utils.logger import get_logger

logger = get_logger(__name__)


class FX:
    """Foreign exchange rate provider for SOL to USD conversion."""
    
    def __init__(
        self, 
        fx_source: str = "FIXED", 
        fixed_rate: float = 150.0,
        refresh_seconds: int = 300,
        timeout_seconds: int = 10,
        retries: int = 3
    ):
        """Initialize FX adapter.
        
        Args:
            fx_source: Source for FX rates ("FIXED", "JUPITER", "COINGECKO")
            fixed_rate: Fixed SOL/USD rate for testing and fallback
            refresh_seconds: Cache TTL in seconds
            timeout_seconds: HTTP timeout for API calls
            retries: Number of retry attempts
        """
        self.fx_source = fx_source.upper()
        self.fixed_rate = fixed_rate
        self.refresh_seconds = refresh_seconds
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        
        # Cache with TTL
        self._cached_rate: Optional[float] = None
        self._cache_timestamp: float = 0
        self._fetch_lock = asyncio.Lock()
        
        logger.info(f"FX adapter initialized: source={self.fx_source}, fixed_rate={fixed_rate}, "
                   f"refresh={refresh_seconds}s, timeout={timeout_seconds}s, retries={retries}")
    
    async def get_sol_usd(self) -> float:
        """Get current SOL/USD exchange rate.
        
        Returns:
            Current SOL/USD rate
        """
        # Check cache first
        current_time = time.time()
        if (self._cached_rate is not None and 
            current_time - self._cache_timestamp < self.refresh_seconds):
            return self._cached_rate
        
        # Use lock to prevent concurrent fetches
        async with self._fetch_lock:
            # Double-check cache after acquiring lock
            if (self._cached_rate is not None and 
                current_time - self._cache_timestamp < self.refresh_seconds):
                return self._cached_rate
            
            if self.fx_source == "FIXED":
                rate = self.fixed_rate
            else:
                # Attempt to fetch from live source
                rate = await self._fetch_live_rate()
                
            # Update cache
            self._cached_rate = rate
            self._cache_timestamp = current_time
            
            return rate
    
    async def _fetch_live_rate(self) -> float:
        """Fetch live SOL/USD rate from configured source."""
        for attempt in range(self.retries):
            try:
                if self.fx_source == "JUPITER":
                    return await self._fetch_jupiter_rate()
                elif self.fx_source == "COINGECKO":
                    return await self._fetch_coingecko_rate()
                else:
                    logger.warning(f"Unknown FX source {self.fx_source}, using fixed rate")
                    return self.fixed_rate
                    
            except Exception as e:
                logger.warning(f"FX fetch attempt {attempt + 1}/{self.retries} failed: {e}")
                if attempt == self.retries - 1:
                    logger.warning(f"All FX fetch attempts failed, falling back to fixed rate {self.fixed_rate}")
                    return self.fixed_rate
                await asyncio.sleep(1)  # Brief delay between retries
        
        return self.fixed_rate
    
    async def _fetch_jupiter_rate(self) -> float:
        """Fetch SOL/USD rate from Jupiter API."""
        url = "https://price.jup.ag/v4/price"
        params = {"ids": "SOL", "vsToken": "USDC"}
        
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # Jupiter API response structure: {"data": {"SOL": {"id": "SOL", "mintSymbol": "SOL", "vsToken": "USDC", "vsTokenSymbol": "USDC", "price": 150.123}}}
                    price = data["data"]["SOL"]["price"]
                    logger.debug(f"Fetched SOL/USD rate from Jupiter: {price}")
                    return float(price)
                else:
                    raise Exception(f"Jupiter API returned status {response.status}")
    
    async def _fetch_coingecko_rate(self) -> float:
        """Fetch SOL/USD rate from CoinGecko API."""
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "solana", "vs_currencies": "usd"}
        
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # CoinGecko API response structure: {"solana": {"usd": 150.123}}
                    price = data["solana"]["usd"]
                    logger.debug(f"Fetched SOL/USD rate from CoinGecko: {price}")
                    return float(price)
                else:
                    raise Exception(f"CoinGecko API returned status {response.status}")
    
    def convert_sol_to_usd(self, sol_amount: float, sol_usd_rate: float) -> float:
        """Convert SOL amount to USD.
        
        Args:
            sol_amount: Amount in SOL
            sol_usd_rate: SOL/USD exchange rate
            
        Returns:
            Equivalent amount in USD
        """
        return sol_amount * sol_usd_rate
    
    def get_cached_rate(self) -> float:
        """Get cached rate without async fetch (for synchronous contexts).
        
        Returns:
            Last cached rate or fixed rate if no cache available
        """
        if self._cached_rate is not None:
            return self._cached_rate
        return self.fixed_rate
    
    def format_usd_value(self, usd_amount: float) -> str:
        """Format USD amount with appropriate precision.
        
        Args:
            usd_amount: Amount in USD
            
        Returns:
            Formatted USD string
        """
        abs_amount = abs(usd_amount)
        if abs_amount >= 1000:
            return f"{usd_amount:.2f}"
        elif abs_amount >= 10:
            return f"{usd_amount:.3f}"
        elif abs_amount >= 0.1:
            return f"{usd_amount:.4f}"
        elif abs_amount >= 0.001:
            return f"{usd_amount:.5f}"
        else:
            return f"{usd_amount:.6f}"
    
    def format_sol_price(self, sol_price: float) -> str:
        """Format SOL price with appropriate precision for token prices.
        
        Args:
            sol_price: Price in SOL
            
        Returns:
            Formatted SOL price string
        """
        if sol_price < 0.000001:
            return f"{sol_price:.2e}"
        elif sol_price < 0.001:
            return f"{sol_price:.8f}"
        elif sol_price < 1:
            return f"{sol_price:.6f}"
        else:
            return f"{sol_price:.4f}"
    
    def format_usd_price(self, usd_price: float) -> str:
        """Format USD price with appropriate precision for token prices.
        
        Args:
            usd_price: Price in USD
            
        Returns:
            Formatted USD price string
        """
        abs_price = abs(usd_price)
        if abs_price < 0.000001:
            return f"{usd_price:.2e}"
        elif abs_price < 0.001:
            return f"{usd_price:.8f}"
        elif abs_price < 1:
            return f"{usd_price:.6f}"
        else:
            return f"{usd_price:.4f}"


# Global FX instance (initialized from environment)
_fx_instance: Optional[FX] = None


def get_fx() -> FX:
    """Get global FX instance, creating it if needed."""
    global _fx_instance
    if _fx_instance is None:
        fx_source = os.getenv("USD_FX_SOURCE", "FIXED")
        fixed_rate = float(os.getenv("FIXED_SOL_USD", "150.0"))
        refresh_seconds = int(os.getenv("FX_REFRESH_SECONDS", "300"))
        timeout_seconds = int(os.getenv("FX_TIMEOUT_SECONDS", "10"))
        retries = int(os.getenv("FX_RETRIES", "3"))
        
        _fx_instance = FX(
            fx_source=fx_source,
            fixed_rate=fixed_rate,
            refresh_seconds=refresh_seconds,
            timeout_seconds=timeout_seconds,
            retries=retries
        )
    return _fx_instance


def reset_fx() -> None:
    """Reset global FX instance (for testing)."""
    global _fx_instance
    _fx_instance = None
