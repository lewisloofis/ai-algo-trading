"""paper_scorecard: per-trip net (fee + one tick), always-long null, drop-2-best, verdict."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from lib.market_calendar import CALENDAR
from strategy_fit import emitter_specs as es
from strategy_fit import paper_scorecard as sc
from strategy_fit import paper_stores as ps
from strategy_fit.paper_store_fixtures import write_store

SPEC = replace(es.SPECS["wifi_momo_h5"], strategy_id="sc_x", symbols=("AAAA",))


def _days(start: date, n: int) -> list[date]:
    out = [start]
    while len(out) < n:
        out.append(CALENDAR.next_trading_day(out[-1]))
    return out


def test_one_trip_net_is_gross_minus_schedule_fee_minus_one_tick():
    t = ps.RoundTrip("AAAA", 0, 1, 100, 100_000.0, 110_000.0, 425.0, 1000.0)
    trip = sc.score_trip(t)
    # gross 10,000; fee 0.15% x 100,000 + 0.25% x 110,000 = 425; tick(1000) = 5 IDR x 100 sh = 500
    assert trip.gross_idr == 10_000 and trip.fee_idr == pytest.approx(425.0) and trip.tick_idr == 500
    assert trip.net_idr == pytest.approx(9_075.0) and trip.net_bps == pytest.approx(907.5)


def test_losing_trip_is_negative_after_costs():
    t = ps.RoundTrip("AAAA", 0, 1, 100, 100_000.0, 100_000.0, 0.0, 1000.0)
    assert sc.score_trip(t).net_idr == pytest.approx(-(150 + 250 + 500))


def test_mean_drop_best():
    assert sc.mean_drop_best([10, 20, 30, 100, 5]) == pytest.approx((5 + 10 + 20) / 3)
    assert sc.mean_drop_best([1, 2]) is None


def test_null_is_the_always_long_mean_net_of_the_same_cost():
    # closes rise 1% a day from 1000: 5-day gross is the same every entry
    closes = [1000 * 1.01 ** i for i in range(12)]
    bars = pd.DataFrame({"date": [f"2026-01-{i + 1:02d}" for i in range(12)], "close": closes})
    got = sc.null_net_bps(bars, "2026-01-01", "2026-01-12", hold=5)
    expect = [((closes[i + 5] / closes[i] - 1) * 1e4) - 40 - sc.tick_size(closes[i]) / closes[i] * 1e4 for i in range(7)]
    assert got == pytest.approx(sum(expect) / len(expect))
    assert sc.null_net_bps(bars, "2027-01-01", "2027-02-01", hold=5) is None


def _world(tmp_path, wins: list[bool], n_bars=400):
    """A store with len(wins) completed trips of AAAA (one lot, entry 1000) + flat-ish bars."""
    days = _days(date(2026, 1, 5), 400)
    runs = tmp_path / "runs"
    for i, win in enumerate(wins):
        e, x = days[i * 6], days[i * 6 + 5]
        px_out = 1100 if win else 900
        write_store(runs / f"{e}-sc_x-paper-a1.db", [(e.isoformat(), "AAAA", "buy", 1000, 1),
                                                    (x.isoformat(), "AAAA", "sell", px_out, 1)])
    data = tmp_path / "daily"
    data.mkdir()
    pd.DataFrame({"date": [d.isoformat() for d in days[:n_bars]], "close": [1000.0] * n_bars,
                  "volume": [1_000_000] * n_bars}).to_parquet(data / "AAAA.parquet")
    return runs, data


def test_incomplete_below_fifteen_trips(tmp_path):
    runs, data = _world(tmp_path, [True] * 5)
    card = sc.build_scorecard(SPEC, runs, data)
    assert card.n_trips == 5 and card.verdict.startswith("INCOMPLETE")
    text = sc.format_scorecard(card)
    assert "NOT PROOF OF EDGE" in text.upper() and "VERDICT: INCOMPLETE" in text


def test_passes_when_every_check_clears(tmp_path):
    runs, data = _world(tmp_path, [True] * 16)
    card = sc.build_scorecard(SPEC, runs, data)
    assert card.n_trips == 16
    assert card.mean_net_bps > 0 and card.edge_over_null_bps > 0 and card.mean_net_drop_best_bps > 0
    assert card.verdict.startswith("PASSES") and card.reasons == ()


def test_stops_when_the_mean_is_carried_by_two_trips(tmp_path):
    # 2 big winners, 14 small losers: mean > 0 but drops negative after the 2 best
    days = _days(date(2026, 1, 5), 400)
    runs = tmp_path / "runs"
    for i in range(16):
        e, x = days[i * 6], days[i * 6 + 5]
        out = 3000 if i < 2 else 995
        write_store(runs / f"{e}-sc_x-paper-a1.db", [(e.isoformat(), "AAAA", "buy", 1000, 1),
                                                    (x.isoformat(), "AAAA", "sell", out, 1)])
    data = tmp_path / "daily"
    data.mkdir()
    pd.DataFrame({"date": [d.isoformat() for d in days], "close": [1000.0] * 400,
                  "volume": [1] * 400}).to_parquet(data / "AAAA.parquet")
    card = sc.build_scorecard(SPEC, runs, data)
    assert card.mean_net_bps > 0 and card.mean_net_drop_best_bps < 0
    assert card.verdict.startswith("STOP") and any("dropping the 2 best" in r for r in card.reasons)


def test_stops_when_it_does_not_beat_the_null(tmp_path):
    runs, data = _world(tmp_path, [True] * 16)
    days = _days(date(2026, 1, 5), 400)
    # a market that rises faster than the trips earn: null (net) beats the strategy
    pd.DataFrame({"date": [d.isoformat() for d in days],
                  "close": [1000.0 * 1.02 ** i for i in range(400)],
                  "volume": [1_000_000] * 400}).to_parquet(data / "AAAA.parquet")
    card = sc.build_scorecard(SPEC, runs, data)
    assert card.edge_over_null_bps < 0 and card.verdict.startswith("STOP")


def test_missing_bars_means_the_null_is_unknown_not_zero(tmp_path):
    runs, data = _world(tmp_path, [True] * 16)
    (data / "AAAA.parquet").unlink()
    card = sc.build_scorecard(SPEC, runs, data)
    assert card.null_net_bps is None and card.edge_over_null_bps is None
    assert card.verdict.startswith("STOP") and any("not computable" in r for r in card.reasons)


def test_open_position_is_reported_and_not_a_trip(tmp_path):
    runs = tmp_path / "runs"
    write_store(runs / "2026-01-05-sc_x-paper-a1.db", [("2026-01-05", "AAAA", "buy", 1000, 1)])
    card = sc.build_scorecard(SPEC, runs, tmp_path / "none")
    assert card.n_trips == 0 and card.open_positions == {"AAAA": 1}
    assert "AAAA 1 lot(s)" in sc.format_scorecard(card)


def test_live_stores_are_never_scored(tmp_path):
    runs = tmp_path / "runs"
    write_store(runs / "2026-01-05-sc_x-a1.db", [("2026-01-05", "AAAA", "buy", 1000, 1),
                                                ("2026-01-12", "AAAA", "sell", 1100, 1)])
    assert sc.build_scorecard(SPEC, runs, tmp_path / "none").n_trips == 0


def test_cli_unknown_strategy_and_runs(tmp_path, capsys):
    assert sc.main(["--strategy", "nope", "--runs-dir", str(tmp_path)]) == 2
    assert sc.main(["--strategy", "wifi_momo_h5", "--runs-dir", str(tmp_path)]) == 0
    assert "INCOMPLETE: 0 of 15" in capsys.readouterr().out
