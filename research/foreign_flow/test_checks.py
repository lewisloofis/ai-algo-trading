"""Each plan 22 s2.3 check must detect an injected violation (and stay quiet on clean data)."""
import pandas as pd

from foreign_flow import checks as ck
from foreign_flow import client as cl

CAL = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]))


def ff(dates, **over):
    rows = []
    for d in dates:
        r = {"date": d, "net_foreign": 90.0, "foreign_buy": 100.0, "foreign_sell": 10.0,
             "foreign_flow": 0.0, "net_lot": 1.0, "net_frequency": 1, "average_price": 5.0,
             "percentage_foreign_value": 10.0, "percentage_domestic_value": 90.0, "sb_close": 50.0}
        r.update(over.get(d, {}))
        rows.append(r)
    return cl.normalise(pd.DataFrame(rows))


def bars(dates, value=1000.0, close=50.0):
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": close, "volume": 1.0, "value": value})


D = list(CAL.strftime("%Y-%m-%d"))


def test_clean_data_passes_everything():
    f, d = {"A": ff(D)}, {"A": bars(D)}
    assert ck.check_net_identity(f).empty
    a, b = ck.check_sessions(f, d, CAL)
    assert a.empty and b.empty
    assert ck.check_value_bound(f, d).empty
    bad, rate = ck.check_close_match(f, d)
    assert bad.empty and rate == 1.0


def test_check1_net_identity_violation_detected_and_tolerance():
    f = {"A": ff(D, **{"2024-01-03": {"net_foreign": 90.0 + 5_000_000}, "2024-01-04": {"net_foreign": 90.0 + 500_000}})}
    bad = ck.check_net_identity(f)
    assert list(bad["date"]) == [pd.Timestamp("2024-01-03")]


def test_check2_non_session_and_missing_row():
    f = {"A": ff(["2024-01-02", "2024-01-04", "2024-01-06"])}        # 01-06 is a Saturday, 01-03 missing
    d = {"A": bars(D)}
    not_session, missing = ck.check_sessions(f, d, CAL)
    assert list(not_session["date"]) == [pd.Timestamp("2024-01-06")]
    assert list(missing["date"]) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-05")]


def test_check3_buy_plus_sell_over_twice_value():
    f = {"A": ff(D, **{"2024-01-04": {"foreign_buy": 1500.0, "foreign_sell": 600.0}})}   # 2100 > 2000
    bad = ck.check_value_bound(f, {"A": bars(D)})
    assert list(bad["date"]) == [pd.Timestamp("2024-01-04")]


def test_check4_close_mismatch_rate():
    f = {"A": ff(D, **{"2024-01-04": {"sb_close": 77.0}})}
    bad, rate = ck.check_close_match(f, {"A": bars(D)})
    assert len(bad) == 1 and rate == 0.75 and rate < ck.CLOSE_MATCH_MIN


def test_check5_coverage_table_counts_and_first_date():
    f = {"A": ff(["2023-12-29", "2024-01-02", "2024-01-03"])}
    cov = ck.coverage_table(f)
    r = cov.iloc[0]
    assert r["first_date"] == "2023-12-29" and r[2023] == 1 and r[2024] == 2


def test_missing_name_days_flags_data_blocked_year_only_when_over_5pct():
    cal = pd.bdate_range("2024-01-01", "2025-03-31")
    uni = pd.DataFrame({"month_end": ["2023-12-31"] * 2 + ["2024-12-31"] * 2,
                        "symbol": ["A", "B"] * 2})
    full = lambda idx: ff(list(idx.strftime("%Y-%m-%d")))
    frames = {"A": full(cal), "B": full(cal[cal.year == 2025])}     # B has no 2024 rows at all
    out = ck.missing_name_days(frames, uni, cal).set_index("year")
    assert out.loc[2024, "status"] == "data_blocked" and abs(out.loc[2024, "missing_share"] - 0.5) < 1e-9
    assert out.loc[2025, "status"] == "ok" and out.loc[2025, "missing"] == 0


def test_membership_uses_prior_month_end_only():
    cal = pd.bdate_range("2024-01-01", "2024-02-29")
    uni = pd.DataFrame({"month_end": ["2023-12-31", "2024-01-31"], "symbol": ["A", "B"]})
    frames = {"A": ff(list(cal.strftime("%Y-%m-%d"))), "B": ff([])}
    out = ck.missing_name_days(frames, uni, cal).iloc[0]
    n_jan, n_feb = (cal.month == 1).sum(), (cal.month == 2).sum()
    assert out["expected"] == n_jan + n_feb and out["missing"] == n_feb   # B is a member from Feb only


def test_report_end_to_end(tmp_path):
    f, d = {"A": ff(D)}, {"A": bars(D)}
    uni = pd.DataFrame({"month_end": ["2023-12-31"], "symbol": ["A"]})
    md, stats = ck.build_report(f, d, CAL, uni)
    assert stats["c1"] == 0 and stats["blocked"] == [] and "match rate 1.0000" in md
