"""strategy_status / strategy_sections: stages, families, screen, combinations, escaping, missing inputs."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from dashboard import live, strategy_families as F, strategy_sections as SEC, strategy_status as S
from dashboard.test_site import text, world  # noqa: F401  (fixture)
from dashboard.site import build_site
from strategy_fit import emitter_specs as es

BASE = es.SPECS["wifi_momo_h5"]
PASS_ALL = {"G2_x": True, "G3_x": True, "G4_x": True, "G5_x": True, "G7_x": True}


def gates(**kw):
    out = {f"{g}_x": False for g in S.GATE_KEYS}
    out.update({f"{g}_x": v for g, v in kw.items()})
    return out


def row(sym="AAAA", family="daily_momentum", hold=5, edgenet=20.0, g=None, **kw):
    return {"symbol": sym, "family": family, "hold": hold, "n": 90, "edgenet_1lot": edgenet,
            "drop5_net_1lot": 5.0, "wf_oos_edge": 10.0, "wf_oos_n": 30, "state": "IN POSITION (exit in 2 session(s))",
            "lot_notional": 100000.0, "trip_cost_idr": 900.0, "month_cost_idr": 2500.0,
            "gates": gates(G3=True) if g is None else g, **kw}


def shortlist(*rows, as_of="2026-10-14"):
    return S.Shortlist(as_of, 199, tuple(rows), ())


def spec(sid, status="paper", sym="AAAA", hold=5):
    return replace(BASE, strategy_id=sid, symbols=(sym,), status=status, hold=hold)


def reg(*specs):
    return {s.strategy_id: s for s in specs}


class Run:
    def __init__(self, sid, state="online", mode="practice (paper)"):
        self.strategy_id, self.state, self.mode = sid, state, mode


class Live:
    def __init__(self, *runs):
        self.runs = runs


def test_stage_candidate_online_is_live_else_live_candidate():
    specs = reg(spec("c1", "candidate"))
    assert S.stage_rows(specs, [], Live(Run("c1", "online", "real money")), None)[0].stage == "live"
    assert S.stage_rows(specs, [], Live(Run("c1", "online", "practice (paper)")), None)[0].stage == "live_candidate"
    assert S.stage_rows(specs, [], Live(Run("c1", "stopped", "real money")), None)[0].stage == "live_candidate"


def test_paper_running_now_and_failed_gates_in_plain_words():
    sl = shortlist(row(g=gates(G3=True, G4=True)))
    (r,) = S.stage_rows(reg(spec("p1")), [], Live(Run("p1")), sl)
    joined = " ".join(r.lines)
    assert r.stage == "paper" and "Running now." in joined and "luck" in joined


def test_retired_and_example_specs():
    rows = S.stage_rows(reg(spec("r1", "retired"), spec("e1", "example")), [], None, None)
    assert [x.stage for x in rows] == ["retired"]


def test_close_names_need_study_and_zero_gate_names_do_not():
    sl = shortlist(row("BBBB", g=gates(G4=True)), row("CCCC", g=gates()), row("AAAA", g=gates(G3=True)))
    rows = S.stage_rows(reg(spec("p1", "paper", "AAAA")), [], None, sl)
    study = [r for r in rows if r.stage == "study"]
    assert [r.name.split()[0] for r in study] == ["BBBB"] and "luck" in study[0].lines[0]


def test_stage_order_live_before_paper_before_study_before_retired():
    sl = shortlist(row("BBBB", g=gates(G4=True)))
    rows = S.stage_rows(reg(spec("r1", "retired", "RRRR"), spec("p1", "paper"), spec("c1", "candidate", "CCCC")), [], None, sl)
    assert [r.stage for r in rows] == ["live_candidate", "paper", "study", "retired"]


def test_combos_only_positive_ranked_by_gates_then_edge_and_paper_marked():
    sl = shortlist(row("AAAA", edgenet=50, g=gates(G3=True)), row("BBBB", edgenet=10, g=gates(G3=True, G4=True)),
                   row("CCCC", edgenet=-5, g=gates(G3=True, G4=True, G5=True)))
    combos = S.promising_combos(sl, reg(spec("p1", "paper", "BBBB")))
    assert [c.symbol for c in combos] == ["BBBB", "AAAA"] and combos[0].on_paper and not combos[1].on_paper


def test_state_words_and_age_warning():
    assert S.state_words("IN POSITION (exit in 1 session(s))") == "In position, exit in 1 session"
    assert S.state_words("ENTER next session").startswith("Signal")
    html = SEC.combos_section(shortlist(row(), as_of="2026-10-01"), "", S.promising_combos(shortlist(row()), None), {}, date(2026, 10, 15))
    assert "14 days old" in html and "shortlist_2026_10" in html


def test_combos_limit_and_show_all():
    rows = [row(f"S{i:03d}"[:4].replace("S0", "S"), edgenet=float(i + 1)) for i in range(20)]
    sl = shortlist(*rows)
    html = SEC.combos_section(sl, "", S.promising_combos(sl, None), {}, date(2026, 10, 14))
    assert "Show all (20)" in html


def test_missing_shortlist_and_universe_say_not_checked_with_commands(tmp_path):
    inp = S.gather_inputs(str(tmp_path), str(tmp_path))
    assert inp.shortlist is None and inp.universe is None
    assert "Not checked" in SEC.combos_section(None, inp.shortlist_problem, [], {}, date(2026, 10, 14))
    html = SEC.screen_section(inp)
    assert "make refresh-prices" in html and "Funding gate" in html


def test_unreadable_shortlist_is_not_checked(tmp_path):
    (tmp_path / "results/shortlist").mkdir(parents=True)
    (tmp_path / "results/shortlist" / "shortlist.json").write_text("{not json")
    sl, why = S.load_shortlist(str(tmp_path))
    assert sl is None and "could not be read" in why


def test_values_are_escaped():
    sl = shortlist(row("<b>x</b>", g=gates(G3=True)))
    html = SEC.combos_section(sl, "", S.promising_combos(sl, None), {}, date(2026, 10, 14))
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_family_rows_cover_catalog_and_derive_result_from_data():
    pooled = ({"family": "daily_momentum", "hold": 5, "names": 199, "edgenet_1lot": -86.0, "p_holm_30": 1.0},)
    sl = S.Shortlist("2026-10-14", 199, (row(),), pooled)
    vd = _verdicts(("mean_reversion", "FAIL", "z"))
    rows = {f.family_id: f for f in F.family_rows([], sl, reg(spec("p1", "paper")), vd)}
    from strategy_fit.catalog_meta import ALL_FAMILIES
    assert {m.family_id for m in ALL_FAMILIES} <= set(rows)
    assert rows["daily_momentum"].headline == "1 name(s) on paper"
    assert rows["mean_reversion"].headline == "Failed its test"  # outcome outranks "not scored"
    assert rows["multi_week_trend"].headline == "Not testable yet"
    assert any("-86" in e for e in rows["daily_momentum"].evidence)


def test_recent_runs_excludes_online(tmp_path):
    import time
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for n, outcome in (("a", "running"), ("b", "complete")):
        (tmp_path / f"{n}.status.json").write_text(json.dumps(
            {"wall_clock_ns": int(time.time() * 1e9), "outcome": outcome, "strategy_id": n, "sink_kind": "paper"}))
    summary = live.collect(str(tmp_path), now, recent_days=S.RUN_HISTORY_DAYS)
    assert [r.strategy_id for r in S.recent_runs(summary)] == ["b"]
    assert "Finished" in SEC.runs_section(S.recent_runs(summary))


def test_built_page_has_recent_runs_last_and_overview_has_no_history(world):  # noqa: F811
    opt, runs, out = world
    import time
    (runs / "z.status.json").write_text(json.dumps(
        {"wall_clock_ns": int(time.time() * 1e9) - 10**12, "outcome": "complete", "strategy_id": "zz_done", "sink_kind": "paper"}))
    build_site(opt)
    page = text(out, "strategies.html")
    assert page.index("Promising strategy") < page.index("Recent runs")
    assert "zz_done" in page and "zz_done" not in text(out, "index.html")


def test_every_catalog_family_has_words_and_no_side_list():
    from dashboard import strategy_words as W
    from strategy_fit.catalog_meta import ALL_FAMILIES
    assert not hasattr(W, "EXTRA_FAMILIES"), "uncatalogued families belong in catalog_meta"
    assert {m.family_id for m in ALL_FAMILIES} == set(W.FAMILY_WORDS)


def _verdicts(*vs, problems=()):
    from strategy_fit.study_verdicts import Loaded, Verdict
    return Loaded({v[0]: Verdict(v[0], v[1], "2026-10-15", "plan 99 §1", "docs/plans/README.md", v[2]) for v in vs},
                  tuple(problems))


def test_family_rows_show_test_outcome():
    vd = _verdicts(("mean_reversion", "FAIL", "TINS fails cost gate"), ("obi_ofi_tick", "NO_GO", "x"),
                   ("factor_books", "INCONCLUSIVE", "y"))
    rows = {f.family_id: f for f in F.family_rows([], None, reg(), vd)}
    mr = rows["mean_reversion"]
    assert mr.test_outcome == "FAIL"
    assert (mr.headline, mr.tone) == ("Failed its test", "red")
    assert mr.evidence[0] == "Test (2026-10-15, plan 99 §1): TINS fails cost gate"
    assert rows["obi_ofi_tick"].headline == "Failed its test (no-go)"
    assert (rows["factor_books"].headline, rows["factor_books"].tone) == ("Inconclusive: needs more data", "amber")
    assert rows["multi_week_trend"].headline == "Not testable yet"
    assert rows["multi_week_trend"].test_outcome == "untested"


def test_paper_names_outrank_family_test_outcome():
    vd = _verdicts(("daily_momentum", "FAIL", "no edge"))
    rows = {f.family_id: f for f in F.family_rows([], None, reg(spec("p1", "paper")), vd)}
    assert rows["daily_momentum"].headline == "1 name(s) on paper"
    assert any(e.startswith("Test (") for e in rows["daily_momentum"].evidence)


def test_families_section_shows_outcome_and_post_triage_status():
    vd = _verdicts(("mean_reversion", "FAIL", "a"), ("factor_books", "INCONCLUSIVE", "b"))
    html = SEC.families_section(F.family_rows([], None, reg(), vd))
    assert "Failed its test" in html and "Inconclusive: needs more data" in html
    assert "Yes, in its own study" in html
    assert "post_triage" not in html.replace("post-triage", "")


def test_no_verdicts_means_every_family_untested_and_nothing_crashes():
    for vd in (None, _verdicts()):
        rows = F.family_rows([], None, reg(), vd)
        assert rows and all(f.test_outcome == "untested" for f in rows)
        assert not any(e.startswith("Test (") for f in rows for e in f.evidence)
        assert "Strategy families we consider" in SEC.families_section(rows)


def test_verdict_problem_is_shown_escaped_as_not_checked():
    html = SEC.families_section(F.family_rows([], None, reg()), ("bad/verdict.json: <b>x</b> & more",))
    assert '<p class="empty">Not checked: bad/verdict.json: &lt;b&gt;x&lt;/b&gt; &amp; more</p>' in html
    assert "<b>x</b>" not in html


def test_gather_inputs_loads_verdicts_and_survives_failure(tmp_path, monkeypatch):
    inp = S.gather_inputs(str(tmp_path), str(tmp_path))
    assert inp.verdicts is not None and inp.verdicts_problem == ""
    from strategy_fit import study_verdicts as SV

    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(SV, "load_verdicts", boom)
    inp = S.gather_inputs(str(tmp_path), str(tmp_path))
    assert inp.verdicts is None and "RuntimeError" in inp.verdicts_problem


def test_positional_inputs_still_construct():
    inp = S.Inputs(None, "not loaded", None, (), None)
    assert inp.verdicts is None and inp.verdicts_problem == ""
