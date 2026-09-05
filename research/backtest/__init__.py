"""backtest — fill simulator and test suite (plan 06).

Re-exports the public surface of ``backtest.backtest`` so that
``from backtest import Trade, leg_cost, ...`` resolves whether the caller
imports this as a package or runs a module inside it directly.

Why this exists: ``test_backtest.py`` was written to be run as
``python test_backtest.py`` from inside this directory, where a bare
``import backtest`` finds the sibling ``backtest.py`` module. Under pytest's
package-aware import mode the same statement resolves to THIS package
instead, so with an empty ``__init__.py`` the whole file failed collection
with ``ImportError: cannot import name 'Trade' from 'backtest'`` — which
aborted collection for the entire ``research/`` suite, not just this file.
The private helpers below are re-exported deliberately: the tests exercise
them directly, and ``import *`` would skip a leading-underscore name.
"""

from .backtest import *  # noqa: F401,F403
from .backtest import (  # noqa: F401  — private helpers under test
    _calc_shares,
    _compute_metrics,
    _find_exit_idx,
    _load_trades_df,
    _long_pnl,
    _passive_entry_fill,
    _passive_exit_fill,
    _short_pnl,
)
