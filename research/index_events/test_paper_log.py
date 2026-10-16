"""Tests for index_events.paper_log (paper forward log, plan 21 H2). No network, no real bars."""
import ast
import csv
import subprocess
import sys
import urllib.error
from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from index_events import msci_lists
from index_events import paper_log as pl
from lib.market_calendar import MarketCalendar

CAL = MarketCalendar(holidays=frozenset({date(2026, 11, 25)}), provisional=False)
WIB_EVENING = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)   # long after every synthetic session


# --------------------------------------------------------------------------- session planning

def test_plan_sessions_skips_synthetic_holiday():
    plan = pl.plan_sessions("2026-11-24", "2026-11-30", CAL, 2026)
    assert plan["a_session"] == "2026-11-24"
    assert plan["p_session"] == "2026-11-30"
    assert plan["entry_session"] == "2026-12-01"
    assert plan["exit_session_k5"] == "2026-12-08"
    # announcement on the holiday itself rolls back to the previous session
    assert pl.plan_sessions("2026-11-25", "2026-11-30", CAL, 2026)["a_session"] == "2026-11-24"


def test_plan_sessions_effective_on_weekend_and_real_2026_holiday():
    cal = MarketCalendar.load()
    plan = pl.plan_sessions("2026-08-13", "2026-08-31", cal, pl.calendar_covered_year())
    assert (plan["p_session"], plan["entry_session"]) == ("2026-08-31", "2026-09-01")
    # effective Sat 2026-08-22 -> p = Fri 08-21, entry Mon 08-24, k=1 skips the 08-25 holiday
    plan = pl.plan_sessions("2026-08-10", "2026-08-22", cal, pl.calendar_covered_year())
    assert plan["p_session"] == "2026-08-21" and plan["entry_session"] == "2026-08-24"
    assert cal.add_trading_days(date(2026, 8, 24), 1) == date(2026, 8, 26)


def test_plan_nov26_k20_uses_jan1_closure_and_warns():
    plan = pl.plan_sessions("2026-11-11", "2026-11-30", MarketCalendar.load(), pl.calendar_covered_year())
    assert plan["exit_session_k20"] == "2027-01-04"
    assert "unverified" in plan["plan_note"]


def test_plan_refuses_provisional_calendar():
    with pytest.raises(SystemExit):
        pl.plan_sessions("2026-11-11", "2026-11-30", MarketCalendar(frozenset(), provisional=True), 2026)


# --------------------------------------------------------------------------- log: append-only, unmapped

def _fake_rows(names):
    def r(name, index="MSCI_ST", change="del"):
        return msci_lists.Row("MSCI", index, "MSCI-2099-11", "2099-11-11", "2099-11-30", "Deletions", change,
                              name, "MSCI_Nov99_st.pdf", "")
    return [(r(n), Path("/nonexistent/MSCI_Nov99_st.pdf")) for n in names]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.csv"
    pl.init_ledger(path)
    monkeypatch.setattr(pl, "deletion_rows", lambda review, raw_dir: _fake_rows(["BANK JAGO", "ZZZ NOT MAPPED CO"]))
    monkeypatch.setattr(pl, "sha256_file", lambda p: "0" * 64)
    return path


def _log(path, review="MSCI-2099-11"):
    return pl.cmd_log(Namespace(ledger=str(path), review=review, raw_dir="x", allow_late=False))


def test_log_appends_then_refuses_second_time(ledger):
    assert _log(ledger) == 0
    rows = pl.read_ledger(ledger)
    assert [r["status"] for r in rows] == ["planned", "unmapped"]
    assert rows[0]["ticker"] == "ARTO" and rows[0]["entry_session"] > rows[0]["p_session"]
    before = ledger.read_bytes()
    with pytest.raises(SystemExit):
        _log(ledger)
    assert ledger.read_bytes() == before


def test_log_refuses_when_entry_already_past(ledger, monkeypatch):
    monkeypatch.setattr(pl, "deletion_rows", lambda review, raw_dir: [
        (msci_lists.Row("MSCI", "MSCI_ST", "MSCI-2020-05", "2020-05-12", "2020-05-29", "Deletions", "del",
                        "BANK JAGO", "x.pdf", ""), Path("x.pdf"))])
    with pytest.raises(SystemExit):
        _log(ledger, "MSCI-2020-05")
    assert pl.read_ledger(ledger) == []


