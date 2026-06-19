"""
screen_universe.py — Tier A forward screener for the IDX algo-trading universe.

Three-layer selection: funding-eligible ∩ liquid ∩ not-tick-capped.
Deliberately excludes alpha/opportunity signals (those belong in Step 4).

Pipeline shape: fan-out per symbol (compute metrics) → barrier → rank.

Usage:
    python screener/screen_universe.py [--source fixture|live] [options]
    python screener/screen_universe.py --source fixture   (default, no token needed)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from screener.metrics import (
    max_position_for_budget,
    median_daily_turnover,
    min_spread_bps,
    realised_vol_bps,
    tick_size,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIXTURES_DIR = os.path.join(SCRIPT_DIR, "fixtures")

# Fee gate: round-trip cost in bps.  Single source of truth from backtest.
# Import lazily to avoid a hard dependency when only metrics are needed.
try:
    from backtest.backtest import FEE as _FEE
    BUY_BPS: float = _FEE["buy"] * 1e4   # 15.0
    SELL_BPS: float = _FEE["sell"] * 1e4  # 25.0
except ImportError:
    BUY_BPS, SELL_BPS = 15.0, 25.0  # fallback if backtest not on path
ROUND_TRIP_BPS: float = BUY_BPS + SELL_BPS  # 40.0

# EDGE_CAP_THRESHOLD_BPS: flag a symbol EDGE-CAPPED when its one-tick
# min_spread_bps exceeds this threshold.
#
# Rationale: we use the FULL round-trip fee gate (40 bps) not the half
# (20 bps).  The one-tick spread is the gross edge a passive maker can
# capture on the best-case fill.  If that gross edge (min_spread_bps) does
# not even clear the round-trip fee, the structural arithmetic is unfavourable
# regardless of signal quality.  Using the full gate is the conservative,
# consistent choice — it matches the language in plan 06 and the backtest
# report commentary.  Using half the gate would be a different (more
# permissive) filter.
EDGE_CAP_THRESHOLD_BPS: float = ROUND_TRIP_BPS  # 40.0 bps

# TOO_THIN_TURNOVER_IDR: minimum median daily value traded to be considered
# liquid enough for a retail-lot algo.
#
# PLACEHOLDER — pending derivation from position-size model
# (see plan 08 §7.1 and max_position_for_budget in metrics.py).
# Rule of thumb: a 100-lot position (10,000 shares) at a 2000 IDR stock is
# 20 M IDR.  A 1% participation rate implies ADV ≥ 2 B IDR.  This is an
# intentionally conservative starting floor; adjust after the first live run.
TOO_THIN_TURNOVER_IDR: float = 5_000_000_000.0  # 5 B IDR (PLACEHOLDER)

# Live API defaults
# TODO: confirm exact host and that HTTP/JSON transcoding is live with one
# manual read-only call (e.g. curl the catalog endpoint with a real token).
DEFAULT_API_HOST = os.environ.get("SB_API_HOST", "exodus.stockbit.com")
CATALOG_BASE_URL = f"https://{DEFAULT_API_HOST}/oms/catalog"
HISTORY_BASE_URL = f"https://{DEFAULT_API_HOST}/company-price-feed/historical/summary"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DataSource Protocol
# ---------------------------------------------------------------------------

class DataSource(Protocol):
    """
    Pluggable data source for the screener.

    Implementations: FixtureDataSource (offline), LiveDataSource (HTTP).
    """

    def funding_universe(self) -> set[str]:
        """Return union of DBX and MBX catalog memberships (the hard gate)."""
        ...

    def daily_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Return daily OHLCV bars.

        Columns required: date (str), close (float), volume (float).
        Optional: value (pre-computed daily IDR turnover — used by
        median_daily_turnover when present).
        """
        ...


# ---------------------------------------------------------------------------
# FixtureDataSource
# ---------------------------------------------------------------------------

