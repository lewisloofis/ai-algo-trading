"""
strategy_fit/test_sleeve_sizing.py — TDD tests for the ADR-0017 sleeve sizing
descriptor.

Validates strategy_fit/sleeve_sizing.py:
  - SleeveSizing.per_name_notional_idr derivation arithmetic
  - Preset consistency: every preset's per-name notional × N == its equity
  - Frozen-ness (immutability)
  - ACTIVE_SLEEVE_SIZING is one of the measured presets, not the void legacy one
"""
from __future__ import annotations

import dataclasses

import pytest

from strategy_fit.sleeve_sizing import (
    ACTIVE_SLEEVE_SIZING,
    ALL_PRESETS,
    LEGACY_ADR0011,
    SLEEVE_N1,
    SLEEVE_N3,
    SLEEVE_N5,
    SLEEVE_N10,
    SleeveSizing,
)


# ---------------------------------------------------------------------------
# Section A: derivation arithmetic
# ---------------------------------------------------------------------------

class TestPerNameNotionalDerivation:
    def test_per_name_notional_is_equity_over_concurrent_names(self) -> None:
        s = SleeveSizing(
            name="test",
            equity_idr=10_000_000.0,
            concurrent_names=4,
            measured_at="2026-01-01",
            source="unit test",
        )
        assert s.per_name_notional_idr == 2_500_000.0

    def test_single_concurrent_name_returns_full_equity(self) -> None:
        s = SleeveSizing(
            name="test",
            equity_idr=15_000_000.0,
            concurrent_names=1,
            measured_at="2026-01-01",
            source="unit test",
        )
        assert s.per_name_notional_idr == 15_000_000.0

    def test_more_concurrent_names_means_smaller_per_name_slice(self) -> None:
        few = SleeveSizing(
            name="few", equity_idr=15_000_000.0, concurrent_names=3,
            measured_at="2026-01-01", source="unit test",
        )
        many = SleeveSizing(
            name="many", equity_idr=15_000_000.0, concurrent_names=10,
            measured_at="2026-01-01", source="unit test",
        )
        assert many.per_name_notional_idr < few.per_name_notional_idr


# ---------------------------------------------------------------------------
# Section B: preset consistency
# ---------------------------------------------------------------------------

class TestPresetConsistency:
    @pytest.mark.parametrize("preset", [SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10, LEGACY_ADR0011])
    def test_per_name_times_n_equals_equity(self, preset: SleeveSizing) -> None:
        """Every preset's per-name notional × concurrent_names must reconstruct its equity."""
        reconstructed = preset.per_name_notional_idr * preset.concurrent_names
        assert abs(reconstructed - preset.equity_idr) < 1e-6, (
            f"{preset.name}: per_name({preset.per_name_notional_idr}) x "
            f"N({preset.concurrent_names}) = {reconstructed} != equity({preset.equity_idr})"
        )

    def test_measured_presets_share_the_same_measured_equity(self) -> None:
        """SLEEVE_N1/N3/N5/N10 all describe the SAME measured sleeve (15M,
        2026-09-14) at different assumed concurrent-name counts — only N
        differs, not the underlying measurement."""
        measured = [SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10]
        equities = {p.equity_idr for p in measured}
        measured_ats = {p.measured_at for p in measured}
        assert equities == {15_000_000.0}
        assert measured_ats == {"2026-09-14"}

    def test_legacy_preset_is_void_and_labelled_as_such(self) -> None:
        """LEGACY_ADR0011 is retained for comparability, not as a candidate sizing."""
        assert LEGACY_ADR0011.equity_idr == 300_000_000.0
        assert LEGACY_ADR0011.concurrent_names == 10
        assert LEGACY_ADR0011.per_name_notional_idr == 30_000_000.0
        assert "void" in LEGACY_ADR0011.source.lower() or "VOID" in LEGACY_ADR0011.source
        assert "void" in LEGACY_ADR0011.name.lower()

    def test_per_name_notional_strictly_decreases_with_n_for_measured_presets(self) -> None:
        """N=1 > N=3 > N=5 > N=10 in per-name notional, for the same equity."""
        ordered = [SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10]
        notionals = [p.per_name_notional_idr for p in ordered]
        assert notionals == sorted(notionals, reverse=True)

    def test_all_presets_lists_every_named_preset(self) -> None:
        assert set(ALL_PRESETS) == {SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10, LEGACY_ADR0011}


# ---------------------------------------------------------------------------
# Section C: frozen-ness
# ---------------------------------------------------------------------------

class TestFrozenness:
    def test_sleeve_sizing_is_frozen(self) -> None:
        s = SLEEVE_N5
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.equity_idr = 999.0  # type: ignore[misc]

    def test_frozen_instance_can_still_be_replaced_into_a_new_one(self) -> None:
        """Immutability means no in-place mutation, not that derived variants
        are impossible — dataclasses.replace must still work."""
        variant = dataclasses.replace(SLEEVE_N5, concurrent_names=7)
        assert variant.concurrent_names == 7
        assert variant.equity_idr == SLEEVE_N5.equity_idr
        assert SLEEVE_N5.concurrent_names == 5, "original preset must be untouched"


# ---------------------------------------------------------------------------
# Section D: active selection
# ---------------------------------------------------------------------------

class TestActiveSleeveSizing:
    def test_active_is_one_of_the_measured_presets(self) -> None:
        """The active default must be a real measured preset, not the void legacy one."""
        assert ACTIVE_SLEEVE_SIZING in (SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10)
        assert ACTIVE_SLEEVE_SIZING is not LEGACY_ADR0011

    def test_active_is_currently_n5(self) -> None:
        """Documented placeholder choice (ADR-0017 Open Questions) — pinned so
        a silent change is visible as a failing test, not a quiet drift."""
        assert ACTIVE_SLEEVE_SIZING is SLEEVE_N5
        assert ACTIVE_SLEEVE_SIZING.per_name_notional_idr == 3_000_000.0
