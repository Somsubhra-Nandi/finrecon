"""Synthetic bounded-search challenge generator.

This package is additive.  It owns only the ``bounded-search-v1`` split and
never opens or writes benchmark v3 artifacts.
"""

from finrecon.benchmark.generator_search.config import (
    BENCHMARK_NAME,
    SEARCH_SEED,
    TOOL_CALL_BUDGET,
)

__all__ = ["BENCHMARK_NAME", "SEARCH_SEED", "TOOL_CALL_BUDGET"]