def test_unmapped_is_loud_and_resolved_later_without_touching_planned(ledger, monkeypatch, capsys):
    _log(ledger)
    assert "UNMAPPED" in capsys.readouterr().err
    rows = pl.read_ledger(ledger)
    monkeypatch.setitem(pl.MSCI_TICKERS, "ZZZ NOT MAPPED CO", ("ZZZ", "high", ""))
    early = datetime(2099, 11, 20, 3, tzinfo=timezone.utc)
    out, _ = pl.update_rows(rows, None, early)
    assert out[1]["status"] == "planned" and out[1]["resolved_ticker"] == "ZZZ" and out[1]["ticker"] == ""
    late, _ = pl.update_rows(rows, None, datetime(2099, 12, 2, 12, tzinfo=timezone.utc))
    assert late[1]["status"] == "excluded_resolved_late"


# --------------------------------------------------------------------------- planned columns immutable

def test_update_never_alters_planned_columns(ledger):
    _log(ledger)
    rows = pl.read_ledger(ledger)
    tampered = [dict(r) for r in rows]
    out, _ = pl.update_rows(rows, None, datetime(2099, 12, 2, 12, tzinfo=timezone.utc))
    pl.assert_planned_unchanged(rows, out)
    tampered[0]["p_session"] = "2099-01-01"
    with pytest.raises(AssertionError):
        pl.assert_planned_unchanged(rows, tampered)
    pl.write_ledger(ledger, out)          # atomic rewrite keeps planned columns byte-identical
    again = pl.read_ledger(ledger)
    pl.assert_planned_unchanged(rows, again)
    assert not list(ledger.parent.glob("*.tmp"))


# --------------------------------------------------------------------------- evaluation on a synthetic panel

N_DAYS = 330
DATES = pd.bdate_range("2025-01-01", periods=N_DAYS)


def _frame(close, volume=None, dividend=None):
    close = np.asarray(close, float)
    vol = np.full(N_DAYS, 2_000_000.0) if volume is None else np.asarray(volume, float)
    return pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in DATES], "open": close, "high": close, "low": close, "close": close,
        "volume": vol, "value": close * vol,
        "dividend": np.zeros(N_DAYS) if dividend is None else np.asarray(dividend, float)})


def _hist(target_close=None, target_volume=None, dividend=None):
    base = 5000.0 + np.sin(np.arange(N_DAYS)) * 40
    hist = {f"S{i:02d}": _frame(base * (1 + i / 100)) for i in range(12)}
    hist["TGT"] = _frame(base if target_close is None else target_close, target_volume, dividend)
    return hist


def _ihsg(level=lambda i: 7000.0 + 10 * i):
    s = pd.Series([level(i) for i in range(N_DAYS)], index=DATES)
    return s


def _row(a, p, entry, ex5, ex10, ex20, ticker="TGT"):
    r = {c: "" for c in pl.COLUMNS}
    r.update(review_label="MSCI-2026-08", index_name="MSCI_ST", change="del", msci_name="TGT CO", ticker=ticker,
             a_session=a, p_session=p, entry_session=entry, exit_session_k5=ex5, exit_session_k10=ex10,
             exit_session_k20=ex20, status="planned")
    return r


def _sessions(ip):
    d = lambda i: DATES[i].strftime("%Y-%m-%d")
    return _row(d(ip - 10), d(ip), d(ip + 1), d(ip + 6), d(ip + 11), d(ip + 21))


IP = 280   # signal session index; p+1+20 = 301 < N_DAYS


