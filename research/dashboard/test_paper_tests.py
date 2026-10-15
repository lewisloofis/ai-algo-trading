"""Strategies page paper rows (via strategy_status): rows from synthetic paper stores, failure isolation, escaping."""
from __future__ import annotations

from dataclasses import replace

import pytest

from dashboard import paper_tests, strategy_sections, strategy_status
from dashboard.site import build_site
from dashboard.test_site import text, world  # noqa: F401  (fixture)
from strategy_fit import emitter_specs as es
from strategy_fit.paper_store_fixtures import write_store

BASE = es.SPECS["wifi_momo_h5"]


def spec(sid, status="paper", symbol="AAAA"):
    return replace(BASE, strategy_id=sid, symbols=(symbol,), status=status)


def registry(*specs):
    return {s.strategy_id: s for s in specs}


def store(runs, day, sid, fills, n=1):
    return write_store(runs / f"{day}-{sid}-paper-a{n}.db", fills)


@pytest.fixture
def dirs(tmp_path):
    runs, daily = tmp_path / "runs", tmp_path / "daily"
    runs.mkdir()
    daily.mkdir()
    return runs, daily


def stand(dirs, *specs, rows=None):
    """Rendered 'Where each strategy stands' section for synthetic specs (paper rows collected unless given)."""
    reg = registry(*specs)
    rows = collect(dirs, *specs) if rows is None else rows
    return strategy_sections.stand_section(strategy_status.stage_rows(reg, rows, None, None), rows)


def collect(dirs, *specs):
    return paper_tests.collect(str(dirs[0]), str(dirs[1]), registry(*specs))


def test_not_started_row(dirs):
    (row,) = collect(dirs, spec("s_new"))
    assert (row.days_run, row.holding, row.n_trips, row.error) == (0, (), 0, None)
    html = stand(dirs, spec("s_new"))
    assert "Not started" in html and "0 / 15" in html


def test_only_paper_and_candidate_specs_are_listed(dirs):
    rows = collect(dirs, spec("a", "paper"), spec("b", "candidate"), spec("c", "retired"), spec("d", "example"))
    assert [r.strategy_id for r in rows] == ["a", "b"]
    assert "Live candidate" in stand(dirs, spec("a", "paper"), spec("b", "candidate"))


def test_holding_row(dirs):
    runs, _ = dirs
    store(runs, "2026-10-12", "s_hold", [("2026-10-12", "AAAA", "buy", 1000, 1)])
    store(runs, "2026-10-13", "s_hold", [("2026-10-13", "AAAA", "buy", 1010, 1)], n=1)
    (row,) = collect(dirs, spec("s_hold"))
    assert row.days_run == 2 and dict(row.holding) == {"AAAA": 2}
    html = stand(dirs, spec("s_hold"))
    assert "2 lots AAAA" in html and "0 / 15" in html and "Not enough trips yet" in html


def test_trips_row_with_verdict(dirs):
    runs, _ = dirs
    store(runs, "2026-10-12", "s_trip", [("2026-10-12", "AAAA", "buy", 1000, 1), ("2026-10-19", "AAAA", "sell", 1100, 1)])
    (row,) = collect(dirs, spec("s_trip"))
    assert row.n_trips == 1 and row.holding == () and row.mean_net_bps is not None
    html = stand(dirs, spec("s_trip"))
    assert "flat" in html and "1 / 15" in html and "Not enough trips yet" in html


def test_a_failing_store_is_isolated(dirs):
    runs, _ = dirs
    (runs / "2026-10-12-s_bad-paper-a1.db").write_text("not a database")
    store(runs, "2026-10-12", "s_ok", [("2026-10-12", "AAAA", "buy", 1000, 1)])
    bad, ok = collect(dirs, spec("s_bad"), spec("s_ok"))
    assert bad.error and ok.error is None and ok.days_run == 1
    assert "Could not read" in stand(dirs, spec("s_bad"), spec("s_ok"), rows=[bad, ok])


def test_unimportable_strategy_fit_gives_not_checked(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "strategy_fit.paper_scorecard", None)
    assert paper_tests.collect("x", "y") is None
    assert "Not checked" in strategy_sections.stand_section(None, None)


def test_values_are_escaped(dirs):
    row = paper_tests.PaperRow("<b>x</b>", ("<i>",), 5, "paper", days_run=1,
                               holding=(("<s>", 1),), verdict="<script>1</script>")
    err = paper_tests.PaperRow("e", ("E",), 5, "paper", error="<img src=x>")
    reg = registry(spec("<b>x</b>"), spec("e"))
    reg = {"<b>x</b>": replace(spec("q"), strategy_id="<b>x</b>", symbols=("<i>",)),
           "e": replace(spec("q"), strategy_id="e", symbols=("E",))}
    rows = [row, err]
    html = strategy_sections.stand_section(strategy_status.stage_rows(reg, rows, None, None), rows)
    for raw in ("<b>x</b>", "<i>", "<s>", "<script>", "<img"):
        assert raw not in html
    assert "&lt;script&gt;" in html


def test_footer_line_has_banner_and_command(dirs):
    html = stand(dirs, spec("s_new"))
    assert "not proof of edge" in html and "make paper-scorecard STRATEGY=s_new" in html


def test_paper_rows_are_on_strategies_page_not_overview(world):  # noqa: F811
    opt, _runs, out = world
    build_site(opt)
    assert "Paper tests" not in text(out, "index.html")
    page = text(out, "strategies.html")
    assert "wifi_momo_h5" in page and "Not started" in page
