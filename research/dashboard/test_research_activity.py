"""Research activity section on the Strategies page: running, finished, empty, problems, escaping."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dashboard import strategy_sections as SEC, strategy_status as S
from dashboard.site import SiteOptions, build_site
from dashboard.test_site import text, world  # noqa: F401  (fixture)
from strategy_fit import research_log as RL

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=WIB)


def ev(event="start", study="s1", ts="2026-10-17T10:30:00+07:00", outcome=None, **kw):
    base = dict(ts=ts, event=event, study=study, plan="plan 26", families=("daily_momentum",),
                outcome=outcome, note="a note")
    base.update(kw)
    return RL.Event(**base)


def state(*events, problems=()):
    running = tuple(e for e in events if e.event == "start")
    recent = tuple(sorted((e for e in reversed(events) if e.event == "finish"), key=lambda e: e.ts, reverse=True))
    return RL.LogState(tuple(events), running, recent, tuple(problems))


def test_empty_states():
    html = SEC.research_activity_section(state(), (), NOW)
    assert "<h2>Research activity</h2>" in html
    assert "No research is running." in html and "No research finished yet." in html


def test_running_shows_study_plan_families_note_start_and_age():
    html = SEC.research_activity_section(state(ev()), (), NOW)
    assert "Running now" in html
    for word in ("s1", "plan 26", "daily_momentum", "a note", "2026-10-17 10:30 WIB", "1 h 30 min ago"):
        assert word in html
    assert "No research is running." not in html
    assert "No research finished yet." in html


def test_finished_table_has_outcome_pill_tones_and_limit():
    fins = [ev("finish", study=f"s{i}", ts=f"2026-10-17T{i:02d}:00:00+07:00", outcome="FAIL") for i in range(1, 11)]
    fins.append(ev("finish", study="old", ts="2026-10-01T00:00:00+07:00", outcome="PASS"))
    fins.append(ev("finish", study="gone", ts="2026-10-17T11:00:00+07:00", outcome="ABANDONED"))
    html = SEC.research_activity_section(state(*fins), (), NOW)
    assert 'pill red">FAIL' in html and 'pill grey">ABANDONED' in html
    assert "<b>s2</b>" in html and "<b>gone</b>" in html
    assert "<b>s1</b>" not in html and "<b>old</b>" not in html  # only the newest 10 are shown
    assert "No research finished yet." not in html


def test_outcome_tones_match_family_headlines():
    html = SEC.research_activity_section(
        state(ev("finish", outcome="PASS"), ev("finish", study="b", outcome="INCONCLUSIVE")), (), NOW)
    assert 'pill green">PASS' in html and 'pill amber">INCONCLUSIVE' in html


def test_values_are_escaped():
    e = ev(study="s1", plan="<b>p</b>", note="<script>x()</script>", families=("<i>f</i>",))
    html = SEC.research_activity_section(state(e, ev("finish", study="z", note="<img src=x>", outcome="FAIL")), (), NOW)
    assert "<script>" not in html and "<b>p</b>" not in html and "<img" not in html and "<i>f</i>" not in html
    assert "&lt;script&gt;" in html


def test_problems_render_as_not_checked_escaped():
    html = SEC.research_activity_section(state(), ("line 2: <bad>",), NOW)
    assert "Not checked: line 2: &lt;bad&gt;" in html and "<bad>" not in html


def test_load_research_log_wraps_failures(tmp_path):
    st, problem = S.load_research_log(str(tmp_path / "missing.jsonl"))
    assert st.events == () and problem == ""
    st, problem = S.load_research_log(str(tmp_path))  # a directory, not a file
    assert st is None and "research log could not be loaded" in problem


def test_strategies_page_shows_section_after_families(world):  # noqa: F811
    opt, runs, out = world
    RL.append(str(runs / "research_log.jsonl"), ev(families=()), family_ids=())
    build_site(opt)
    html = text(out, "strategies.html")
    assert html.index("Strategy families we consider") < html.index("Research activity")
    assert html.index("Research activity") < html.index("Backtest results:")
    assert "Running now" in html and "s1" in html


def test_site_options_log_path_override(world):  # noqa: F811
    opt, runs, out = world
    other = runs.parent / "elsewhere.jsonl"
    RL.append(str(other), ev(study="elsewhere-study", families=()), family_ids=())
    build_site(SiteOptions(**{**opt.__dict__, "research_log_path": str(other)}))
    assert "elsewhere-study" in text(out, "strategies.html")


def test_age_uses_real_elapsed_time_not_wib_shifted_clock(world):  # noqa: F811
    opt, runs, out = world
    started = (opt.now.astimezone(WIB) - timedelta(minutes=5)).isoformat(timespec="seconds")
    RL.append(str(runs / "research_log.jsonl"), ev(ts=started, families=()), family_ids=())
    build_site(opt)
    assert "5 min ago" in text(out, "strategies.html")
