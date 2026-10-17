"""
recheck/test_specs.py — the frozen spec set, and the poison test: no pre-verdict data enters a window.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from recheck import fixtures as FX
from recheck import loaders
from recheck import specs as SP
from recheck.stats import RECORD_COLUMNS
from strategy_fit.catalog_meta import ALL_FAMILIES
from strategy_fit.study_verdicts import load_verdicts

FLOAT_NOISE_BPS = 1e-2
"""factor_books / dividend returns subtract two running dividend cumsums; garbage 1e12 dividends far before the
window cancel mathematically but leave ~up to ~1e-3 bps of float noise. 1e-2 bps is three orders below any real edge."""

pytestmark = pytest.mark.filterwarnings("ignore:invalid value encountered")

PINNED_HOLDS = {
    "daily_momentum": 5, "mean_reversion": 1, "event_driven_gap": 2, "volume_breakout": 2,
    "volume_spike_event": 2, "foreign_flow_spike_event": 5, "cross_sectional": 5, "factor_books": 21,
    "limit_move_events": 5, "dividend_events": 5, "calendar_effects": 3,
}


# -- the spec set ---------------------------------------------------------------------------------

def test_k_is_a_literal_pinned_to_the_number_of_specs():
    assert SP.K == len(SP.SPECS) == 11


def test_holds_are_frozen_one_per_family():
    assert {s.family_id: s.hold for s in SP.SPECS} == PINNED_HOLDS
    assert all(s.entry == "next_close" for s in SP.SPECS)


def test_unit_hold_is_explicit_period_families_are_one_unit_per_period():
    unit = {s.family_id: s.unit_hold for s in SP.SPECS}
    assert unit["cross_sectional"] == unit["factor_books"] == unit["calendar_effects"] == 1
    pooled = [s for s in SP.SPECS if s.family_id not in ("cross_sectional", "factor_books", "calendar_effects")]
    assert all(s.unit_hold == s.hold for s in pooled)


def test_the_frozen_bar_constants():
    assert (SP.N_LOOKS, SP.MILESTONES, SP.ALPHA_FAMILY) == (3, (30, 60, 120), 0.10)


def test_every_tested_family_is_in_exactly_one_of_specs_or_excluded_and_untested_in_neither():
    loaded = load_verdicts()
    assert loaded.problems == ()
    specced = {s.family_id for s in SP.SPECS}
    excluded = set(SP.EXCLUDED)
    assert not specced & excluded
    for m in ALL_FAMILIES:
        tested = loaded.outcome(m.family_id) != "untested"
        n = (m.family_id in specced) + (m.family_id in excluded)
        assert n == (1 if tested else 0), (m.family_id, tested, n)
    assert specced | excluded <= {m.family_id for m in ALL_FAMILIES}


def test_excluded_reasons_state_the_plan_wording():
    assert SP.EXCLUDED["foreign_selling_rebound"].startswith("excluded (owner, 2026-10-17)")
    assert SP.EXCLUDED["indicator_rules"].startswith("excluded (owner, 2026-10-17)")
    assert SP.EXCLUDED["pairs_stat_arb"].startswith("skipped")
    assert all(SP.EXCLUDED[f].startswith("waiting") for f in ("index_rebalance_deletion", "obi_ofi_tick", "intraday_momentum"))


# -- records and the poison test -----------------------------------------------------------------

@pytest.fixture(scope="module")
def store(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("clean"))
    dates = FX.write_store(path)
    return path, dates


def _window(dates):
    return date.fromisoformat(dates[FX.VERDICT_IDX]), date.fromisoformat(dates[-1])


def _records(spec, path, dates):
    decided, as_of = _window(dates)
    return spec.build(path, decided, as_of)


@pytest.mark.parametrize("spec", SP.SPECS, ids=lambda s: s.family_id)
def test_records_are_post_verdict_and_well_formed(spec, store):
    path, dates = store
    decided, _ = _window(dates)
    rec = _records(spec, path, dates)
    assert set(RECORD_COLUMNS) <= set(rec.columns)
    if rec.empty:
        pytest.skip(f"{spec.family_id}: no records on the small fixture")
    assert (rec["signal_date"] > decided.isoformat()).all()
    assert (rec["entry_date"] > rec["signal_date"]).all()
    assert rec[["gross_bps", "null_bps", "cost_bps"]].notna().all().all()
    assert (rec["cost_bps"] > 0).all()


def test_the_fixture_exercises_most_families(store):
    path, dates = store
    non_empty = {s.family_id for s in SP.SPECS if not _records(s, path, dates).empty}
    assert len(non_empty) >= 9, sorted(non_empty)


def _compare_cols(rec: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in rec.columns if c != "regime_bear"]
    sort = [c for c in ("signal_date", "symbol", "entry_date") if c in cols]
    return rec[cols].sort_values(sort).reset_index(drop=True)


@pytest.mark.parametrize("mode", ["nan", "garbage"])
def test_poison_pre_verdict_bars_outside_the_lookback_change_nothing(mode, store, tmp_path):
    """Every bar BEFORE (first post-verdict bar - spec.lookback_bars) is NaN or garbage in the poisoned store.

    Why not every bar on or before the verdict: a signal needs trailing bars (a 60-bar impact window, a 252-bar
    momentum rank), so the bars just before the verdict are read to compute the first post-verdict signals;
    poisoning them would change the first signals by construction. What must hold, and is asserted here, is
    that (a) signal dates and entry dates are all strictly after the verdict (checked on the clean store in
    ``test_records_are_post_verdict_and_well_formed``), and (b) nothing OLDER than the family's declared
    lookback reaches the records, so the lookback bound in the spec is honest. The regime flag is excluded
    from the comparison: it is descriptive only and has unbounded memory by design."""
    path, dates = store
    first_post = FX.VERDICT_IDX + 1
    for spec in SP.SPECS:
        dst = str(tmp_path / f"{mode}_{spec.family_id}")
        FX.poison(path, dst, first_post - spec.lookback_bars, mode)
        clean = _records(spec, path, dates)
        dirty = _records(spec, dst, dates)
        pd.testing.assert_frame_equal(_compare_cols(clean), _compare_cols(dirty), check_exact=False, rtol=1e-9, atol=FLOAT_NOISE_BPS,
                                      obj=f"{spec.family_id} [{mode}]")


def test_poison_test_has_teeth_a_shorter_lookback_than_declared_is_caught(store, tmp_path):
    path, dates = store
    spec = SP.spec_for("daily_momentum")
    dst = str(tmp_path / "teeth")
    FX.poison(path, dst, FX.VERDICT_IDX + 1 - 10, "garbage")       # poison inside the 61-bar lookback
    clean, dirty = _records(spec, path, dates), _records(spec, dst, dates)
    assert not _compare_cols(clean).equals(_compare_cols(dirty))


def test_nothing_after_as_of_is_read(store, tmp_path):
    path, dates = store
    decided, _ = _window(dates)
    early = date.fromisoformat(dates[FX.VERDICT_IDX + 90])
    spec = SP.spec_for("daily_momentum")
    rec = spec.build(path, decided, early)
    assert rec["entry_date"].max() <= early.isoformat()
    assert loaders.latest_bar_date(path) == date.fromisoformat(dates[-1])


def test_trading_calendar_is_the_stores_sorted_trading_dates(store):
    path, dates = store
    assert loaders.trading_calendar(path, date.fromisoformat(dates[-1])) == dates
    assert loaders.trading_calendar(path, date.fromisoformat(dates[100])) == dates[:101]
