"""
Universal trading coordinator that works with any platform.
Cleaned up to remove all platform-specific hardcoding.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

from solders.pubkey import Pubkey

# Windows-compatible uvloop setup
if sys.platform != "win32":
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

from cleanup.modes import (
    handle_cleanup_after_failure,
    handle_cleanup_after_sell,
    handle_cleanup_post_session,
)
from core.client import SolanaClient
from core.priority_fee.manager import PriorityFeeManager
from core.wallet import Wallet
from interfaces.core import Platform, TokenInfo
from monitoring.listener_factory import ListenerFactory
from platforms import get_platform_implementations
from sim import PortfolioSimulator, ValueMode
from trading.base import TradeResult
from trading.platform_aware import PlatformAwareBuyer, PlatformAwareSeller
from trading.position import Position
from utils.logger import get_logger

logger = get_logger(__name__)


class UniversalTrader:
    """Universal trading coordinator that works with any supported platform."""

    def __init__(
        self,
        rpc_endpoint: str,
        wss_endpoint: str,
        private_key: str,
        buy_amount: float,
        buy_slippage: float,
        sell_slippage: float,
        # Platform configuration
        platform: Platform | str = Platform.PUMP_FUN,
        # Listener configuration
        listener_type: str = "logs",
        geyser_endpoint: str | None = None,
        geyser_api_token: str | None = None,
        geyser_auth_type: str = "x-token",
        pumpportal_url: str = "wss://pumpportal.fun/api/data",
        # Trading configuration
        extreme_fast_mode: bool = False,
        extreme_fast_token_amount: int = 30,
        # Exit strategy configuration
        exit_strategy: str = "time_based",
        take_profit_percentage: float | None = None,
        stop_loss_percentage: float | None = None,
        max_hold_time: int | None = None,
        price_check_interval: int = 10,
        # Priority fee configuration
        enable_dynamic_priority_fee: bool = False,
        enable_fixed_priority_fee: bool = True,
        fixed_priority_fee: int = 200_000,
        extra_priority_fee: float = 0.0,
        hard_cap_prior_fee: int = 200_000,
        # Retry and timeout settings
        max_retries: int = 3,
        wait_time_after_creation: int = 15,
        wait_time_after_buy: int = 15,
        wait_time_before_new_token: int = 15,
        max_token_age: int | float = 0.001,
        token_wait_timeout: int = 30,
        # Cleanup settings
        cleanup_mode: str = "disabled",
        cleanup_force_close_with_burn: bool = False,
        cleanup_with_priority_fee: bool = False,
        # Trading filters
        match_string: str | None = None,
        bro_address: str | None = None,
        marry_mode: bool = False,
        yolo_mode: bool = False,
    ):
        """Initialize the universal trader."""
        # Core components
        self.solana_client = SolanaClient(rpc_endpoint)
        
        # Only create wallet if we have a private key (for dry-run mode)
        if private_key and private_key.strip():
            self.wallet = Wallet(private_key)
        else:
            self.wallet = None
            logger.info("No private key provided - running in read-only mode")
            
        self.priority_fee_manager = PriorityFeeManager(
            client=self.solana_client,
            enable_dynamic_fee=enable_dynamic_priority_fee,
            enable_fixed_fee=enable_fixed_priority_fee,
            fixed_fee=fixed_priority_fee,
            extra_fee=extra_priority_fee,
            hard_cap=hard_cap_prior_fee,
        )

        # Platform setup
        if isinstance(platform, str):
            self.platform = Platform(platform)
        else:
            self.platform = platform

        logger.info(f"Initialized Universal Trader for platform: {self.platform.value}")

        # Validate platform support
        try:
            from platforms import platform_factory

            if not platform_factory.registry.is_platform_supported(self.platform):
                raise ValueError(f"Platform {self.platform.value} is not supported")
        except Exception:
            logger.exception("Platform validation failed")
            raise

        # Get platform-specific implementations
        self.platform_implementations = get_platform_implementations(
            self.platform, self.solana_client
        )

        # Create platform-aware traders (will be None if no wallet)
        if self.wallet:
            self.buyer = PlatformAwareBuyer(
                self.solana_client,
                self.wallet,
                self.priority_fee_manager,
                buy_amount,
                buy_slippage,
                max_retries,
                extreme_fast_token_amount,
                extreme_fast_mode,
            )

            self.seller = PlatformAwareSeller(
                self.solana_client,
                self.wallet,
                self.priority_fee_manager,
                sell_slippage,
                max_retries,
            )
            
            # Set console reporter on traders
            self.buyer.console_reporter = self.console_reporter
            self.seller.console_reporter = self.console_reporter
        else:
            self.buyer = None
            self.seller = None
            logger.info("Traders disabled - no wallet available")

        # Initialize the appropriate listener with platform filtering
        self.token_listener = ListenerFactory.create_listener(
            listener_type=listener_type,
            wss_endpoint=wss_endpoint,
            geyser_endpoint=geyser_endpoint,
            geyser_api_token=geyser_api_token,
            geyser_auth_type=geyser_auth_type,
            pumpportal_url=pumpportal_url,
            platforms=[self.platform],  # Only listen for our platform
        )

        # Trading parameters
        self.buy_amount = buy_amount
        self.buy_slippage = buy_slippage
        self.sell_slippage = sell_slippage
        self.max_retries = max_retries
        self.extreme_fast_mode = extreme_fast_mode
        self.extreme_fast_token_amount = extreme_fast_token_amount

        # Exit strategy parameters
        self.exit_strategy = exit_strategy.lower()
        self.take_profit_percentage = take_profit_percentage
        self.stop_loss_percentage = stop_loss_percentage
        self.max_hold_time = max_hold_time
        self.price_check_interval = price_check_interval

        # Timing parameters
        self.wait_time_after_creation = wait_time_after_creation
        self.wait_time_after_buy = wait_time_after_buy
        self.wait_time_before_new_token = wait_time_before_new_token
        self.max_token_age = max_token_age
        self.token_wait_timeout = token_wait_timeout

        # Cleanup parameters
        self.cleanup_mode = cleanup_mode
        self.cleanup_force_close_with_burn = cleanup_force_close_with_burn
        self.cleanup_with_priority_fee = cleanup_with_priority_fee

        # Trading filters/modes
        self.match_string = match_string
        self.bro_address = bro_address
        self.marry_mode = marry_mode
        self.yolo_mode = yolo_mode

        # State tracking
        self.traded_mints: set[Pubkey] = set()
        self.token_queue: asyncio.Queue = asyncio.Queue()
        self.processing: bool = False
        self.processed_tokens: set[str] = set()
        self.token_timestamps: dict[str, float] = {}
        
        # Dry-run and safety flags (will be set from config)
        self.halt_flag: bool = False
        self.dry_run_flag: bool = False
        self.dry_run_duration_seconds: int = 300
        self.portfolio_simulator: PortfolioSimulator | None = None
        
        # Trading state
        self.trades_simulated: int = 0
        self.dry_run_start_time: float = 0.0
        
        # Console reporting
        from monitoring.console_reporter import ConsoleReporter
        self.console_reporter = ConsoleReporter()
        self.console_snapshot_task: asyncio.Task | None = None
        
        # Debug valuation flag
        self.debug_valuation_flag: bool = False

    async def start(self) -> None:
        """Start the trading bot and listen for new tokens."""
        logger.info(f"Starting Universal Trader for {self.platform.value}")
        logger.info(
            f"Match filter: {self.match_string if self.match_string else 'None'}"
        )
        logger.info(
            f"Creator filter: {self.bro_address if self.bro_address else 'None'}"
        )
        logger.info(f"Marry mode: {self.marry_mode}")
        logger.info(f"YOLO mode: {self.yolo_mode}")
        logger.info(f"Exit strategy: {self.exit_strategy}")

        if self.exit_strategy == "tp_sl":
            logger.info(
                f"Take profit: {self.take_profit_percentage * 100 if self.take_profit_percentage else 'None'}%"
            )
            logger.info(
                f"Stop loss: {self.stop_loss_percentage * 100 if self.stop_loss_percentage else 'None'}%"
            )
            logger.info(
                f"Max hold time: {self.max_hold_time if self.max_hold_time else 'None'} seconds"
            )

        logger.info(f"Max token age: {self.max_token_age} seconds")

        # Run startup test snapshot if console reporter is enabled
        if self.console_reporter.is_enabled:
            await self.console_reporter.startup_test_snapshot()
            
        # Print comprehensive flags for debugging
        if self.console_reporter:
            console_flags = f"console={self.console_reporter.is_enabled} trades={self.console_reporter.verbose_trade_lines} interval={self.console_reporter.snapshot_interval} include_positions={self.console_reporter.verbose_include_positions}"
        else:
            console_flags = "console=False trades=False interval=0 include_positions=False"
            
        print(f"[VERBOSE] flags: {console_flags} dry_run={self.dry_run_flag} debug_valuation={self.debug_valuation_flag}", flush=True)

        try:
            health_resp = await self.solana_client.get_health()
            logger.info(f"RPC warm-up successful (getHealth passed: {health_resp})")
        except Exception as e:
            logger.warning(f"RPC warm-up failed: {e!s}")

        try:
            # Choose operating mode based on dry_run and yolo_mode
            if self.dry_run_flag:
                # Dry-run mode: run for specified duration
                logger.info(
                    f"Running in DRY-RUN mode for {self.dry_run_duration_seconds} seconds"
                )
                
                # Run manual test if debug mode is enabled
                if self.debug_valuation_flag:
                    await self.simulate_manual_trade(
                        symbol="TEST", 
                        p_buy=0.00009, 
                        p_sell=0.000095, 
                        amount_sol=0.1
                    )
                
                await self._run_dry_run_simulation()
            elif not self.yolo_mode:
                # Single token mode: process one token and exit
                logger.info(
                    "Running in single token mode - will process one token and exit"
                )
                token_info = await self._wait_for_token()
                if token_info:
                    await self._handle_token(token_info)
                    logger.info("Finished processing single token. Exiting...")
                else:
                    logger.info(
                        f"No suitable token found within timeout period ({self.token_wait_timeout}s). Exiting..."
                    )
            else:
                # Continuous mode: process tokens until interrupted
                logger.info(
                    "Running in continuous mode - will process tokens until interrupted"
                )
                processor_task = asyncio.create_task(self._process_token_queue())

                try:
                    await self.token_listener.listen_for_tokens(
                        lambda token: self._queue_token(token),
                        self.match_string,
                        self.bro_address,
                    )
                except Exception:
                    logger.exception("Token listening stopped due to error")
                finally:
                    processor_task.cancel()
                    try:
                        await processor_task
                    except asyncio.CancelledError:
                        pass

        except Exception:
            logger.exception("Trading stopped due to error")

        finally:
            await self._cleanup_resources()
            logger.info("Universal Trader has shut down")

    async def _run_dry_run_simulation(self) -> None:
        """Run dry-run simulation for the specified duration."""
        import os
        from time import monotonic
        
        self.dry_run_start_time = monotonic()
        self.trades_simulated = 0
        
        # Create periodic snapshot task for snapshots
        snapshot_task = asyncio.create_task(self._periodic_snapshot_task())
        
        # Create console snapshot task if enabled
        if self.console_reporter.is_enabled:
            self.console_snapshot_task = asyncio.create_task(self._console_snapshot_task())
        
        # Create token processing task
        processor_task = asyncio.create_task(self._process_token_queue())
        
        try:
            # Start listening for tokens
            listener_task = asyncio.create_task(
                self.token_listener.listen_for_tokens(
                    lambda token: self._queue_token_for_simulation(token),
                    self.match_string,
                    self.bro_address,
                )
            )
            
            # Wait for duration or HALT signal
            while True:
                current_time = monotonic()
                elapsed = current_time - self.dry_run_start_time
                
                # Check HALT flag dynamically (reload from environment)
                halt_flag = int(os.getenv("HALT", "0")) == 1
                if halt_flag:
                    logger.warning("HALT signal detected - stopping dry-run simulation early")
                    break
                    
                if elapsed >= self.dry_run_duration_seconds:
                    logger.info(f"Dry-run duration complete ({self.dry_run_duration_seconds}s)")
                    break
                    
                # Check every second
                await asyncio.sleep(1)
                
        except Exception:
            logger.exception("Error during dry-run simulation")
        finally:
            # Cancel all tasks
            listener_task.cancel()
            processor_task.cancel()
            snapshot_task.cancel()
            if self.console_snapshot_task:
                self.console_snapshot_task.cancel()
            
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
            try:
                await processor_task  
            except asyncio.CancelledError:
                pass
            try:
                await snapshot_task
            except asyncio.CancelledError:
                pass
            try:
                if self.console_snapshot_task:
                    await self.console_snapshot_task
            except asyncio.CancelledError:
                pass
                
            # Print final summary
            await self._print_dry_run_summary()

    async def _periodic_snapshot_task(self) -> None:
        """Periodically log portfolio snapshots during simulation."""
        await asyncio.sleep(30)  # Wait 30 seconds before first snapshot
        
        while True:
            try:
                if self.portfolio_simulator:
                    await self.portfolio_simulator.log_periodic_snapshot()
                await asyncio.sleep(30)  # Snapshot every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in periodic snapshot task")
                await asyncio.sleep(30)

    async def _console_snapshot_task(self) -> None:
        """Periodically print console portfolio snapshots."""
        interval = self.console_reporter.snapshot_interval
        await asyncio.sleep(interval)  # Wait before first snapshot
        
        while True:
            try:
                if self.portfolio_simulator:
                    portfolio_value = await self.portfolio_simulator.get_portfolio_value()
                    stats = self.portfolio_simulator.get_summary_stats()
                    
                    await self.console_reporter.snapshot(
                        sol_balance=stats["current_sol_balance"],
                        total_value=portfolio_value,
                        realized_pnl=stats["realized_pnl"],
                        positions=stats["positions"]
                    )
                elif self.wallet:
                    # For live trading, we don't have a portfolio simulator
                    # Just report basic SOL balance (if we can get it)
                    await self.console_reporter.snapshot(
                        sol_balance=0.0,  # Would need to query actual balance
                        total_value=0.0,
                        realized_pnl=0.0,
                        positions={}
                    )
                
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in console snapshot task")
                await asyncio.sleep(interval)

    async def _queue_token_for_simulation(self, token_info: TokenInfo) -> None:
        """Queue a token for simulation processing."""
        token_key = str(token_info.mint)

        if token_key in self.processed_tokens:
            logger.debug(f"Token {token_info.symbol} already processed. Skipping...")
            return

        # Record timestamp when token was discovered
        self.token_timestamps[token_key] = monotonic()

        await self.token_queue.put(token_info)
        logger.info(
            f"Queued new token for simulation: {token_info.symbol} ({token_info.mint}) on {token_info.platform.value}"
        )

    async def _get_token_price_for_simulation(self, token_info) -> float | None:
        """Get real token price for simulation using same logic as live trading."""
        from sim.quote_failures import QuoteFailureLogger
        import random
        
        max_retries = 8
        base_delay = 1.0  # seconds
        quote_logger = QuoteFailureLogger()
        
        # Get platform-specific implementations (same as live trading)
        from platforms import get_platform_implementations
        implementations = get_platform_implementations(token_info.platform, self.solana_client)
        address_provider = implementations.address_provider
        curve_manager = implementations.curve_manager
        
        # Use existing bonding curve if available from logs, otherwise derive
        if hasattr(token_info, 'bonding_curve') and token_info.bonding_curve:
            pool_address = token_info.bonding_curve
            logger.info(f"[QUOTE-DEBUG] using existing bonding_curve from logs: {pool_address}")
        else:
            pool_address = self._get_pool_address_for_simulation(token_info, address_provider)
            logger.info(f"[QUOTE-DEBUG] derived pool_address: {pool_address}")
        
        # Comprehensive debug info (requirement A)
        logger.info(f"[QUOTE-DEBUG] platform={token_info.platform.value} mint={token_info.mint}")
        logger.info(f"[QUOTE-DEBUG] derived_pool_addr={pool_address}")
        logger.info(f"[QUOTE-DEBUG] commitment=confirmed")
        logger.info(f"[QUOTE-DEBUG] program_id_used={address_provider.program_id}")
        logger.info(f"[QUOTE-DEBUG] source=curve_manager.calculate_price")
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Calculate retry delay with exponential backoff + jitter
                if attempt > 0:
                    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                    logger.info(f"[QUOTE-DEBUG] attempt={attempt+1} error=\"{last_error}\"")
                    await asyncio.sleep(delay)
                
                # Calculate price using curve manager with confirmed commitment
                token_price_sol = await curve_manager.calculate_price(pool_address, commitment="confirmed")
                
                logger.info(f"DRY_RUN: Got real quote for {token_info.symbol}: {token_price_sol:.8f} SOL (attempt {attempt+1})")
                return token_price_sol
                
            except Exception as e:
                last_error = str(e)
                
                if attempt < max_retries - 1:
                    # Continue retrying
                    logger.warning(f"DRY_RUN: Quote attempt {attempt+1} failed for {token_info.symbol}: {e}")
                    continue
                else:
                    # Final attempt failed - log structured failure
                    logger.warning(f"[QUOTE-DEBUG] platform={token_info.platform.value} pool_addr={pool_address} source=curve_manager.calculate_price reason=\"{e}\" final_attempt=True")
                    
                    # Log to quote_failures.ndjson
                    quote_logger.log_failure(
                        mint=str(token_info.mint),
                        derived_addr=str(pool_address),
                        commitment="confirmed",
                        rpc_url=self.solana_client.rpc_endpoint,
                        error=str(e),
                        platform=token_info.platform.value,
                        program_id=str(address_provider.program_id),
                        attempts=max_retries
                    )
                    
                    # Try fallback pricing using virtual reserves from create event
                    fallback_price = self._get_fallback_price_from_virtual_reserves(token_info)
                    if fallback_price:
                        logger.warning(f"[QUOTE-FALLBACK] using virtual reserves price for mint={token_info.mint} price={fallback_price:.10f}")
                        return fallback_price
                    
                    logger.warning(f"DRY_RUN: Failed to get price for {token_info.symbol} after {max_retries} attempts: {e}")
                    return None
                    
        return None
    
    def _get_fallback_price_from_virtual_reserves(self, token_info) -> float | None:
        """Calculate fallback price from virtual reserves if available from create event."""
        try:
            # Check if token_info has virtual reserves from the create event
            if hasattr(token_info, 'virtual_sol_reserves') and hasattr(token_info, 'virtual_token_reserves'):
                virtual_sol = getattr(token_info, 'virtual_sol_reserves', 0)
                virtual_tokens = getattr(token_info, 'virtual_token_reserves', 0)
                
                if virtual_tokens > 0 and virtual_sol > 0:
                    # Price = virtual_sol_reserves / virtual_token_reserves (in SOL/token)
                    from core.pubkeys import LAMPORTS_PER_SOL, TOKEN_DECIMALS
                    price_lamports = virtual_sol / virtual_tokens
                    price_sol = price_lamports * (10**TOKEN_DECIMALS) / LAMPORTS_PER_SOL
                    return price_sol
            
            # Also check if they're in a metadata dict
            if hasattr(token_info, 'metadata') and isinstance(token_info.metadata, dict):
                virtual_sol = token_info.metadata.get('virtual_sol_reserves', 0)
                virtual_tokens = token_info.metadata.get('virtual_token_reserves', 0)
                
                if virtual_tokens > 0 and virtual_sol > 0:
                    from core.pubkeys import LAMPORTS_PER_SOL, TOKEN_DECIMALS
                    price_lamports = virtual_sol / virtual_tokens
                    price_sol = price_lamports * (10**TOKEN_DECIMALS) / LAMPORTS_PER_SOL
                    return price_sol
                    
        except Exception as e:
            logger.debug(f"Failed to calculate fallback price from virtual reserves: {e}")
            
        return None

    async def simulate_manual_trade(self, symbol: str, p_buy: float, p_sell: float, amount_sol: float = 0.1):
        """DEBUG ONLY: Simulate a manual BUY then SELL with known prices to test portfolio math."""
        if not self.debug_valuation_flag:
            return
            
        logger.info(f"[MANUAL-TEST] Starting manual trade simulation: {symbol} buy={p_buy:.10f} sell={p_sell:.10f} amount={amount_sol}")
        
        try:
            # Create a fake token info for manual testing
            from interfaces.core import TokenInfo, Platform
            from solders.pubkey import Pubkey
            
            fake_mint = Pubkey.from_string("11111111111111111111111111111112")  # System program as placeholder
            fake_token = TokenInfo(
                mint=fake_mint,
                name=f"Manual Test {symbol}",
                symbol=symbol,
                uri="",
                platform=Platform.PUMP_FUN,
                creator=fake_mint,
                user=fake_mint
            )
            
            # Simulate BUY
            estimated_fee = 0.005
            sim_result = await self.portfolio_simulator.simulate_buy(
                mint=fake_mint,
                symbol=symbol,
                sol_amount=amount_sol,
                price_per_token=p_buy,
                fee_sol=estimated_fee
            )
            
            if sim_result and sim_result.get("success"):
                qty = sim_result["tokens_received"]
                logger.info(f"[MANUAL-TEST] BUY completed, qty={qty:.6f}")
                
                # Wait a moment, then simulate SELL
                await asyncio.sleep(0.1)
                
                sell_result = await self.portfolio_simulator.simulate_sell(
                    mint=fake_mint,
                    symbol=symbol,
                    price_per_token=p_sell,
                    sell_percentage=1.0,  # Sell 100%
                    fee_sol=estimated_fee
                )
                
                if sell_result and sell_result.get("success"):
                    sol_received = sell_result.get("sol_received", "unknown")
                    logger.info(f"[MANUAL-TEST] SELL completed, sol_received={sol_received}")
                
        except Exception as e:
            logger.error(f"[MANUAL-TEST] Failed: {e}")

    def _get_pool_address_for_simulation(self, token_info, address_provider):
        """Get pool address for simulation (same logic as PlatformAwareBuyer)."""
        from interfaces.core import Platform
        
        # Try to get the address from token_info first, then derive if needed
        if token_info.platform == Platform.PUMP_FUN:
            if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
                return token_info.bonding_curve
        elif token_info.platform == Platform.LETS_BONK:
            if hasattr(token_info, "pool_state") and token_info.pool_state:
                return token_info.pool_state
        
        # Fallback to deriving the address using platform provider
        return address_provider.derive_pool_address(token_info.mint)

    async def _print_dry_run_summary(self) -> None:
        """Print final dry-run simulation summary."""
        if not self.portfolio_simulator:
            logger.warning("No portfolio simulator available for summary")
            return
            
        try:
            final_value = await self.portfolio_simulator.get_portfolio_value()
            stats = self.portfolio_simulator.get_summary_stats()
            
            starting_sol = stats["starting_sol"]
            realized_pnl = stats["realized_pnl"]
            num_positions = stats["num_positions"]
            
            # Calculate total PnL and percentage
            total_pnl = final_value - starting_sol
            pnl_percentage = (total_pnl / starting_sol) * 100 if starting_sol > 0 else 0
            
            elapsed = monotonic() - self.dry_run_start_time
            
            # Print summary
            logger.info("=" * 60)
            logger.info("DRY-RUN SIMULATION SUMMARY")
            logger.info("=" * 60)
            logger.info(f"Duration: {elapsed:.1f} seconds")
            logger.info(f"Total trades simulated: {self.trades_simulated}")
            logger.info(f"Starting portfolio value: {starting_sol:.4f} SOL")
            logger.info(f"Final portfolio value: {final_value:.4f} SOL")
            logger.info(f"Total PnL: {total_pnl:+.4f} SOL ({pnl_percentage:+.2f}%)")
            logger.info(f"Realized PnL: {realized_pnl:+.4f} SOL")
            logger.info(f"Active positions: {num_positions}")
            logger.info(f"SOL balance: {stats['current_sol_balance']:.4f}")
            
            if num_positions > 0:
                logger.info("Active positions:")
                for mint_str, pos in stats["positions"].items():
                    logger.info(f"  {pos['symbol']}: {pos['amount']:.2f} tokens @ {pos['entry_price']:.6f} SOL")
                    
            logger.info("=" * 60)
            
        except Exception:
            logger.exception("Error generating dry-run summary")

    async def _wait_for_token(self) -> TokenInfo | None:
        """Wait for a single token to be detected."""
        # Create a one-time event to signal when a token is found
        token_found = asyncio.Event()
        found_token = None

        async def token_callback(token: TokenInfo) -> None:
            nonlocal found_token
            token_key = str(token.mint)

            # Only process if not already processed and fresh
            if token_key not in self.processed_tokens:
                # Record when the token was discovered
                self.token_timestamps[token_key] = monotonic()
                found_token = token
                self.processed_tokens.add(token_key)
                token_found.set()

        listener_task = asyncio.create_task(
            self.token_listener.listen_for_tokens(
                token_callback,
                self.match_string,
                self.bro_address,
            )
        )

        # Wait for a token with a timeout
        try:
            logger.info(
                f"Waiting for a suitable token (timeout: {self.token_wait_timeout}s)..."
            )
            await asyncio.wait_for(token_found.wait(), timeout=self.token_wait_timeout)
            logger.info(f"Found token: {found_token.symbol} ({found_token.mint})")
            return found_token
        except TimeoutError:
            logger.info(
                f"Timed out after waiting {self.token_wait_timeout}s for a token"
            )
            return None
        finally:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_resources(self) -> None:
        """Perform cleanup operations before shutting down."""
        if self.traded_mints:
            try:
                logger.info(f"Cleaning up {len(self.traded_mints)} traded token(s)...")
                await handle_cleanup_post_session(
                    self.solana_client,
                    self.wallet,
                    list(self.traded_mints),
                    self.priority_fee_manager,
                    self.cleanup_mode,
                    self.cleanup_with_priority_fee,
                    self.cleanup_force_close_with_burn,
                )
            except Exception:
                logger.exception("Error during cleanup")

        old_keys = {k for k in self.token_timestamps if k not in self.processed_tokens}
        for key in old_keys:
            self.token_timestamps.pop(key, None)

        await self.solana_client.close()

    async def _queue_token(self, token_info: TokenInfo) -> None:
        """Queue a token for processing if not already processed."""
        token_key = str(token_info.mint)

        if token_key in self.processed_tokens:
            logger.debug(f"Token {token_info.symbol} already processed. Skipping...")
            return

        # Record timestamp when token was discovered
        self.token_timestamps[token_key] = monotonic()

        await self.token_queue.put(token_info)
        logger.info(
            f"Queued new token: {token_info.symbol} ({token_info.mint}) on {token_info.platform.value}"
        )

    async def _process_token_queue(self) -> None:
        """Continuously process tokens from the queue, only if they're fresh."""
        while True:
            try:
                token_info = await self.token_queue.get()
                token_key = str(token_info.mint)

                # Check if token is still "fresh"
                current_time = monotonic()
                token_age = current_time - self.token_timestamps.get(
                    token_key, current_time
                )

                if token_age > self.max_token_age:
                    logger.info(
                        f"Skipping token {token_info.symbol} - too old ({token_age:.1f}s > {self.max_token_age}s)"
                    )
                    continue

                self.processed_tokens.add(token_key)

                logger.info(
                    f"Processing fresh token: {token_info.symbol} (age: {token_age:.1f}s)"
                )
                await self._handle_token(token_info)

            except asyncio.CancelledError:
                logger.info("Token queue processor was cancelled")
                break
            except Exception:
                logger.exception("Error in token queue processor")
            finally:
                self.token_queue.task_done()

    async def _handle_token(self, token_info: TokenInfo) -> None:
        """Handle a new token creation event."""
        try:
            # Validate that token is for our platform
            if token_info.platform != self.platform:
                logger.warning(
                    f"Token platform mismatch: expected {self.platform.value}, got {token_info.platform.value}"
                )
                return

            # Wait for pool/curve to stabilize (unless in extreme fast mode)
            if not self.extreme_fast_mode:
                await self._save_token_info(token_info)
                logger.info(
                    f"Waiting for {self.wait_time_after_creation} seconds for the pool/curve to stabilize..."
                )
                await asyncio.sleep(self.wait_time_after_creation)

            # Buy token
            logger.info(
                f"Buying {self.buy_amount:.6f} SOL worth of {token_info.symbol} on {token_info.platform.value}..."
            )
            
            if self.dry_run_flag and self.portfolio_simulator:
                # Simulate the buy instead of executing it
                logger.info(f"WOULD_BUY {token_info.symbol} for {self.buy_amount:.6f} SOL")
                
                # Get real token price using same logic as live trading
                try:
                    real_price = await self._get_token_price_for_simulation(token_info)
                    if real_price is None or real_price <= 0:
                        logger.warning(f"DRY_RUN: Unable to get quote for {token_info.symbol}, skipping trade")
                        return
                    
                    # Estimate transaction fees (simplified model)
                    estimated_fee = 0.005  # ~0.005 SOL for priority fee + tx fee
                    
                    sim_result = await self.portfolio_simulator.simulate_buy(
                        mint=token_info.mint,
                        symbol=token_info.symbol,
                        sol_amount=self.buy_amount,
                        price_per_token=real_price,
                        fee_sol=estimated_fee
                    )
                except Exception as e:
                    logger.warning(f"DRY_RUN: Price fetch failed for {token_info.symbol}: {e}, skipping trade")
                    return
                
                if sim_result["success"]:
                    # Create a simulated buy result
                    buy_result = TradeResult(
                        success=True,
                        platform=token_info.platform,
                        tx_signature="DRY_RUN_BUY_" + str(token_info.mint)[:16],
                        amount=sim_result["tokens_received"],
                        price=real_price,
                    )
                    self.trades_simulated += 1
                    await self._handle_successful_buy(token_info, buy_result)
                else:
                    buy_result = TradeResult(
                        success=False,
                        platform=token_info.platform,
                        error_message=sim_result.get("error", "Simulation failed"),
                    )
                    await self._handle_failed_buy(token_info, buy_result)
            else:
                # Execute real transaction
                buy_result: TradeResult = await self.buyer.execute(token_info)

                if buy_result.success:
                    await self._handle_successful_buy(token_info, buy_result)
                else:
                    await self._handle_failed_buy(token_info, buy_result)

            # Only wait for next token in yolo mode
            if self.yolo_mode:
                logger.info(
                    f"YOLO mode enabled. Waiting {self.wait_time_before_new_token} seconds before looking for next token..."
                )
                await asyncio.sleep(self.wait_time_before_new_token)

        except Exception:
            logger.exception(f"Error handling token {token_info.symbol}")

    async def _handle_successful_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle successful token purchase."""
        logger.info(
            f"Successfully bought {token_info.symbol} on {token_info.platform.value}"
        )
        self._log_trade(
            "buy",
            token_info,
            buy_result.price,
            buy_result.amount,
            buy_result.tx_signature,
        )
        self.traded_mints.add(token_info.mint)

        # Choose exit strategy
        if not self.marry_mode:
            if self.exit_strategy == "tp_sl":
                await self._handle_tp_sl_exit(token_info, buy_result)
            elif self.exit_strategy == "time_based":
                await self._handle_time_based_exit(token_info)
            elif self.exit_strategy == "manual":
                logger.info("Manual exit strategy - position will remain open")
        else:
            logger.info("Marry mode enabled. Skipping sell operation.")

    async def _handle_failed_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle failed token purchase."""
        logger.error(f"Failed to buy {token_info.symbol}: {buy_result.error_message}")
        # Close ATA if enabled
        await handle_cleanup_after_failure(
            self.solana_client,
            self.wallet,
            token_info.mint,
            self.priority_fee_manager,
            self.cleanup_mode,
            self.cleanup_with_priority_fee,
            self.cleanup_force_close_with_burn,
        )

    async def _handle_tp_sl_exit(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle take profit/stop loss exit strategy."""
        # Create position
        position = Position.create_from_buy_result(
            mint=token_info.mint,
            symbol=token_info.symbol,
            entry_price=buy_result.price,
            quantity=buy_result.amount,
            take_profit_percentage=self.take_profit_percentage,
            stop_loss_percentage=self.stop_loss_percentage,
            max_hold_time=self.max_hold_time,
        )

        logger.info(f"Created position: {position}")
        if position.take_profit_price:
            logger.info(f"Take profit target: {position.take_profit_price:.8f} SOL")
        if position.stop_loss_price:
            logger.info(f"Stop loss target: {position.stop_loss_price:.8f} SOL")

        # Monitor position until exit condition is met
        await self._monitor_position_until_exit(token_info, position)

    async def _handle_time_based_exit(self, token_info: TokenInfo) -> None:
        """Handle legacy time-based exit strategy."""
        logger.info(f"Waiting for {self.wait_time_after_buy} seconds before selling...")
        await asyncio.sleep(self.wait_time_after_buy)

        logger.info(f"Selling {token_info.symbol}...")
        
        if self.dry_run_flag and self.portfolio_simulator:
            # Simulate the sell instead of executing it
            logger.info(f"WOULD_SELL {token_info.symbol}")
            
            # Get real token price using same logic as live trading
            try:
                real_price = await self._get_token_price_for_simulation(token_info)
                if real_price is None or real_price <= 0:
                    logger.warning(f"DRY_RUN: Unable to get quote for {token_info.symbol}, skipping sell")
                    return
                
                # Estimate transaction fees (simplified model)
                estimated_fee = 0.005  # ~0.005 SOL for priority fee + tx fee
                
                sim_result = await self.portfolio_simulator.simulate_sell(
                    mint=token_info.mint,
                    symbol=token_info.symbol,
                    price_per_token=real_price,
                    sell_percentage=1.0,  # Sell entire position
                    fee_sol=estimated_fee
                )
            except Exception as e:
                logger.warning(f"DRY_RUN: Price fetch failed for {token_info.symbol}: {e}, skipping sell")
                return
            
            if sim_result["success"]:
                sell_result = TradeResult(
                    success=True,
                    platform=token_info.platform,
                    tx_signature="DRY_RUN_SELL_" + str(token_info.mint)[:16],
                    amount=sim_result["tokens_sold"],
                    price=real_price,
                )
                self.trades_simulated += 1
                logger.info(f"Successfully simulated sale of {token_info.symbol}")
                self._log_trade(
                    "sell",
                    token_info,
                    sell_result.price,
                    sell_result.amount,
                    sell_result.tx_signature,
                )
            else:
                sell_result = TradeResult(
                    success=False,
                    platform=token_info.platform,
                    error_message=sim_result.get("error", "Simulation failed"),
                )
                logger.error(
                    f"Failed to simulate sale of {token_info.symbol}: {sell_result.error_message}"
                )
        else:
            # Execute real transaction  
            sell_result: TradeResult = await self.seller.execute(token_info)

            if sell_result.success:
                logger.info(f"Successfully sold {token_info.symbol}")
                self._log_trade(
                    "sell",
                    token_info,
                    sell_result.price,
                    sell_result.amount,
                    sell_result.tx_signature,
                )
                # Close ATA if enabled
                await handle_cleanup_after_sell(
                    self.solana_client,
                    self.wallet,
                    token_info.mint,
                    self.priority_fee_manager,
                    self.cleanup_mode,
                    self.cleanup_with_priority_fee,
                    self.cleanup_force_close_with_burn,
                )
            else:
                logger.error(
                    f"Failed to sell {token_info.symbol}: {sell_result.error_message}"
                )

    async def _monitor_position_until_exit(
        self, token_info: TokenInfo, position: Position
    ) -> None:
        """Monitor a position until exit conditions are met."""
        logger.info(
            f"Starting position monitoring (check interval: {self.price_check_interval}s)"
        )

        # Get pool address for price monitoring using platform-agnostic method
        pool_address = self._get_pool_address(token_info)
        curve_manager = self.platform_implementations.curve_manager

        while position.is_active:
            try:
                # Get current price from pool/curve
                current_price = await curve_manager.calculate_price(pool_address)

                # Check if position should be exited
                should_exit, exit_reason = position.should_exit(current_price)

                if should_exit and exit_reason:
                    logger.info(f"Exit condition met: {exit_reason.value}")
                    logger.info(f"Current price: {current_price:.8f} SOL")

                    # Log PnL before exit
                    pnl = position.get_pnl(current_price)
                    logger.info(
                        f"Position PnL: {pnl['price_change_pct']:.2f}% ({pnl['unrealized_pnl_sol']:.6f} SOL)"
                    )

                    # Execute sell based on dry-run vs live mode
                    if self.dry_run_flag and self.portfolio_simulator:
                        # DRY RUN: Simulate the sell instead of executing it
                        logger.info(f"WOULD_SELL {token_info.symbol} (position exit)")
                        
                        # Get real token price using same logic as buy
                        try:
                            real_price = await self._get_token_price_for_simulation(token_info)
                            if real_price is None or real_price <= 0:
                                logger.warning(f"DRY_RUN: Unable to get quote for {token_info.symbol}, skipping exit")
                                break  # Exit monitoring loop since we can't get price
                            
                            # Estimate transaction fees (simplified model)
                            estimated_fee = 0.005
                            
                            # Simulate the sell (100% of position)
                            sim_result = await self.portfolio_simulator.simulate_sell(
                                mint=token_info.mint,
                                symbol=token_info.symbol,
                                price_per_token=real_price,
                                sell_percentage=1.0,  # Exit full position
                                fee_sol=estimated_fee
                            )
                            
                            if sim_result and sim_result.get("success"):
                                # Close position with simulated exit price
                                position.close_position(real_price, exit_reason)
                                
                                logger.info(f"Successfully simulated exit: {exit_reason.value}")
                                
                                # Log post-exit debug info
                                if self.debug_valuation_flag:
                                    post_value = await self.portfolio_simulator.get_portfolio_value()
                                    logger.info(f"[DEBUG] post-exit-dry: sol={self.portfolio_simulator.sol_balance:.6f} value={post_value:.6f} realized_pnl={self.portfolio_simulator.realized_pnl:.6f}")
                                
                                break  # Exit monitoring loop
                            else:
                                logger.error(f"DRY_RUN: Failed to simulate sell for {token_info.symbol}: {sim_result.get('error', 'Unknown error')}")
                                # Keep monitoring in case we can retry
                                
                        except Exception as e:
                            logger.exception(f"DRY_RUN: Error simulating sell for {token_info.symbol}: {e}")
                            # Keep monitoring in case error is transient
                    else:
                        # LIVE MODE: Execute actual sell
                        sell_result = await self.seller.execute(token_info)

                        if sell_result.success:
                            # Close position with actual exit price
                            position.close_position(sell_result.price, exit_reason)

                            logger.info(
                                f"Successfully exited position: {exit_reason.value}"
                            )
                            self._log_trade(
                                "sell",
                                token_info,
                                sell_result.price,
                                sell_result.amount,
                                sell_result.tx_signature,
                            )

                            # Log final PnL
                            final_pnl = position.get_pnl()
                            logger.info(
                                f"Final PnL: {final_pnl['price_change_pct']:.2f}% ({final_pnl['unrealized_pnl_sol']:.6f} SOL)"
                            )

                            # Close ATA if enabled
                            await handle_cleanup_after_sell(
                                self.solana_client,
                                self.wallet,
                                token_info.mint,
                                self.priority_fee_manager,
                                self.cleanup_mode,
                                self.cleanup_with_priority_fee,
                                self.cleanup_force_close_with_burn,
                            )
                            break  # Exit monitoring loop
                        else:
                            logger.error(
                                f"Failed to exit position: {sell_result.error_message}"
                            )
                            # Keep monitoring in case sell can be retried

                    break
                else:
                    # Log current status
                    pnl = position.get_pnl(current_price)
                    logger.debug(
                        f"Position status: {current_price:.8f} SOL ({pnl['price_change_pct']:+.2f}%)"
                    )

                # Wait before next price check
                await asyncio.sleep(self.price_check_interval)

            except Exception:
                logger.exception("Error monitoring position")
                await asyncio.sleep(
                    self.price_check_interval
                )  # Continue monitoring despite errors

    def _get_pool_address(self, token_info: TokenInfo) -> Pubkey:
        """Get the pool/curve address for price monitoring using platform-agnostic method."""
        address_provider = self.platform_implementations.address_provider

        # Use platform-specific logic to get the appropriate address
        if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
            return token_info.bonding_curve
        elif hasattr(token_info, "pool_state") and token_info.pool_state:
            return token_info.pool_state
        else:
            # Fallback to deriving the address using platform provider
            return address_provider.derive_pool_address(token_info.mint)

    async def _save_token_info(self, token_info: TokenInfo) -> None:
        """Save token information to a file."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)
            file_path = trades_dir / f"{token_info.mint}.txt"

            # Convert to dictionary for saving - platform-agnostic
            token_dict = {
                "name": token_info.name,
                "symbol": token_info.symbol,
                "uri": token_info.uri,
                "mint": str(token_info.mint),
                "platform": token_info.platform.value,
                "user": str(token_info.user) if token_info.user else None,
                "creator": str(token_info.creator) if token_info.creator else None,
                "creation_timestamp": token_info.creation_timestamp,
            }

            # Add platform-specific fields only if they exist
            platform_fields = {
                "bonding_curve": token_info.bonding_curve,
                "associated_bonding_curve": token_info.associated_bonding_curve,
                "creator_vault": token_info.creator_vault,
                "pool_state": token_info.pool_state,
                "base_vault": token_info.base_vault,
                "quote_vault": token_info.quote_vault,
            }

            for field_name, field_value in platform_fields.items():
                if field_value is not None:
                    token_dict[field_name] = str(field_value)

            file_path.write_text(json.dumps(token_dict, indent=2))

            logger.info(f"Token information saved to {file_path}")
        except OSError:
            logger.exception("Failed to save token information")

    def _log_trade(
        self,
        action: str,
        token_info: TokenInfo,
        price: float,
        amount: float,
        tx_hash: str | None,
    ) -> None:
        """Log trade information."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)

            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "platform": token_info.platform.value,
                "token_address": str(token_info.mint),
                "symbol": token_info.symbol,
                "price": price,
                "amount": amount,
                "tx_hash": str(tx_hash) if tx_hash else None,
            }

            log_file_path = trades_dir / "trades.log"
            with log_file_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to log trade information")


# Backward compatibility alias
PumpTrader = UniversalTrader  # Legacy name for backward compatibility