def test_entry_exit_cost_ihsg_and_dividend_on_synthetic_panel():
    close = np.full(N_DAYS, 1000.0)
    close[IP + 1] = 1000.0
    close[IP + 6], close[IP + 11], close[IP + 21] = 1100.0, 900.0, 1200.0
    div = np.zeros(N_DAYS)
    div[IP + 10] = 50.0                          # ex-date inside the k10 and k20 windows only
    env = pl.make_env(_hist(close, dividend=div), _ihsg())
    row = _sessions(IP)
    (out,), need = pl.update_rows([row], env, WIB_EVENING)
    assert out["status"] == "closed" and not need.symbols
    assert float(out["entry_px"]) == 1000.0
    ih = lambda k: (7000 + 10 * (IP + 1 + k)) / (7000 + 10 * (IP + 1)) - 1
    assert float(out["gross_k5"]) == pytest.approx(1000.0, abs=0.01)                       # +10%, no dividend yet
    assert float(out["gross_k10"]) == pytest.approx((900 + 45) / 1000 * 1e4 - 1e4, abs=0.01)  # dividend net of 10% tax
    assert float(out["gross_k20"]) == pytest.approx((1200 + 45) / 1000 * 1e4 - 1e4, abs=0.01)
    cost = float(out["cost_bps"])
    assert float(out["net_k20"]) == pytest.approx(float(out["gross_k20"]) - cost, abs=0.02)
    assert float(out["ihsg_ret_k20"]) == pytest.approx(ih(20) * 1e4, abs=0.02)
    assert float(out["net_vs_ihsg_k20"]) == pytest.approx(float(out["net_k20"]) - ih(20) * 1e4, abs=0.02)
    # cost = tick(1000 -> 5 IDR = 50 bps) + 40 bps fee/tax + impact; 1 lot is no more expensive than Rp 3M
    assert cost >= 90.0 and float(out["cost_bps_1lot"]) <= cost
    assert out["coeffs_sha256"] != ""


def test_not_eligible_suspended_and_entry_not_traded_are_excluded():
    base = np.full(N_DAYS, 1000.0)
    short = {f"S{i:02d}": _frame(base) for i in range(12)}
    # illiquid name: Rp 1000 * 100 shares = 1e5 ADV, far below the harness floor
    short["TGT"] = _frame(base, np.full(N_DAYS, 100.0))
    (out,), _ = pl.update_rows([_sessions(IP)], pl.make_env(short, _ihsg()), WIB_EVENING)
    assert out["status"] == "excluded_not_eligible"

    vol = np.full(N_DAYS, 2_000_000.0)
    vol[IP - 6: IP + 1] = 0.0                    # 7 zero-volume sessions in a+1..p
    (out,), _ = pl.update_rows([_sessions(IP)], pl.make_env(_hist(target_volume=vol), _ihsg()), WIB_EVENING)
    assert out["status"] == "excluded_suspended_a_to_p"

    vol = np.full(N_DAYS, 2_000_000.0)
    vol[IP + 1] = 0.0
    (out,), _ = pl.update_rows([_sessions(IP)], pl.make_env(_hist(target_volume=vol), _ihsg()), WIB_EVENING)
    assert out["status"] == "excluded_entry_not_traded"


def test_partial_progress_open_then_closed_and_recorded_prices_not_overwritten():
    env = pl.make_env(_hist(), _ihsg())
    row = _sessions(IP)
    # asof between the entry close and the k5 exit: open
    asof = datetime.combine(DATES[IP + 3].date(), datetime.min.time(), tzinfo=timezone.utc)
    (opened,), _ = pl.update_rows([row], env, asof)
    assert opened["status"] == "open" and opened["entry_px"] != "" and opened["exit_px_k5"] == ""
    (closed,), _ = pl.update_rows([opened], env, WIB_EVENING)
    assert closed["status"] == "closed"
    forged = {**closed, "entry_px": "123.00"}
    (again,), _ = pl.update_rows([forged], env, WIB_EVENING)
    assert again["entry_px"] == "123.00"       # terminal rows are left alone


def test_stale_and_missing_tickers_are_reported_for_snapshot():
    hist = _hist()
    hist["TGT"] = hist["TGT"].iloc[: IP - 1]                   # ticker bars end before p
    env = pl.make_env(hist, _ihsg())
    rows = [_sessions(IP), {**_sessions(IP), "ticker": "NOPE"}]
    out, need = pl.update_rows(rows, env, WIB_EVENING)
    assert [r["status"] for r in out] == ["planned", "planned"]
    assert need.symbols == {"TGT": "stale", "NOPE": "missing"}
    line = pl.snapshot_line(need)
    assert "screener.daily_store snapshot --symbols NOPE,TGT" in line


def test_ihsg_stale_is_reported():
    env = pl.make_env(_hist(), _ihsg().iloc[: IP + 3])
    (out,), need = pl.update_rows([_sessions(IP)], env, WIB_EVENING)
    assert out["status"] == "open" and "IHSG" in need.symbols


