"""strategy_fit/test_local_source.py — offline parquet DataSource."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.local_source import LocalParquetDataSource


def _write(tmp_path, sym, n=10):
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": np.arange(n, dtype=float) + 100,
            "volume": 1000,
        }
    )
    df.to_parquet(tmp_path / f"{sym}.parquet")


def test_universe_is_the_explicit_list_that_has_files(tmp_path):
    _write(tmp_path, "AAAA")
    ds = LocalParquetDataSource(["aaaa", "ZZZZ"], tmp_path)
    assert ds.funding_universe() == {"AAAA"}


def test_daily_history_filters_dates_and_sorts(tmp_path):
    _write(tmp_path, "AAAA")
    ds = LocalParquetDataSource(["AAAA"], tmp_path)
    out = ds.daily_history("AAAA", "2025-01-03", "2025-01-07")
    assert out["date"].tolist() == ["2025-01-03", "2025-01-06", "2025-01-07"]


def test_intraday_is_unavailable_not_faked(tmp_path):
    ds = LocalParquetDataSource(["AAAA"], tmp_path)
    with pytest.raises(NotImplementedError):
        ds.intraday_history("AAAA")


def test_empty_symbol_list_rejected(tmp_path):
    with pytest.raises(ValueError):
        LocalParquetDataSource([], tmp_path)
