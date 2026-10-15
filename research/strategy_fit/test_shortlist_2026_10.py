"""Tests for strategy_fit/shortlist_2026_10.py (synthetic data only; no data dir, no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_fit import shortlist_2026_10 as sl
from strategy_fit.families_single_name import daily_momentum_signal, run_single_name_family
from strategy_fit.impact import TrailingImpactModel


def _bars(n: int = 400, seed: int = 7) -> sl.Bars:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    close = np.round(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, n))))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    df = pd.DataFrame({"date": dates, "open": close, "close": close, "volume": vol,
                       "value": close * vol, "net_foreign": rng.normal(0, 1e6, n)})
    lr = np.log(df["close"]).diff()
    df["vol60"] = lr.rolling(59, min_periods=19).std(ddof=1) * 1e4
    df["adv60"] = df["value"].rolling(60, min_periods=20).median()
    return sl.Bars("SYN", df)


def test_vectorised_impact_matches_trailing_model() -> None:
    b = _bars()
    fit = {"buckets": {"high_liq": 0.4, "mid_liq": 0.4, "low_liq": 0.4}, "per_name": {}}
    model = TrailingImpactModel(b.df[["date", "close", "volume", "value"]], symbol="SYN", use_calibrated=False)
    for i in (100, 250, 399):
        mine = sl.impact_bps(b.df["vol60"].to_numpy()[[i]], b.df["adv60"].to_numpy()[[i]], 3e6, fit)[0]
        theirs = model.impact_bps(b.df["date"].iloc[i]) * 0.4  # use_calibrated=False -> coeff 1.0
        assert abs(mine - theirs) < 1e-6 * max(1.0, abs(theirs))


def test_trades_match_harness_cell() -> None:
    b = _bars()
    tr, null = sl.trades_for(b, "daily_momentum", 2, "2024-01-01", "2030-01-01")
    ref = run_single_name_family(b.df, daily_momentum_signal, "SYN", 100.0, "daily_momentum", 2)
    assert abs(tr["gross_bps"].mean() - ref["gross_edge_bps"]) < 1e-9
    assert abs(null.mean() * 1e4 - ref["null_edge_bps"]) < 1e-9


def test_entry_is_next_session_close() -> None:
    b = _bars()
    tr, _ = sl.trades_for(b, "daily_momentum", 1, "2024-01-01", "2030-01-01")
    d = b.df.set_index("date")["close"]
    dates = list(b.df["date"])
    row = tr.iloc[0]
    assert dates.index(row["edate"]) == dates.index(row["sdate"]) + 1       # never the signal close
    assert row["epx"] == d[row["edate"]]


def test_drop_top_and_holm_and_cluster() -> None:
    assert sl.drop_top(np.array([1.0, 2, 3, 4, 5, 6, 100]), 5) == 1.5
    assert np.isnan(sl.drop_top(np.array([1.0, 2.0]), 5))
    adj = sl.holm([0.01, 0.04, 0.03])
    assert adj == [0.03, 0.06, 0.06]
    m, se, t = sl.cluster_t(np.array([1.0, 1.0, 1.0, 1.0]), np.array(["a", "a", "b", "b"]))
    assert m == 1.0 and se == 0.0 or np.isnan(t) or t > 0


def test_fee_and_cost_floor() -> None:
    assert abs((sl.FEE_BUY + sl.FEE_SELL) * 1e4 - sl.FEE_RT_BPS) < 1e-9
    tr = pd.DataFrame({"epx": [100.0], "gross_bps": [0.0], "vol60": [100.0], "adv60": [1e9]})
    out = sl.add_costs(tr, None)
    assert abs(out["floor_bps"].iloc[0] - (100.0 + 40.0)) < 1e-9           # 1 IDR tick on Rp100 = 100 bps
    assert out["imp1_current"].iloc[0] < 1.0                               # one lot is negligible impact
