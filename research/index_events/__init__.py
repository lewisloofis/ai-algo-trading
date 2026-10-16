"""Index-rebalance event collection (MSCI / FTSE / IDX) for plan 21."""

INDEX_SYMBOLS = frozenset({"IHSG"})
"""Index series stored beside the stocks in data/daily. They are benchmarks, never tradable names:
excluded from the stock panel, the equal-weight market null, the session calendar and ticker coverage."""
