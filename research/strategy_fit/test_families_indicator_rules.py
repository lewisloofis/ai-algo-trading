"""Tests for strategy_fit/families_indicator_rules.py on synthetic series (no data dir, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit import families_indicator_rules as ir

N_NAMES = 12                  # build_panel keeps a date only if >= 10 names traded (one name may be halted)
N_DAYS = 520                  # 2021-03-01 .. 2023-02 : crosses the 2023-01-01 holdout boundary
T0 = 300                      # a session that is eligible (>= 252 sessions of history) and well before dev end


def _hist(seed: int | None = None, n_days: int = N_DAYS, mods: dict | None = None) -> dict[str, pd.DataFrame]:
    """Deterministic prices (open_i = 1000 + i, close_i = open_i + 0.5) or a random walk when ``seed`` is set.
    ``mods`` = {(name_index, day_index): {column: value}}."""
    dates = pd.bdate_range("2021-03-01", periods=n_days)
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(N_NAMES):
        if seed is None:
            open_ = 1000.0 + np.arange(n_days)
        else:
            open_ = 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
        df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": open_, "close": open_ + 0.5,
                           "high": open_ + 1.0, "low": open_ - 1.0, "volume": 1e6, "value": 5e9, "dividend": 0.0})
        out[f"N{k:02d}"] = df
    for (k, i), cols in (mods or {}).items():
        for c, v in cols.items():
            out[f"N{k:02d}"].loc[i, c] = v
    return out


def _ihsg(n_days: int = N_DAYS) -> pd.DataFrame:
    dates = pd.bdate_range("2021-03-01", periods=n_days)
    px = 6000.0 + np.arange(n_days)
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": px, "close": px + 1.0})


def _env(hist=None, ihsg=None, end=ir.DEV_END):
    return ir.build_env(hist or _hist(), ihsg if ihsg is not None else _ihsg(), end)


def _masks(book, entries=(), exits=(), name=0):
    en = np.zeros((book.n, len(book.symbols)), bool)
    ex = en.copy()
    for t in entries:
        en[t, name] = True
    for u in exits:
        ex[u, name] = True
    return en, ex


def _trades(hist, entries, exits, time_stop=None, name=0):
    env, _ = _env(hist)
    book = ir.build_book(env)
    return book, *ir.build_trades(book, *_masks(book, entries, exits, name), time_stop)


# ---- indicator values on small hand-checked series ---------------------------------------------------------

def test_sma_has_no_value_before_full_window():
    out = ir.sma(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert out.isna().tolist() == [True, True, False, False, False]
    assert out.dropna().tolist() == [2.0, 3.0, 4.0]


def test_ema_adjust_false_recursion():
    out = ir.ema(pd.Series([1.0, 2.0, 4.0]), 2)          # alpha = 2/3
    assert out.tolist() == pytest.approx([1.0, 1.0 / 3 + 4.0 / 3, (1.0 / 3) * (5.0 / 3) + (2.0 / 3) * 4])


def test_macd_matches_python_recursion_and_waits_for_26_plus_9():
    rng = np.random.default_rng(1)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))

    def ema_loop(x, span):
        a, y, out = 2.0 / (span + 1), x[0], [x[0]]
        for v in x[1:]:
            y = (1 - a) * y + a * v
            out.append(y)
        return np.array(out)
    line = ema_loop(close.to_numpy(), 12) - ema_loop(close.to_numpy(), 26)
    sig = ema_loop(line, 9)
    m, s = ir.macd(close)
    assert m.iloc[:34].isna().all() and s.iloc[:34].isna().all()
    assert m.iloc[34:].to_numpy() == pytest.approx(line[34:])
    assert s.iloc[34:].to_numpy() == pytest.approx(sig[34:])


def test_rsi_wilder_period_2_hand_values():
    # diffs: -,+1,-1,+2,-1 ; ewm alpha=.5 : avg gain .5,1.25,.625 ; avg loss .5,.25,.625 (from the 3rd value on)
    out = ir.rsi_wilder(pd.Series([10.0, 11, 10, 12, 11]), 2)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(50.0)
    assert out.iloc[3] == pytest.approx(100 - 100 / 6)
    assert out.iloc[4] == pytest.approx(50.0)


def test_rsi_is_100_on_pure_gains_and_0_on_pure_losses():
    assert ir.rsi_wilder(pd.Series(np.arange(1.0, 30)), 14).iloc[-1] == pytest.approx(100.0)
    assert ir.rsi_wilder(pd.Series(np.arange(30.0, 1, -1)), 14).iloc[-1] == pytest.approx(0.0)


def test_bollinger_uses_population_std():
    lower, mid, upper = ir.bollinger(pd.Series([2.0, 4, 4, 4, 5]), n=4, k=2.0)
    assert mid.iloc[3] == pytest.approx(3.5)
    assert (upper - mid).iloc[3] == pytest.approx(2 * np.sqrt(0.75))          # ddof=0: var = 3.0 / 4
    assert mid.iloc[4] == pytest.approx(4.25)
    assert (mid - lower).iloc[4] == pytest.approx(2 * np.sqrt(0.1875))
    assert lower.iloc[:3].isna().all()


def test_crosses_need_false_then_true_and_ignore_missing():
    a = pd.Series([1.0, 1.0, 2.0, 2.0, 1.0, np.nan, 2.0])
    b = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert ir.crosses_above(a, b).tolist() == [False, False, True, False, False, False, False]
    assert ir.crosses_below(a, b).tolist() == [False] * 7           # 2 -> 1 is only "equal", not below


# ---- each rule's signal timing ----------------------------------------------------------------------------

def test_r1_golden_cross_fires_on_first_close_sma50_exceeds_sma200():
    close = pd.Series([100.0] * 250 + list(np.arange(101.0, 151.0)) + list(np.arange(150.0, 0, -4)))
    entry, exit_ = ir.rule_signals("R1", close)
    assert entry[entry].index.tolist() == [250]
    f, s = ir.sma(close, 50), ir.sma(close, 200)
    first_below = int(np.flatnonzero((f < s).to_numpy())[0])
    assert exit_[exit_].index.tolist() == [first_below]
    assert ir.rule_signals("R1", close.iloc[:199])[0].sum() == 0       # warm-up: no signal before SMA200 exists


def test_r2_signals_equal_python_loop_crosses():
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 120)))
    m, s = ir.macd(close)
    mm, ss = m.to_numpy(), s.to_numpy()
    up = [i for i in range(1, 120) if mm[i - 1] <= ss[i - 1] and mm[i] > ss[i]]
    dn = [i for i in range(1, 120) if mm[i - 1] >= ss[i - 1] and mm[i] < ss[i]]
    entry, exit_ = ir.rule_signals("R2", close)
    assert entry[entry].index.tolist() == up and exit_[exit_].index.tolist() == dn
    assert len(up) > 0 and len(dn) > 0 and min(up + dn) >= 35


def test_r3_enters_on_the_cross_below_30_and_exits_at_50():
    close = pd.Series([100.0] * 30 + list(np.arange(100.0, 60, -1.0)) + list(np.arange(60.0, 120, 3.0)))
    r = ir.rsi_wilder(close, 14)
    entry, exit_ = ir.rule_signals("R3", close)
    first = int(np.flatnonzero((r < 30).to_numpy())[0])
    assert entry[entry].index.tolist()[0] == first and r[first - 1] >= 30
    assert entry.sum() == 1                                           # stays below 30 afterwards: no new "cross"
    assert (exit_ == (r >= 50)).all()


def test_r4_enters_below_lower_band_and_exits_at_middle_band():
    close = pd.Series([100.0] * 20 + [90.0, 100.0, 100.0])
    entry, exit_ = ir.rule_signals("R4", close)
    assert entry[entry].index.tolist() == [20]                        # flat history: close == lower is NOT below
    assert not exit_.iloc[20]                                         # 90 < mid
    assert exit_.iloc[21]                                             # 100 >= (18*100+90+100)/20 = 99.5


def test_r5_needs_rsi2_below_10_inside_an_uptrend_and_exits_above_sma5():
    c = [100.0 + i for i in range(250)] + [345.0, 341.0, 361.0]       # +1/day, then 4 down, 4 down, +20
    entry, exit_ = ir.rule_signals("R5", pd.Series(c))
    assert entry[entry].index.tolist() == [251]                       # RSI2 = 7.69; at 250 it is 20
    assert not exit_.iloc[251]                                        # 341 < SMA5 = 346
    assert exit_.iloc[252]                                            # 361 > SMA5 = 348.8
    downtrend = pd.Series([400.0 - i for i in range(250)] + [150.0, 140.0])
    assert ir.rule_signals("R5", downtrend)[0].sum() == 0             # close below SMA200: no dip buy


# ---- trade builder: timing, time stop, one position, dividends, bad bars --------------------------------------

def test_signal_on_t_trades_open_t_plus_1_and_exit_signal_u_trades_open_u_plus_1():
    book, T, diag = _trades(_hist(), [T0], [T0 + 5])
    r = T.iloc[0]
    assert (r["pos"], r["e"], r["x"], r["hold"]) == (T0, T0 + 1, T0 + 6, 5)
    assert r["gross"] == pytest.approx((1000.0 + T0 + 6) / (1000.0 + T0 + 1) - 1)
    assert r["exit_reason"] == "exit_signal" and diag["trades"] == 1


def test_exit_signal_on_the_entry_signal_day_is_ignored_but_day_after_entry_counts():
    _, T, _ = _trades(_hist(), [T0], [T0, T0 + 1])
    assert (T.iloc[0]["e"], T.iloc[0]["x"]) == (T0 + 1, T0 + 2)       # u = t+1 = e is allowed, u = t is not


def test_time_stop_counts_sessions_held_and_exits_next_open():
    _, T, diag = _trades(_hist(), [T0], [], time_stop=20)
    r = T.iloc[0]
    assert r["hold"] == 20 and r["exit_reason"] == "exit_time_stop"
    assert (r["e"], r["x"]) == (T0 + 1, T0 + 21)                      # held on sessions e..e+19, sold at open e+20
    assert diag["exit_time_stop"] == 1


def test_earlier_exit_signal_beats_the_time_stop():
    _, T, _ = _trades(_hist(), [T0], [T0 + 4], time_stop=20)
    assert T.iloc[0]["hold"] == 4 and T.iloc[0]["exit_reason"] == "exit_signal"


def test_one_position_per_name_ignores_signals_while_open_and_allows_reentry_at_the_exit_open():
    _, T, diag = _trades(_hist(), [T0, T0 + 2, T0 + 5, T0 + 10], [T0 + 5])
    assert T["e"].tolist() == [T0 + 1, T0 + 6]                        # t+2 ignored; t+5 enters at open t+6 = exit open
    assert diag["overlap_ignored"] == 2                                # t+2, and t+10 (2nd position runs to dev end)
    assert T.iloc[0]["x"] == T0 + 6


def test_exit_at_next_traded_open_when_the_name_does_not_trade_at_the_exit_open():
    hist = _hist(mods={(0, T0 + 6): {"volume": 0.0}, (0, T0 + 7): {"volume": 0.0}})
    _, T, _ = _trades(hist, [T0], [T0 + 5])
    assert T.iloc[0]["x"] == T0 + 8


def test_entry_skipped_when_next_open_is_untraded_or_locked_up_or_ineligible():
    hist = _hist(mods={(0, T0 + 1): {"volume": 0.0}})
    _, T, diag = _trades(hist, [T0], [T0 + 5])
    assert T.empty and diag["entry_not_traded"] == 1
    close_t = 1000.0 + T0 + 0.5
    hist = _hist(mods={(0, T0 + 1): {"low": close_t * 1.3, "high": close_t * 1.4}})
    _, T, diag = _trades(hist, [T0], [T0 + 5])
    assert T.empty and diag["locked_upper_limit"] == 1
    _, T, diag = _trades(_hist(), [100], [105])                       # < 252 sessions of history
    assert T.empty and diag["not_eligible"] == 1


def test_dividends_with_ex_date_in_entry_exit_window_are_added_back_net_of_tax():
    hist = _hist(mods={(0, T0 + 1): {"dividend": 50.0},               # ex-date == entry session: not in (e, x]
                       (0, T0 + 3): {"dividend": 10.0},
                       (0, T0 + 6): {"dividend": 20.0},               # ex-date == exit session: included
                       (0, T0 + 7): {"dividend": 99.0}})              # after the exit: excluded
    _, T, _ = _trades(hist, [T0], [T0 + 5])
    expected = ((1000.0 + T0 + 6) + (10.0 + 20.0) * 0.9) / (1000.0 + T0 + 1) - 1
    assert T.iloc[0]["gross"] == pytest.approx(expected)
    assert T.iloc[0]["gross"] > (1000.0 + T0 + 6) / (1000.0 + T0 + 1) - 1


def test_trade_with_a_bad_bar_inside_is_voided_and_still_blocks_the_name():
    hist = _hist(mods={(0, T0 + 3): {"close": 2.5 * (1000.0 + T0 + 2)}})
    _, T, diag = _trades(hist, [T0, T0 + 2], [T0 + 5])
    assert T.empty and diag["bad_bar_void"] == 1 and diag["overlap_ignored"] == 1


def test_open_trade_at_dev_end_is_closed_at_the_last_dev_session_open():
    env, _ = _env()
    book = ir.build_book(env)
    t = book.last - 4
    T, diag = ir.build_trades(book, *_masks(book, [t], []), None)
    r = T.iloc[0]
    assert r["x"] == book.last and r["exit_reason"] == "dev_end" and diag["closed_at_dev_end"] == 1
    T2, diag2 = ir.build_trades(book, *_masks(book, [book.last - 1], []), None)   # enters at the last open: 0 hold
    assert T2.empty and diag2["zero_hold"] == 1
    T3, _ = ir.build_trades(book, *_masks(book, [t], [], ), 20)                   # time stop beyond dev end
    assert T3.iloc[0]["x"] == book.last


def test_dev_panel_never_contains_a_session_dated_2023_or_later():
    env, ihsg_open = _env()
    assert env.panel.dates.max() <= ir.DEV_END
    assert len(ihsg_open) == len(env.panel.dates)


# ---- no read of post-dev prices -------------------------------------------------------------------------------

def _poison(hist, value):
    out = {}
    for s, df in hist.items():
        d = df.copy()
        late = pd.to_datetime(d["date"]) >= ir.HOLDOUT_START
        for c in ("open", "high", "low", "close", "value"):
            d.loc[late, c] = value
        d.loc[late, "dividend"] = 1e9
        out[s] = d
    return out


@pytest.mark.parametrize("value", [np.nan, 1e12])
def test_poisoned_2023_prices_leave_development_results_unchanged(value):
    hist = _hist(seed=7)
    ihsg = _ihsg()
    base_env, base_open = _env(hist, ihsg)
    poisoned_ihsg = ihsg.copy()
    late = pd.to_datetime(poisoned_ihsg["date"]) >= ir.HOLDOUT_START
    poisoned_ihsg.loc[late, ["open", "close"]] = value
    pz_env, pz_open = _env(_poison(hist, value), poisoned_ihsg)
    a = ir.run_dev(base_env, base_open, n_draws=10)
    b = ir.run_dev(pz_env, pz_open, n_draws=10)
    ta = pd.concat(a["_trades"], ignore_index=True)
    tb = pd.concat(b["_trades"], ignore_index=True)
    assert len(ta) > 0
    pd.testing.assert_frame_equal(ta, tb)
    assert ir.jsonable(a)["cells"]["R2"]["metrics"] == ir.jsonable(b)["cells"]["R2"]["metrics"]
    assert (ta["exit_date"] <= ir.DEV_END).all() and (ta["entry_date"] <= ir.DEV_END).all()


# ---- holdout guard ---------------------------------------------------------------------------------------------

def test_guard_holdout_raises_without_the_flag_and_passes_with_it(tmp_path):
    late = pd.DatetimeIndex(["2022-12-30", "2023-01-02"])
    flag = tmp_path / "HOLDOUT_AUTHORIZED"
    with pytest.raises(ir.HoldoutError):
        ir.guard_holdout(late, flag)
    ir.guard_holdout(pd.DatetimeIndex(["2022-12-30"]), flag)             # dev dates never need the flag
    flag.write_text("")
    ir.guard_holdout(late, flag)


def test_building_an_env_beyond_the_dev_end_is_refused_and_the_real_flag_does_not_exist():
    assert not ir.HOLDOUT_FLAG.exists()
    with pytest.raises(ir.HoldoutError):
        ir.build_env(_hist(), _ihsg(), end=pd.Timestamp("2023-02-15"))


# ---- Holm, verdicts, gates ------------------------------------------------------------------------------------

def _fake(t: float, status="tested"):
    return {"status": status, "metrics": {"t": t}, "gates": {"G2": True}}


def test_holm_is_over_exactly_five_cells_and_insufficient_n_counts_as_p_one():
    assert ir.RULES == ("R1", "R2", "R3", "R4", "R5")
    res = {r: _fake(0.0) for r in ir.RULES}
    res["R2"] = _fake(6.0)                                              # p ~ 1e-9, others p = 0.5
    res["R5"] = _fake(9.0, "insufficient_n")
    ir.apply_holm(res)
    assert res["R2"]["p_holm"] == pytest.approx(5 * res["R2"]["p_one_sided"])      # multiplier m = 5
    assert res["R5"]["p_one_sided"] == 1.0
    assert res["R2"]["gates"]["G7"] and not res["R1"]["gates"]["G7"]
    assert "G7" not in res["R5"]["gates"]


def test_verdict_lists_failed_gates_and_insufficient_n_is_not_a_fail():
    g = {"G2": True, "G3": False, "G3a_current": True, "G3b_candidate": False, "G3c_1lot": True, "G4": True,
         "G6": True, "G7": False, "G9": True}
    assert ir.verdict({"status": "tested", "gates": g}) == ("FAIL", ["G3b_candidate", "G7"])
    assert ir.verdict({"status": "insufficient_n", "gates": {}}) == ("insufficient_n", [])
    ok = {k: True for k in g}
    assert ir.verdict({"status": "tested", "gates": ok}) == ("PASS_DEV", [])


def test_sample_floor_is_100_trades_and_24_months():
    assert ir.sample_status(100, 24) == "ok"
    assert ir.sample_status(99, 24) == "insufficient_n"
    assert ir.sample_status(100, 23) == "insufficient_n"


def _tbl(edge, cost, dates, gross=None):
    n = len(edge)
    d = pd.to_datetime(dates)
    return pd.DataFrame({"edge_bps": edge, "gross_bps": edge if gross is None else gross, "cost_cur": cost,
                         "ihsg_ret": 0.0, "entry_date": d, "date_label": d.strftime("%Y-%m-%d"),
                         "review_label": d.strftime("%Y-%m")})


def test_drop_best_dates_removes_the_five_best_entry_dates():
    dates = [f"2019-01-{i:02d}" for i in range(1, 9)]
    T = _tbl([100.0, 90, 80, 70, 60, 10, 10, 10], [0.0] * 8, dates)
    assert ir.drop_best_dates(T) == pytest.approx(10.0)
    assert np.isnan(ir.drop_best_dates(T.iloc[:5]))


def test_g9_removes_entries_dated_2020_02_to_2020_12():
    dates = ["2019-06-03", "2019-06-04", "2020-03-02", "2020-03-03", "2021-01-04"]
    T = _tbl([50.0, 50, -500, -500, 50], [10.0] * 5, dates)
    g = ir.g9_metrics(T)
    assert g["n_removed"] == 2 and g["n"] == 3 and g["pass"]
    assert g["edge"] == pytest.approx(50.0) and g["net"] == pytest.approx(40.0)
    assert not ir.g9_metrics(T.assign(edge_bps=[50.0, 50, -500, -500, -150]))["pass"]


# ---- views ------------------------------------------------------------------------------------------------------

def test_c_rand_is_reproducible_and_percentile_is_in_range():
    from strategy_fit import families_indicator_rules_views as views
    env, ihsg_open = _env(_hist(seed=11))
    book = ir.build_book(env)
    en = np.zeros((book.n, N_NAMES), bool)
    en[T0:book.last - 30:15, :] = True
    T, _ = ir.build_trades(book, en, np.zeros_like(en), 10, 0)
    T = ir.score_trades(env, book, T, ihsg_open)
    a, b = views.c_rand(book, T, 50, 24), views.c_rand(book, T, 50, 24)
    assert a == b and 0.0 <= a["percentile"] <= 100.0 and a["n_used"] > 0
    port = views.portfolio_view(env, book, T, ihsg_open)
    assert -1.0 <= port["rule"]["max_drawdown"] <= 0.0 and 0.0 < port["time_in_market"] <= 1.0
