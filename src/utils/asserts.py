"""
Assertion utilities for numerical comparisons in trading simulations.

This module provides helper functions for approximate equality checks
and assertions, particularly useful for financial calculations where
floating-point precision matters.
"""

import math
from typing import Union
from utils.logger import get_logger

logger = get_logger(__name__)


def approx_equal(
    actual: Union[float, int], 
    expected: Union[float, int], 
    rel: float = 1e-6, 
    abs_tol: float = 1e-9
) -> bool:
    """Check if two numbers are approximately equal.
    
    Uses both relative and absolute tolerance to handle cases where
    one of the numbers might be very small or zero.
    
    Args:
        actual: The actual value
        expected: The expected value
        rel: Relative tolerance (default 1e-6)
        abs_tol: Absolute tolerance (default 1e-9)
        
    Returns:
        True if the numbers are approximately equal
    """
    if actual == expected:
        return True
        
    # Use math.isclose for robust comparison
    return math.isclose(actual, expected, rel_tol=rel, abs_tol=abs_tol)


def assert_approx_equal(
    actual: Union[float, int], 
    expected: Union[float, int], 
    rel: float = 1e-6, 
    abs_tol: float = 1e-9, 
    msg: str = ""
) -> None:
    """Assert that two numbers are approximately equal.
    
    This assertion is only active when DEBUG_VALUATION environment
    variable is set to true. Otherwise, it's a no-op.
    
    Args:
        actual: The actual value
        expected: The expected value  
        rel: Relative tolerance (default 1e-6)
        abs_tol: Absolute tolerance (default 1e-9)
        msg: Optional message for assertion failure
        
    Raises:
        AssertionError: If values are not approximately equal (only when DEBUG_VALUATION=true)
    """
    import os
    
    # Only perform assertions when DEBUG_VALUATION is enabled
    debug_enabled = os.getenv("DEBUG_VALUATION", "false").lower() == "true"
    if not debug_enabled:
        return
        
    if not approx_equal(actual, expected, rel=rel, abs_tol=abs_tol):
        error_msg = f"Values not approximately equal: actual={actual:.10f} expected={expected:.10f} diff={abs(actual-expected):.10f}"
        if msg:
            error_msg = f"{msg}: {error_msg}"
        
        logger.error(f"[ASSERT-FAIL] {error_msg}")
        raise AssertionError(error_msg)


def format_decimal_debug(value: Union[float, int], precision: int = 10) -> str:
    """Format a decimal value for debug output with high precision.
    
    Uses scientific notation for very small values and fixed-point
    for normal values.
    
    Args:
        value: The value to format
        precision: Number of decimal places (default 10)
        
    Returns:
        Formatted string representation
    """
    if abs(value) < 1e-6:
        return f"{value:.2e}"
    else:
        return f"{value:.{precision}f}"