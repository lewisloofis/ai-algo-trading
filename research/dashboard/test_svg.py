"""Inline SVG charts: valid, self-contained, and honest about what they mark."""
from __future__ import annotations

import xml.dom.minidom

import pytest

from dashboard import svg


def parse(s: str):
    return xml.dom.minidom.parseString(s)


def test_nice_domain_rounds_outwards_to_round_steps():
    lo, hi, ticks = svg._nice_domain(-1.6e6, 2.3e7)
    assert lo <= -1.6e6 and hi >= 2.3e7 and 0 in ticks
    assert svg._nice_domain(25, 52)[2] == [20, 30, 40, 50, 60]
    assert svg._nice_domain(3, 3)[0] <= 3  # degenerate range does not crash


def test_line_chart_is_well_formed_with_one_path_per_series():
    s = svg.line_chart(
        "t",
        [
            svg.Series("A", [("2026-01-01", 0.0), ("2026-02-01", 5000.0)]),
            svg.Series("B", [("2026-01-01", 0.0), ("2026-02-01", -2000.0)], svg.MUTED, dashed=True),
        ],
    )
    doc = parse(s)
    assert len(doc.getElementsByTagName("path")) == 2
    assert "stroke-dasharray" in s and "<script" not in s


def test_empty_line_chart_says_so():
    assert "No data" in svg.line_chart("t", [])


def test_price_chart_marks_filled_and_unfilled_orders_differently():
    closes = [(f"2026-09-{d:02d}", 100.0 + d) for d in range(1, 12)]
    marks = [
        svg.Mark("2026-09-05", 105.0, "buy", True),
        svg.Mark("2026-09-08", 108.0, "sell", False),
    ]
    s = svg.price_chart("t", closes, marks)
    doc = parse(s)
    polys = doc.getElementsByTagName("polygon")
    assert len(polys) == 2
    assert polys[0].getAttribute("fill") == svg.GREEN
    assert polys[1].getAttribute("fill") == "#ffffff"  # hollow = not filled


def test_price_chart_places_orders_after_the_last_close():
    closes = [("2026-09-01", 50.0), ("2026-09-11", 50.0)]
    s = svg.price_chart("t", closes, [svg.Mark("2026-10-14", 28.0, "buy", True)])
    assert len(parse(s).getElementsByTagName("polygon")) == 1
    assert "2026-10-14" in s


def test_price_chart_without_any_data():
    assert "No price history" in svg.price_chart("t", [], [])


@pytest.mark.parametrize("v,expect", [(-4, "-Rp 4"), (31, "Rp 31"), (25_000, "Rp 25k"), (2_500_000, "Rp 2.5M")])
def test_rupiah_axis_labels(v, expect):
    assert svg.rupiah(v) == expect
