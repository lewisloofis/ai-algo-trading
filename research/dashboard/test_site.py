"""
End-to-end: build the whole site from a synthetic world and check the guarantees a reader relies on.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from dashboard import config
from dashboard.site import SiteOptions, build_site
from dashboard.strategy_map import UnmappedStrategyError

NOW = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
NS = 1_791_990_000_000_000_000
ACCOUNT = "007777L"

REPORT = """# Strategy Portfolio

**Generated:** 2026-10-14T13:06:33Z
**Capital model:** `owned_cash` — no financing

## Surviving Strategies

| Symbol | Strategy | Hold (days) | Shape | Hit Rate | Net Edge (bps) | Eff-N | Best Entry Day | Friction Hurdle (bps) | Impact (bps) | Null Edge (bps) | Edge over Null (bps) | OOS Edge (bps) | OOS Eff-N | OOS Persistent | Decay Metric (Sharpe) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAAA | daily_momentum | 2 | single_name_time_series | 0.5 | 10.0 | 60 | Mon | 50.0 | 1.0 | 5.0 | 20.0 | 30.0 | 30 | YES | 0.1 |

## Coverage Table (All Cells)

| Symbol | Strategy | Hold | Shape | Status | Reason / Unblock |
|---|---|---|---|---|---|
| BBBB | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-20.0 bps ≤ 0 (gross − hurdle, where hurdle=50.0 bps) |
| BBBB | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.2 |
| (pooled) | gap_event | 5 | event_driven | tested | impact_not_priced (excluded from PASS) |
| BBBB | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |

