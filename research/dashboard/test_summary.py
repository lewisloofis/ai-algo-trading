"""summary.json for the portfolio overview: state rule, links, recon hygiene, and the schema."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from dashboard import config, health, summary
from dashboard.ledger import Position

NOW = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)  # 21:00 WIB
VALIDATE = os.path.join(config.REPO_ROOT, "..", "overview", "validate.py")
ACCOUNT_NO = "9876543Z"  # synthetic stand-in for an account number


def spec(sid, sym, family="volume_breakout", hold=20, status="candidate"):
    return NS(strategy_id=sid, symbols=(sym,), family=family, hold=hold, status=status, status_note="")


def sl_row(sym, state, family="volume_breakout", hold=20):
    return {"symbol": sym, "family": family, "hold": hold, "state": state, "gates": {}}


def score_row(sym, family="volume_breakout", hold=20, link=True):
    slug = f"{sym}-{family}-h{hold}".lower().replace("_", "-")
    return NS(cell=NS(symbol=sym, family=family, hold=hold), slug=slug, stats=object(),
              detail_page=f"strategy-{slug}.html" if link else None, verdict=NS(headline="Candidate"))


def write_recon(runs, doc):
    (runs / "recon").mkdir(exist_ok=True)
    (runs / "recon" / "2026-10-14.json").write_text(json.dumps(doc))


def site(tmp_path, specs=(), shortlist_rows=(), rows=(), positions=(), closings=(), health_rows=(), closes=None):
    return NS(
        now=NOW, now_wib=NOW.replace(hour=21), options=NS(runs_dir=str(tmp_path)),
        strategy_inputs=NS(specs={s.strategy_id: s for s in specs},
                           shortlist=strategy_status_shortlist(shortlist_rows)),
        paper_tests=None, online=None, rows=list(rows), health=list(health_rows),
        closes=closes or {}, ledger=NS(positions=tuple(positions), closings=tuple(closings), realised_pnl_idr=1234.6),
    )


def strategy_status_shortlist(rows):
    from dashboard.strategy_status import Shortlist
    return Shortlist("2026-10-14", len(rows), tuple(rows), ())


@pytest.mark.parametrize("raw,expected", [
    ("IN POSITION (exit in 2 session(s))", ("in_position", 2)),
    ("IN POSITION (exit in 1 session(s))", ("exiting", 1)),
    ("IN POSITION (exit in 0 session(s))", ("exiting", 0)),
    ("IN POSITION", ("in_position", None)),
    ("ENTER next session", ("enter", None)),
    ("FLAT", ("flat", None)),
    ("???", (None, None)),
])
def test_parse_state(raw, expected):
    assert summary.parse_state(raw) == expected


def test_state_uses_live_strategies_only_and_most_active_wins(tmp_path):
    d = site(tmp_path,
             specs=[spec("live-a", "AAAA"), spec("paper-b", "BBBB", status="paper"),
                    spec("live-c", "CCCC"), spec("live-c2", "CCCC", family="mean_reversion", hold=5)],
             shortlist_rows=[sl_row("AAAA", "IN POSITION (exit in 1 session(s))"),
                             sl_row("BBBB", "IN POSITION (exit in 9 session(s))"),
                             sl_row("CCCC", "FLAT"),
                             sl_row("CCCC", "ENTER next session", family="mean_reversion", hold=5)])
    t = summary.build_summary(d)["tickers"]
    assert (t["AAAA"]["state"], t["AAAA"]["exit_in_sessions"]) == ("exiting", 1)
    assert "state" not in t["BBBB"] and t["BBBB"]["strategies"][0]["stage"] == "paper"
    assert t["CCCC"]["state"] == "enter"


def test_real_holding_forces_in_position_when_chain_is_flat(tmp_path):
    d = site(tmp_path, specs=[spec("live-a", "AAAA")], shortlist_rows=[sl_row("AAAA", "FLAT")],
             positions=[Position("AAAA", 3, 1000.0, 300000.0)], closes={"AAAA": [("2026-10-13", 1100.0), ("2026-10-14", 1200.0)]})
    doc = summary.build_summary(d)
    assert doc["tickers"]["AAAA"]["state"] == "in_position"
    p = doc["positions"][0]
    assert (p["lots"], p["shares"], p["last_price"], p["market_value"], p["unrealized_pnl"]) == (3, 300, 1200.0, 360000, 60000)


def test_held_ticker_without_strategy_is_listed(tmp_path):
    d = site(tmp_path, positions=[Position("ZZZZ", 1, 500.0, 50000.0)])
    assert summary.build_summary(d)["tickers"]["ZZZZ"]["state"] == "in_position"


def test_links_and_verdict_come_from_the_scorecard(tmp_path):
    d = site(tmp_path, specs=[spec("live-a", "AAAA"), spec("live-b", "BBBB")],
             rows=[score_row("AAAA"), score_row("BBBB", link=False)])
    t = summary.build_summary(d)["tickers"]
    assert t["AAAA"]["strategies"][0]["link"] == "strategy-aaaa-volume-breakout-h20.html"
    assert "link" not in t["BBBB"]["strategies"][0]
    assert t["AAAA"]["last_verdict"] == "Candidate"


def test_pnl_buckets_by_wib_sell_date(tmp_path):
    cl = [NS(sell=NS(day=day), pnl_idr=p) for day, p in
          [("2026-10-14", 1000.0), ("2026-10-02", 500.0), ("2026-03-01", 250.0), ("2025-12-31", 99.0)]]
    pnl = summary.build_summary(site(tmp_path, closings=cl))["pnl"]
    assert pnl == {"day": 1000, "mtd": 1500, "ytd": 1750, "realised_total": 1235}


def test_recon_match_copies_only_status_and_as_of(tmp_path):
    write_recon(tmp_path, {"status": "match", "as_of": "2026-10-14T21:00:00+07:00", "account_label": "sleeve",
                           "account_no": ACCOUNT_NO, "positions": [{"symbol": "AAAA", "expected_lots": 1, "broker_lots": 1,
                                                                    "status": "match", "account": ACCOUNT_NO}],
                           "errors": []})
    doc = summary.build_summary(site(tmp_path))
    assert doc["recon"] == {"status": "match", "as_of": "2026-10-14T21:00:00+07:00"}
    assert ACCOUNT_NO not in json.dumps(doc) and "account" not in json.dumps(doc).lower().replace("accounts", "")


def test_recon_mismatch_alert_has_symbol_and_lots_but_no_report_text(tmp_path):
    write_recon(tmp_path, {"status": "mismatch", "as_of": "2026-10-14T21:00:00+07:00", "account_no": ACCOUNT_NO,
                           "positions": [{"symbol": "AAAA", "expected_lots": 2, "broker_lots": 1, "status": "mismatch",
                                          "explanation": f"acct {ACCOUNT_NO} is short", "account": ACCOUNT_NO}],
                           "errors": [f"login failed for {ACCOUNT_NO}"]})
    doc = summary.build_summary(site(tmp_path))
    text = json.dumps(doc)
    assert ACCOUNT_NO not in text
    assert {"severity": "critical", "ticker": "AAAA",
            "message": "Positions do not match the broker: expected 2 lots, broker 1 lot."} in doc["alerts"]


def test_recon_missing_and_unreadable(tmp_path):
    assert summary.build_summary(site(tmp_path))["recon"] == {"status": "not_checked"}
    write_recon(tmp_path, {"status": "bogus"})
    doc = summary.build_summary(site(tmp_path))
    assert doc["recon"] == {"status": "error"} and doc["alerts"][0]["severity"] == "critical"


def test_health_alerts_skip_green_and_recon_row(tmp_path):
    rows = [health.HealthRow("Collector", health.RED, "x"), health.HealthRow("Prices", health.GREEN, "ok"),
            health.HealthRow("Positions match the broker", health.RED, "has account text")]
    alerts = summary.build_summary(site(tmp_path, health_rows=rows))["alerts"]
    assert [a["message"] for a in alerts] == [f"Collector: {health.RED}"]


def test_output_passes_the_schema(tmp_path):
    write_recon(tmp_path, {"status": "match", "as_of": "2026-10-14T21:00:00+07:00", "positions": []})
    d = site(tmp_path, specs=[spec("live-a", "AAAA"), spec("paper-b", "BBBB", status="paper")],
             shortlist_rows=[sl_row("AAAA", "IN POSITION (exit in 1 session(s))")], rows=[score_row("AAAA")],
             positions=[Position("AAAA", 2, 1000.0, 200000.0)], closes={"AAAA": [("2026-10-14", 1100.0)]},
             closings=[NS(sell=NS(day="2026-10-14"), pnl_idr=5000.0)])
    out = summary.write_summary(d, str(tmp_path))
    pytest.importorskip("jsonschema")  # not in the dashboard venv; validate.py needs it
    r = subprocess.run([sys.executable, VALIDATE, out], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- edge cases -----------------------------------------------------------------------------

def _wib_site(tmp_path, now, **kw):
    d = site(tmp_path, **kw)
    d.now = now
    return d


def test_pnl_day_uses_the_wib_date_across_the_utc_boundary(tmp_path):
    now = datetime(2026, 10, 14, 17, 30, tzinfo=timezone.utc)  # 00:30 WIB on the 15th
    cl = [NS(sell=NS(day="2026-10-15"), pnl_idr=700.0), NS(sell=NS(day="2026-10-14"), pnl_idr=300.0)]
    doc = summary.build_summary(_wib_site(tmp_path, now, closings=cl))
    assert doc["generated_at"] == "2026-10-15T00:30:00+07:00"
    assert (doc["pnl"]["day"], doc["pnl"]["mtd"]) == (700, 1000)


def test_a_naive_now_is_taken_as_utc(tmp_path):
    doc = summary.build_summary(_wib_site(tmp_path, datetime(2026, 10, 14, 14, 0)))
    assert doc["generated_at"] == "2026-10-14T21:00:00+07:00"


def test_equal_states_keep_the_smallest_exit_in(tmp_path):
    d = site(tmp_path, specs=[spec("a", "AAAA"), spec("b", "AAAA", family="mean_reversion", hold=5)],
             shortlist_rows=[sl_row("AAAA", "IN POSITION (exit in 5 session(s))"),
                             sl_row("AAAA", "IN POSITION (exit in 3 session(s))", family="mean_reversion", hold=5)])
    t = summary.build_summary(d)["tickers"]["AAAA"]
    assert (t["state"], t["exit_in_sessions"]) == ("in_position", 3)


def test_a_held_ticker_whose_chain_says_enter_stays_enter(tmp_path):
    d = site(tmp_path, specs=[spec("a", "AAAA")], shortlist_rows=[sl_row("AAAA", "ENTER next session")],
             positions=[Position("AAAA", 1, 1000.0, 100000.0)])
    assert summary.build_summary(d)["tickers"]["AAAA"]["state"] == "enter"


def test_no_close_means_no_market_value_and_no_closings_means_zero_pnl(tmp_path):
    doc = summary.build_summary(site(tmp_path, positions=[Position("AAAA", 2, 1000.0, 200000.0)]))
    p = doc["positions"][0]
    assert (p["last_price"], p["market_value"], p["unrealized_pnl"]) == (None, None, None)
    assert doc["pnl"] == {"day": 0, "mtd": 0, "ytd": 0, "realised_total": 1235}


def test_a_stale_last_close_raises_an_info_alert(tmp_path):
    d = site(tmp_path, positions=[Position("AAAA", 1, 1000.0, 100000.0), Position("BBBB", 1, 1000.0, 100000.0)],
             closes={"AAAA": [("2026-10-01", 1000.0)], "BBBB": [("2026-10-13", 1000.0)]})
    alerts = summary.build_summary(d)["alerts"]
    assert {"severity": "info", "ticker": "AAAA", "message": "Last price for AAAA is 13 days old."} in alerts
    assert not any(a.get("ticker") == "BBBB" for a in alerts)


@pytest.mark.parametrize("positions", [
    ["not a dict"],
    [{"symbol": "AAAA", "status": "mismatch"}],                                         # no lot counts
    [{"symbol": "AAAA", "status": "mismatch", "expected_lots": "x", "broker_lots": 1}],  # not numeric
    [{"status": "mismatch", "expected_lots": 1, "broker_lots": 2}],                      # no symbol
])
def test_a_malformed_recon_report_gives_error_status_and_a_fixed_alert(tmp_path, positions):
    write_recon(tmp_path, {"status": "mismatch", "as_of": "2026-10-14T21:00:00+07:00", "positions": positions})
    doc = summary.build_summary(site(tmp_path))
    assert doc["recon"] == {"status": "error", "as_of": "2026-10-14T21:00:00+07:00"}
    assert doc["alerts"] == [summary.RECON_FORMAT_ALERT]


def test_invalid_as_of_and_symbol_are_not_copied(tmp_path):
    write_recon(tmp_path, {"status": "mismatch", "as_of": f"acct {ACCOUNT_NO}", "positions": [
        {"symbol": f"{ACCOUNT_NO}!!", "expected_lots": 1, "broker_lots": 0, "status": "mismatch"}]})
    doc = summary.build_summary(site(tmp_path))
    assert doc["recon"] == {"status": "mismatch"}
    assert ACCOUNT_NO not in json.dumps(doc)
    assert doc["alerts"] == [{"severity": "critical",
                              "message": "Positions do not match the broker: expected 1 lot, broker 0 lots."}]


def test_write_summary_is_atomic(tmp_path):
    out = summary.write_summary(site(tmp_path), str(tmp_path))
    assert os.path.exists(out) and not os.path.exists(out + ".tmp")
