"""
recheck/test_run.py — milestone gating, ledger idempotence, the alert bar, refusals and the excluded list,
on injected synthetic specs (no parquet reads except the staleness check's tiny files).
"""

from __future__ import annotations

import json
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

from recheck import ledger as L
from recheck import run as R
from recheck import specs as SP
from recheck import stats as ST
from strategy_fit.study_verdicts import Loaded, Verdict

AS_OF = date(2026, 12, 1)
TODAY = date(2026, 12, 2)


def _records(nets):
    dates = pd.bdate_range("2026-10-19", periods=len(nets)).strftime("%Y-%m-%d")
    return pd.DataFrame({"signal_date": dates, "entry_date": dates,
                         "gross_bps": np.asarray(nets, float) + 10.0, "null_bps": 0.0, "cost_bps": 10.0})


CAL = list(pd.bdate_range("2026-01-05", periods=900).strftime("%Y-%m-%d"))


def _spec(fid, nets, hold=1):
    def build(daily_dir, start_excl, as_of):
        return _records(nets)
    return SP.RecheckSpec(fid, "plan x", hold, "next_close", "unit", 61, build, unit_hold=hold)


def _verdicts(*fids):
    return Loaded({f: Verdict(f, "FAIL", "2026-10-15", "plan x", "docs/x.md", "s") for f in fids}, ())


def _noise(n, seed=1):
    return list(np.random.default_rng(seed).normal(0.0, 30.0, n))


@pytest.fixture()
def daily(tmp_path):
    d = tmp_path / "daily"
    d.mkdir()
    pd.DataFrame({"date": ["2026-11-27", "2026-11-30"], "close": [1.0, 1.0], "volume": [10, 10]}).to_parquet(d / "AAA.parquet")
    return str(d)


def _run(tmp_path, daily, specs, verdicts=None, **kw):
    out = str(tmp_path / "out")
    doc = R.recheck(AS_OF, daily, out, verdicts or _verdicts(*(s.family_id for s in specs)), specs=specs,
                    excluded=kw.pop("excluded", {}), calendar=CAL, **kw)
    return doc, out


def _fam(doc, fid):
    return next(f for f in doc["families"] if f["family_id"] == fid)


# -- milestone gating -------------------------------------------------------------------------------

def test_below_the_first_milestone_it_waits_and_writes_no_ledger_entry(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(29))])
    f = _fam(doc, "alpha")
    assert (f["status"], f["n"], f["next_milestone"], f["evaluation"]) == ("waiting", 29, 30, None)
    assert L.load(os.path.join(out, "milestones.json")) == {}
    assert "waiting, 29 of 30" in open(os.path.join(out, "2026-12-01.md")).read()


def test_at_a_milestone_it_evaluates_once_and_stores_the_result(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(30))])
    f = _fam(doc, "alpha")
    assert f["status"] == "evaluated_now" and f["evaluation"]["milestone"] == 30 and f["next_milestone"] == 60
    stored = L.load(os.path.join(out, "milestones.json"))["alpha"]
    assert [e["milestone"] for e in stored] == [30]
    assert set(stored[0]) >= {"milestone", "as_of", "edge", "p", "drop5", "alert"}


def test_second_run_at_the_same_n_reports_the_stored_result_without_re_evaluating(tmp_path, daily):
    spec = [_spec("alpha", _noise(30))]
    _, out = _run(tmp_path, daily, spec)
    ledger_path = os.path.join(out, "milestones.json")
    before = open(ledger_path).read()
    # a different look at the same milestone would give a different edge: make the data differ on purpose
    doc2 = R.recheck(date(2026, 12, 2), daily, out, _verdicts("alpha"), specs=[_spec("alpha", _noise(30, seed=9))],
                     excluded={}, calendar=CAL)
    f = _fam(doc2, "alpha")
    assert f["status"] == "evaluated_earlier"
    assert f["evaluation"]["as_of"] == "2026-12-01"
    assert open(ledger_path).read() == before


def test_between_milestones_it_waits_for_the_next_and_keeps_the_stored_result(tmp_path, daily):
    _, out = _run(tmp_path, daily, [_spec("alpha", _noise(30))])
    doc = R.recheck(AS_OF, daily, out, _verdicts("alpha"), specs=[_spec("alpha", _noise(45))], excluded={}, calendar=CAL)
    f = _fam(doc, "alpha")
    assert f["status"] == "evaluated_earlier" and f["n"] == 45 and f["next_milestone"] == 60
    assert "milestone 30" in open(os.path.join(out, "2026-12-01.md")).read()
    assert [e["milestone"] for e in L.load(os.path.join(out, "milestones.json"))["alpha"]] == [30]


