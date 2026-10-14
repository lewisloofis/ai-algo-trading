"""
Plan-19 §8: the no-new-metric test.

D2 says the dashboard computes nothing — every threshold and every statistical
definition is imported from the module that owns it.  A second implementation
of a metric is a second answer, which is how "which number is right?" starts.

This test enforces the rule mechanically by reading the package's own source:

1. No function in dashboard/ may be named like a metric or threshold.
2. No module-level constant may be named like a threshold.
3. The thresholds the panels display must come from an import, not a literal.

Counting rows by status is not computing a metric; deciding what "decayed"
means would be.  ``statistics.stdev`` as the *input* to an imported power
function is allowed and is called out explicitly below.
"""
from __future__ import annotations

import ast
import os
import re

import pytest

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

# Names that would mean the dashboard had started defining a metric.
BANNED_FUNCTION_PATTERNS = (
    r"sharpe",
    r"hit_rate",
    r"hurdle",
    r"^required_effective_n$",
    r"bootstrap",
    r"^fdr",
    r"benjamini",
    r"p_value",
    r"^compute_verdict$",
    r"effective_n$",
    r"de_?overlap",
    r"null_edge",
)

BANNED_CONSTANT_PATTERNS = (
    r"^DECAY_",
    r"^MIN_EFF_N$",
    r"^MIN_OOS_EFF_N$",
    r"_THRESHOLD$",
    r"_HURDLE",
)

# Functions and constants that are allowed despite matching, with the reason.
ALLOWED: set[tuple[str, str]] = set()


def _source_files() -> list[str]:
    return sorted(
        os.path.join(PACKAGE_DIR, f)
        for f in os.listdir(PACKAGE_DIR)
        if f.endswith(".py") and not f.startswith("test_")
    )


def _tree(path: str) -> ast.Module:
    return ast.parse(open(path, encoding="utf-8").read())


@pytest.mark.parametrize("path", _source_files(), ids=os.path.basename)
def test_defines_no_metric_function(path):
    """No function in the package may be named like a metric it does not own."""
    offenders = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if (os.path.basename(path), name) in ALLOWED:
            continue
        for pattern in BANNED_FUNCTION_PATTERNS:
            if re.search(pattern, name):
                offenders.append(f"{name} (matches {pattern!r})")
    assert not offenders, (
        f"{os.path.basename(path)} defines metric-shaped function(s): {offenders}. "
        "Import the definition from the module that owns it (plan-19 §2 D2)."
    )


@pytest.mark.parametrize("path", _source_files(), ids=os.path.basename)
def test_defines_no_threshold_constant(path):
    """Thresholds are imported, never restated as a module constant here."""
    offenders = []
    tree = _tree(path)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            if name in imported:
                continue
            for pattern in BANNED_CONSTANT_PATTERNS:
                if re.search(pattern, name):
                    offenders.append(f"{name} (matches {pattern!r})")
    assert not offenders, (
        f"{os.path.basename(path)} defines threshold constant(s): {offenders}. "
        "Import them from strategy_fit instead (plan-19 §2 D2)."
    )


def test_the_top_n_trades_rule_is_the_harness_s():
    """The 'without its best 5 trades' check uses compare_d1_rescore's own TOP_N, never a copy."""
    import strategy_fit.compare_d1_rescore as harness

    from dashboard import scorecard, verdicts

    assert verdicts.TOP_N is harness.TOP_N
    assert scorecard.TOP_N is harness.TOP_N


def test_the_trade_floor_shown_is_lib_stats():
    """The research funnel quotes the independent-trade floor from lib.stats, not a literal."""
    import lib.stats as stats

    from dashboard import pages_research

    assert pages_research.MIN_EFF_N is stats.MIN_EFF_N


def test_gate_outcomes_are_the_harness_s_words_not_new_verdicts():
    """
    results.py only maps the harness's own reason text onto labels. Every label that can mean
    "failed a gate" must come from a prefix the harness actually writes (also pinned in
    test_verdicts.py), so a new gate has to be added to the harness first.
    """
    from dashboard import results

    prefixes = {p for p, _ in results._REASON_PREFIXES}
    assert {"cost_gated", "fdr_rejected", "loses_to_null"} <= prefixes


def test_the_entry_convention_shown_is_the_harness_default():
    from strategy_fit.entry_convention import DEFAULT_ENTRY
    from strategy_fit.trade_export import ENTRY_USED

    assert ENTRY_USED == DEFAULT_ENTRY == "next_close"


def test_only_sanctioned_statistics_are_used():
    """
    The package may take means, counts, min/max and a sample standard
    deviation; anything richer would be a metric it does not own. stdev is
    permitted only as the INPUT to lib.stats.required_effective_n.
    """
    allowed = {"fmean", "stdev", "mean", "median"}
    used = set()
    for path in _source_files():
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "statistics"
            ):
                used.add(node.attr)
    assert used <= allowed, f"unsanctioned statistics functions used: {sorted(used - allowed)}"
