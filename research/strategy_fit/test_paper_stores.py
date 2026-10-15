"""paper_stores: naming (paper and live never share a glob), read-only fills, net lots, round trips."""

from __future__ import annotations

import fnmatch
import os

from strategy_fit import paper_stores as ps
from strategy_fit.paper_store_fixtures import write_store


def test_paper_key_includes_the_sink_and_never_matches_a_live_glob(tmp_path):
    paper = ps.store_glob(tmp_path, "wifi_momo_h5", "paper")
    live = ps.store_glob(tmp_path, "wifi_momo_h5", "live")
    paper_store = str(tmp_path / "2026-10-16-wifi_momo_h5-paper-a1.db")
    live_store = str(tmp_path / "2026-10-16-wifi_momo_h5-a1.db")
    assert fnmatch.fnmatch(paper_store, paper) and not fnmatch.fnmatch(paper_store, live)
    assert fnmatch.fnmatch(live_store, live) and not fnmatch.fnmatch(live_store, paper)


def test_glob_matches_only_attempt_stores_of_this_strategy(tmp_path):
    g = ps.store_glob(tmp_path, "momo", "paper")
    assert not fnmatch.fnmatch(str(tmp_path / "2026-10-16-wifi_momo-paper-a1.db"), g)
    assert not fnmatch.fnmatch(str(tmp_path / "2026-10-16-momo-paper-a1.txt"), g)
    assert fnmatch.fnmatch(str(tmp_path / "2026-10-16-momo-paper-a12.db"), g)


def test_list_stores_spans_all_days_and_only_this_strategy(tmp_path):
    for n in ("2026-10-16-wifi_momo_h5-paper-a1.db", "2026-10-19-wifi_momo_h5-paper-a1.db",
              "2026-10-19-ptro_momo_h5-paper-a1.db", "2026-10-19-wifi_momo_h5-a1.db"):
        write_store(tmp_path / n, [])
    got = [p.name for p in ps.list_stores(tmp_path, "wifi_momo_h5")]
    assert got == ["2026-10-16-wifi_momo_h5-paper-a1.db", "2026-10-19-wifi_momo_h5-paper-a1.db"]


def test_read_fills_net_lots_and_round_trips(tmp_path):
    a = write_store(tmp_path / "2026-10-16-s-paper-a1.db", [("2026-10-16", "WIFI", "buy", 1000, 1)])
    b = write_store(tmp_path / "2026-10-23-s-paper-a1.db", [("2026-10-23", "WIFI", "sell", 1100, 1),
                                                          ("2026-10-26", "WIFI", "buy", 1200, 2)])
    fills = ps.read_fills([b, a])
    assert [f.side for f in fills] == ["buy", "sell", "buy"]
    assert ps.net_lots(fills) == {"WIFI": 2}
    trips = ps.round_trips(fills)
    assert len(trips) == 1                       # the second buy is an open position, not a trip
    t = trips[0]
    assert (t.symbol, t.shares, t.buy_notional_idr, t.sell_notional_idr) == ("WIFI", 100, 100_000, 110_000)
    assert abs(t.fees_idr - (150.0 + 275.0)) < 1e-9


def test_live_sink_rows_are_ignored(tmp_path):
    p = write_store(tmp_path / "2026-10-16-s-paper-a1.db", [("2026-10-16", "WIFI", "buy", 1000, 1)], sink="live")
    assert ps.read_fills([p]) == []


def test_reading_never_modifies_the_store(tmp_path):
    p = write_store(tmp_path / "2026-10-16-s-paper-a1.db", [("2026-10-16", "WIFI", "buy", 1000, 1)])
    before = (p.read_bytes(), os.stat(p).st_mtime_ns)
    ps.read_fills([p])
    assert (p.read_bytes(), os.stat(p).st_mtime_ns) == before
