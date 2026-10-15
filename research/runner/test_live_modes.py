"""LIVE mode decision matrix and arm-file validation. The mode decision is the single gate."""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from runner import arm, modes
from runner.conftest import CAND_SPEC, DAY, PAPER_SPEC, RETIRED_SPEC, REGISTRY
from strategy_fit import emitter_specs as es

TOMORROW = date(2026, 10, 16)       # a Friday, trading day


def write_arm(runs_dir, day=DAY, strategies=None, doc_day=None):
    (runs_dir / "arm").mkdir(parents=True, exist_ok=True)
    p = runs_dir / "arm" / f"ARM-{day}.json"
    p.write_text(json.dumps({"date": str(doc_day or day),
                             "strategies": strategies if strategies is not None else {"cand_x": {"max_idr": 500_000}}}))
    return p


def gate_for(runs_dir, *, day=DAY, today=DAY, kill=False, preflight=True, problems=(), started=()):
    return modes.LiveGate(day=day, today=today, arm=arm.read_arm_file(runs_dir, day), kill_present=kill,
                          preflight_ok=preflight, live_problems=tuple(problems), already_started=frozenset(started))


# ---- the decision matrix ---------------------------------------------------------------------

def test_candidate_goes_live_only_when_every_condition_holds(tmp_path):
    write_arm(tmp_path)
    m = modes.decide_mode(CAND_SPEC, gate_for(tmp_path))
    assert m.mode == modes.LIVE and m.token.strategy_id == "cand_x" and m.token.max_idr == 500_000
    assert m.token.day == DAY


@pytest.mark.parametrize("status", [es.STATUS_RETIRED, es.STATUS_EXAMPLE])
def test_non_candidate_non_paper_never_live_even_if_armed(tmp_path, status):
    write_arm(tmp_path, strategies={"cand_x": {"max_idr": 500_000}})
    spec = replace(CAND_SPEC, status=status)
    m = modes.decide_mode(spec, gate_for(tmp_path))
    assert m.mode == modes.SKIP and m.token is None


def test_paper_spec_is_paper_and_can_never_be_live_even_if_the_arm_file_names_it(tmp_path):
    write_arm(tmp_path, strategies={"paper_x": {"max_idr": 500_000}})
    m = modes.decide_mode(PAPER_SPEC, gate_for(tmp_path))
    assert m.mode == modes.PAPER and m.token is None
    assert "never go live" in m.reason


@pytest.mark.parametrize("name,arm_kw,gate_kw,why", [
    ("no arm file", dict(write=False), {}, "not armed"),
    ("arm for another strategy", dict(strategies={"other": {"max_idr": 1}}), {}, "not armed"),
    ("arm file for another date inside", dict(doc_day=TOMORROW), {}, "invalid"),
    ("arm file invalid cap", dict(strategies={"cand_x": {"max_idr": 0}}), {}, "invalid"),
    ("arm file over the ceiling", dict(strategies={"cand_x": {"max_idr": arm.HARD_MAX_IDR + 1}}), {}, "invalid"),
    ("kill switch present", {}, dict(kill=True), "kill switch"),
    ("preflight failed", {}, dict(preflight=False), "preflight"),
    ("trading token invalid", {}, dict(problems=("SB_TRADING_ACCESS_TOKEN is not set",)), "live preflight"),
    ("today is not the arm date", {}, dict(today=DAY - timedelta(days=1)), "actual date"),
    ("live already started today", {}, dict(started=("cand_x",)), "never restarted"),
])
def test_every_failed_condition_blocks_live_with_a_reason(tmp_path, name, arm_kw, gate_kw, why):
    if arm_kw.get("write", True):
        write_arm(tmp_path, **{k: v for k, v in arm_kw.items() if k != "write"})
    m = modes.decide_mode(CAND_SPEC, gate_for(tmp_path, **gate_kw))
    assert m.mode == modes.SKIP and m.token is None, name
    assert why in m.reason, (name, m.reason)


def test_arm_file_of_a_different_day_is_not_picked_up(tmp_path):
    write_arm(tmp_path, day=TOMORROW)                 # armed for tomorrow only
    m = modes.decide_mode(CAND_SPEC, gate_for(tmp_path, day=DAY))
    assert m.mode == modes.SKIP and "not armed" in m.reason


def test_a_misnamed_file_whose_inner_date_differs_is_invalid(tmp_path):
    write_arm(tmp_path, day=DAY, doc_day=DAY - timedelta(days=1))     # yesterday's content, today's name
    assert modes.decide_mode(CAND_SPEC, gate_for(tmp_path)).mode == modes.SKIP


