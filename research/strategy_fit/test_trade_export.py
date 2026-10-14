"""trade_export must reproduce the harness's own trade population, not approximate it."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.families_single_name import run_single_name_family
from strategy_fit.run_strategy_fit import SINGLE_NAME_SIGNAL_FNS
from strategy_fit.trade_export import ENTRY_USED, export_cell, export_cells, export_trades, export_with_baseline


def _bars(n: int = 1500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    dates = pd.bdate_range("2019-01-01", periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * (1 + rng.normal(0, 0.003, n)),
            "close": close,
            "volume": rng.integers(1_000, 100_000, n).astype(float),
        }
    )


@pytest.mark.parametrize("family", sorted(SINGLE_NAME_SIGNAL_FNS))
@pytest.mark.parametrize("hold", [1, 2, 5])
def test_matches_harness_gross_edge(family, hold):
    df = _bars()
    result = run_single_name_family(
        df, SINGLE_NAME_SIGNAL_FNS[family], "TEST", 50.0, family, hold
    )
    trades = export_trades(df, family, hold)
    if result["status"] != "tested":
        pytest.skip("too few trades on this synthetic series")
    mean_bps = np.mean([t.gross_return for t in trades]) * 1e4
    assert mean_bps == pytest.approx(result["gross_edge_bps"], abs=1e-6)
    _, always_long = export_with_baseline(df, family, hold)
    assert always_long * 1e4 == pytest.approx(result["null_edge_bps"], abs=1e-6)


def test_uses_next_session_entry():
    df = _bars()
    assert ENTRY_USED == "next_close"
    trades = export_trades(df, "daily_momentum", 2)
    assert trades
    for t in trades:
        i = df.index[df["date"] == t.signal_date][0]
        assert t.entry_date == df["date"].iloc[i + 1]
        assert t.exit_date == df["date"].iloc[i + 3]
        assert t.entry_price == df["close"].iloc[i + 1]


def test_trades_do_not_overlap():
    trades = export_trades(_bars(), "mean_reversion", 5)
    assert len(trades) > 30
    for a, b in zip(trades, trades[1:]):
        assert b.entry_date >= a.exit_date


def test_unknown_family_fails_loudly():
    with pytest.raises(KeyError):
        export_trades(_bars(), "no_such_family", 2)


def test_export_cell_reads_local_parquet(tmp_path):
    df = _bars()
    df["date"] = pd.bdate_range("2024-01-01", periods=len(df)).strftime("%Y-%m-%d")
    df.to_parquet(tmp_path / "TEST.parquet")
    cell = export_cell("TEST", "daily_momentum", 2, data_dir=tmp_path)
    assert cell.trades and list(cell.bars.columns) == ["date", "close"]
    many = export_cells(
        [("TEST", "daily_momentum", 2), ("TEST", "mean_reversion", 5)], data_dir=tmp_path
    )
    assert len(many) == 2
