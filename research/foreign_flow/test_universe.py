"""Tests for foreign_flow.universe: point-in-time ranking, no look-ahead, value definition."""
import numpy as np
import pandas as pd

from foreign_flow import universe as un

DATES = pd.bdate_range("2016-06-01", "2017-02-28")


def panel(**cols):
    return pd.DataFrame({k: pd.Series(v, index=DATES) for k, v in cols.items()})


def test_late_bloomer_not_in_early_months_and_no_lookahead():
    switch = pd.Timestamp("2017-01-02")
    liquid = np.where(DATES >= switch, 1e12, 1.0)      # becomes huge only from 2017-01-02
    p = panel(OLD=np.full(len(DATES), 1e9), LATE=liquid)
    uni = un.monthly_universe(p, start="2016-11", end="2017-01", top_n=1, window=60)
    top = dict(zip(uni["month_end"], uni["symbol"]))
    assert top["2016-11-30"] == "OLD" and top["2016-12-31"] == "OLD"   # LATE's future is not seen
    # Jan: window = 60 sessions ending 2017-01-31 -> LATE has ~21 huge days of 60; median still tiny
    assert top["2017-01-31"] == "OLD"


def test_value_after_month_end_never_changes_membership():
    base = panel(A=np.full(len(DATES), 5.0), B=np.full(len(DATES), 3.0))
    spiked = base.copy()
    spiked.loc[spiked.index > "2016-12-31", "B"] = 1e15
    a = un.monthly_universe(base, start="2016-12", end="2016-12", top_n=2, window=60)
    b = un.monthly_universe(spiked, start="2016-12", end="2016-12", top_n=2, window=60)
    pd.testing.assert_frame_equal(a, b)


def test_ranking_median_missing_as_zero_and_top_n():
    n = len(DATES)
    sparse = np.full(n, np.nan); sparse[-5:] = 1e12            # few huge days: median 0 -> excluded
    p = panel(A=np.full(n, 9.0), B=np.full(n, 8.0), C=np.full(n, 7.0), S=sparse)
    uni = un.monthly_universe(p, start="2017-02", end="2017-02", top_n=2, window=20)
    assert list(uni["symbol"]) == ["A", "B"] and list(uni["rank"]) == [1, 2]
    assert uni["month_end"].iloc[0] == "2017-02-28"


def test_insufficient_history_month_skipped():
    p = panel(A=np.full(len(DATES), 1.0))
    uni = un.monthly_universe(p, start="2016-05", end="2016-06", top_n=1, window=60)
    assert uni.empty


def test_ties_break_by_symbol():
    p = panel(Z=np.full(len(DATES), 2.0), A=np.full(len(DATES), 2.0))
    uni = un.monthly_universe(p, start="2016-12", end="2016-12", top_n=1, window=60)
    assert list(uni["symbol"]) == ["A"]


def test_value_traded_prefers_value_column_else_close_times_volume():
    df = pd.DataFrame({"close": [10.0, 10.0, 10.0], "volume": [5.0, 5.0, np.nan], "value": [100.0, 0.0, 0.0]})
    out = un.value_traded(df)
    assert out[0] == 100.0 and out[1] == 50.0 and np.isnan(out[2])
    assert un.value_traded(df.drop(columns="value"))[0] == 50.0


def test_load_daily_excludes_index_symbols_and_reads_value(tmp_path):
    rows = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [1.0, 2.0],
                         "volume": [10, 10], "value": [7, 0]})
    rows.to_parquet(tmp_path / "AAA.parquet"); rows.to_parquet(tmp_path / "IHSG.parquet")
    d = un.load_daily(tmp_path)
    assert list(d) == ["AAA"] and list(d["AAA"]["value"]) == [7.0, 20.0]


def test_month_ends_and_download_list_union():
    assert un.month_ends("2016-12", "2017-02")[0] == pd.Timestamp("2016-12-31")
    uni = pd.DataFrame({"symbol": ["B", "A", "B"], "month_end": ["x", "x", "y"]})
    assert un.download_list(uni) == ["A", "B"]