def test_exhaustive_matrix_is_live_in_exactly_one_cell(tmp_path):
    """status x arm x date x kill x preflight x live-preflight: exactly one LIVE cell."""
    live_cells = []
    for status, armed, date_ok, kill, pre, problems in itertools.product(
            (es.STATUS_CANDIDATE, es.STATUS_PAPER, es.STATUS_RETIRED, es.STATUS_EXAMPLE),
            (True, False), (True, False), (True, False), (True, False), (True, False)):
        runs = tmp_path / f"{status}-{armed}-{date_ok}-{kill}-{pre}-{problems}"
        runs.mkdir()
        if armed:
            write_arm(runs, strategies={"cand_x": {"max_idr": 100_000}})
        spec = replace(CAND_SPEC, status=status)
        g = gate_for(runs, today=DAY if date_ok else DAY - timedelta(days=1), kill=kill, preflight=pre,
                     problems=("bad",) if problems else ())
        m = modes.decide_mode(spec, g)
        assert m.mode in (modes.PAPER, modes.SKIP, modes.LIVE)
        assert (m.mode == modes.LIVE) == (m.token is not None)
        if m.mode == modes.LIVE:
            live_cells.append((status, armed, date_ok, kill, pre, problems))
    assert live_cells == [(es.STATUS_CANDIDATE, True, True, False, True, False)]


def test_no_gate_means_a_candidate_is_skipped():
    m = modes.decide_mode(CAND_SPEC)
    assert m.mode == modes.SKIP and m.token is None
    assert modes.decide_mode(PAPER_SPEC).mode == modes.PAPER
    assert modes.decide_mode(RETIRED_SPEC).mode == modes.SKIP


# ---- the sealed token ------------------------------------------------------------------------

def test_a_live_token_cannot_be_built_by_hand():
    with pytest.raises(TypeError):
        modes.LiveToken("cand_x", DAY, 500_000)                      # type: ignore[call-arg]
    with pytest.raises(TypeError):
        modes.LiveToken(object(), "cand_x", DAY, 500_000)            # type: ignore[arg-type]


def test_a_live_token_is_immutable_and_not_copyable(tmp_path):
    import copy
    import pickle
    write_arm(tmp_path)
    tok = modes.decide_mode(CAND_SPEC, gate_for(tmp_path)).token
    with pytest.raises(AttributeError):
        tok.max_idr = 10 ** 9                                         # type: ignore[misc]
    with pytest.raises(TypeError):
        copy.copy(tok)
    with pytest.raises(TypeError):
        pickle.dumps(tok)


# ---- arm file validation ---------------------------------------------------------------------

def test_valid_arm_file_roundtrip(tmp_path):
    write_arm(tmp_path, strategies={"cand_x": {"max_idr": 1_000_000}})
    a = arm.read_arm_file(tmp_path, DAY)
    assert a.present and a.valid and a.max_idr == {"cand_x": 1_000_000} and a.day == DAY


def test_absent_arm_file(tmp_path):
    a = arm.read_arm_file(tmp_path, DAY)
    assert not a.present and not a.valid


@pytest.mark.parametrize("doc", [
    {"date": "2026-10-14", "strategies": {"cand_x": {"max_idr": 5}}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": -1}}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": 0}}},
    {"date": str(DAY), "strategies": {}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": True}}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": 1.5}}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": "500000"}}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": arm.HARD_MAX_IDR + 1}}},
    {"date": str(DAY), "strategies": {"cand_x": 500_000}},
    ["not", "a", "dict"],
])
def test_invalid_arm_files(tmp_path, doc):
    (tmp_path / "arm").mkdir()
    (tmp_path / "arm" / f"ARM-{DAY}.json").write_text(json.dumps(doc))
    a = arm.read_arm_file(tmp_path, DAY)
    assert a.present and not a.valid and a.problems and a.max_idr == {}


def test_arm_file_not_json_and_symlink_are_invalid(tmp_path):
    (tmp_path / "arm").mkdir()
    p = tmp_path / "arm" / f"ARM-{DAY}.json"
    p.write_text("{nope")
    assert not arm.read_arm_file(tmp_path, DAY).valid
    p.unlink()
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"date": str(DAY), "strategies": {"cand_x": {"max_idr": 5}}}))
    p.symlink_to(real)
    a = arm.read_arm_file(tmp_path, DAY)
    assert a.present and not a.valid


