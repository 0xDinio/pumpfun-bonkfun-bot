"""
Symbol throttle for limiting repeated symbols within time windows.
Prevents symbol flooding by tracking normalized symbol occurrences.
"""

import time
import unicodedata
from collections import deque
from typing import Dict, Set, Tuple


def normalize_symbol(symbol: str) -> str:
    """Normalize symbol for matching purposes.
    
    Args:
        symbol: Raw symbol string
        
    Returns:
        Normalized symbol key for deduplication
    """
    if not symbol:
        return ""
    
    # NFKC normalize and casefold
    normalized = unicodedata.normalize('NFKC', symbol).casefold()
    
    # Remove emoji and non-word characters, keep only alphanumeric and basic punctuation
    cleaned = ''.join(char for char in normalized if char.isalnum() or char in '._-')
    
    # Strip whitespace and limit length
    return cleaned.strip()[:64]


class SymbolThrottle:
    """Throttles symbols based on normalized matching and configurable limits."""
    
    def __init__(
        self,
        per_symbol_limit: int = 2,
        window_seconds: int = 60,
        per_creator_limit: int = 0,
        min_spacing_seconds: int = 0
    ):
        """Initialize symbol throttle.
        
        Args:
            per_symbol_limit: Max accepted events per normalized symbol in window
            window_seconds: Rolling window duration in seconds
            per_creator_limit: Max events per creator in window (0 = disabled)
            min_spacing_seconds: Minimum seconds between same symbol (0 = disabled)
        """
        self.per_symbol_limit = per_symbol_limit
        self.window_seconds = window_seconds
        self.per_creator_limit = per_creator_limit
        self.min_spacing_seconds = min_spacing_seconds
        
        # Track symbol events: normalized_symbol -> deque of timestamps
        self.symbol_events: Dict[str, deque] = {}
        
        # Track last time for symbol spacing: normalized_symbol -> timestamp
        self.symbol_last_time: Dict[str, float] = {}
        
        # Track creator events: creator_address -> deque of timestamps
        self.creator_events: Dict[str, deque] = {}
        
        # Track seen mints to prevent duplicates
        self.seen_mints: Set[str] = set()
    
    def should_accept(
        self, 
        *, 
        mint: str, 
        symbol: str, 
        creator: str | None = None
    ) -> Tuple[bool, str, str]:
        """Check if a symbol should be accepted based on throttling rules.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            creator: Creator address (optional)
            
        Returns:
            Tuple of (should_accept, reason, normalized_key)
            Reasons: duplicate_mint, symbol_quota_exceeded, symbol_spacing, 
                    creator_quota_exceeded, accepted
        """
        current_time = time.time()
        normalized_key = normalize_symbol(symbol)
        
        # Check for duplicate mint
        if mint in self.seen_mints:
            return False, "duplicate_mint", normalized_key
        
        # Clean up old entries first
        self._cleanup_old_entries(current_time)
        
        # Check symbol quota
        if normalized_key in self.symbol_events:
            symbol_count = len(self.symbol_events[normalized_key])
            if symbol_count >= self.per_symbol_limit:
                return False, "symbol_quota_exceeded", normalized_key
        
        # Check minimum spacing
        if (self.min_spacing_seconds > 0 and 
            normalized_key in self.symbol_last_time):
            time_since_last = current_time - self.symbol_last_time[normalized_key]
            if time_since_last < self.min_spacing_seconds:
                return False, "symbol_spacing", normalized_key
        
        # Check creator quota
        if (creator and 
            self.per_creator_limit > 0 and 
            creator in self.creator_events):
            creator_count = len(self.creator_events[creator])
            if creator_count >= self.per_creator_limit:
                return False, "creator_quota_exceeded", normalized_key
        
        # Accept: record the event
        self._record_event(mint, normalized_key, creator, current_time)
        return True, "accepted", normalized_key
    
    def check_without_recording(
        self, 
        *, 
        mint: str, 
        symbol: str, 
        creator: str | None = None
    ) -> Tuple[bool, str, str]:
        """Check if a symbol should be accepted WITHOUT recording the event.
        
        This is used for buy-time guards where we want to double-check
        without modifying the throttle state.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            creator: Creator address (optional)
            
        Returns:
            Tuple of (should_accept, reason, normalized_key)
        """
        current_time = time.time()
        normalized_key = normalize_symbol(symbol)
        
        # Check for duplicate mint
        if mint in self.seen_mints:
            return False, "duplicate_mint", normalized_key
        
        # Clean up old entries first (read-only, doesn't affect the check)
        # Note: We don't actually clean up here since this is read-only
        
        # Check symbol quota
        if normalized_key in self.symbol_events:
            # Count events in current window
            cutoff_time = current_time - self.window_seconds
            valid_events = [t for t in self.symbol_events[normalized_key] if t >= cutoff_time]
            if len(valid_events) >= self.per_symbol_limit:
                return False, "symbol_quota_exceeded", normalized_key
        
        # Check minimum spacing
        if (self.min_spacing_seconds > 0 and 
            normalized_key in self.symbol_last_time):
            time_since_last = current_time - self.symbol_last_time[normalized_key]
            if time_since_last < self.min_spacing_seconds:
                return False, "symbol_spacing", normalized_key
        
        # Check creator quota
        if (creator and 
            self.per_creator_limit > 0 and 
            creator in self.creator_events):
            # Count events in current window
            cutoff_time = current_time - self.window_seconds
            valid_events = [t for t in self.creator_events[creator] if t >= cutoff_time]
            if len(valid_events) >= self.per_creator_limit:
                return False, "creator_quota_exceeded", normalized_key
        
        return True, "accepted", normalized_key
    
    def _cleanup_old_entries(self, current_time: float) -> None:
        """Remove entries older than the window."""
        cutoff_time = current_time - self.window_seconds
        
        # Clean symbol events
        for symbol_key in list(self.symbol_events.keys()):
            events = self.symbol_events[symbol_key]
            while events and events[0] < cutoff_time:
                events.popleft()
            if not events:
                del self.symbol_events[symbol_key]
        
        # Clean creator events
        for creator in list(self.creator_events.keys()):
            events = self.creator_events[creator]
            while events and events[0] < cutoff_time:
                events.popleft()
            if not events:
                del self.creator_events[creator]
    
    def _record_event(
        self, 
        mint: str, 
        normalized_key: str, 
        creator: str | None, 
        timestamp: float
    ) -> None:
        """Record an accepted event."""
        # Record mint
        self.seen_mints.add(mint)
        
        # Record symbol event
        if normalized_key not in self.symbol_events:
            self.symbol_events[normalized_key] = deque()
        self.symbol_events[normalized_key].append(timestamp)
        
        # Update last time for spacing
        self.symbol_last_time[normalized_key] = timestamp
        
        # Record creator event
        if creator and self.per_creator_limit > 0:
            if creator not in self.creator_events:
                self.creator_events[creator] = deque()
            self.creator_events[creator].append(timestamp)
    
    def get_stats(self) -> Dict[str, int]:
        """Get current throttle statistics."""
        return {
            "tracked_symbols": len(self.symbol_events),
            "tracked_creators": len(self.creator_events),
            "seen_mints": len(self.seen_mints)
        }
