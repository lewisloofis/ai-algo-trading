"""Tests for index_events.event_legs / event_time on synthetic data."""
import numpy as np
import pandas as pd
import pytest

from index_events import event_legs as el
from index_events import event_time as et


def _synthetic(T=80, N=12, seed=0):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.01, (T, N)), axis=0))
    open_ = np.vstack([close[0:1], close[:-1]]) * np.exp(rng.normal(0, 0.004, (T, N)))
    volume = rng.integers(1, 1000, (T, N)).astype(float)
    return close, open_, volume


def test_legs_sum_to_full_window_market_adjusted_return():
    close, open_, volume = _synthetic()
    mkt = el.build_market(close, open_, volume)
    j, ia, ip = 3, 30, 43
    res = el.event_legs(close, open_, mkt, j, ia, ip)
    assert res["l5_k"] == 20
    legs_sum = sum(res[L] for L in el.LEGS)
    full_name = np.log(close[ip + 20, j] / close[ia - 20, j])
    m_on, m_id = el.market_excl(mkt, j, ia - 20, ip + 20)
    full_mkt = (np.nan_to_num(m_on) + np.nan_to_num(m_id))[1:].sum()
    assert legs_sum == pytest.approx(full_name - full_mkt, abs=1e-12)
    assert res["T15"] == pytest.approx(legs_sum, abs=1e-12)
    assert res["T14"] == pytest.approx(sum(res[L] for L in el.LEGS[:4]), abs=1e-12)


def test_raw_legs_telescope_and_market_excludes_event_name():
    close, open_, volume = _synthetic(seed=1)
    mkt = el.build_market(close, open_, volume)
    j, ia, ip = 5, 25, 40
    res = el.event_legs(close, open_, mkt, j, ia, ip)
    assert sum(res[f"raw_{L}"] for L in el.LEGS) == pytest.approx(np.log(close[ip + 20, j] / close[ia - 20, j]))
    # a huge move in the event name must not change its own market series
    c2 = close.copy()
    c2[ia + 1:, j] *= 3.0
    o2 = open_.copy()
    o2[ia + 1:, j] *= 3.0
    r2 = el.event_legs(c2, o2, el.build_market(c2, o2, volume), j, ia, ip)
    assert r2["mkt_L4"] == pytest.approx(res["mkt_L4"], abs=1e-9)


def test_partial_l5_uses_available_sessions_and_missing_endpoint_returns_none():
    close, open_, volume = _synthetic(T=60)
    mkt = el.build_market(close, open_, volume)
    res = el.event_legs(close, open_, mkt, 2, 30, 45)          # only 14 sessions after p
    assert res["l5_k"] == 14
    c2 = close.copy()
    c2[30, 2] = np.nan                                          # close_a missing
    assert el.event_legs(c2, open_, el.build_market(c2, open_, volume), 2, 30, 45) is None
    assert el.event_legs(close, open_, mkt, 2, 10, 45) is None   # a-20 before calendar start


def test_bad_bar_detection():
    c = np.array([100, 101, 102, 160, 161.0])
    assert el.bad_bar_in_window(c, 0, 4) is True
    assert el.bad_bar_in_window(c, 0, 2) is False


def test_clustered_t_hand_computed():
    # reviews: R1 -> events (1, 3) mean 2; R2 -> (4) mean 4; R3 -> (6, 8) mean 7
    vals = [1, 3, 4, 6, 8]
    rev = ["R1", "R1", "R2", "R3", "R3"]
    out = el.clustered_t(vals, rev)
    means = np.array([2.0, 4.0, 7.0])
    expect_t = means.mean() / (means.std(ddof=1) / np.sqrt(3))
    assert out["n_reviews"] == 3 and out["n_events"] == 5
    assert out["mean_reviews"] == pytest.approx(13 / 3)
    assert out["t"] == pytest.approx(expect_t)
    assert out["t"] == pytest.approx(2.0 / 1.0 * 0 + (13 / 3) / (np.sqrt(6.333333333333333) / np.sqrt(3)), rel=1e-6)


def test_clustered_t_single_review_is_nan():
    assert np.isnan(el.clustered_t([1, 2, 3], ["R", "R", "R"])["t"])


def test_decision_readings_direction_and_thresholds():
    def tab(l, share1=None):
        t = {k: {"mean": v[0], "t": v[1], "share": v[2] if len(v) > 2 else np.nan, "N": 5, "reviews": 5,
                 "median": v[0]} for k, v in l.items()}
        t["events"], t["reviews"] = 10, 15
        return t
    anticip = tab({"L1": (300, 2.5, 0.6), "L2": (50, 1, 0.1), "L3": (50, 1, 0.1), "L4": (100, 1, 0.2),
                   "L5": (0, 0), "T14": (500, 2)})
    assert any("anticipation" in x for x in el.decision_readings(anticip, "add"))
    wrong_dir = tab({"L1": (-300, -2.5, 0.6), "L2": (-50, 1, 0.1), "L3": (50, 1, 0.1), "L4": (-100, 1, 0.2),
                     "L5": (0, 0), "T14": (-500, -2)})
    assert not any(x.startswith("anticipation") for x in el.decision_readings(wrong_dir, "add"))
    rebound = tab({"L1": (-100, -1, 0.2), "L2": (-50, -1, 0.1), "L3": (-50, -1, 0.1), "L4": (-300, -3, 0.6),
                   "L5": (400, 2.5), "T14": (-500, -3)})
    assert any("H2 live candidate" in x for x in el.decision_readings(rebound, "del"))


def _ev(label, ann, rtype="semi"):
    return pd.DataFrame([{"review_label": label, "announce_ts_local": ann, "review_type": rtype,
                          "event_id": f"{label}:x"}])


def test_holdout_guard_raises_for_2022_non_freeze():
    with pytest.raises(et.HoldoutError):
        et.guard_holdout(_ev("MSCI-2022-05", "2022-05-10"))
    with pytest.raises(et.HoldoutError):
        et.guard_holdout(_ev("FTSE-2023-03", ""))                   # blank date falls back to the label month


def test_holdout_guard_allows_dev_and_freeze_and_split_drops_holdout():
    et.guard_holdout(_ev("MSCI-2021-11", "2021-11-10"))
    et.guard_holdout(_ev("MSCI-2026-05", "2026-05-12", "freeze"))
    ev = pd.concat([_ev("MSCI-2021-11", "2021-11-10"), _ev("MSCI-2022-02", "2022-02-08"),
                    _ev("MSCI-2026-05", "2026-05-12", "freeze")], ignore_index=True)
    dev, frz = et.split_events(ev)
    assert list(dev["review_label"]) == ["MSCI-2021-11"]
    assert list(frz["review_label"]) == ["MSCI-2026-05"]


def test_run_events_refuses_holdout_events():
    with pytest.raises(et.HoldoutError):
        et.run_events(_ev("MSCI-2024-05", "2024-05-14"), ctx=None)


def test_is_suspended_needs_a_run_of_zero_volume_sessions():
    import numpy as np
    from index_events.event_time import is_suspended
    assert not is_suspended(np.array([100, 0, 100, 0, 0, 100], float))
    assert is_suspended(np.array([100, 0, 0, 0, 0, 0, 100], float))
    assert is_suspended(np.array([np.nan] * 5, float))