def test_scorecard_counts_and_msci_st_progress():
    mk = lambda idx, net, vs, st="closed": {**{c: "" for c in pl.COLUMNS}, "index_name": idx, "status": st,
                                            "net_k20": str(net), "net_vs_ihsg_k20": str(vs), "net_1lot_k20": str(net)}
    sc = pl.scorecard([mk("MSCI_ST", 100, 50), mk("MSCI_ST", -50, -20), mk("MSCI_SC", 30, 10), mk("MSCI_SC", 0, 0, "open")])
    assert sc["per_index"]["MSCI_ST"]["closed_trips"] == 2
    assert sc["per_index"]["MSCI_ST"]["mean_net_k20_bps"] == 25.0
    assert sc["per_index"]["MSCI_ST"]["share_net_k20_gt0"] == 0.5
    assert sc["per_index"]["ALL"]["closed_trips"] == 3
    assert sc["msci_st_progress"]["line"] == "development 23 + paper 2 = 25 of 30 trades needed (plan 21 §10)"


# --------------------------------------------------------------------------- fetch

def test_fetch_refuses_non_pdf_and_writes_nothing(tmp_path):
    with pytest.raises(SystemExit):
        pl.fetch_review("Nov26", tmp_path, getter=lambda url: b"<html>Not Found</html>")
    assert not list(tmp_path.rglob("*.pdf"))


def test_fetch_404_is_nonzero_and_clear(tmp_path, capsys):
    def getter(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    with pytest.raises(SystemExit) as exc:
        pl.fetch_review("Nov26", tmp_path, getter=getter)
    assert exc.value.code != 0
    assert "not published" in capsys.readouterr().err


def test_fetch_saves_both_with_convention_and_never_overwrites(tmp_path):
    urls = []
    def getter(url):
        urls.append(url)
        return b"%PDF-1.4 fake"
    saved = pl.fetch_review("Nov26", tmp_path, getter=getter)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in saved) == ["sc/MSCI_Nov26_sc.pdf", "st/MSCI_Nov26_st.pdf"]
    assert urls == ["https://app2.msci.com/eqb/gimi/stdindex/MSCI_Nov26_STPublicList.pdf",
                    "https://app2.msci.com/eqb/gimi/smallcap/MSCI_Nov26_SCPublicList.pdf"]
    with pytest.raises(SystemExit):
        pl.fetch_review("Nov26", tmp_path, getter=getter)


def test_http_get_uses_urllib_with_timeout_and_ua(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b"%PDF-x"

    def fake_urlopen(req, timeout):
        seen["ua"], seen["timeout"] = req.get_header("User-agent"), timeout
        return Resp()
    monkeypatch.setattr(pl.urllib.request, "urlopen", fake_urlopen)
    assert pl.http_get("https://example.invalid/x.pdf") == b"%PDF-x"
    assert seen["timeout"] == pl.FETCH_TIMEOUT_S and "Mozilla" in seen["ua"]


def test_review_tag_validation():
    assert pl.review_to_file_tag("Nov26") == ("Nov", "26")
    assert pl.label_to_file_tag("MSCI-2026-11") == "Nov26"
    for bad in ("nov26", "Nov2026", "Xyz26"):
        with pytest.raises(SystemExit):
            pl.review_to_file_tag(bad)


# --------------------------------------------------------------------------- paper only

def _is_forbidden(mod: str) -> bool:
    parts = mod.split(".")
    if parts[0] in ("runner", "strategy", "token_rotation"):
        return True
    return any(p in ("target_emitter", "executor", "liveorders", "daily_store") or p.startswith("live") for p in parts)


def test_module_imports_exclude_order_paths_statically():
    tree = ast.parse(Path(pl.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            mods.add(base)
            mods.update(f"{base}.{a.name}" for a in node.names)
    assert mods, "expected to see imports"
    assert [m for m in mods if _is_forbidden(m)] == []


def test_runtime_import_closure_excludes_order_paths():
    code = (
        "import sys; from index_events import paper_log as p; p._harness()\n"
        "print('\\n'.join(sorted(sys.modules)))"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(pl.__file__).parents[1])
    assert res.returncode == 0, res.stderr
    loaded = res.stdout.split()
    assert "index_events.paper_log" in loaded
    first_party = ("index_events", "lib", "strategy_fit", "screener", "runner", "strategy", "token_rotation", "study", "backtest")
    assert [m for m in loaded if m.split(".")[0] in first_party and _is_forbidden(m)] == []
