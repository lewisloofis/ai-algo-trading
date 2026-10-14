"""
dashboard — a static, multi-page, plain-language view of the trading system.

    python -m dashboard build          (or: make dashboard)

Pages: Overview, Trades, Strategies (+ one detail page per highlighted backtest case),
Research progress, Glossary. Built for the account owner, who is technically strong but
should not need to know this project's vocabulary.

The package's one architectural rule (plan-19 §2 D2): **it computes no metric of its own.**
Gates and verdicts (cost gate, FDR, always-long null, out-of-sample) are the strategy-fit
harness's, read from its report; the per-trade backtest records come from the harness's own
functions (strategy_fit/trade_export.py); realised profit is the runtime's ``fill.net_pnl_idr``.
What this package adds is wording, and arithmetic a person can check by hand (averages, counts).
``test_no_new_metrics.py`` enforces the rule mechanically.
"""

__all__ = [
    "annotations",
    "config",
    "glossary",
    "health",
    "inputs",
    "render",
    "results",
    "scorecard",
    "site",
    "store",
    "strategy_map",
    "svg",
    "terms",
    "trades_data",
    "verdicts",
]