def test_hard_ceiling_is_within_the_measured_sleeve():
    from strategy_fit import sleeve_sizing
    assert 0 < arm.HARD_MAX_IDR <= sleeve_sizing.ACTIVE_SLEEVE_SIZING.equity_idr


# ---- writing / removing the arm file ---------------------------------------------------------

def test_write_arm_requires_a_candidate_spec(tmp_path):
    for sid in ("paper_x", "dead_x", "unknown"):
        with pytest.raises(arm.ArmError):
            arm.write_arm(tmp_path, DAY, sid, 100_000, registry=REGISTRY, today=DAY)
    assert not (tmp_path / "arm").exists()


@pytest.mark.parametrize("n", [0, -5, True, 1.5, "100", arm.HARD_MAX_IDR + 1])
def test_write_arm_refuses_bad_amounts(tmp_path, n):
    with pytest.raises(arm.ArmError):
        arm.write_arm(tmp_path, DAY, "cand_x", n, registry=REGISTRY, today=DAY)
    assert not (tmp_path / "arm" / f"ARM-{DAY}.json").exists()


def test_write_arm_accepts_the_ceiling_exactly_and_is_readable(tmp_path):
    arm.write_arm(tmp_path, DAY, "cand_x", arm.HARD_MAX_IDR, registry=REGISTRY, today=DAY)
    a = arm.read_arm_file(tmp_path, DAY)
    assert a.valid and a.max_idr == {"cand_x": arm.HARD_MAX_IDR}


def test_write_arm_refuses_past_dates_and_non_trading_days(tmp_path):
    with pytest.raises(arm.ArmError, match="past"):
        arm.write_arm(tmp_path, DAY - timedelta(days=1), "cand_x", 1, registry=REGISTRY, today=DAY)
    with pytest.raises(arm.ArmError, match="trading day"):
        arm.write_arm(tmp_path, date(2026, 10, 17), "cand_x", 1, registry=REGISTRY, today=DAY)


def test_write_arm_adds_a_second_strategy_and_replaces_the_same_one(tmp_path):
    reg = {**REGISTRY, "cand_y": replace(CAND_SPEC, strategy_id="cand_y", symbols=("DDDD",))}
    arm.write_arm(tmp_path, DAY, "cand_x", 100_000, registry=reg, today=DAY)
    arm.write_arm(tmp_path, DAY, "cand_y", 200_000, registry=reg, today=DAY)
    arm.write_arm(tmp_path, DAY, "cand_x", 300_000, registry=reg, today=DAY)
    assert arm.read_arm_file(tmp_path, DAY).max_idr == {"cand_x": 300_000, "cand_y": 200_000}


def test_write_arm_refuses_to_build_on_an_invalid_existing_file(tmp_path):
    write_arm(tmp_path, strategies={"cand_x": {"max_idr": 0}})
    with pytest.raises(arm.ArmError, match="invalid"):
        arm.write_arm(tmp_path, DAY, "cand_x", 100_000, registry=REGISTRY, today=DAY)


def test_disarm_removes_only_that_dates_file(tmp_path):
    write_arm(tmp_path, day=DAY)
    write_arm(tmp_path, day=TOMORROW)
    assert arm.disarm(tmp_path, DAY) is True
    assert not arm.arm_path(tmp_path, DAY).exists() and arm.arm_path(tmp_path, TOMORROW).exists()
    assert arm.disarm(tmp_path, DAY) is False


def test_arm_cli_arm_and_disarm(tmp_path, capsys):
    rc = arm.main(["arm", "--strategy", "cand_x", "--max-idr", "250000", "--date", str(DAY)],
                  runs_dir=tmp_path, registry=REGISTRY, today=DAY)
    out = capsys.readouterr().out
    assert rc == 0 and "ARMED: cand_x, max 250,000 IDR" in out and str(DAY) in out
    assert arm.read_arm_file(tmp_path, DAY).max_idr == {"cand_x": 250_000}
    rc = arm.main(["arm", "--strategy", "paper_x", "--max-idr", "1", "--date", str(DAY)],
                  runs_dir=tmp_path, registry=REGISTRY, today=DAY)
    assert rc == 2 and "candidate" in capsys.readouterr().err
    assert arm.main(["disarm", "--date", str(DAY)], runs_dir=tmp_path, registry=REGISTRY, today=DAY) == 0
    assert "disarmed" in capsys.readouterr().out and not arm.arm_path(tmp_path, DAY).exists()
    assert arm.main(["disarm", "--date", str(DAY)], runs_dir=tmp_path, registry=REGISTRY, today=DAY) == 0
    assert "nothing to disarm" in capsys.readouterr().out
