"""The broker reconciliation on the dashboard: the Overview row, and broker-imported trades."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from dashboard import broker_import, config, health, recon
from dashboard.manual_trades import ManualTrade
from dashboard.trades_data import LIVE, OrderLine

# Shape of a brokerage account number (6-7 digits, optional trailing letter); no real value is kept here.
_ACCOUNT_LIKE = re.compile(r"\b\d{6,7}[A-Z]?\b")

NOW = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)  # 21:00 WIB


def report(status="match", **over):
    base = {
        "version": 1, "as_of": "2026-10-14T21:00:00+07:00", "date": "2026-10-14", "account_label": "sleeve",
        "status": status,
        "positions": [{"symbol": "GOTO", "expected_lots": 1, "broker_lots": 1, "status": "match",
                       "explanation": "expected 1 = +2 from bot, -1 from broker-import; broker holds 1",
                       "breakdown": {"bot_lots": 2, "manual_lots": 0, "broker_import_lots": -1}}],
        "trade_check": {"checked": True, "broker_trades": 3, "matched": 2, "broker_only": 1, "store_only": 0,
                        "newly_imported": 1, "fee_gaps_flagged": 1},
        "sources": {"stores": ["a.db"], "store_fills": 2, "unattributed_fills": 0, "manual_trades": 0, "broker_imports": 1},
        "errors": [],
    }
    base.update(over)
    return base


def write(runs, name, doc):
    d = runs / "recon"
    d.mkdir(exist_ok=True)
    (d / name).write_text(json.dumps(doc) if not isinstance(doc, str) else doc)


def test_no_recon_file_says_not_checked_with_the_exact_command(tmp_path):
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.name == "Positions match the broker"
    assert row.state == health.NOT_CHECKED
    assert row.summary.startswith("Not checked: ")
    assert row.run_hint and row.summary.endswith(row.run_hint)
    # The command is complete: loads the env file, names the binary, and carries no account number.
    assert row.run_hint.startswith("set -a && source .env.local && set +a && ")
    assert "make -C " in row.run_hint and row.run_hint.endswith(" recon")
    assert not _ACCOUNT_LIKE.search(row.run_hint)


def test_a_matching_report_is_green(tmp_path):
    write(tmp_path, "2026-10-14.json", report())
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.GREEN
    assert "match" in row.summary.lower()
    assert "GOTO" in row.summary and "1 lot" in row.summary


def test_a_mismatch_is_red_and_lists_every_difference(tmp_path):
    pos = [
        {"symbol": "GOTO", "expected_lots": 2, "broker_lots": 1, "status": "mismatch",
         "explanation": "broker holds 1 fewer lot than the records explain", "breakdown": {}},
        {"symbol": "BBCA", "expected_lots": 0, "broker_lots": 3, "status": "mismatch",
         "explanation": "broker holds 3 lots with no record", "breakdown": {}},
        {"symbol": "TLKM", "expected_lots": 1, "broker_lots": 1, "status": "match", "explanation": "ok", "breakdown": {}},
    ]
    write(tmp_path, "2026-10-14.json", report("mismatch", positions=pos))
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.RED
    for want in ("GOTO", "expected 2", "broker 1", "BBCA", "expected 0", "broker 3"):
        assert want in row.summary, want
    assert "TLKM" not in row.summary  # matching symbols are not differences


def test_a_phantom_fill_makes_the_row_red_even_when_positions_match(tmp_path):
    write(tmp_path, "2026-10-14.json", report(errors=["phantom fill: store order x has no matching broker trade"]))
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.RED
    assert "phantom" in row.summary


def test_pending_history_fills_make_the_row_amber_not_red(tmp_path):
    tc = {"checked": True, "broker_trades": 3, "matched": 2, "broker_only": 1, "store_only": 0,
          "pending_history": 1, "newly_imported": 0, "fee_gaps_flagged": 0}
    write(tmp_path, "2026-10-14.json", report(trade_check=tc, warnings=["awaiting broker history: store order x"]))
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.AMBER
    assert "next trading day" in row.summary
    assert "1 awaiting broker history" in row.summary


def test_a_phantom_still_beats_pending_history(tmp_path):
    write(tmp_path, "2026-10-14.json", report(errors=["phantom fill: store order y"], warnings=["awaiting broker history: x"]))
    assert recon.recon_row(str(tmp_path), NOW).state == health.RED


def test_the_newest_report_wins(tmp_path):
    write(tmp_path, "2026-10-13.json", report("mismatch"))
    write(tmp_path, "2026-10-14.json", report("match"))
    assert recon.recon_row(str(tmp_path), NOW).state == health.GREEN


def test_an_old_report_is_amber_not_green(tmp_path):
    write(tmp_path, "2026-10-09.json", report(as_of="2026-10-09T21:00:00+07:00", date="2026-10-09"))
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.AMBER
    assert "2026-10-09" in row.summary


@pytest.mark.parametrize("body", ["not json", "[]", json.dumps({"status": "maybe"}), json.dumps({"status": "match"})])
def test_an_unreadable_report_is_red_never_green(tmp_path, body):
    write(tmp_path, "2026-10-14.json", body)
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.RED
    assert "could not be read" in row.summary


def test_history_not_checked_is_said_plainly(tmp_path):
    tc = {"checked": False, "error": "broker history skipped", "broker_trades": 0, "matched": 0, "broker_only": 0,
          "store_only": 0, "newly_imported": 0, "fee_gaps_flagged": 0}
    write(tmp_path, "2026-10-14.json", report(trade_check=tc))
    row = recon.recon_row(str(tmp_path), NOW)
    assert row.state == health.GREEN
    assert "trade-by-trade check was not done" in row.summary


# ---- broker-imported trades ---------------------------------------------------------------


def imp(**kw):
    d = dict(broker_id="900000002", date="2026-10-13", time_wib="14:14:15", symbol="GOTO", side="sell", lots=1,
             price_idr=26.0, fee_idr=6.5)
    d.update(kw)
    return broker_import.BrokerImport(**d)


def bot_buy(price=27, when=datetime(2026, 10, 13, 14, 12, 2), lots=1):
    return OrderLine(when=when, symbol="GOTO", side="buy", lots=lots, limit_price=int(price), strategy_id="s",
                     strategy_label="s", strategy_note="", run_id="r", sink_kind=LIVE, filled_lots=lots,
                     fill_price=float(price), fees_idr=0, realised_pnl_idr=None, status="filled", reject_reason=None,
                     order_id="o1")


def manual(**kw):
    d = dict(date="2026-10-13", time_wib=None, symbol="GOTO", side="sell", lots=1, price_idr=26.0, fee_idr=6.5, note="by hand")
    d.update(kw)
    return ManualTrade(**d)


def test_load_missing_file_is_empty(tmp_path):
    assert broker_import.load_broker_imports(str(tmp_path / "none.json")) == []


def test_load_reads_the_go_ledger_and_is_strict(tmp_path):
    p = tmp_path / "b.json"
    row = {"broker_id": "9", "source": "broker-import", "date": "2026-10-13", "time_wib": "14:14:15", "symbol": "GOTO",
           "side": "sell", "lots": 1, "price_idr": 26, "fee_idr": 6.5}
    p.write_text(json.dumps({"_schema": "x", "trades": [row]}))
    got = broker_import.load_broker_imports(str(p))
    assert got == [imp(broker_id="9")]
    for bad in ({**row, "extra": 1}, {**row, "source": "manual"}, {**row, "lots": 0}, {**row, "side": "hold"}):
        p.write_text(json.dumps({"trades": [bad]}))
        with pytest.raises(broker_import.BrokerImportError):
            broker_import.load_broker_imports(str(p))


def test_import_becomes_a_ledger_trade():
    out = broker_import.fold_into_manual([], [imp()], [bot_buy()])
    assert len(out) == 1
    t = out[0]
    assert (t.date, t.time_wib, t.symbol, t.side, t.lots, t.price_idr, t.fee_idr) == (
        "2026-10-13", "14:14:15", "GOTO", "sell", 1, 26.0, 6.5)
    assert "900000002" in t.note and "broker" in t.note.lower()


def test_import_supersedes_a_manual_entry_for_the_same_trade():
    out = broker_import.fold_into_manual([manual()], [imp()], [bot_buy()])
    assert len(out) == 1, "the sale must be counted once"
    assert "900000002" in out[0].note


def test_a_different_manual_trade_is_kept():
    other = manual(price_idr=27.0)
    out = broker_import.fold_into_manual([other], [imp()], [bot_buy()])
    assert len(out) == 2 and other in out


def test_import_already_explained_by_a_bot_fill_is_dropped():
    buy = imp(broker_id="1", side="buy", time_wib="14:12:00", price_idr=27.0, fee_idr=4.05)
    assert broker_import.fold_into_manual([], [buy], [bot_buy()]) == []


def test_import_far_in_time_from_a_bot_fill_is_not_dropped():
    buy = imp(broker_id="1", side="buy", time_wib="09:00:00", price_idr=27.0, fee_idr=4.05)
    assert len(broker_import.fold_into_manual([], [buy], [bot_buy()])) == 1


def test_one_bot_fill_explains_only_one_import():
    a = imp(broker_id="1", side="buy", time_wib="14:12:00", price_idr=27.0, fee_idr=4.05)
    b = imp(broker_id="2", side="buy", time_wib="14:12:30", price_idr=27.0, fee_idr=4.05)
    assert len(broker_import.fold_into_manual([], [a, b], [bot_buy()])) == 1


def test_config_has_a_recon_command_without_an_account_number():
    assert not _ACCOUNT_LIKE.search(config.RECON_RUN_HINT)
