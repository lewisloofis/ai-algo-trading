"""
Paper specs (wifi_momo_h5, ptro_momo_h5) and the paper-only emit path.

* the specs carry the EXACT cell the shortlist scored, and the emitter's replay
  entries equal the shortlist's trade list (synthetic always; real parquet if present);
* a paper spec can never go live (status gate, printed command);
* multi-day hold: day 2 sees the day-1 paper fill and does not re-buy; the exit day
  sells to zero; a test started mid-hold does not buy late;
* a zero-volume last bar (the refresh's in-progress placeholder) is refused.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lib.market_calendar import CALENDAR
from strategy_fit import emitter_specs as es
from strategy_fit import paper_stores as ps
from strategy_fit import shortlist_2026_10 as sl
from strategy_fit import target_emitter as te
from strategy_fit.families_single_name import daily_momentum_signal
from strategy_fit.paper_store_fixtures import write_store
from strategy_fit.run_strategy_fit import HISTORY_START, SINGLE_NAME_SIGNAL_FNS
from strategy_fit.target_emitter_replay import calendar_from_bars, replay
from strategy_fit.test_target_emitter import _bars, _closes_with_up_rows

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"
PAPER_IDS = {"wifi_momo_h5": "WIFI", "ptro_momo_h5": "PTRO"}


# ---------------------------------------------------------------------------
# Registration = the shortlist's cell
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid,sym", PAPER_IDS.items())
def test_spec_is_the_scored_cell_and_paper_only(sid, sym):
    spec = te.SPECS[sid]
    assert spec.symbols == (sym,) and spec.family == "daily_momentum" and spec.hold == 5
    assert spec.signal_fn is SINGLE_NAME_SIGNAL_FNS["daily_momentum"] is daily_momentum_signal
    assert spec.lots == 1 and spec.capital_model == "owned_cash"
    assert spec.status == es.STATUS_PAPER and spec.is_paper
    assert not spec.is_candidate and not spec.can_go_live


def test_only_a_candidate_can_go_live():
    base = te.SPECS["wifi_momo_h5"]
    for status, live in ((es.STATUS_CANDIDATE, True), (es.STATUS_PAPER, False),
                         (es.STATUS_RETIRED, False), (es.STATUS_EXAMPLE, False)):
        assert replace(base, status=status).can_go_live is live


def _shortlist_vs_emitter(df: pd.DataFrame, sym: str, spec: es.EmitterSpec) -> None:
    last = str(df["date"].iloc[-1])
    bars = sl.Bars(sym, df)
    trades, _ = sl.trades_for(bars, "daily_momentum", spec.hold, HISTORY_START, last)
    expected = {date.fromisoformat(d) for d in trades["edate"]}
    assert expected, "need at least one scored trade to compare"
    norm = te.normalise_bars(df)
    res = replay(norm, spec, calendar_from_bars(norm, CALENDAR), symbol=sym)
    assert set(res.emitter_entry_days) == expected
    assert res.entry_match_rate == 1.0 and not res.weight_mismatches


def test_replay_entries_equal_shortlist_trades_synthetic():
    rng = np.random.default_rng(11)
    n = 420
    dates = pd.bdate_range("2024-01-02", periods=n)
    dates = [d for d in dates if CALENDAR.is_trading_day(d.date()) or d.year != 2026]
    close = np.round(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, len(dates)))))
    df = pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in dates], "open": close, "close": close,
                       "volume": 1e6, "value": close * 1e6, "net_foreign": 0.0})
    df["vol60"] = np.log(df["close"]).diff().rolling(59, min_periods=19).std(ddof=1) * 1e4
    df["adv60"] = df["value"].rolling(60, min_periods=20).median()
    for spec in (te.SPECS["wifi_momo_h5"], te.SPECS["ptro_momo_h5"]):
        _shortlist_vs_emitter(df, spec.symbol, spec)


@pytest.mark.skipif(not (DATA_DIR / "WIFI.parquet").exists(), reason="local daily parquet not present")
@pytest.mark.parametrize("sid,sym", PAPER_IDS.items())
def test_replay_entries_equal_shortlist_trades_real_data(sid, sym):
    raw = pd.read_parquet(DATA_DIR / f"{sym}.parquet")
    raw["date"] = raw["date"].astype(str)
    traded = raw[pd.to_numeric(raw["volume"], errors="coerce") > 0]
    df = sl.load_bars(sym, str(traded["date"].iloc[-1])).df   # drops any in-progress placeholder
    _shortlist_vs_emitter(df, sym, te.SPECS[sid])


# ---------------------------------------------------------------------------
# Paper never goes live
# ---------------------------------------------------------------------------

def test_emit_of_a_paper_spec_prints_a_paper_command_only(tmp_path, capsys):
    data = tmp_path / "daily"
    data.mkdir()
    last = date(2026, 8, 14)
    _bars(last, _closes_with_up_rows(40, {38}, start=1370.0)).to_parquet(data / "WIFI.parquet")
    as_of = CALENDAR.next_trading_day(last)
    rc = te.main(["emit", "--strategy", "wifi_momo_h5", "--as-of", as_of.isoformat(),
                  "--out", str(tmp_path / "runs" / "t.json"), "--data-dir", str(data)])
    out = capsys.readouterr().out
    assert rc == 0
    cmd = [ln for ln in out.splitlines() if ln.startswith("live-session command: ")][0]
    assert "--sink paper --source live" in cmd and "--sink live" not in cmd and "ACCOUNT" not in cmd
    assert "PAPER only" in out


# ---------------------------------------------------------------------------
# Multi-day hold across paper stores
# ---------------------------------------------------------------------------

HOLD = 5
SIG_DAY = date(2026, 8, 14)                         # signal fires on this close
ENTRY = CALENDAR.next_trading_day(SIG_DAY)          # E
SPEC = replace(te.SPECS["wifi_momo_h5"], strategy_id="paper_hold", symbols=("AAAA",))
REG = {"paper_hold": SPEC}


def _days_from_entry() -> list[date]:
    days = [ENTRY]
    for _ in range(HOLD):
        days.append(CALENDAR.next_trading_day(days[-1]))
    return days                                     # E, E+1 .. E+5 (exit)


def _write_bars_through(data: Path, as_of: date) -> None:
    """Bars up to the session before as_of: the up-bar signal at SIG_DAY, then decline."""
    data.mkdir(parents=True, exist_ok=True)
    base = _bars(SIG_DAY, _closes_with_up_rows(40, {38}, start=100.0))
    rows, d, px = base.to_dict("records"), SIG_DAY, float(base["close"].iloc[-1])
    while CALENDAR.next_trading_day(d) < as_of:
        d = CALENDAR.next_trading_day(d)
        px -= 1.0
        rows.append({"date": d.isoformat(), "close": px, "volume": 1_000_000})
    pd.DataFrame(rows).to_parquet(data / "AAAA.parquet")


def _emit_day(tmp_path, as_of: date, capsys) -> dict:
    import json
    data, runs = tmp_path / "daily", tmp_path / "runs"
    _write_bars_through(data, as_of)
    out = runs / f"target_paper_hold_{as_of}.json"
    rc = te.main(["emit", "--strategy", "paper_hold", "--as-of", as_of.isoformat(), "--out", str(out),
                  "--data-dir", str(data)], registry=REG)
    assert rc == 0
    capsys.readouterr()
    return {"weight": json.loads(out.read_text())["targets"][0]["weight"],
            "reason": json.loads(te.sidecar_path(out).read_text())["symbols"][0]["reason"]}


def _paper_store(tmp_path, day: date, fills: list[tuple]) -> Path:
    return write_store(tmp_path / "runs" / f"{day}-paper_hold-paper-a1.db", fills)


def test_day2_sees_the_day1_paper_fill_and_does_not_rebuy(tmp_path, capsys):
    days = _days_from_entry()
    d1 = _emit_day(tmp_path, days[0], capsys)
    assert d1["weight"] == 1.0 and "enter today" in d1["reason"]          # day 1: buy
    _paper_store(tmp_path, days[0], [(days[0].isoformat(), "AAAA", "buy", 100, 1)])
    for day in days[1:HOLD]:                                              # E+1 .. E+4: keep, never re-buy
        d = _emit_day(tmp_path, day, capsys)
        assert d["weight"] == 1.0 and "holding since" in d["reason"], (day, d)
    assert ps.paper_net_lots(tmp_path / "runs", "paper_hold") == {"AAAA": 1}


def test_exit_day_target_is_zero_so_the_position_sells_to_zero(tmp_path, capsys):
    days = _days_from_entry()
    _paper_store(tmp_path, days[0], [(days[0].isoformat(), "AAAA", "buy", 100, 1)])
    d = _emit_day(tmp_path, days[HOLD], capsys)
    assert d["weight"] == 0.0 and "exit leg" in d["reason"]


def test_started_mid_hold_does_not_buy_late(tmp_path, capsys):
    days = _days_from_entry()
    d = _emit_day(tmp_path, days[2], capsys)                              # no paper store at all
    assert d["weight"] == 0.0 and "not entering mid-hold" in d["reason"]


def test_missed_entry_day_is_not_bought_on_day_two(tmp_path, capsys):
    days = _days_from_entry()
    _paper_store(tmp_path, days[0], [])                                   # day-1 run placed nothing
    d = _emit_day(tmp_path, days[1], capsys)
    assert d["weight"] == 0.0


def test_live_stores_never_count_as_paper_position(tmp_path, capsys):
    days = _days_from_entry()
    write_store(tmp_path / "runs" / f"{days[0]}-paper_hold-a1.db",
                [(days[0].isoformat(), "AAAA", "buy", 100, 1)], sink="live")
    assert _emit_day(tmp_path, days[1], capsys)["weight"] == 0.0


# ---------------------------------------------------------------------------
# In-progress placeholder bar
# ---------------------------------------------------------------------------

def test_zero_volume_last_bar_is_refused(tmp_path):
    p = tmp_path / "AAAA.parquet"
    df = _bars(SIG_DAY, _closes_with_up_rows(40, {38}, start=100.0))
    df.loc[df.index[-1], "volume"] = 0
    df.to_parquet(p)
    with pytest.raises(te.StaleDataError, match="zero volume"):
        te.load_local_bars(p, require_traded_last_bar=SIG_DAY)
    assert len(te.load_local_bars(p)) == 40                               # default path unchanged
