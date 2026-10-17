"""Tests for strategy_fit/families_foreign_flow.py on synthetic panels (no data dir, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit import families_foreign_flow as ff

N_DAYS, N_NAMES = 60, 40
WIN, MINH = 20, 8           # small percentile window / warm-up so synthetic panels stay tiny
BAD = 5e12                  # buy+sell far above 2 x value => invalid by check 3


def _frames(seed: int = 0, n_days: int = N_DAYS, n_names: int = N_NAMES):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n_days)
    syms = [f"N{i:02d}" for i in range(n_names)]
    close = pd.DataFrame(1000 * np.exp(np.cumsum(rng.normal(0, 0.002, (n_days, n_names)), axis=0)),
                         index=dates, columns=syms)
    value = pd.DataFrame(rng.uniform(1e10, 2e10, (n_days, n_names)), index=dates, columns=syms)
    net = pd.DataFrame(rng.normal(0, 1e8, (n_days, n_names)), index=dates, columns=syms)
    bs = pd.DataFrame(1e9, index=dates, columns=syms)
    member = pd.DataFrame(True, index=dates, columns=syms)
    return close, value, net, bs, member


def _build(frames, **kw):
    kw = {"window": WIN, "min_hist": MINH, **kw}
    return ff.build_signals(*frames, **kw)


def _equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return a.equals(b)


# ---- percentile helper ------------------------------------------------------------------------------------

def test_trailing_percentile_uses_only_prior_values_and_warmup():
    x = np.arange(30, dtype=float)
    out = ff.trailing_percentile(x, 5, window=10, min_hist=4)
    assert np.isnan(out[:4]).all()
    assert out[4] == pytest.approx(np.percentile(x[:4], 5))
    assert out[7] == pytest.approx(np.percentile(x[:7], 5))
    assert out[10] == pytest.approx(np.percentile(x[0:10], 5))      # first full window, excludes x[10]
    assert out[25] == pytest.approx(np.percentile(x[15:25], 5))     # last 10 strictly before index 25


# ---- no look-ahead ------------------------------------------------------------------------------------------

def test_changing_data_after_t_never_changes_signals_up_to_t():
    close, value, net, bs, member = _frames(1)
    net.iloc[45, :] = -1e12                                   # make something happen late
    close.iloc[45, :] *= 0.9
    base = _build((close, value, net, bs, member))
    t = 40
    c2, v2, n2, b2, m2 = (x.copy() for x in (close, value, net, bs, member))
    for x in (c2, v2, n2, b2):
        x.iloc[t + 1:] = x.iloc[t + 1:] * 3.7 + 11             # scramble everything strictly after t
    m2.iloc[t + 1:] = False
    alt = _build((c2, v2, n2, b2, m2))
    for name in ("s1", "s2", "c1a", "c1b", "valid"):
        assert _equal(getattr(base, name).iloc[: t + 1], getattr(alt, name).iloc[: t + 1]), name
    assert _equal(base.F.iloc[: t + 1], alt.F.iloc[: t + 1])


def test_flow_ratio_excludes_todays_value_from_the_denominator():
    close, value, net, bs, member = _frames(2)
    f0 = ff.flow_ratio(net, value)
    value2 = value.copy()
    value2.iloc[30] *= 100
    f1 = ff.flow_ratio(net, value2)
    assert _equal(f0.iloc[:31], f1.iloc[:31])                  # a change in value_t never moves F_t
    assert not _equal(f0.iloc[31:52], f1.iloc[31:52])          # it does enter the next 20 days' median


# ---- S1: data rule, warm-up, price filter ------------------------------------------------------------------

def _s1_frames(day: int = 30, sym: str = "N00", ret: float = -0.03):
    close, value, net, bs, member = _frames(3)
    net[sym] = 1e8                                            # flat, so a big dump is far below the 5th pct
    net.iloc[day, net.columns.get_loc(sym)] = -1e11
    close.iloc[day:, close.columns.get_loc(sym)] *= (1 + ret) * 1.0
    return close, value, net, bs, member


def test_s1_triggers_on_extreme_outflow_with_price_drop():
    sig = _build(_s1_frames())
    d = sig.s1.index[30]
    assert sig.s1.loc[d, "N00"]
    assert sig.s1.to_numpy().sum() == 1


def test_s1_needs_the_price_drop():
    sig = _build(_s1_frames(ret=-0.01))
    assert not sig.s1.to_numpy().any()


def test_s1_needs_membership():
    close, value, net, bs, member = _s1_frames()
    member["N00"] = False
    assert not _build((close, value, net, bs, member)).s1.to_numpy().any()


def test_warmup_requires_min_hist_prior_valid_observations():
    # F is first defined at row 20 (20-session value median). A dump at row 27 has 7 prior valid observations
    # (< MINH=8) and cannot trigger; at row 28 it has 8 and can.
    early = _build(_s1_frames(day=27))
    assert early.valid.iloc[20:28, 0].all() and not early.valid.iloc[:20, 0].any()
    assert not early.s1.iloc[27, 0]
    late = _build(_s1_frames(day=28))
    assert late.s1.iloc[28, 0]


def test_invalid_day_never_triggers():
    close, value, net, bs, member = _s1_frames()
    bs.iloc[30, 0] = BAD                                      # fails check 3 on the dump day
    sig = _build((close, value, net, bs, member))
    assert not sig.valid.iloc[30, 0]
    assert not sig.s1.iloc[30, 0]


def test_missing_day_never_triggers_and_is_not_zero_flow():
    close, value, net, bs, member = _s1_frames()
    net.iloc[30, 0] = np.nan
    sig = _build((close, value, net, bs, member))
    assert not sig.valid.iloc[30, 0] and not sig.s1.iloc[30, 0]


def test_invalid_days_are_excluded_from_percentile_history():
    # name flat at +1e8 with a run of invalid days carrying a huge fake outflow. If those leaked into the history,
    # the 5th percentile would be hugely negative and the later moderate dump would not trigger.
    close, value, net, bs, member = _frames(4)
    j = 0
    net.iloc[:, j] = 1e8
    net.iloc[22:26, j] = -9e12
    bs.iloc[22:26, j] = BAD                                   # invalid -> must be ignored
    net.iloc[40, j] = -5e9
    close.iloc[40:, j] *= 0.95
    clean = _build((close, value, net, bs, member))
    assert clean.s1.iloc[40, j]
    bs2 = bs.copy()
    bs2.iloc[22:26, j] = 1e9                                  # same numbers but now VALID: they enter the history
    leaked = _build((close, value, net, bs2, member))
    assert not leaked.s1.iloc[40, j]


# ---- S2: 35-of-40 rule, top-5 selection --------------------------------------------------------------------

def _s2_frames(dump_day: int = 30):
    close, value, net, bs, member = _frames(5)
    net[:] = 1e8
    net.iloc[dump_day, :] = np.linspace(-1e10, -2e9, N_NAMES)   # market sum far below history; N00 most negative
    return close, value, net, bs, member


def test_s2_fires_and_buys_the_five_lowest_f():
    sig = _build(_s2_frames())
    d = sig.s2.index[30]
    assert sig.s2_day.iloc[30]
    picked = list(sig.s2.columns[sig.s2.loc[d].to_numpy()])
    f_t = sig.F.loc[d].sort_values()
    assert sorted(picked) == sorted(f_t.index[:5])
    assert int(sig.s2.loc[d].sum()) == 5


def test_s2_tie_break_larger_value_traded_first():
    close, value, net, bs, member = _s2_frames()
    # N00..N05 get identical F: the 5 lowest are N00..N05 minus the one with the smallest value traded.
    # F uses the *previous* 20 sessions' median value, so equalise those and tie F; today's value drives the tie-break.
    value[:] = 1e10
    net.iloc[30, :] = -2e9
    net.iloc[30, :6] = -1e11
    value.iloc[30, :6] = [1e10, 6e10, 3e10, 2e10, 5e10, 4e10]
    sig = _build((close, value, net, bs, member))
    picked = set(sig.s2.columns[sig.s2.iloc[30].to_numpy()])
    assert picked == {"N01", "N02", "N04", "N05", "N03"}      # N00 has the smallest value and is dropped


def test_s2_requires_35_of_40_valid_members():
    close, value, net, bs, member = _s2_frames()
    bs.iloc[30, :6] = BAD                                     # 34 valid members left on the dump day
    sig = _build((close, value, net, bs, member))
    assert not sig.s2_day.iloc[30]
    bs2 = bs.copy()
    bs2.iloc[30, :5] = 1e9                                    # only 5 invalid -> 35 valid: allowed
    sig2 = _build((close, value, net, bs2, member))
    assert sig2.s2_day.iloc[30]
    assert not sig2.s2.iloc[30, 5]                            # invalid name 5 never picked even if lowest


def test_s2_nonqualifying_days_are_excluded_from_market_history():
    close, value, net, bs, member = _s2_frames()
    # day 10 is non-qualifying (only 20 valid) and carries a gigantic fake outflow; it must not enter the history
    bs.iloc[10, :20] = BAD
    net.iloc[10, 20:] = -9e12
    sig = _build((close, value, net, bs, member))
    assert not sig.s2_day.iloc[10]
    assert sig.s2_day.iloc[30]


# ---- window / holdout guard --------------------------------------------------------------------------------

def _mask_on(date: str, ncols: int = 3) -> pd.DataFrame:
    idx = pd.bdate_range("2022-12-20", "2023-01-20")
    m = pd.DataFrame(False, index=idx, columns=[f"X{i}" for i in range(ncols)])
    m.loc[pd.Timestamp(date), "X0"] = True
    return m


def test_holdout_guard_raises_without_authorization_file(tmp_path):
    with pytest.raises(ff.HoldoutError):
        ff.guard_holdout(_mask_on("2023-01-03"), flag=tmp_path / "HOLDOUT_AUTHORIZED")


def test_holdout_guard_allows_dev_signals_and_authorized_runs(tmp_path):
    flag = tmp_path / "HOLDOUT_AUTHORIZED"
    ff.guard_holdout(_mask_on("2022-12-30"), flag=flag)       # dev signal: fine
    flag.write_text("ok")
    ff.guard_holdout(_mask_on("2023-01-03"), flag=flag)       # authorized: fine


def test_default_flag_is_not_present_in_the_repo():
    assert not ff.HOLDOUT_FLAG.exists()


def test_restrict_dates_cuts_to_the_dev_window():
    idx = pd.bdate_range("2016-12-20", "2023-01-10")
    m = pd.DataFrame(True, index=idx, columns=["A"])
    r = ff.restrict_dates(m, ff.DEV_START, ff.DEV_END)
    assert r.index[r["A"]].min() == pd.Timestamp("2017-01-02")
    assert r.index[r["A"]].max() == pd.Timestamp("2022-12-30")


def test_evaluate_refuses_a_holdout_dated_mask_before_computing(tmp_path):
    with pytest.raises(ff.HoldoutError):
        ff.evaluate(None, None, _mask_on("2023-01-03"), 1, "next_open", flag=tmp_path / "nope")


# ---- G9 ----------------------------------------------------------------------------------------------------

def _trades(dates, edge=10.0, cost=5.0):
    d = pd.to_datetime(pd.Series(dates))
    n = len(d)
    return pd.DataFrame({"date": d, "review_label": d.dt.strftime("%Y-%m-%d"), "edge_bps": float(edge),
                         "cost_cur": float(cost), "gross_bps": float(edge), "ihsg_ret": 0.0})


def test_g9_filter_removes_exactly_2020_02_01_to_12_31():
    dates = ["2020-01-31", "2020-02-01", "2020-06-15", "2020-12-31", "2021-01-04", "2019-05-01"]
    g = ff.g9_metrics(_trades(dates))
    assert g["n_removed"] == 3                                 # Feb 1, Jun 15, Dec 31 only
    assert g["n"] == 3


def test_g9_pass_requires_edge_net_and_net_minus_ihsg_positive():
    base = ["2019-05-01", "2021-01-04"]
    assert ff.g9_metrics(_trades(base, edge=10, cost=5))["pass"]
    assert not ff.g9_metrics(_trades(base, edge=10, cost=15))["pass"]       # net < 0
    assert not ff.g9_metrics(_trades(base, edge=-1, cost=0))["pass"]        # edge < 0
    t = _trades(base, edge=10, cost=5)
    t["ihsg_ret"] = 0.01                                                    # IHSG +100 bps beats net
    assert not ff.g9_metrics(t)["pass"]


def test_g9_fails_when_the_edge_is_only_2020():
    t = pd.concat([_trades(["2020-04-01"] * 3, edge=300, cost=5), _trades(["2019-05-01", "2021-03-01"], edge=-20, cost=5)],
                  ignore_index=True)
    assert ff.g9_metrics(t)["pass"] is False


# ---- Holm over exactly 6 cells -----------------------------------------------------------------------------

def _res(t: float, status: str = "tested") -> dict:
    return {"status": status, "metrics": {"t": t}, "gates": {}}


def test_holm_is_over_exactly_six_cells():
    results = {s: {k: _res(1.0) for k in ff.HOLDS} for s in ff.SIGNALS}
    results["S1"][1] = _res(3.0)
    ff.apply_holm(results)
    p = float(0.5 * __import__("math").erfc(3.0 / 2 ** 0.5))
    assert results["S1"][1]["p_holm"] == pytest.approx(min(1.0, 6 * p))      # smallest p multiplied by 6
    assert results["S1"][1]["gates"]["G7"] is True                           # 6 * 0.00135 < 0.05
    assert all("G7" in results[s][k]["gates"] for s in ff.SIGNALS for k in ff.HOLDS)


def test_holm_counts_insufficient_cells_as_p_one_and_still_uses_denominator_six():
    results = {s: {k: _res(0.0, "insufficient_n") for k in ff.HOLDS} for s in ff.SIGNALS}
    results["S2"][5] = _res(2.3)
    ff.apply_holm(results)
    p = 0.5 * __import__("math").erfc(2.3 / 2 ** 0.5)
    assert results["S2"][5]["p_holm"] == pytest.approx(6 * p)
    assert results["S1"][1]["p_holm"] == 1.0


# ---- drop 5 best dates ---------------------------------------------------------------------------------------

def test_drop_best_dates_removes_whole_dates_not_trades():
    dates = [f"2019-01-{d:02d}" for d in range(1, 11)]
    t = pd.concat([_trades([d] * 2, edge=0, cost=0) for d in dates], ignore_index=True)
    t.loc[t["review_label"].isin(dates[:5]), "edge_bps"] = 100.0           # the 5 best dates, 2 trades each
    assert ff.drop_best_dates(t) == pytest.approx(0.0)
    assert np.isnan(ff.drop_best_dates(_trades(dates[:5])))