## Methodology Notes
"""


def _bars(path, name, n=700, seed=1, last="2026-10-13"):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    dates = pd.bdate_range(end=last, periods=n).strftime("%Y-%m-%d")
    pd.DataFrame({"date": dates, "open": close, "close": close,
                  "volume": rng.integers(1000, 90000, n).astype(float)}).to_parquet(path / f"{name}.parquet")


@pytest.fixture
def world(tmp_path, make_store):
    runs = tmp_path / "runs"
    runs.mkdir()
    daily = tmp_path / "daily"
    daily.mkdir()
    research = tmp_path / "research"
    (research / "out_d1" / "next_close").mkdir(parents=True)
    (research / "out_d1" / "next_close" / "strategy_portfolio.md").write_text(REPORT)
    for i, sym in enumerate(("AAAA", "BBBB", "GOTO")):
        _bars(daily, sym, seed=i)

    def store(name, **kw):
        src = make_store(name=name, **kw)
        os.replace(src, runs / name)

    store(
        "live.db",
        runs=[{"run_id": "r-live", "started_ns": NS, "ended_ns": NS + 1, "sink_kind": "live",
               "strategy_id": "roundtrip-goto", "source": "live"}],
        intents=[{"seq": 1, "recv_ns": NS, "strategy_id": "roundtrip-goto", "symbol": "GOTO", "side": "buy",
                  "price_idr": 28, "lots": 1, "order_id": "b", "sink_kind": "live", "run_id": "r-live"}],
        fills=[{"seq": 1, "order_id": "b", "fill_price_idr": 28, "fill_lots": 1, "fee_idr": 4,
                "sink_kind": "live", "run_id": "r-live"}],
    )
    store(
        "shadow.db",
        runs=[{"run_id": "r-paper", "started_ns": NS - 10**12, "ended_ns": NS, "sink_kind": "paper",
               "strategy_id": "manual-first-order-shadow", "source": "live"}],
        intents=[{"seq": 1, "recv_ns": NS - 10**12, "strategy_id": "manual-first-order-shadow", "symbol": "BBCA",
                  "side": "buy", "price_idr": 5925, "lots": 1, "order_id": "p", "sink_kind": "paper",
                  "run_id": "r-paper"}],
        fills=[{"seq": 1, "order_id": "p", "fill_price_idr": 5925, "fill_lots": 1, "fee_idr": 889,
                "net_pnl_idr": 123456, "sink_kind": "paper", "run_id": "r-paper"}],
    )
    ann = tmp_path / "annotations.json"
    ann.write_text(json.dumps({
        "_current_decision": {"headline": "No strategy is live-trading.", "reason": f"test; sleeve {ACCOUNT}.",
                              "as_of": "2026-10-14", "source": "test"},
        "_timeline": [{"date": "2026-10-14", "note": f"something about {ACCOUNT}", "source": "t"}],
    }))
    out = tmp_path / "site"
    opt = SiteOptions(runs_dir=str(runs), daily_dir=str(daily), research_dir=str(research), out_dir=str(out),
                      annotations_path=str(ann), healthcheck="skip", now=NOW)
    return opt, runs, out


def text(out, name):
    return (out / name).read_text(encoding="utf-8")


def test_every_page_is_written_with_the_shared_nav(world):
    opt, _runs, out = world
    written = build_site(opt)
    names = {os.path.basename(p) for p in written}
    assert {"index.html", "trades.html", "strategies.html", "research.html", "glossary.html"} <= names
    assert any(n.startswith("strategy-aaaa") for n in names)  # a detail page for the survivor
    for n in names:
        page = text(out, n)
        # No external scripts, ever; the only scripts are the inline chart-hover one and, on the
        # Overview, the live "Online now" refresher.
        assert 'class="top"' in page and "<script src" not in page
        assert page.count("<script>") == (1 if "data-hv=" in page else 0) + (1 if n == "index.html" else 0)


def test_the_account_number_is_never_rendered(world):
    opt, _runs, out = world
    build_site(opt)
    for p in out.iterdir():
        assert "007777" not in p.read_text(encoding="utf-8"), p.name


def test_overview_leads_with_the_decision_and_never_says_bare_unknown(world):
    opt, _runs, out = world
    build_site(opt)
    page = text(out, "index.html")
    assert page.index("No strategy is live-trading.") < page.index("Is the machinery working?")
    health_part = page[page.index("Is the machinery working?"): page.index("Headline numbers")]
    assert "unknown" not in health_part.lower()
    # collector + S3 are skipped here, and no broker reconciliation has been run.
    assert health_part.count("Not checked: ") == 3 and "Run: " in health_part
    assert "Positions match the broker" in health_part
    assert "make -C " in health_part and " recon" in health_part


def _write_recon(runs, status="match", positions=None):
    (runs / "recon").mkdir(exist_ok=True)
    (runs / "recon" / "2026-10-14.json").write_text(json.dumps({
        "version": 1, "as_of": "2026-10-14T21:00:00+07:00", "date": "2026-10-14", "account_label": "sleeve",
        "status": status,
        "positions": positions if positions is not None else [
            {"symbol": "GOTO", "expected_lots": 1, "broker_lots": 1, "status": "match", "explanation": "ok",
             "breakdown": {}}],
        "trade_check": {"checked": True, "matched": 1, "broker_only": 0, "store_only": 0, "fee_gaps_flagged": 0},
        "sources": {}, "errors": []}))


def test_overview_shows_a_green_row_when_positions_match_the_broker(world):
    opt, runs, out = world
    _write_recon(runs)
    build_site(opt)
    page = text(out, "index.html")
    row = page[page.index("Positions match the broker"):]
    row = row[: row.index("</tr>")]
    assert "Yes: the bot&#x27;s records agree" in row or "agree with the broker" in row
    assert "OK" in row and "Not checked" not in row


def test_overview_shows_a_red_row_with_per_symbol_differences(world):
    opt, runs, out = world
    _write_recon(runs, "mismatch", [
        {"symbol": "GOTO", "expected_lots": 2, "broker_lots": 1, "status": "mismatch",
         "explanation": "broker holds 1 fewer lot than the records explain", "breakdown": {}}])
    build_site(opt)
    page = text(out, "index.html")
    row = page[page.index("Positions match the broker"):]
    row = row[: row.index("</tr>")]
    assert "Problem" in row and "GOTO: expected 2 lots, broker 1 lot" in row


def test_a_broker_imported_sale_is_counted_once_in_the_ledger(world):
    opt, runs, out = world
    # The bot bought GOTO @28 (live.db). The broker's history shows a sale made by hand in the app.
    (runs / "broker_trades.json").write_text(json.dumps({"trades": [{
        "broker_id": "900000002", "source": "broker-import", "date": "2026-10-14", "time_wib": "23:30:00",
        "symbol": "GOTO", "side": "sell", "lots": 1, "price_idr": 26, "fee_idr": 6.5}]}))
    # The owner also typed the same sale into manual_trades.json: the import must supersede it.
    (runs / "manual_trades.json").write_text(json.dumps({"trades": [{
        "date": "2026-10-14", "time_wib": None, "symbol": "GOTO", "side": "sell", "lots": 1, "price_idr": 26,
        "fee_idr": 6.5, "note": "typed by hand"}]}))
    build_site(opt)  # a double count would raise NegativePositionError here
    trades = text(out, "trades.html")
    assert "900000002" in trades and "typed by hand" not in trades
    assert "Rp 6.50" in trades  # fractional fee shown exactly


def test_fractional_store_fees_are_shown_exactly(world, make_store):
    opt, runs, out = world
    import sqlite3
    con = sqlite3.connect(runs / "live.db")
    con.execute("UPDATE fill SET fee_idr = 4.2")
    con.commit()
    con.close()
    build_site(opt)
    assert "Rp 4.20" in text(out, "trades.html")


def test_live_and_paper_are_never_mixed_in_money_totals(world):
    opt, _runs, out = world
    build_site(opt)
    index = text(out, "index.html")
    assert "123,456" not in index and "Rp 889" not in index  # paper numbers
    assert "Rp 4" in index  # the live fee
    trades = text(out, "trades.html")
    live_part = trades[trades.index("Real-money orders"): trades.index("Practice (paper) orders")]
    assert "BBCA" not in live_part and "GOTO" in live_part
    paper_part = trades[trades.index("Practice (paper) orders"): trades.index("Price charts")]
    assert "BBCA" in paper_part and "GOTO" not in paper_part


def test_excluded_store_is_not_read(world):
    opt, runs, out = world
    # A store with a strategy id nobody mapped would fail the build if it were read.
    from dashboard.conftest import go_schema_sql  # noqa: F401  (schema helper lives in conftest)
    import sqlite3

    bad = runs / "2026-10-14-limitprobe.db"
    conn = sqlite3.connect(bad)
    conn.executescript(go_schema_sql())
    conn.execute("INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id,"
                 " sink_kind, run_id) VALUES (1, 1, 'mystery-algo', 'ZZZZ', 'buy', 1, 1, 'z', 'live', 'x')")
    conn.commit()
    conn.close()
    build_site(opt)  # must not raise
    assert "ZZZZ" not in text(out, "trades.html")
    assert "2026-10-14-limitprobe.db" in text(out, "index.html")  # footer says it was skipped


def test_an_unmapped_strategy_id_fails_the_build(world, make_store):
    opt, runs, _out = world
    os.replace(
        make_store(name="x.db", intents=[{"seq": 1, "strategy_id": "mystery-algo", "order_id": "m"}]),
        runs / "x.db",
    )
    with pytest.raises(UnmappedStrategyError, match="mystery-algo"):
        build_site(opt)


def test_calibration_rows_do_not_reach_any_page(world, make_store):
    opt, runs, out = world
    os.replace(
        make_store(
            name="cal.db",
            intents=[{"seq": 1, "strategy_id": "calibration", "symbol": "CALX", "order_id": "c", "sink_kind": "live"}],
            fills=[{"seq": 1, "order_id": "c", "net_pnl_idr": 777777, "sink_kind": "live"}],
        ),
        runs / "cal.db",
    )
    build_site(opt)
    assert "CALX" not in text(out, "trades.html") and "777,777" not in text(out, "index.html")


def test_strategies_page_names_the_run_and_the_corrected_entry(world):
    opt, _runs, out = world
    build_site(opt)
    page = text(out, "strategies.html")
    assert "2026-10-14T13:06:33Z" in page and "Next-session entry" in page
    assert "Backtest chart pages" in page and "strategy-aaaa" in page
    for heading in ("Where each strategy stands", "Strategy families we consider", "Stocks in the screen",
                    "Promising strategy + stock combinations", "Recent runs"):
        assert heading in page
    assert "The three cases you may have heard about" not in page


def test_internal_links_and_glossary_anchors_resolve(world):
    opt, _runs, out = world
    build_site(opt)
    files = {p.name for p in out.iterdir()}
    anchors = set(re.findall(r'id="([^"]+)"', text(out, "glossary.html")))
    for p in out.iterdir():
        for href in re.findall(r'href="([^"#]+)(?:#([^"]*))?"', p.read_text(encoding="utf-8")):
            target, frag = href
            assert target in files, f"{p.name} links to missing {target}"
            if target == "glossary.html" and frag:
                assert frag in anchors, f"{p.name} links to missing glossary anchor {frag}"


def test_stale_pages_are_removed_on_rebuild(world):
    opt, _runs, out = world
    build_site(opt)
    (out / "strategy-old.html").write_text("stale")
    build_site(opt)
    assert not (out / "strategy-old.html").exists()


def test_without_backtest_results_the_site_still_builds_and_says_so(world, tmp_path):
    opt, _runs, out = world
    empty = tmp_path / "empty_research"
    empty.mkdir()
    opt2 = SiteOptions(**{**opt.__dict__, "research_dir": str(empty)})
    build_site(opt2)
    assert "No backtest results were found" in text(out, "strategies.html")


def test_the_default_output_dir_is_gitignored():
    import subprocess

    out = os.path.relpath(config.DEFAULT_OUT_DIR, config.REPO_ROOT)
    proc = subprocess.run(["git", "check-ignore", "-q", os.path.join(out, "index.html")],
                          cwd=config.REPO_ROOT)
    assert proc.returncode == 0, f"{out} must be gitignored"


def test_the_checked_in_annotations_render_without_any_account_shaped_number(world):
    """annotations.json carries a real sleeve id in one note; it must be scrubbed on every page."""
    from dashboard.annotations import DEFAULT_PATH

    opt, _runs, out = world
    opt2 = SiteOptions(**{**opt.__dict__, "annotations_path": DEFAULT_PATH})
    build_site(opt2)
    shaped = re.compile(r"(?<!\w)(?:0\d{5,6}[A-Z]?|\d{6}[A-Z])(?!\w)")
    for p in out.iterdir():
        assert not shaped.search(p.read_text(encoding="utf-8")), p.name
    assert "[account]" in text(out, "research.html")


# --- manual (app) trades ----------------------------------------------------------------------

def _manual_file(runs, *trades):
    base = {"time_wib": None, "fee_idr": 0, "note": "by hand"}
    (runs / "manual_trades.json").write_text(json.dumps({"trades": [{**base, **t} for t in trades]}))


def _goto_world(world, make_store):
    """The 10-13 live buy (27, fee 0) plus the world's 10-14 buy (28): the real GOTO situation."""
    opt, runs, out = world
    os.replace(
        make_store(
            name="live-1013.db",
            runs=[{"run_id": "r-1013", "started_ns": 1_791_875_520_000_000_000, "ended_ns": 1_791_875_530_000_000_000,
                   "sink_kind": "live", "strategy_id": "manual-first-order-fill", "source": "live"}],
            intents=[{"seq": 1, "recv_ns": 1_791_875_520_080_349_000, "strategy_id": "manual-first-order-fill",
                      "symbol": "GOTO", "side": "buy", "price_idr": 27, "lots": 1,
                      "order_id": "rebalance-GOTO-2026-10-13", "sink_kind": "live", "run_id": "r-1013"}],
            fills=[{"seq": 1, "order_id": "rebalance-GOTO-2026-10-13", "fill_price_idr": 27, "fill_lots": 1,
                    "fee_idr": 0, "sink_kind": "live", "run_id": "r-1013"}],
        ),
        runs / "live-1013.db",
    )
    _manual_file(runs, {"date": "2026-10-13", "symbol": "GOTO", "side": "sell", "lots": 1, "price_idr": 26,
                        "fee_idr": 6.5, "net_amount_idr": 2593.5, "broker_realised_pnl_idr": -110.55,
                        "closes_order_id": "rebalance-GOTO-2026-10-13"})
    return opt, runs, out


