"""cost_report: modelled vs realised cost per leg and round trip, aggregates, labels, CLI."""

from __future__ import annotations

import json

import pytest

from strategy_fit import capital_model as cm
from strategy_fit import cost_fills as cf
from strategy_fit import cost_model as m
from strategy_fit import cost_report as cr
from strategy_fit.cost_report_fixtures import (write_broker_trades, write_cost_store, write_daily, write_recon)


def _fill(**kw):
    base = dict(source="live", origin="store", strategy_id="s", order_id="o1", symbol="AAAA", side="buy",
                day="2026-02-02", time_ns=1, lots=1, fill_price_idr=1000.0, decision_price_idr=1000.0,
                arrival_mid_idr=None, store_fee_idr=150.0, broker_fee_idr=None, fee_source="store", store="x.db")
    base.update(kw)
    return cf.CostFill(**base)


# ---- model ---------------------------------------------------------------------------------------

def test_modelled_fee_is_the_schedule_per_side():
    assert m.modelled_fee_bps("buy") == pytest.approx(15.0)
    assert m.modelled_fee_bps("sell") == pytest.approx(25.0)


def test_modelled_spread_is_half_a_tick_in_bps():
    # tick(1000) = 5 IDR -> half = 2.5 IDR = 25 bps
    assert m.modelled_spread_bps(1000.0) == pytest.approx(25.0)


def test_impact_uses_only_bars_before_the_fill_day(tmp_path):
    write_daily(tmp_path, "AAAA", n=80, end="2026-01-30")
    bars = m.load_daily(tmp_path, "AAAA")
    leg = m.price_leg(_fill(day="2026-02-02", lots=10), bars)
    assert leg.impact_priced and leg.as_of == "2026-01-30" and leg.modelled_impact_bps >= 0
    # a fill dated inside the series must not see its own day's bar
    inside = m.price_leg(_fill(day="2026-01-20"), bars)
    assert inside.as_of < "2026-01-20"


def test_impact_is_none_not_zero_when_bars_are_missing(tmp_path):
    leg = m.price_leg(_fill(), m.load_daily(tmp_path, "NOPE"))
    assert leg.modelled_impact_bps is None and not leg.impact_priced and "no daily bars" in leg.impact_note


def test_impact_is_none_when_history_is_too_short(tmp_path):
    write_daily(tmp_path, "AAAA", n=5, end="2026-01-30")
    leg = m.price_leg(_fill(), m.load_daily(tmp_path, "AAAA"))
    assert leg.modelled_impact_bps is None and "history" in leg.impact_note.lower()


def test_bigger_order_has_more_modelled_impact(tmp_path):
    write_daily(tmp_path, "AAAA", n=80, value=1e9, end="2026-01-30")
    bars = m.load_daily(tmp_path, "AAAA")
    small = m.price_leg(_fill(lots=1), bars).modelled_impact_bps
    big = m.price_leg(_fill(lots=1000), bars).modelled_impact_bps
    assert big > small > 0


# ---- per-leg realised vs modelled -----------------------------------------------------------------

def test_live_leg_fee_residual_is_realised_minus_modelled_and_slippage_vs_arrival_is_not_invented():
    f = _fill(side="buy", fill_price_idr=1000.0, decision_price_idr=1000.0, store_fee_idr=150.0)
    leg = cr.cost_leg(f, None)
    assert leg.realised_fee_bps == pytest.approx(15.0) and leg.fee_resid_bps == pytest.approx(0.0)
    assert leg.slippage_arrival_bps is None
    assert leg.realised_cost_bps is None and leg.resid_bps is None and leg.basis is None


def test_live_leg_with_arrival_mid_is_comparable():
    f = _fill(side="buy", fill_price_idr=1010.0, arrival_mid_idr=1000.0, store_fee_idr=151.5)
    leg = cr.cost_leg(f, None)
    assert leg.slippage_arrival_bps == pytest.approx(100.0)
    assert leg.basis == "arrival_mid" and leg.realised_cost_bps == pytest.approx(115.0)
    assert leg.resid_bps == pytest.approx(leg.realised_cost_bps - leg.modelled_cost_bps)


def test_slippage_sign_is_a_cost_for_both_sides():
    buy = cr.cost_leg(_fill(side="buy", fill_price_idr=1010.0, decision_price_idr=1000.0), None)
    sell = cr.cost_leg(_fill(side="sell", fill_price_idr=990.0, decision_price_idr=1000.0, store_fee_idr=247.5), None)
    better = cr.cost_leg(_fill(side="sell", fill_price_idr=1010.0, decision_price_idr=1000.0), None)
    assert buy.slippage_decision_bps == pytest.approx(100.0)
    assert sell.slippage_decision_bps == pytest.approx(100.0)
    assert better.slippage_decision_bps == pytest.approx(-100.0)


