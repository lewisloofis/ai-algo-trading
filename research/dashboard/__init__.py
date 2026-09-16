"""
dashboard — one readable surface over the three pipelines (plan-19).

Panel A  (panel_a.py)  the alpha mining process, from the strategy-fit sidecar
Panel B  (panel_b.py)  process health, from collector_healthcheck + status.json
Panel C  (panel_c.py)  per-strategy profitability, from the post-trade store

The package's one architectural rule (plan-19 §2 D2): **it computes nothing.**
Every threshold and every statistical definition is imported from the module
that owns it — decay thresholds from ``strategy_fit.walk_forward``, the OOS
floor from ``strategy_fit.intraday_walk_forward``, power from
``lib.stats.required_effective_n``, PASS eligibility and the null gate from
``strategy_fit.report``.  Counting rows by status is not computing a metric;
defining what "decayed" means would be.  ``test_no_new_metrics.py`` enforces
this mechanically.
"""

__all__ = [
    "build",
    "glossary",
    "panel_a",
    "panel_b",
    "panel_c",
    "render",
    "store",
    "strategy_map",
]