class FixtureDataSource:
    """
    Reads from small CSV fixtures under ``fixtures_dir``.

    Catalog file: catalog.csv with columns: symbol, dbx, mbx (bool strings).
    Per-symbol file: {SYMBOL}.csv with columns: date, open, high, low, close, volume.

    Lines starting with '#' are treated as comments and ignored.
    """

    def __init__(self, fixtures_dir: str = DEFAULT_FIXTURES_DIR) -> None:
        self._dir = fixtures_dir

    def funding_universe(self) -> set[str]:
        path = os.path.join(self._dir, "catalog.csv")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Catalog fixture not found: {path}")
        symbols: set[str] = set()
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(
                (line for line in f if not line.lstrip().startswith("#"))
            ):
                if (
                    row.get("dbx", "").strip().lower() == "true"
                    or row.get("mbx", "").strip().lower() == "true"
                ):
                    symbols.add(row["symbol"].strip().upper())
        return symbols

    def daily_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        path = os.path.join(self._dir, f"{symbol.upper()}.csv")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Fixture file not found: {path}")
        # Strip comment lines before parsing
        lines = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.lstrip().startswith("#"):
                    lines.append(line)
        df = pd.read_csv(io.StringIO("".join(lines)))
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        # Filter to requested date range (inclusive)
        df["date"] = df["date"].astype(str)
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# LiveDataSource
# ---------------------------------------------------------------------------