def test_goto_manual_sale_end_to_end_on_overview_and_trades(world, make_store):
    opt, _runs, out = _goto_world(world, make_store)
    build_site(opt)
    index = text(out, "index.html")
    assert "1 lot GOTO" in index and "2 lots GOTO" not in index
    assert "bought @ Rp 28 on 2026-10-14" in index
    assert "-Rp 106.50" in index and "-Rp 110.55" in index  # ours, with the broker's beside it
    trades = text(out, "trades.html")
    assert "manual (app)" in trades and "time not recorded" in trades
    live_part = trades[trades.index("Real-money orders"): trades.index("Refused or failed")]
    assert live_part.index("2026-10-14") < live_part.index("2026-10-13")
    assert "Reconciliation" in trades and "-Rp 4.05" in trades and "bot store recorded buy fee 0 (broker 4.05)" in trades
    assert "manual-mark" in trades  # a distinct chart marker
    assert "007777" not in trades and "007777" not in index


def test_manual_trades_never_reach_the_paper_table(world, make_store):
    opt, _runs, out = _goto_world(world, make_store)
    build_site(opt)
    trades = text(out, "trades.html")
    paper_part = trades[trades.index("Practice (paper) orders"): trades.index("Price charts")]
    assert "manual (app)" not in paper_part and "GOTO" not in paper_part