def test_paper_leg_uses_the_simulator_slippage_and_is_labelled_so():
    f = _fill(source="paper", fill_price_idr=1005.0, decision_price_idr=1000.0)
    leg = cr.cost_leg(f, None)
    assert leg.basis == "simulator" and leg.realised_cost_bps is not None


def test_broker_import_leg_has_fee_but_no_slippage():
    f = _fill(origin="broker_import", decision_price_idr=None, broker_fee_idr=247.5, fee_source="broker",
              side="sell", fill_price_idr=1000.0)
    leg = cr.cost_leg(f, None)
    assert leg.slippage_decision_bps is None and leg.realised_fee_bps == pytest.approx(24.75)


def test_size_buckets():
    assert cr.size_bucket(5_000) == "<100k" and cr.size_bucket(500_000) == "100k-1M"
    assert cr.size_bucket(5_000_000) == "1M-10M" and cr.size_bucket(20_000_000) == "10M-50M"
    assert cr.size_bucket(80_000_000) == ">=50M"


# ---- round trips --------------------------------------------------------------------------------

def _legs(fills, bars=None, model=cm.OWNED_CASH):
    return [cr.cost_leg(f, bars) for f in fills]


def test_round_trip_pairs_flat_to_flat_and_sums_fees():
    buy = _fill(order_id="b", side="buy", day="2026-02-02", time_ns=1, fill_price_idr=1000.0, store_fee_idr=150.0)
    sell = _fill(order_id="s", side="sell", day="2026-02-03", time_ns=2, fill_price_idr=1000.0, store_fee_idr=250.0)
    trips, orphans = cr.build_round_trips(_legs([buy, sell]), capital_model=cm.OWNED_CASH)
    (t,) = trips
    assert orphans == 0 and t.shares == 100 and t.hold_trading_days == 1
    assert t.realised_fee_bps == pytest.approx(40.0)
    assert t.price_move_bps == pytest.approx(0.0)


def test_round_trip_modelled_financing_follows_the_capital_model_and_is_separate():
    buy = _fill(order_id="b", side="buy", day="2026-02-02", time_ns=1)  # Monday
    sell = _fill(order_id="s", side="sell", day="2026-02-03", time_ns=2, store_fee_idr=250.0)
    (lev,), _ = cr.build_round_trips(_legs([buy, sell]), capital_model=cm.LEVERAGED_SLEEVE)
    (own,), _ = cr.build_round_trips(_legs([buy, sell]), capital_model=cm.OWNED_CASH)
    assert lev.modelled_financing_bps == pytest.approx(10.0) and own.modelled_financing_bps == 0.0
    assert lev.modelled_cost_bps == own.modelled_cost_bps  # financing is reported beside, never mixed in
    assert lev.modelled_incl_financing_bps == pytest.approx(lev.modelled_cost_bps + 10.0)
    assert lev.realised_financing_bps is None  # the broker's financing charge is not in any local store


def test_unpaired_sell_is_an_orphan_and_open_buy_is_not_a_trip():
    sell = _fill(order_id="s", side="sell", time_ns=1)
    buy = _fill(order_id="b", side="buy", symbol="BBBB", time_ns=2)
    trips, orphans = cr.build_round_trips(_legs([sell, buy]), capital_model=cm.OWNED_CASH)
    assert trips == [] and orphans == 1


def test_live_and_paper_never_pair_with_each_other():
    buy = _fill(order_id="b", side="buy", source="paper", time_ns=1)
    sell = _fill(order_id="s", side="sell", source="live", time_ns=2)
    trips, _ = cr.build_round_trips(_legs([buy, sell]), capital_model=cm.OWNED_CASH)
    assert trips == []


def test_round_trip_cost_is_comparable_only_when_every_leg_is():
    buy = _fill(order_id="b", side="buy", time_ns=1, arrival_mid_idr=1000.0)
    sell = _fill(order_id="s", side="sell", day="2026-02-03", time_ns=2, store_fee_idr=250.0)
    (t,), _ = cr.build_round_trips(_legs([buy, sell]), capital_model=cm.OWNED_CASH)
    assert t.realised_cost_bps is None and t.resid_bps is None


# ---- statistics -----------------------------------------------------------------------------------

