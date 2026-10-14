"""Hover on every chart: data embedded (light), a graceful no-JS fallback, and one inline script per page."""
from __future__ import annotations

import html
import json
import re
import xml.dom.minidom

from dashboard import hover, svg
from dashboard.pages_strategies import RANDOM_DAYS, chart_caption
from dashboard.render import page
from dashboard.scorecard import TradeStats


def days(n: int):
    from datetime import date, timedelta

    d0 = date(2025, 1, 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def hv(chart: str) -> dict:
    m = re.search(r' data-hv="([^"]*)"', chart)
    assert m, "chart has no data-hv"
    return json.loads(html.unescape(m.group(1)))


def two_lines(n=10):
    ds = days(n)
    return [
        svg.Series("Strategy", [(d, i * 1000.0) for i, d in enumerate(ds)]),
        svg.Series("Random days", [(d, i * 400.0) for i, d in enumerate(ds)], svg.AMBER),
    ]


def test_line_chart_embeds_both_series_and_the_difference_request():
    chart = svg.line_chart("t", two_lines(), diff=(0, 1), diff_label="Strategy minus random days")
    d = hv(chart)
    assert [s["n"] for s in d["s"]] == ["Strategy", "Random days"]
    assert d["d"] == [0, 1] and d["dl"] == "Strategy minus random days" and d["fmt"] == "idr"
    xml.dom.minidom.parseString(chart)  # still well-formed with the attribute


def test_hover_data_is_downsampled_but_keeps_the_ends_and_the_line_is_not_thinned():
    ds = days(2000)
    chart = svg.line_chart("t", [svg.Series("A", [(d, float(i)) for i, d in enumerate(ds)])])
    pts = hv(chart)["s"][0]["p"]
    assert len(pts) <= svg.HOVER_MAX_POINTS
    assert pts[0][1] == 0 and pts[-1][1] == 1999
    assert chart.count("L") >= 1999  # full path kept
    assert len(chart) < 60_000


def test_no_js_fallback_titles_carry_date_values_and_difference():
    chart = svg.line_chart("t", two_lines(), diff=(0, 1), diff_label="Strategy minus random days")
    doc = xml.dom.minidom.parseString(chart)
    titles = [t.firstChild.data for c in doc.getElementsByTagName("circle") for t in c.getElementsByTagName("title")]
    assert titles and len(titles) <= svg.POINT_TITLE_MAX
    last = titles[-1]
    assert "2025-01-10" in last and "Strategy: Rp 9,000" in last and "Random days: Rp 3,600" in last
    assert "Strategy minus random days: +Rp 5,400" in last


def test_value_at_interpolates_and_is_none_outside_the_span():
    pts = [("2025-01-01", 0.0), ("2025-01-11", 100.0)]
    assert svg._value_at(pts, "2025-01-06") == 50.0
    assert svg._value_at(pts, "2024-12-31") is None
    assert svg._value_at(pts, "2025-01-12") is None


def test_price_chart_is_hoverable_too():
    closes = [(d, 100.0 + i) for i, d in enumerate(days(30))]
    chart = svg.price_chart("t", closes, [svg.Mark(closes[10][0], 110.0, "buy", True)])
    d = hv(chart)
    assert d["fmt"] == "num" and d["s"][0]["n"] == "Close"
    assert "<title>" in chart  # order markers keep their native tooltip


def test_page_inlines_the_script_once_only_when_a_chart_needs_it():
    chart = svg.line_chart("t", two_lines())
    with_chart = page("T", "strategies.html", chart, "f")
    assert with_chart.count("<script>") == 1 and "svg[data-hv]" in with_chart
    assert "<script" not in page("T", "strategies.html", "<p>no chart</p>", "f")


def test_script_uses_no_network_or_external_resources():
    js = hover.HOVER_JS
    for banned in ("fetch(", "XMLHttpRequest", "http://", "https://", "import(", "eval("):
        # the SVG namespace URL is the only permitted URL-looking string
        assert banned not in js.replace("http://www.w3.org/2000/svg", ""), banned


def _stats(**kw) -> TradeStats:
    base = dict(
        n_trades=40, cost_pct=0.5, gross_avg_pct=3.6, net_avg_pct=3.10, net_avg_idr=31000.0,
        net_without_best_pct=1.0, net_without_best_idr=10000.0, win_rate=0.5,
        hold_total_pct=640.0, hold_total_idr=6_400_000.0, first_entry="a", last_exit="b",
        any_day_avg_pct=1.80, any_day_avg_idr=18000.0, edge_vs_null_pct=1.3, t_stat=0.9, in_market_pct=12.0,
    )
    base.update(kw)
    return TradeStats(**base)


class _Cell:
    symbol = "PTRO"


def test_caption_states_both_per_trade_numbers_and_why_holding_is_not_comparable():
    s = chart_caption(_Cell, _stats())
    assert "+3.10%" in s and "+1.80%" in s and "random days" in s
    assert "t = 0.9" in s and "12% of days" in s and "PTRO" in s and "not a fair comparison" in s


def test_caption_without_a_baseline_is_empty_and_the_chart_label_is_plain():
    assert chart_caption(_Cell, _stats(any_day_avg_pct=None)) == ""
    assert RANDOM_DAYS.startswith("Same trades on random days")