def test_at_most_one_evaluation_per_family_per_run(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(130))])
    assert _fam(doc, "alpha")["evaluation"]["milestone"] == 120
    assert [e["milestone"] for e in L.load(os.path.join(out, "milestones.json"))["alpha"]] == [120]
    assert _fam(doc, "alpha")["next_milestone"] is None


def test_families_are_gated_independently(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(31)), _spec("beta", _noise(12))])
    assert _fam(doc, "alpha")["status"] == "evaluated_now" and _fam(doc, "beta")["status"] == "waiting"
    assert list(L.load(os.path.join(out, "milestones.json"))) == ["alpha"]


def test_an_unreadable_ledger_stops_the_run_before_any_evaluation(tmp_path, daily):
    out = tmp_path / "out"
    out.mkdir()
    (out / "milestones.json").write_text("{broken")
    with pytest.raises(ValueError, match="milestones.json"):
        R.recheck(AS_OF, daily, str(out), _verdicts("alpha"), specs=[_spec("alpha", _noise(30))], excluded={}, calendar=CAL)
    assert not (out / "2026-12-01.json").exists()


def test_overlapping_hold_five_needs_150_trading_days_for_milestone_30_not_30(tmp_path, daily):
    doc, _ = _run(tmp_path, daily, [_spec("alpha", _noise(30), hold=5)])           # 30 daily entries = 6 blocks
    f = _fam(doc, "alpha")
    assert (f["status"], f["n"], f["next_milestone"]) == ("waiting", 6, 30)
    doc, _ = _run(tmp_path, daily, [_spec("beta", _noise(150, seed=2), hold=5)])
    assert _fam(doc, "beta")["n"] == 30 and _fam(doc, "beta")["status"] == "evaluated_now"


def test_cross_sectional_universe_deviation_is_a_visible_note(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("cross_sectional", _noise(3))])
    note = "cross_sectional universe differs from the verdict's (S.txt not in repo): traded-within-7-days, "
    assert any(n.startswith(note) and n.endswith(" names") for n in doc["notes"])
    assert note in open(os.path.join(out, "2026-12-01.md")).read()
    doc2, _ = _run(tmp_path, daily, [_spec("alpha", _noise(3))])
    assert doc2["notes"] == []


# -- alert bar ----------------------------------------------------------------------------------------

def test_threshold_in_the_report_is_point_ten_over_thirty_three_for_eleven_families(tmp_path, daily):
    doc, _ = _run(tmp_path, daily, [_spec("alpha", _noise(5))])          # real K
    assert doc["k"] == 11 and doc["alert_p_threshold"] == pytest.approx(0.10 / 33)


def test_clear_edge_alerts_and_noise_or_concentration_does_not(tmp_path, daily):
    strong = [40.0 + x for x in np.random.default_rng(3).normal(0, 5, 30)]
    lucky = [0.0] * 25 + [400.0] * 5                                      # edge > 0 but all in the top 5
    losing = [-20.0 + x for x in np.random.default_rng(4).normal(0, 5, 30)]
    doc, _ = _run(tmp_path, daily, [_spec("strong", strong), _spec("lucky", lucky), _spec("losing", losing),
                                    _spec("noise", _noise(30))])
    alerts = {f["family_id"]: f["evaluation"]["alert"] for f in doc["families"]}
    assert alerts == {"strong": True, "lucky": False, "losing": False, "noise": False}
    assert _fam(doc, "lucky")["evaluation"]["drop5"] <= 0 < _fam(doc, "lucky")["evaluation"]["edge"]
    assert "**ALERT**" in open(os.path.join(tmp_path, "out", "2026-12-01.md")).read()


def test_p_just_inside_and_outside_the_bar(tmp_path, daily):
    k = 11
    thr = ST.alert_p_threshold(k)
    base = np.random.default_rng(5).normal(0, 1, 30)
    base = (base - base.mean()) / base.std(ddof=1)                         # mean 0, sd 1
    # t = shift * sqrt(30): pick the shift whose one-sided p is just under / over the bar
    from scipy import stats as sps
    t_bar = sps.norm.isf(thr)
    per_unit_t = ST.hac_t(base + 1.0)               # HAC t of (base + shift) is shift * (t of base + 1)
    inside, outside = (t_bar + 0.05) / per_unit_t, (t_bar - 0.05) / per_unit_t
    doc, _ = _run(tmp_path, daily, [_spec("inside", list(base * 10 + inside * 10)),
                                    _spec("outside", list(base * 10 + outside * 10))], k=k)
    assert _fam(doc, "inside")["evaluation"]["alert"] is True
    assert _fam(doc, "outside")["evaluation"]["alert"] is False


# -- not re-checked list and records -----------------------------------------------------------------

