"""
strategy_fit/test_long_only.py — Tests for the shared long-only enforcement
helper (strategy_fit/long_only.py).

This module is the single place the "IDX retail accounts cannot short-sell"
constraint (commit c62eba8) is implemented, so every directional family
(families_intraday.py, families_single_name.py, families_event.py) can share
one behaviour instead of re-implementing (and potentially mis-implementing)
it independently. These tests pin down that shared contract directly; the
per-family regression tests (test_intraday.py, test_families_single_name.py,
test_families_event.py, test_families_cross_sectional.py,
test_families_pairs.py) verify each family actually calls it.
"""

from __future__ import annotations

import numpy as np
import pytest

from strategy_fit.long_only import (
    STATUS_LONG_SHORT_INFEASIBLE,
    is_long_signal,
    long_only_mask,
    long_short_infeasible_result,
)


class TestIsLongSignal:
    def test_positive_signal_is_long(self):
        assert is_long_signal(1.0) is True
        assert is_long_signal(0.001) is True

    def test_negative_signal_is_not_long(self):
        assert is_long_signal(-1.0) is False
        assert is_long_signal(-0.001) is False

    def test_zero_signal_is_not_long(self):
        """Flat (zero) signal is not a buyable trade — must be excluded, not
        treated as tradeable."""
        assert is_long_signal(0.0) is False


class TestLongOnlyMask:
    def test_mask_selects_only_positive_signals(self):
        signals = np.array([1.0, -1.0, 0.5, -0.5, 0.0])
        mask = long_only_mask(signals)
        np.testing.assert_array_equal(mask, [True, False, True, False, False])

    def test_mask_can_be_applied_to_aligned_array(self):
        """The mask must be directly usable to filter BOTH the signal array
        and any aligned forward-return array — a negative-signal
        observation must never contribute to a scored quantity."""
        signals = np.array([1.0, -1.0, 1.0, -1.0])
        forward_returns = np.array([0.01, -0.02, 0.03, -0.04])
        mask = long_only_mask(signals)
        kept_returns = forward_returns[mask]
        np.testing.assert_array_equal(kept_returns, [0.01, 0.03])


class TestLongShortInfeasibleResult:
    def test_status_is_long_short_infeasible(self):
        result = long_short_infeasible_result()
        assert result["status"] == STATUS_LONG_SHORT_INFEASIBLE

    def test_skip_reason_mentions_short_selling(self):
        result = long_short_infeasible_result()
        reason = (result["skip_reason"] or "").lower()
        assert "short" in reason

    def test_reason_detail_is_prepended(self):
        result = long_short_infeasible_result(reason_detail="Pair XYZ found.")
        assert result["skip_reason"].startswith("Pair XYZ found.")

    def test_scored_metrics_are_none(self):
        """A long_short_infeasible result must never carry a scored, tradeable
        number — a downstream consumer must be unable to mistake this for a
        deployable result."""
        result = long_short_infeasible_result()
        for key in ("hit_rate", "gross_edge_bps", "net_edge_bps", "p_value"):
            assert result[key] is None, f"{key} must be None, got {result[key]}"

    def test_eff_n_defaults_to_none_but_can_be_overridden(self):
        assert long_short_infeasible_result()["eff_n"] is None
        assert long_short_infeasible_result(eff_n=42)["eff_n"] == 42

    def test_extra_none_keys_are_merged_in(self):
        """Callers can add family-specific keys (e.g. ic/ic_tstat/icir for
        cross-sectional) so the result satisfies that family's own
        required-keys contract."""
        result = long_short_infeasible_result(ic=None, ic_tstat=None, icir=None)
        assert set(["ic", "ic_tstat", "icir"]).issubset(result.keys())
        assert result["ic"] is None and result["ic_tstat"] is None and result["icir"] is None