def test_no_manual_file_builds_as_before_with_no_manual_section(world):
    opt, _runs, out = world
    build_site(opt)
    assert "manual (app)" not in text(out, "trades.html")
    assert "Reconciliation notes" not in text(out, "trades.html")


def test_a_double_entered_manual_sale_fails_the_build(world, make_store):
    opt, runs, _out = _goto_world(world, make_store)
    sale = {"date": "2026-10-13", "symbol": "GOTO", "side": "sell", "lots": 1, "price_idr": 26, "fee_idr": 7}
    _manual_file(runs, sale, sale, sale)  # holds 2 lots (10-13 and 10-14 buys); the third sale is negative
    from dashboard.ledger import NegativePositionError

    with pytest.raises(NegativePositionError, match="GOTO"):
        build_site(opt)


def test_a_malformed_manual_file_fails_the_build(world):
    opt, runs, _out = world
    _manual_file(runs, {"date": "2026-10-13", "symbol": "GOTO", "side": "sideways", "lots": 1, "price_idr": 26})
    from dashboard.manual_trades import ManualTradesError

    with pytest.raises(ManualTradesError, match="side"):
        build_site(opt)


def test_glossary_explains_manual_trade_and_reconciliation_note(world):
    opt, _runs, out = world
    build_site(opt)
    g = text(out, "glossary.html")
    assert "Manual trade" in g and "Reconciliation note" in g


def test_overview_has_the_online_now_panel_and_lists_every_fresh_session(world):
    opt, runs, out = world
    for name, strat in (("a.status.json", "wifi_momo_h5"), ("b.status.json", "ptro_momo_h5")):
        (runs / name).write_text(json.dumps({
            "wall_clock_ns": int((NOW.timestamp() - 5) * 1e9), "outcome": "running", "strategy_id": strat,
            "sink_kind": "paper", "source": "live"}))
    (runs / "dead.status.json").write_text(json.dumps({
        "wall_clock_ns": int((NOW.timestamp() - 3600) * 1e9), "outcome": "running", "strategy_id": "orb",
        "sink_kind": "paper", "source": "live"}))
    build_site(opt)
    page = text(out, "index.html")
    assert 'id="live-panel"' in page and "Online now" in page and "(2)" in page
    assert "wifi_momo_h5" in page and "ptro_momo_h5" in page
    assert "Stopped without ending" not in page and "orb" not in page.split('id="live-panel"')[1].split("</section>")[0]
    assert "static snapshot" in page and "Last rebuilt" in page
    assert page.index('id="live-panel"') < page.index("Is the machinery working?")
    assert "innerHTML" not in page
