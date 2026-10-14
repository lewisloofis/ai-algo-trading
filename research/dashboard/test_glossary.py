"""
Plan-19 §8: glossary completeness and D3's "generated, never hand-written".
"""
from __future__ import annotations

import dataclasses

import pytest

from strategy_fit.harness import CellResult

from dashboard.glossary import (
    build_glossary,
    go_struct_docs,
    python_attribute_docs,
    snake_case,
    HARNESS_PY,
    STORE_GO,
)


def test_every_cellresult_field_has_an_entry():
    """
    Field-drift guard: adding a CellResult field without documenting it fails
    here, because the dashboard renders every field in the cell detail view.
    """
    g = build_glossary()
    missing = [f.name for f in dataclasses.fields(CellResult) if f"cell.{f.name}" not in g]
    assert not missing, (
        f"CellResult field(s) with no docstring: {missing}. Document them in "
        "strategy_fit/harness.py — the glossary is generated from there, not written here."
    )


def test_definitions_come_from_the_source_verbatim():
    """A definition must be the docstring, not a paraphrase stored here."""
    g = build_glossary()
    raw = python_attribute_docs(HARNESS_PY, "CellResult")
    assert g["cell.impact_priced"].definition == raw["impact_priced"]
    assert "never being priced" in g["cell.impact_priced"].definition


@pytest.mark.parametrize(
    "key, must_contain",
    [
        # The five nuances a prose glossary would flatten (plan-19 §2 D3).
        ("cell.net_edge_bps", "exactly once"),
        ("cell.eff_n", "de-overlapped"),
        ("cell.oos_required_eff_n", "MIN_OOS_EFF_N"),
        ("cell.oos_underpowered", "could not be assessed"),
        ("cell.impact_priced", "never being priced"),
    ],
)
def test_load_bearing_distinctions_survive(key, must_contain):
    assert must_contain in build_glossary()[key].definition


def test_go_struct_docs_read_the_post_trade_schema():
    docs = go_struct_docs(STORE_GO, "AttributionRow")
    assert "net_pnl_bps" in docs
    assert "financing" in docs["net_pnl_bps"], (
        "attribution.net_pnl_bps must say what it does NOT include"
    )
    assert "as_of_date" in docs
    assert "WIB" in docs["as_of_date"]


def test_go_struct_docs_cover_every_row_struct():
    for struct in ("DecisionRow", "IntentRow", "FillRow", "AttributionRow", "RunRow", "RejectRow"):
        docs = go_struct_docs(STORE_GO, struct)
        assert docs, f"{struct} has no documented fields"


def test_go_struct_docs_raise_for_an_unknown_struct():
    with pytest.raises(LookupError):
        go_struct_docs(STORE_GO, "NoSuchRow")


@pytest.mark.parametrize(
    "go_name, expected",
    [
        ("NetPnlBps", "net_pnl_bps"),
        ("RunID", "run_id"),
        ("PriceIDR", "price_idr"),
        ("TotalFeesIDR", "total_fees_idr"),
        ("EntryRecvNs", "entry_recv_ns"),
        ("AsOfDate", "as_of_date"),
        ("OrdersAttempted", "orders_attempted"),
    ],
)
def test_snake_case_matches_the_sql_column_names(go_name, expected):
    assert snake_case(go_name) == expected
