"""
strategy_fit/test_target_emitter_job.py — the strategy-agnostic pre-open job:
spec registry, multi-symbol targets, rebalance window, non-trading-day exit,
the printed strategy-run command, and the golden fixtures the Go loader test
(strategy/portfolio/emitted_fixture_test.go) parses.

No network: bars come from parquet files written into tmp_path.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest

from lib.market_calendar import CALENDAR
from strategy_fit import emitter_specs as es
from strategy_fit import target_emitter as te
from strategy_fit.families_single_name import daily_momentum_signal
from strategy_fit.test_target_emitter import _bars, _closes_with_up_rows, _go_lots

LAST = date(2026, 8, 14)        # Fri; as_of below is Tue 08-18 (08-17 holiday)
AS_OF = CALENDAR.next_trading_day(LAST)
N = 40
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "target_emitter"

MULTI = es.EmitterSpec(
    strategy_id="test_multi",
    symbols=("AAAA", "BBBB", "CCCC"),
    family="daily_momentum",
    hold=2,
    signal_fn=daily_momentum_signal,
    status=es.STATUS_CANDIDATE,
)
REGISTRY = {"test_multi": MULTI, "test_retired": replace(MULTI, strategy_id="test_retired",
                                                         status=es.STATUS_RETIRED)}


def _write_universe(data_dir: Path) -> dict[str, float]:
    """AAAA and CCCC fire a long signal on the last bar, BBBB does not."""
    data_dir.mkdir(parents=True, exist_ok=True)
    closes = {}
    for sym, start, up in (("AAAA", 100.0, {38}), ("BBBB", 1500.0, set()), ("CCCC", 250.0, {38})):
        df = _bars(LAST, _closes_with_up_rows(N, up, start=start))
        df.to_parquet(data_dir / f"{sym}.parquet")
        closes[sym] = float(df["close"].iloc[-1])
    return closes


def _emit(tmp_path, *extra, strategy="test_multi", as_of=AS_OF, registry=REGISTRY, out=None):
    data_dir = tmp_path / "daily"
    if not data_dir.exists():
        _write_universe(data_dir)
    out = out or tmp_path / "runs" / f"target_{strategy}_{as_of}.json"
    rc = te.main(
        ["emit", "--strategy", strategy, "--as-of", as_of.isoformat(), "--out", str(out),
         "--data-dir", str(data_dir), *extra],
        registry=registry,
    )
    return rc, out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_bnbr_example_is_marked_retired_not_candidate(self):
        spec = es.SPECS["bnbr_momo_h2"]
        assert spec.status == es.STATUS_RETIRED and not spec.is_candidate
        assert "not a live candidate" in spec.status_note
        assert spec.signal_fn is daily_momentum_signal

    def test_every_registered_spec_validates(self):
        for spec in es.SPECS.values():
            es.validate_spec(spec)

    @pytest.mark.parametrize("bad", [
        dict(strategy_id="Has-Caps"), dict(strategy_id="calibration"), dict(symbols=()),
        dict(symbols=("AAAA", "AAAA")), dict(symbols=("bbca",)), dict(hold=0), dict(lots=0),
        dict(status="live"), dict(capital_model="margin"),
    ])
    def test_malformed_specs_are_rejected(self, bad):
        with pytest.raises(es.SpecError):
            es.validate_spec(replace(MULTI, **bad))

    def test_register_rejects_duplicates_and_adds_one_entry(self):
        reg: dict = {}
        es.register(MULTI, reg)
        assert list(reg) == ["test_multi"]
        with pytest.raises(es.SpecError):
            es.register(MULTI, reg)

    def test_symbol_property_is_single_name_only(self):
        assert es.SPECS["bnbr_momo_h2"].symbol == "BNBR"
        with pytest.raises(es.SpecError):
            MULTI.symbol


# ---------------------------------------------------------------------------
# Retired gate
# ---------------------------------------------------------------------------

class TestRetiredGate:
    def test_retired_spec_is_refused_without_the_flag_and_writes_nothing(self, tmp_path, capsys):
        rc, out = _emit(tmp_path, strategy="test_retired")
        assert rc == te.EXIT_RETIRED
        err = capsys.readouterr().err
        assert "retired" in err and "--allow-retired" in err
        assert not out.exists() and not te.sidecar_path(out).exists()

    def test_flag_allows_a_retired_spec(self, tmp_path):
        rc, out = _emit(tmp_path, "--allow-retired", strategy="test_retired")
        assert rc == 0 and out.exists()
        assert json.loads(te.sidecar_path(out).read_text())["spec_status"] == "retired"

    def test_candidate_needs_no_flag(self, tmp_path):
        assert _emit(tmp_path)[0] == 0


# ---------------------------------------------------------------------------
# Non-trading day
# ---------------------------------------------------------------------------

class TestNonTradingDay:
    @pytest.mark.parametrize("day", [date(2026, 8, 17), date(2026, 8, 15), date(2026, 12, 25)])
    def test_emits_nothing_with_distinct_exit_code_and_clear_message(self, tmp_path, capsys, day):
        rc, out = _emit(tmp_path, as_of=day)
        assert rc == te.EXIT_NOT_TRADING_DAY
        assert rc not in (0, te.EXIT_DATA_ERROR, te.EXIT_NO_TOKEN, te.EXIT_RETIRED, te.EXIT_BAD_WINDOW)
        err = capsys.readouterr().err
        assert "not an IDX trading day" in err and day.isoformat() in err
        assert not out.exists() and not te.sidecar_path(out).exists()

    def test_check_happens_before_any_data_access(self, tmp_path):
        # no data dir at all: a holiday must still report "not a trading day"
        out = tmp_path / "t.json"
        rc = te.main(["emit", "--strategy", "test_multi", "--as-of", "2026-08-17", "--out", str(out),
                      "--data-dir", str(tmp_path / "missing")], registry=REGISTRY)
        assert rc == te.EXIT_NOT_TRADING_DAY


# ---------------------------------------------------------------------------
# Rebalance window
# ---------------------------------------------------------------------------

class TestWindow:
    def test_default_is_session_one_and_rebalance_inside(self):
        w = te.RebalanceWindow()
        assert (w.start, w.end) == (time(9, 0), time(11, 30))
        assert w.start <= w.rebalance_at < w.end

    @pytest.mark.parametrize("at", ["15:00", "08:59", "11:30", "12:00", "13:45"])
    def test_rebalance_outside_the_window_is_rejected(self, at):
        with pytest.raises(te.BadWindowError):
            te.RebalanceWindow(rebalance_at=te.parse_hhmm(at))

    def test_the_bots_default_1500_does_not_fit(self, tmp_path, capsys):
        rc, out = _emit(tmp_path, "--rebalance-at", "15:00")
        assert rc == te.EXIT_BAD_WINDOW
        assert "15:00" in capsys.readouterr().err and not out.exists()

    def test_inverted_window_is_rejected(self):
        with pytest.raises(te.BadWindowError):
            te.RebalanceWindow(start=time(11, 30), end=time(9, 0), rebalance_at=time(10, 0))

    @pytest.mark.parametrize("bad", ["9", "25:00", "09:60", "abc", "09:30:00"])
    def test_hhmm_parse_rejects_garbage(self, bad):
        with pytest.raises(te.BadWindowError):
            te.parse_hhmm(bad)

    def test_target_times_cover_the_rebalance_time(self, tmp_path):
        rc, out = _emit(tmp_path, "--rebalance-at", "10:15")
        assert rc == 0
        doc = json.loads(out.read_text())
        day = AS_OF.isoformat()
        assert doc["as_of"] == f"{day}T09:00:00+07:00"
        assert doc["constraints"]["complete_by"] == f"{day}T11:30:00+07:00"
        assert doc["as_of"] <= f"{day}T10:15:00+07:00" < doc["constraints"]["complete_by"]

    def test_custom_window_flags(self, tmp_path):
        rc, out = _emit(tmp_path, "--window-start", "09:30", "--window-end", "11:00",
                        "--rebalance-at", "09:45")
        doc = json.loads(out.read_text())
        assert rc == 0 and doc["as_of"].endswith("T09:30:00+07:00")
        assert doc["constraints"]["complete_by"].endswith("T11:00:00+07:00")


# ---------------------------------------------------------------------------
# Multi-symbol target + sizing
# ---------------------------------------------------------------------------

class TestMultiSymbol:
    def test_one_file_lists_every_symbol_in_spec_order(self, tmp_path):
        rc, out = _emit(tmp_path)
        doc = json.loads(out.read_text())
        assert rc == 0
        assert [t["symbol"] for t in doc["targets"]] == ["AAAA", "BBBB", "CCCC"]
        w = {t["symbol"]: t["weight"] for t in doc["targets"]}
        assert w["AAAA"] > 0 and w["CCCC"] > 0 and w["BBBB"] == 0.0

    def test_weights_sum_to_at_most_one_and_are_non_negative(self, tmp_path):
        _, out = _emit(tmp_path)
        ws = [t["weight"] for t in json.loads(out.read_text())["targets"]]
        assert all(0.0 <= x <= 1.0 for x in ws)
        assert sum(ws) <= 1.0 + 1e-9

    @pytest.mark.parametrize("lots", [1, 2, 3])
    def test_active_names_size_to_exactly_n_lots_across_the_price_band(self, tmp_path, lots):
        spec = replace(MULTI, lots=lots)
        data_dir = tmp_path / "daily"
        closes = _write_universe(data_dir)
        decisions = [te.decide(te.load_local_bars(data_dir / f"{s}.parquet"), AS_OF, spec, CALENDAR, symbol=s)
                     for s in spec.symbols]
        doc = te.build_target(decisions, spec, te.RebalanceWindow())
        base = doc["base_notional_idr"]
        lo, hi = te.price_band(lots)
        for t in doc["targets"]:
            for frac in (lo, 1.0, 1.15, hi):
                price = closes[t["symbol"]] * frac
                got = _go_lots(base, t["weight"], price)
                assert got == (lots if t["weight"] > 0 else 0), (t, frac, got)

    def test_single_symbol_keeps_weight_one_and_the_one_lot_base(self, tmp_path):
        spec = replace(MULTI, symbols=("AAAA",))
        data_dir = tmp_path / "daily"
        closes = _write_universe(data_dir)
        dec = te.decide(te.load_local_bars(data_dir / "AAAA.parquet"), AS_OF, spec, CALENDAR)
        doc = te.build_target([dec], spec, te.RebalanceWindow())
        assert doc["targets"] == [{"symbol": "AAAA", "weight": 1.0}]
        assert doc["base_notional_idr"] == math.ceil(1.3 * closes["AAAA"] * 100)

    def test_all_flat_day_still_emits_a_valid_zero_target(self, tmp_path):
        data_dir = tmp_path / "daily"
        data_dir.mkdir()
        for sym in MULTI.symbols:
            _bars(LAST, _closes_with_up_rows(N, set())).to_parquet(data_dir / f"{sym}.parquet")
        rc, out = _emit(tmp_path)
        doc = json.loads(out.read_text())
        assert rc == 0 and all(t["weight"] == 0.0 for t in doc["targets"])
        assert doc["base_notional_idr"] > 0

    def test_one_stale_symbol_refuses_the_whole_file(self, tmp_path, capsys):
        data_dir = tmp_path / "daily"
        _write_universe(data_dir)
        old = _bars(CALENDAR.prev_trading_day(LAST), _closes_with_up_rows(N, {38}))
        old.to_parquet(data_dir / "BBBB.parquet")
        rc, out = _emit(tmp_path)
        assert rc == te.EXIT_DATA_ERROR and not out.exists()
        assert "BBBB" in capsys.readouterr().err

    def test_missing_symbol_file_is_a_data_error(self, tmp_path, capsys):
        data_dir = tmp_path / "daily"
        _write_universe(data_dir)
        (data_dir / "CCCC.parquet").unlink()
        rc, out = _emit(tmp_path)
        assert rc == te.EXIT_DATA_ERROR and "CCCC" in capsys.readouterr().err

    def test_output_is_byte_identical_across_runs(self, tmp_path):
        _, out = _emit(tmp_path)
        first = out.read_bytes(), te.sidecar_path(out).read_bytes()
        _emit(tmp_path)
        assert (out.read_bytes(), te.sidecar_path(out).read_bytes()) == first

    def test_sidecar_has_one_entry_per_symbol_with_reasons(self, tmp_path):
        _, out = _emit(tmp_path)
        side = json.loads(te.sidecar_path(out).read_text())
        assert [s["symbol"] for s in side["symbols"]] == ["AAAA", "BBBB", "CCCC"]
        assert all(s["reason"] for s in side["symbols"])
        assert side["lots_per_name"] == 1 and side["rebalance_at"] == "09:30"
        assert "symbols" not in json.loads(out.read_text())


# ---------------------------------------------------------------------------
# Printed strategy-run command
# ---------------------------------------------------------------------------

class TestPrintedCommand:
    def _cmd_line(self, capsys) -> str:
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("live-session command: ")]
        assert len(lines) == 1
        return lines[0][len("live-session command: "):]

    def test_candidate_prints_a_live_session_live_sink_command(self, tmp_path, capsys):
        rc, out = _emit(tmp_path, "--rebalance-at", "09:45")
        assert rc == 0
        cmd = self._cmd_line(capsys)
        assert "\n" not in cmd
        assert cmd.split(" ")[0].endswith("/bin/live-session")
        assert "go run" not in cmd and "strategy-run" not in cmd
        assert "--sink live" in cmd and '--account "$ACCOUNT"' in cmd and '--equity-idr "$EQUITY_IDR"' in cmd
        assert f"--target-portfolio {out.resolve()}" in cmd
        assert "--rebalance-at 09:45" in cmd
        assert "--symbols AAAA,BBBB,CCCC" in cmd
        # live-session names its own stores and prior-stores: the emitter must not print them
        assert "--store" not in cmd and "--prior-stores" not in cmd
        for flag in ("--target-portfolio", "--runs-dir"):
            val = cmd.split(flag + " ")[1].split(" ")[0].strip("'")
            assert os.path.isabs(val), (flag, val)

    def test_paper_spec_prints_only_a_paper_command(self, tmp_path, capsys):
        reg = {"test_paper": replace(MULTI, strategy_id="test_paper", status=es.STATUS_PAPER)}
        rc, _ = _emit(tmp_path, strategy="test_paper", registry=reg)
        assert rc == 0
        cmd = self._cmd_line(capsys)
        assert "--sink paper --source live" in cmd
        assert "--sink live" not in cmd and "--account" not in cmd and "ACCOUNT" not in cmd

    def test_command_argv_for_a_candidate_is_refused(self):
        with pytest.raises(te.EmitterError):
            te.live_session_argv(MULTI, Path("/tmp/x.json"), te.DEFAULT_WINDOW)

    def test_default_1500_is_never_printed(self, tmp_path, capsys):
        _emit(tmp_path)
        assert "15:00" not in self._cmd_line(capsys)

    def test_strategy_run_is_not_executed(self, tmp_path, monkeypatch):
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("subprocess run"))
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("subprocess Popen"))
        assert _emit(tmp_path)[0] == 0


# ---------------------------------------------------------------------------
# Live source stays optional, env-only
# ---------------------------------------------------------------------------

class TestLiveSource:
    def test_live_without_token_fails_before_writing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SB_BEARER_TOKEN", raising=False)
        rc, out = _emit(tmp_path, "--source", "live")
        assert rc == te.EXIT_NO_TOKEN and "SB_BEARER_TOKEN" in capsys.readouterr().err
        assert not out.exists()


# ---------------------------------------------------------------------------
# Golden fixtures (also parsed by the Go test)
# ---------------------------------------------------------------------------

def _golden_case(tmp: Path, name: str, spec: es.EmitterSpec) -> dict[str, bytes]:
    data_dir = tmp / "daily"
    _write_universe(data_dir)
    out = tmp / f"{name}.json"
    rc = te.main(["emit", "--strategy", spec.strategy_id, "--as-of", AS_OF.isoformat(),
                  "--out", str(out), "--data-dir", str(data_dir), "--rebalance-at", "09:30"],
                 registry={spec.strategy_id: spec})
    assert rc == 0
    return {f"{name}.json": out.read_bytes(), f"{name}.signal.json": te.sidecar_path(out).read_bytes()}


GOLDEN_SPECS = {
    "emitted_multi_symbol": MULTI,
    "emitted_single_symbol": replace(MULTI, strategy_id="test_single", symbols=("AAAA",)),
}


class TestGolden:
    @pytest.mark.parametrize("name", sorted(GOLDEN_SPECS))
    def test_emitter_output_matches_committed_fixture(self, tmp_path, name):
        got = _golden_case(tmp_path, name, GOLDEN_SPECS[name])
        if os.environ.get("UPDATE_GOLDEN") == "1":
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            for fname, data in got.items():
                (FIXTURE_DIR / fname).write_bytes(data)
        for fname, data in got.items():
            assert (FIXTURE_DIR / fname).read_bytes() == data, (
                f"{fname} drifted; if intended: UPDATE_GOLDEN=1 pytest strategy_fit/test_target_emitter_job.py")