class LiveDataSource:
    """
    Thin HTTP client against the Stockbit/Exodus API.

    Requires SB_BEARER_TOKEN env var (pre-obtained; this class does NOT
    perform login or token refresh — see docs/auth for that flow).

    TODO: confirm exact API host and that HTTP/JSON transcoding for the
    catalog and historical-summary endpoints is live by making one manual
    read-only call before relying on this in production.
    """

    def __init__(self, bearer_token: str | None = None) -> None:
        token = bearer_token or os.environ.get("SB_BEARER_TOKEN")
        if not token:
            raise EnvironmentError(
                "SB_BEARER_TOKEN env var must be set to use LiveDataSource"
            )
        self._token = token
        self._catalog_logged = False

    def _get(self, url: str) -> dict:
        """Make an authenticated GET request; return parsed JSON body."""
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def funding_universe(self) -> set[str]:
        """
        Fetch DBX and MBX catalog membership and return the union.

        The catalog endpoint returns CatalogData with a stock list and a
        catalog[] array.  On first call, logs the returned catalog labels
        so the operator can confirm the mapping.
        """
        symbols: set[str] = set()
        for catalog_type in ("CATALOG_TYPE_DBX", "CATALOG_TYPE_MBX"):
            url = f"{CATALOG_BASE_URL}?catalog_type={catalog_type}"
            try:
                body = self._get(url)
            except Exception as exc:
                logger.warning("catalog fetch failed for %s: %s", catalog_type, exc)
                continue
            data = body.get("data", {})
            if not self._catalog_logged:
                logger.info(
                    "catalog labels from API (first call): %s",
                    data.get("catalog", []),
                )
                self._catalog_logged = True
            for stock in data.get("stock", []):
                code = (stock.get("code") or "").strip().upper()
                if code:
                    symbols.add(code)
        return symbols

    def daily_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV summary bars from the historical-summary endpoint.

        Route: GET /company-price-feed/historical/summary/{symbol}
               ?start_date=<start>&end_date=<end>&period=daily
        """
        url = (
            f"{HISTORY_BASE_URL}/{symbol.upper()}"
            f"?start_date={start}&end_date={end}&period=daily"
        )
        body = self._get(url)
        records = body.get("data", {}).get("summaries", [])
        if not records:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(records)
        df["close"] = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# SymbolMetrics dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolMetrics:
    """
    Screening metrics for one symbol, computed from daily history.

    All fields are immutable.  Passed to rank_universe as a plain list.
    """
    symbol: str
    latest_price: float          # last closing price (IDR)
    tick_sz: int                 # IDX tick size at latest_price (IDR)
    min_spread_bps_val: float    # one-tick min spread in bps
    median_daily_turnover_idr: float  # median daily IDR value traded
    realised_vol_bps_val: float  # daily close-to-close vol in bps
    fundable: bool               # in DBX or MBX catalog


# ---------------------------------------------------------------------------
# Tag enum (strings, not an Enum, to stay JSON-friendly)
# ---------------------------------------------------------------------------

TAG_TRADEABLE = "TRADEABLE"
TAG_EDGE_CAPPED = "EDGE-CAPPED"
TAG_TOO_THIN = "TOO-THIN"


# ---------------------------------------------------------------------------
# Ranking dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RankedSymbol:
    """A symbol that passed all gates with its tag and rank."""
    rank: int
    symbol: str
    tag: str
    latest_price: float
    tick_sz: int
    min_spread_bps_val: float
    median_daily_turnover_idr: float
    realised_vol_bps_val: float


@dataclass(frozen=True)
class DroppedSymbol:
    """A symbol excluded from the tradeable list with its reason."""
    symbol: str
    reason: str
    detail: str


@dataclass(frozen=True)
class RankedUniverse:
    """Output of rank_universe — tradeable ranked list + dropped names."""
    tradeable: list[RankedSymbol]
    dropped: list[DroppedSymbol]


# ---------------------------------------------------------------------------
# Pure ranking function
# ---------------------------------------------------------------------------

def rank_universe(records: list[SymbolMetrics]) -> RankedUniverse:
    """
    Rank symbols into TRADEABLE, EDGE-CAPPED, and TOO-THIN tiers.

    Ranking rules (in order of priority):
    1. Non-fundable symbols are excluded entirely (never enter).
    2. EDGE-CAPPED if min_spread_bps >= EDGE_CAP_THRESHOLD_BPS (40 bps).
       Rationale: one tick must beat the round-trip fee gate; if it doesn't,
       the structural arithmetic is unfavourable regardless of signal quality.
    3. TOO-THIN if median_daily_turnover < TOO_THIN_TURNOVER_IDR.
       Note: a symbol can be both thin and edge-capped; EDGE-CAPPED wins
       (edge-cap is the harder structural constraint).
    4. Within TRADEABLE: sort by median_daily_turnover_idr descending
       (tie-break: realised_vol_bps_val descending, then symbol ascending).

    The returned tradeable list has contiguous ranks 1..N.
    Dropped symbols are annotated with reason + detail.

    This function is pure — it creates and returns new objects without
    mutating the input list.
    """
    tradeable: list[SymbolMetrics] = []
    dropped: list[DroppedSymbol] = []

    for m in records:
        if not m.fundable:
            dropped.append(DroppedSymbol(
                symbol=m.symbol,
                reason="NOT-FUNDABLE",
                detail="not in DBX or MBX catalog",
            ))
            continue

        edge_capped = m.min_spread_bps_val >= EDGE_CAP_THRESHOLD_BPS
        too_thin = m.median_daily_turnover_idr < TOO_THIN_TURNOVER_IDR

        if edge_capped:
            dropped.append(DroppedSymbol(
                symbol=m.symbol,
                reason=TAG_EDGE_CAPPED,
                detail=(
                    f"min_spread={m.min_spread_bps_val:.1f} bps >= "
                    f"gate={EDGE_CAP_THRESHOLD_BPS:.0f} bps "
                    f"(tick={m.tick_sz} IDR at price={m.latest_price:.0f})"
                ),
            ))
        elif too_thin:
            dropped.append(DroppedSymbol(
                symbol=m.symbol,
                reason=TAG_TOO_THIN,
                detail=(
                    f"median_turnover={m.median_daily_turnover_idr/1e9:.2f} B IDR < "
                    f"floor={TOO_THIN_TURNOVER_IDR/1e9:.1f} B IDR (PLACEHOLDER)"
                ),
            ))
        else:
            tradeable.append(m)

    # Sort TRADEABLE: primary turnover desc, secondary vol desc, tertiary symbol asc
    tradeable_sorted = sorted(
        tradeable,
        key=lambda m: (-m.median_daily_turnover_idr, -m.realised_vol_bps_val, m.symbol),
    )

    ranked_list = [
        RankedSymbol(
            rank=i + 1,
            symbol=m.symbol,
            tag=TAG_TRADEABLE,
            latest_price=m.latest_price,
            tick_sz=m.tick_sz,
            min_spread_bps_val=m.min_spread_bps_val,
            median_daily_turnover_idr=m.median_daily_turnover_idr,
            realised_vol_bps_val=m.realised_vol_bps_val,
        )
        for i, m in enumerate(tradeable_sorted)
    ]

    return RankedUniverse(tradeable=ranked_list, dropped=dropped)


# ---------------------------------------------------------------------------
# Metric computation per symbol
# ---------------------------------------------------------------------------

def _compute_symbol_metrics(
    symbol: str,
    fundable: bool,
    ds: DataSource,
    start: str,
    end: str,
) -> SymbolMetrics | None:
    """
    Fetch daily history and compute all screening metrics for one symbol.

    Returns None if history cannot be loaded or is too short.
    """
    try:
        hist = ds.daily_history(symbol, start, end)
    except Exception as exc:
        logger.warning("  %s: history load failed: %s", symbol, exc)
        return None

    if len(hist) < 2:
        logger.warning("  %s: insufficient history (%d rows), skipping", symbol, len(hist))
        return None

    latest_price = float(hist["close"].iloc[-1])
    tick_sz = tick_size(latest_price)
    ms_bps = min_spread_bps(latest_price)

    try:
        mdt = median_daily_turnover(hist)
    except Exception as exc:
        logger.warning("  %s: median_daily_turnover failed: %s", symbol, exc)
        return None

    try:
        rv_bps = realised_vol_bps(hist)
    except Exception as exc:
        logger.warning("  %s: realised_vol_bps failed: %s", symbol, exc)
        return None

    return SymbolMetrics(
        symbol=symbol,
        latest_price=latest_price,
        tick_sz=tick_sz,
        min_spread_bps_val=ms_bps,
        median_daily_turnover_idr=mdt,
        realised_vol_bps_val=rv_bps,
        fundable=fundable,
    )


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _fmt_idr(v: float) -> str:
    """Format a large IDR value as '1.23 B' or '456 M'."""
    if v >= 1e9:
        return f"{v/1e9:.2f} B"
    if v >= 1e6:
        return f"{v/1e6:.1f} M"
    return f"{v:.0f}"


def build_report(universe: RankedUniverse) -> str:
    """
    Render a markdown report: ranked table + dropped names table.

    The ranked TRADEABLE list is intended as the next capture watchlist.
    """
    lines: list[str] = [
        "# IDX Universe Screen",
        "",
        "Three-layer filter: **funding-eligible** (DBX/MBX catalog) ∩ "
        "**liquid** (median daily turnover) ∩ **not-tick-capped** "
        "(one-tick spread < fee gate).  "
        "No alpha signal is encoded here — that belongs in Step 4.",
        "",
        f"Edge-cap gate: min_spread_bps ≥ **{EDGE_CAP_THRESHOLD_BPS:.0f} bps** "
        f"(= full round-trip fee gate: buy {BUY_BPS:.0f} bps + sell {SELL_BPS:.0f} bps).  ",
        f"Thin gate: median daily turnover < **{TOO_THIN_TURNOVER_IDR/1e9:.1f} B IDR** "
        "(PLACEHOLDER — pending position-size derivation).",
        "",
        "---",
        "",
        "## Tradeable — ranked capture watchlist",
        "",
    ]

    if universe.tradeable:
        lines += [
            "| Rank | Symbol | Price (IDR) | Tick | min_spread (bps) "
            "| Median Turnover | Vol (bps/day) |",
            "|------|--------|-------------|------|------------------"
            "|-----------------|---------------|",
        ]
        for r in universe.tradeable:
            lines.append(
                f"| {r.rank} | {r.symbol} | {r.latest_price:.0f} | {r.tick_sz} "
                f"| {r.min_spread_bps_val:.1f} "
                f"| {_fmt_idr(r.median_daily_turnover_idr)} "
                f"| {r.realised_vol_bps_val:.0f} |"
            )
    else:
        lines.append("_No tradeable names found._")

    lines += [
        "",
        "---",
        "",
        "## Dropped names",
        "",
        "| Symbol | Reason | Detail |",
        "|--------|--------|--------|",
    ]
    for d in sorted(universe.dropped, key=lambda x: x.reason):
        lines.append(f"| {d.symbol} | {d.reason} | {d.detail} |")

    lines += [
        "",
        "---",
        "",
        "## Notes",
        "",
        "- Fixture data is **synthetic** — prices and volumes are made-up for pipeline "
        "testing only. Replace with real captured data before using this list.",
        "- The TOO-THIN floor is a placeholder; derive from "
        "`max_position_for_budget` once a target position size is agreed.",
        f"- Constants: `EDGE_CAP_THRESHOLD_BPS={EDGE_CAP_THRESHOLD_BPS}`, "
        f"`TOO_THIN_TURNOVER_IDR={TOO_THIN_TURNOVER_IDR:.0f}`.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_screen(
    ds: DataSource,
    start: str,
    end: str,
    outdir: str,
) -> RankedUniverse:
    """
    Fan-out per symbol → barrier → rank → write report.

    Sequential loop (comment marks where a workflow would parallelize).
    """
    os.makedirs(outdir, exist_ok=True)

    # Layer 1: funding gate
    print("Fetching funding universe…", flush=True)
    fundable_set = ds.funding_universe()
    print(f"  {len(fundable_set)} fundable symbols: {sorted(fundable_set)}", flush=True)

    # Fan-out: compute metrics per symbol
    # NOTE: a Claude workflow would run these as parallel agents (plan 08 §3).
    print("Computing per-symbol metrics…", flush=True)
    all_metrics: list[SymbolMetrics] = []
    for symbol in sorted(fundable_set):
        m = _compute_symbol_metrics(symbol, fundable=True, ds=ds, start=start, end=end)
        if m is not None:
            all_metrics.append(m)
            print(
                f"  {symbol}: price={m.latest_price:.0f} tick={m.tick_sz} "
                f"min_spread={m.min_spread_bps_val:.1f}bps "
                f"turnover={_fmt_idr(m.median_daily_turnover_idr)} "
                f"vol={m.realised_vol_bps_val:.0f}bps",
                flush=True,
            )
        else:
            print(f"  {symbol}: skipped (no data)", flush=True)

    # Barrier: rank once all metrics are in
    universe = rank_universe(all_metrics)

    # Print summary
    print(f"\nTRADEABLE ({len(universe.tradeable)}):")
    for r in universe.tradeable:
        print(f"  #{r.rank} {r.symbol}")
    print(f"\nDROPPED ({len(universe.dropped)}):")
    for d in universe.dropped:
        print(f"  {d.symbol}: {d.reason} — {d.detail}")

    # Write report
    report = build_report(universe)
    report_path = os.path.join(outdir, "universe_screen.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport written → {report_path}", flush=True)

    return universe


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="IDX universe screener — funding-eligible ∩ liquid ∩ not-tick-capped."
    )
    p.add_argument(
        "--source", choices=["fixture", "live"], default="fixture",
        help="Data source: 'fixture' (default, offline) or 'live' (needs SB_BEARER_TOKEN).",
    )
    p.add_argument(
        "--fixtures-dir", default=DEFAULT_FIXTURES_DIR, metavar="DIR",
        help="Directory containing fixture CSVs (default: screener/fixtures/).",
    )
    p.add_argument(
        "--outdir", default=os.path.join(SCRIPT_DIR, "..", "out"), metavar="DIR",
        help="Output directory for universe_screen.md (default: research/out/).",
    )
    p.add_argument(
        "--start", default="2026-06-01",
        help="Start date for daily history (YYYY-MM-DD).",
    )
    p.add_argument(
        "--end", default="2026-06-19",
        help="End date for daily history (YYYY-MM-DD).",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    args = parse_args()

    if args.source == "live":
        token = os.environ.get("SB_BEARER_TOKEN")
        if not token:
            print(
                "ERROR: SB_BEARER_TOKEN env var is required for --source live.",
                file=sys.stderr,
            )
            sys.exit(1)
        ds: DataSource = LiveDataSource(bearer_token=token)
    else:
        ds = FixtureDataSource(fixtures_dir=args.fixtures_dir)

    run_screen(ds, start=args.start, end=args.end, outdir=args.outdir)


if __name__ == "__main__":
    main()
