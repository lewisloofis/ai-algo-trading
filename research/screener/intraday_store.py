"""
screener/intraday_store.py — Per-symbol intraday bar accrual store (parquet).

Provides an append-merge store for 1-min intraday bars so that the
intraday walk-forward validator (strategy_fit/intraday_walk_forward.py)
can accumulate history beyond the 90-day chartbit window cap.

Public API
----------
snapshot_intraday(symbols, store_dir, data_source) -> dict[str, AccrualSummary]
    Pull intraday history from data_source and merge-dedup into the store.

load_intraday(symbol, store_dir) -> pd.DataFrame | None
    Load the full accrued history for a symbol; None when absent.

IntraStorePrefDataSource(live_ds, store_dir)
    DataSource wrapper: returns the store when it has more distinct trading
    days than the live pull, otherwise falls through to the live pull.

CLI
---
    python -m screener.intraday_store snapshot [--symbols PTRO,BRPT,...] \
        [--store-dir data/intraday]

Runs daily after market close (~16:30 WIB):
    30 16 * * 1-5 cd /path/to/research && \
        python -m screener.intraday_store snapshot >> logs/intraday_store.log 2>&1

Security note: the live data path requires SB_BEARER_TOKEN.  Tests MUST mock
_build_live_data_source — never pass a real token through the test suite.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default symbols: the four intraday ORB survivors from plan-10 live run.
DEFAULT_SNAPSHOT_SYMBOLS: tuple[str, ...] = ("PTRO", "BRPT", "TPIA", "ENRG")

# Parquet engine — pyarrow is available (confirmed 18.1.0).
_PARQUET_ENGINE = "pyarrow"

# Dedup key: datetime column (unix_timestamp is dropped by _normalise_intraday).
_DEDUP_COL = "datetime"

# Required output columns from LiveDataSource.intraday_history (DataSource Protocol).
_REQUIRED_COLS = frozenset(
    ["datetime", "date", "open", "high", "low", "close", "volume", "value", "net_foreign"]
)


# ---------------------------------------------------------------------------
# AccrualSummary dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccrualSummary:
    """Per-symbol result from a single snapshot_intraday call."""
    symbol: str
    rows_before: int
    rows_after: int
    new_rows: int
    date_range: tuple[str | None, str | None]


# ---------------------------------------------------------------------------
# Core persistence helpers
# ---------------------------------------------------------------------------

def _store_path(symbol: str, store_dir: str) -> str:
    """Return the parquet file path for a symbol."""
    return os.path.join(store_dir, f"{symbol}.parquet")


def _load_existing(symbol: str, store_dir: str) -> pd.DataFrame | None:
    """
    Load the existing store for a symbol.
    Returns None when the file is absent.  Does NOT raise on I/O errors
    but logs them; the caller treats a load error as if the store is absent.
    """
    path = _store_path(symbol, store_dir)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path, engine=_PARQUET_ENGINE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load store for %s (%s); treating as absent", symbol, exc)
        return None


def _merge_dedup(existing: pd.DataFrame | None, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge existing and new DataFrames, dedup by datetime, sort ascending.

    Keeps the row from new_df when the same datetime appears in both
    (new_df takes precedence — consistent with "keep last" semantics for
    any late-arriving corrections).
    """
    if new_df is None or new_df.empty:
        return existing if existing is not None else pd.DataFrame()

    if existing is None or existing.empty:
        merged = new_df.copy()
    else:
        # concat → dedup (keep last occurrence → new_df wins)
        merged = pd.concat([existing, new_df], ignore_index=True)

    if _DEDUP_COL not in merged.columns:
        # No dedup column — return as-is, callers handle this edge case
        return merged.sort_values(_DEDUP_COL, ignore_index=True) if _DEDUP_COL in merged.columns else merged

    merged = (
        merged
        .drop_duplicates(subset=[_DEDUP_COL], keep="last")
        .sort_values(_DEDUP_COL, ignore_index=True)
    )
    return merged