def test_summary_reports_n_mean_median_and_a_t_interval():
    s = cr.summarise([1.0, 2.0, 3.0, 4.0, 5.0], min_n=3)
    assert s["n"] == 5 and s["mean"] == 3.0 and s["median"] == 3.0
    assert s["ci95"][0] < 3.0 < s["ci95"][1] and s["enough"] is True


def test_small_n_is_flagged_and_single_point_has_no_interval():
    s = cr.summarise([2.0], min_n=30)
    assert s["n"] == 1 and s["ci95"] is None and s["enough"] is False and "too small" in s["note"]


def test_none_values_are_dropped_and_empty_is_no_data():
    assert cr.summarise([None, 1.0], min_n=30)["n"] == 1
    e = cr.summarise([None], min_n=30)
    assert e["n"] == 0 and e["mean"] is None and e["note"] == "no data"


# ---- end to end -----------------------------------------------------------------------------------

def _world(tmp_path):
    runs, data = tmp_path / "runs", tmp_path / "data"
    write_daily(data, "AAAA", n=80, end="2026-01-30")
    write_cost_store(runs / "2026-02-02-live.db", [
        dict(day="2026-02-02", symbol="AAAA", side="buy", decision=1000, fill=1000, lots=2, fee=0.0,
             sink="live", order_id="live-b"),
        dict(day="2026-02-03", symbol="AAAA", side="sell", decision=1000, fill=995, lots=2, fee=500.0,
             sink="live", order_id="live-s"),
        dict(day="2026-02-02", symbol="AAAA", side="buy", decision=1000, fill=1005, lots=1, fee=150.0,
             sink="paper", order_id="p-b"),
        dict(day="2026-02-03", symbol="AAAA", side="sell", decision=1000, fill=1000, lots=1, fee=250.0,
             sink="paper", order_id="p-s"),
    ])
    write_recon(runs, "2026-02-04", [{"store_order_id": "live-b", "broker_id": "B1", "broker_fee_idr": 300.0,
                                      "store_fee_idr": 0.0}], [])
    write_broker_trades(runs, [])
    return runs, data


def test_build_report_labels_paper_as_simulator_and_live_as_too_small(tmp_path):
    runs, data = _world(tmp_path)
    doc = cr.build_report(runs, data, capital_model=cm.OWNED_CASH)
    assert doc["coverage"]["live"]["fills"] == 2 and doc["coverage"]["paper"]["fills"] == 2
    assert "simulator" in doc["labels"]["paper"].lower() and "real" in doc["labels"]["live"].lower()
    live_fee = doc["aggregates"]["live"]["overall"]["fee_resid_bps"]
    assert live_fee["n"] == 2 and live_fee["enough"] is False
    assert any("live" in f.lower() and "too small" in f.lower() for f in doc["flags"])
    assert doc["round_trips"] and {t["source"] for t in doc["round_trips"]} == {"live", "paper"}
    live_trip = next(t for t in doc["round_trips"] if t["source"] == "live")
    assert live_trip["realised_fee_bps"] == pytest.approx(40.0, abs=0.01)  # (300 + 500) / 200,000 buy notional


def test_live_slippage_vs_arrival_is_reported_as_unmeasured(tmp_path):
    runs, data = _world(tmp_path)
    doc = cr.build_report(runs, data, capital_model=cm.OWNED_CASH)
    assert doc["aggregates"]["live"]["overall"]["slippage_arrival_bps"]["n"] == 0
    assert any("arrival" in f.lower() for f in doc["flags"])


def test_markdown_has_both_sections_and_the_caveats(tmp_path):
    runs, data = _world(tmp_path)
    md = cr.render_markdown(cr.build_report(runs, data, capital_model=cm.OWNED_CASH))
    assert "# Realised cost vs model" in md
    assert "Live (real fills)" in md and "Paper (simulator check, not market evidence)" in md
    assert "too small" in md.lower()


def test_write_report_and_cli_write_json_and_markdown(tmp_path, capsys):
    runs, data = _world(tmp_path)
    out = tmp_path / "out"
    rc = cr.main(["--runs-dir", str(runs), "--data-dir", str(data), "--out-dir", str(out)])
    assert rc == 0
    doc = json.loads((out / "cost_report.json").read_text())
    assert doc["version"] == 1 and (out / "cost_report.md").read_text().startswith("# Realised cost vs model")


def test_empty_runs_dir_gives_an_empty_but_valid_report(tmp_path):
    doc = cr.build_report(tmp_path / "runs", tmp_path / "data", capital_model=cm.OWNED_CASH)
    assert doc["coverage"]["live"]["fills"] == 0 and doc["round_trips"] == []
    assert cr.render_markdown(doc)
