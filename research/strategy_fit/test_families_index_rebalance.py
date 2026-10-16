"""Tests for strategy_fit/families_index_rebalance.py on synthetic data (no data dir, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from index_events import INDEX_SYMBOLS
from index_events import event_time as et
from strategy_fit import families_index_rebalance as fr
from strategy_fit.families_idx_structural import build_event_trades, build_panel

N_SESS, N_NAMES = 80, 14


def _panel(seed: int = 0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=N_SESS)
    hist = {}
    for i in range(N_NAMES):
        close = np.round(1000 * np.exp(np.cumsum(rng.normal(0, 0.01, N_SESS))), 0)
        hist[f"S{i:02d}"] = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"), "open": close, "high": close, "low": close, "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, N_SESS).astype(float), "dividend": 0.0})
    return build_panel(hist)


def _env(panel):
    ihsg = pd.Series(7000 * np.exp(np.cumsum(np.full(N_SESS, 0.001))), index=panel.dates)
    elig = pd.DataFrame(True, index=panel.dates, columns=panel.symbols)
    return fr.make_env(panel, ihsg, elig_default=elig)


def _ev(rows: list[dict], panel) -> pd.DataFrame:
    base = {"provider": "MSCI", "index_name": "MSCI_ST", "change": "del", "review_type": "quarterly",
            "cross_check": "ok", "map_confidence": "high", "announce_ts_local": "2019-02-01"}
    out = []
    for i, r in enumerate(rows):
        d = {**base, "event_id": f"E{i}", **r}
        d["announce_session"] = panel.dates[d.pop("ia")].strftime("%Y-%m-%d")
        d["passive_trade_session"] = panel.dates[d.pop("ip")].strftime("%Y-%m-%d")
        out.append(d)
    return pd.DataFrame(out)


def test_mask_puts_signal_at_p_so_entry_is_close_p_plus_1():
    panel = _panel()
    env = _env(panel)
    ev = _ev([{"ticker": "S03", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02"}], panel)
    prep, _ = fr.prepare_events(fr.cell_events(ev, "MSCI_ST"), env)
    mask = fr.event_mask(panel, prep)
    assert mask.to_numpy().sum() == 1 and mask.iloc[30, 3]
    T = build_event_trades(panel, mask, 5, eligible=env.elig_default, notional_idr=fr.NOTIONAL_IDR)
    assert len(T) == 1 and int(T["pos"].iloc[0]) == 30
    c = panel.close["S03"].to_numpy()
    assert T["gross"].iloc[0] == pytest.approx(c[36] / c[31] - 1.0)       # close_{p+1} -> close_{p+1+k}
    enriched, res = fr.evaluate(env, prep, 5)
    assert enriched["ihsg_ret"].iloc[0] == pytest.approx(env.ihsg_close[36] / env.ihsg_close[31] - 1.0)
    assert res["exclusions"]["trades"] == 1


def test_pooled_dedups_one_ticker_p_trade_across_providers():
    panel = _panel()
    env = _env(panel)
    ev = _ev([
        {"ticker": "S03", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02"},
        {"ticker": "S03", "ia": 22, "ip": 30, "review_label": "FTSE-2019-03", "provider": "FTSE", "index_name": "FTSE_GEIS"},
        {"ticker": "S04", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02", "change": "migrate_out"},
        {"ticker": "S05", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02", "change": "add"},     # not a deletion
    ], panel)
    prep, pre = fr.prepare_events(fr.cell_events(ev, "pooled"), env)
    assert sorted(prep["ticker"]) == ["S03", "S04"]
    assert pre["duplicate_ticker_p"] == 1
    assert prep.set_index("ticker").loc["S03", "review_label"] == "MSCI-2019-02"   # MSCI outranks FTSE
    ftse_prep, _ = fr.prepare_events(fr.cell_events(ev, "FTSE"), env)
    assert len(ftse_prep) == 1                                                      # still counted in its own cell


def test_pre_exclusions_are_counted():
    panel = _panel()
    env = _env(panel)
    vol = panel.volume.copy()
    vol.iloc[22:30, 7] = 0.0                                                         # S07 suspended a+1..p
    env_s = fr.make_env(type(panel)(**{**panel.__dict__, "volume": vol}), pd.Series(1.0, index=panel.dates),
                        elig_default=env.elig_default)
    ev = _ev([
        {"ticker": "S07", "ia": 20, "ip": 30, "review_label": "R1"},
        {"ticker": "NOPE", "ia": 20, "ip": 30, "review_label": "R1"},
        {"ticker": "S01", "ia": 20, "ip": 30, "review_label": "R1", "cross_check": "mismatch"},
        {"ticker": "S02", "ia": 20, "ip": 30, "review_label": "R1", "map_confidence": "low"},
    ], panel)
    ev.loc[len(ev)] = {**ev.iloc[0], "event_id": "E9", "ticker": "S08", "passive_trade_session": ""}
    prep, pre = fr.prepare_events(fr.cell_events(ev, "MSCI_ST"), env_s)
    assert prep.empty
    assert pre == {"no_dates": 1, "ticker_unresolved": 2, "no_bars": 1, "suspended_a_to_p": 1, "duplicate_ticker_p": 0}


def test_dev_guard_raises_on_non_freeze_2022_review_and_holdout_needs_flag(tmp_path, monkeypatch):
    panel = _panel()
    ok = _ev([{"ticker": "S03", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02"}], panel)
    fr.guard_dev_events(ok)
    bad = _ev([{"ticker": "S03", "ia": 20, "ip": 30, "review_label": "MSCI-2022-02", "announce_ts_local": "2022-02-10"}], panel)
    with pytest.raises(et.HoldoutError):
        fr.guard_dev_events(bad)
    with pytest.raises(et.HoldoutError):
        fr.run_window(bad, _env(panel), "dev")
    assert len(fr.select_window(pd.concat([ok, bad], ignore_index=True), "dev")) == 1   # 2022 row never selected
    freeze = bad.assign(review_type="freeze")
    fr.guard_dev_events(freeze)                                                          # freeze reviews are not holdout
    dates_2022 = pd.DatetimeIndex(pd.bdate_range("2022-01-03", periods=N_SESS))
    prep = pd.DataFrame({"ip": [30]})
    with pytest.raises(et.HoldoutError):
        fr.guard_dev_sessions(prep, dates_2022)
    # --holdout refuses without the authorization file and computes nothing
    flag = tmp_path / "HOLDOUT_AUTHORIZED"
    monkeypatch.setattr(fr, "HOLDOUT_FLAG", flag)
    monkeypatch.setattr(fr, "load_env", lambda: pytest.fail("holdout must not load data without authorization"))
    assert not fr.holdout_authorized(flag)
    assert fr.main(["--holdout"]) == 2


def test_holm_keeps_no_data_and_insufficient_cells_in_the_denominator():
    cells = [(c, k) for c in fr.CELLS for k in fr.HOLDS]
    assert len(cells) == 15
    p = {key: 1.0 for key in cells}
    p[("MSCI_ST", 5)] = 0.004
    adj = fr.holm_adjust(p)
    assert adj[("MSCI_ST", 5)] == pytest.approx(0.06)           # 15 * 0.004, not 12 * 0.004 = 0.048
    assert fr.cell_p({"status": "no_data"}) == 1.0
    assert fr.cell_p({"status": "insufficient_n", "metrics": {"t": 9.0}}) == 1.0
    assert fr.cell_p({"status": "tested", "metrics": {"t": 3.0}}) < 0.01
    assert fr.cell_p({"status": "tested", "metrics": {"t": float("nan")}}) == 1.0


def test_sample_floor_needs_30_events_and_12_reviews():
    assert fr.sample_status(30, 12) == "ok"
    assert fr.sample_status(29, 12) == "insufficient_n"
    assert fr.sample_status(30, 11) == "insufficient_n"
    assert fr.sample_status(0, 0) == "insufficient_n"


def test_run_window_has_15_cells_idx_no_data_and_small_cells_insufficient():
    panel = _panel()
    env = _env(panel)
    ev = _ev([{"ticker": f"S{i:02d}", "ia": 20, "ip": 30, "review_label": "MSCI-2019-02"} for i in range(4)], panel)
    out = fr.run_window(ev, env, "dev")
    assert sum(len(v) for v in out["cells"].values()) == 15 == out["n_cells"]
    assert all(out["cells"]["IDX"][k]["status"] == "no_data" for k in fr.HOLDS)
    assert all(out["cells"]["FTSE"][k]["status"] == "no_data" for k in fr.HOLDS)    # no FTSE rows in this fixture
    for k in fr.HOLDS:
        r = out["cells"]["MSCI_ST"][k]
        assert r["status"] == "insufficient_n" and r["verdict"] == "insufficient_n" and r["p_holm"] == 1.0
    assert out["decision"]["outcome"] == "no_pass"


def test_verdict_requires_every_gate_and_pooled_only_pass_is_investigate():
    base_g = {"G2": True, "G3a_current": True, "G3b_candidate": True, "G3c_1lot": True, "G3": True,
              "G4": True, "G6": True, "G7": True}
    assert fr.verdict({"status": "tested", "gates": base_g}) == ("PASS_DEV", [])
    v, failed = fr.verdict({"status": "tested", "gates": {**base_g, "G7": False, "G3c_1lot": False, "G3": False}})
    assert v == "FAIL" and failed == ["G3c_1lot", "G7"]
    assert fr.verdict({"status": "no_data"}) == ("no_data", [])

    def cells(passing):
        return {c: {k: {"verdict": "PASS_DEV" if (c, k) in passing else "FAIL"} for k in fr.HOLDS} for c in fr.CELLS}
    assert fr.decision(cells({("pooled", 20)}))["outcome"] == "investigate"
    assert fr.decision(cells({("FTSE", 10), ("pooled", 10)}))["outcome"] == "candidate"
    assert fr.decision(cells(set()))["outcome"] == "no_pass"


def test_ihsg_is_an_index_symbol_not_a_panel_stock():
    assert "IHSG" in INDEX_SYMBOLS
    panel = _panel()
    assert "IHSG" not in panel.symbols