def _date_range(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Return (earliest_date_str, latest_date_str) from the stored DataFrame."""
    if df is None or df.empty or _DEDUP_COL not in df.columns:
        return (None, None)
    dts = pd.to_datetime(df[_DEDUP_COL])
    return (str(dts.min().date()), str(dts.max().date()))


# ---------------------------------------------------------------------------
# Public snapshot API
# ---------------------------------------------------------------------------

def snapshot_intraday(
    symbols: list[str] | tuple[str, ...],
    store_dir: str,
    data_source: Any,
) -> dict[str, AccrualSummary]:
    """
    Pull intraday history for each symbol and merge-dedup into the store.

    Parameters
    ----------
    symbols:      List of ticker symbols to snapshot.
    store_dir:    Directory for per-symbol parquet files.  Created if absent.
    data_source:  Any object implementing DataSource.intraday_history(symbol).

    Returns
    -------
    dict mapping symbol → AccrualSummary (rows_before/after/new, date_range).
    """
    os.makedirs(store_dir, exist_ok=True)

    summaries: dict[str, AccrualSummary] = {}

    for symbol in symbols:
        existing = _load_existing(symbol, store_dir)
        rows_before = len(existing) if existing is not None else 0

        # Pull fresh data; defensive: any exception → treat as empty pull
        try:
            fresh = data_source.intraday_history(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intraday_history(%s) raised %s; skipping merge", symbol, exc)
            fresh = pd.DataFrame()

        merged = _merge_dedup(existing, fresh)

        if not merged.empty:
            path = _store_path(symbol, store_dir)
            try:
                merged.to_parquet(path, engine=_PARQUET_ENGINE, index=False)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to write store for %s: %s", symbol, exc)

        rows_after = len(merged)
        new_rows = max(0, rows_after - rows_before)
        dr = _date_range(merged)

        summaries[symbol] = AccrualSummary(
            symbol=symbol,
            rows_before=rows_before,
            rows_after=rows_after,
            new_rows=new_rows,
            date_range=dr,
        )

        logger.info(
            "%s: %d → %d rows (+%d new), %s – %s",
            symbol,
            rows_before,
            rows_after,
            new_rows,
            dr[0],
            dr[1],
        )

    return summaries


# ---------------------------------------------------------------------------
# Public load API
# ---------------------------------------------------------------------------

def load_intraday(symbol: str, store_dir: str) -> pd.DataFrame | None:
    """
    Load the full accrued intraday history for a symbol.

    Returns
    -------
    pd.DataFrame or None when the store file does not exist.
    """
    return _load_existing(symbol, store_dir)


# ---------------------------------------------------------------------------
# IntraStorePrefDataSource — store-aware DataSource wrapper
# ---------------------------------------------------------------------------

class IntraStorePrefDataSource:
    """
    DataSource wrapper that prefers the accrued store when it has more
    distinct trading days than the live intraday pull.

    All other DataSource methods (funding_universe, daily_history, etc.)
    delegate transparently to the wrapped live_ds.

    Design: strategy_fit/run_strategy_fit.py wraps its ds in this class so
    that the intraday walk-forward validator benefits from longer history
    automatically, without changing any other code path.
    """

    def __init__(self, live_ds: Any, store_dir: str) -> None:
        self._live_ds = live_ds
        self._store_dir = store_dir

    def intraday_history(
        self, symbol: str, lookback_days: int = 90
    ) -> pd.DataFrame:
        """
        Return whichever source (store or live) has more distinct trading days.

        Tie-breaks in favour of live (equal days → live).
        """
        stored = load_intraday(symbol, store_dir=self._store_dir)
        try:
            live = self._live_ds.intraday_history(symbol, lookback_days=lookback_days)
        except TypeError:
            # Some mock/fixture sources don't accept lookback_days keyword
            try:
                live = self._live_ds.intraday_history(symbol)
            except Exception:  # noqa: BLE001
                live = pd.DataFrame()
        except Exception:  # noqa: BLE001
            live = pd.DataFrame()

        store_days = _distinct_days(stored)
        live_days = _distinct_days(live)

        if store_days > live_days:
            logger.debug(
                "%s: store preferred (%d days vs live %d)", symbol, store_days, live_days
            )
            return stored  # type: ignore[return-value]

        return live if live is not None else pd.DataFrame()

    # --- pass-through for all other DataSource methods -----------------------

    def funding_universe(self) -> set[str]:
        return self._live_ds.funding_universe()

    def daily_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._live_ds.daily_history(symbol, start, end)

    def __getattr__(self, name: str) -> Any:
        """Forward any other attribute to the wrapped data source."""
        return getattr(self._live_ds, name)


def _distinct_days(df: pd.DataFrame | None) -> int:
    """Return the number of distinct calendar dates in df, or 0 for empty/None."""
    if df is None or df.empty:
        return 0
    if "date" in df.columns:
        return int(df["date"].nunique())
    if _DEDUP_COL in df.columns:
        return int(pd.to_datetime(df[_DEDUP_COL]).dt.date.nunique())
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_live_data_source(bearer_token: str | None = None) -> Any:
    """
    Build a LiveDataSource from the screener package.
    The SB_BEARER_TOKEN env var must be set.  Tests patch this function.
    """
    from screener.screen_universe import LiveDataSource  # local import to keep mock seam clean
    token = bearer_token or os.environ.get("SB_BEARER_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SB_BEARER_TOKEN environment variable is required for live snapshots.\n"
            "Set it and retry, or use --source fixture for offline testing."
        )
    return LiveDataSource(bearer_token=token)


def main(argv: list[str] | None = None) -> None:
    """
    CLI entrypoint: python -m screener.intraday_store snapshot [options]

    Suggested cron (daily post-close, 16:30 WIB = 09:30 UTC):
        30 9 * * 1-5 cd /path/to/research && \
            python -m screener.intraday_store snapshot >> logs/intraday_store.log 2>&1
    """
    parser = argparse.ArgumentParser(
        prog="python -m screener.intraday_store",
        description="Accrue intraday bars for strategy-fit symbols into a local parquet store.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Pull and merge intraday bars for symbols")
    snap.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SNAPSHOT_SYMBOLS),
        help=(
            "Comma-separated ticker list "
            f"(default: {','.join(DEFAULT_SNAPSHOT_SYMBOLS)})"
        ),
    )
    snap.add_argument(
        "--store-dir",
        default="data/intraday",
        help="Directory for parquet store files (default: data/intraday)",
    )
    snap.add_argument(
        "--bearer-token",
        default=None,
        help="SB bearer token (falls back to SB_BEARER_TOKEN env var)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.command == "snapshot":
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        ds = _build_live_data_source(bearer_token=args.bearer_token)
        summaries = snapshot_intraday(symbols, store_dir=args.store_dir, data_source=ds)
        for sym, s in summaries.items():
            print(
                f"  {sym}: {s.rows_before} → {s.rows_after} rows "
                f"(+{s.new_rows} new) | {s.date_range[0]} – {s.date_range[1]}"
            )


if __name__ == "__main__":
    main()
