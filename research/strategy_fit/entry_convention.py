"""
strategy_fit/entry_convention.py — explicit entry-price convention for daily
(and event) families (harness defect D1).

A daily signal is computed from close_s, which is only known after the session
ends, so no real order can transact at close_s. Live (strategy/strategies/
rebalance, strategy_fit/target_emitter.py) enters on the NEXT session. Row s of
every signal frame is the signal day; ``forward_return`` is measured from the
price of session s+1:

    next_close              close_{s+1} -> close_{s+1+hold}   DEFAULT (live bot
                            trades near the close via --rebalance-at)
    next_open               open_{s+1}  -> open_{s+1+hold}    sensitivity
    signal_close_LOOKAHEAD  close_s     -> close_{s+hold}     the pre-fix
                            defect; unattainable live. Only for reproducing
                            historical reports — never the default.
"""

from __future__ import annotations

import pandas as pd

ENTRY_NEXT_CLOSE = "next_close"
ENTRY_NEXT_OPEN = "next_open"
ENTRY_SIGNAL_CLOSE_LOOKAHEAD = "signal_close_LOOKAHEAD"

ENTRY_CONVENTIONS = (ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN, ENTRY_SIGNAL_CLOSE_LOOKAHEAD)
DEFAULT_ENTRY = ENTRY_NEXT_CLOSE


def validate_entry(entry: str) -> str:
    if entry not in ENTRY_CONVENTIONS:
        raise ValueError(f"unknown entry convention {entry!r}; expected one of {ENTRY_CONVENTIONS}")
    return entry


def forward_return_series(df: pd.DataFrame, hold: int, entry: str = DEFAULT_ENTRY) -> pd.Series:
    """Forward return aligned to df's index (row = signal day s); NaN where unpriced."""
    validate_entry(entry)
    close = df["close"]
    if entry == ENTRY_SIGNAL_CLOSE_LOOKAHEAD:
        return close.shift(-hold) / close - 1.0
    if entry == ENTRY_NEXT_CLOSE:
        return close.shift(-(hold + 1)) / close.shift(-1) - 1.0
    if "open" not in df.columns:
        raise ValueError("entry='next_open' requires an 'open' column")
    opens = df["open"]
    return opens.shift(-(hold + 1)) / opens.shift(-1) - 1.0