def test_excluded_and_untested_families_are_listed_with_their_reasons(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(3))],
                    excluded={"gamma": "excluded (owner, 2026-10-17): unspent holdout overlaps post-verdict data",
                              "delta": "waiting: needs new MSCI_ST deletion events; event ingestion not built"},
                    catalog=["alpha", "gamma", "delta", "multi_week_trend"])
    got = {e["family_id"]: e["reason"] for e in doc["excluded"]}
    assert got["gamma"].startswith("excluded (owner, 2026-10-17)")
    assert got["delta"].startswith("waiting: needs new MSCI_ST")
    assert got["multi_week_trend"] == "not re-checked: untested"
    assert "alpha" not in got
    md = open(os.path.join(out, "2026-12-01.md")).read()
    assert "## Not re-checked" in md and "| gamma | excluded (owner" in md and "| multi_week_trend | not re-checked: untested |" in md


def test_the_real_exclusions_are_in_the_report():
    ids = {"foreign_selling_rebound", "indicator_rules", "index_rebalance_deletion", "obi_ofi_tick",
           "intraday_momentum", "pairs_stat_arb"}
    assert ids == set(SP.EXCLUDED)


def test_a_spec_with_no_verdict_is_reported_not_crashed(tmp_path, daily):
    doc, _ = _run(tmp_path, daily, [_spec("alpha", _noise(3)), _spec("beta", _noise(3))], _verdicts("alpha"))
    assert _fam(doc, "beta")["status"] == "no_verdict"


def test_report_records_the_impact_coefficient_file_and_both_outputs_exist(tmp_path, daily):
    doc, out = _run(tmp_path, daily, [_spec("alpha", _noise(3))])
    assert doc["impact_coeffs"]["file"] == "impact_coeffs.json" and len(doc["impact_coeffs"]["sha256"]) == 64
    assert json.load(open(os.path.join(out, "2026-12-01.json")))["as_of"] == "2026-12-01"
    assert "impact_coeffs.json" in open(os.path.join(out, "2026-12-01.md")).read()


def test_the_report_is_one_table_with_the_required_columns(tmp_path, daily):
    _, out = _run(tmp_path, daily, [_spec("alpha", _noise(3))])
    md = open(os.path.join(out, "2026-12-01.md")).read()
    assert ("| family | verdict date | status | N vs next milestone | edge (bps) | cost (bps) | p | drop-5 (bps) "
            "| ALERT |") in md
    assert md.count("| family |") == 2        # the results table and the not-re-checked table, nothing else


# -- refusals ------------------------------------------------------------------------------------------

def _main(daily, tmp_path, as_of, today=TODAY, **kw):
    out = str(tmp_path / "out")
    return R.main(["--as-of", as_of, "--daily-dir", daily, "--results-dir", str(tmp_path), "--out-dir", out],
                  today=today, specs=[_spec("alpha", _noise(3))], verdicts=_verdicts("alpha"), excluded={},
                  catalog=["alpha"], calendar=CAL, **kw), out


def test_future_as_of_is_refused_with_exit_2_and_writes_nothing(tmp_path, daily, capsys):
    code, out = _main(daily, tmp_path, "2026-12-03")
    assert code == 2 and not os.path.exists(out)
    assert "future" in capsys.readouterr().err


def test_stale_daily_data_is_refused_and_points_at_refresh_prices(tmp_path, daily, capsys):
    # latest bar 2026-11-30; as_of 2026-12-11 is 11 days later
    code, out = _main(daily, tmp_path, "2026-12-11", today=date(2026, 12, 12))
    assert code == 2 and not os.path.exists(out)
    assert "make refresh-prices" in capsys.readouterr().err


def test_exactly_ten_days_behind_is_not_stale(tmp_path, daily):
    code, out = _main(daily, tmp_path, "2026-12-10", today=date(2026, 12, 12))
    assert code == 0 and os.path.exists(os.path.join(out, "2026-12-10.json"))


def test_empty_daily_store_is_refused(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _ = _main(str(empty), tmp_path, "2026-12-01")
    assert code == 2 and "refresh-prices" in capsys.readouterr().err


def test_bad_date_argument_exits_2(tmp_path, daily):
    with pytest.raises(SystemExit) as e:
        R.main(["--as-of", "not-a-date"])
    assert e.value.code == 2


def test_zero_volume_in_progress_bar_does_not_count_as_fresh(tmp_path):
    d = tmp_path / "daily"
    d.mkdir()
    pd.DataFrame({"date": ["2026-11-01", "2026-12-01"], "volume": [10, 0]}).to_parquet(d / "AAA.parquet")
    code, _ = _main(str(d), tmp_path, "2026-12-01")
    assert code == 2
