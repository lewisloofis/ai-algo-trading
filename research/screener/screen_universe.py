"""
screen_universe.py — IDX funding-eligible universe characterizer.

Defines the investable universe as the funding-eligible set (day-trade ∩
trading-limit subsectors, i.e. "all funding options unlocked") and annotates
each name with structural friction metrics.  No strategy-specific filtering
is encoded here — edge-cap and thinness are informational flags only.
Strategy fit is decided downstream by backtest (plan 10).

Pipeline shape: fan-out per symbol (compute metrics) → barrier → rank.

Usage:
    python screener/screen_universe.py [--source fixture|live] [options]
    python screener/screen_universe.py --source fixture   (default, no token needed)
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import logging
import os
import ssl
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
DEFAULT_API_HOST = os.environ.get("SB_API_HOST", "exodus.stockbit.com")

# Funding-universe (emitten) endpoints — VERIFIED live (HTTP 200).
# The funding gate is the INTERSECTION of these two subsectors
# (day-trade ∩ trading-limit — "all funding options unlocked").
EMITTEN_BASE_URL = f"https://{DEFAULT_API_HOST}/emitten/v3/sector/88/subsector"
SUBSECTOR_DAY_TRADE = "1000004658"      # day-trade eligible
SUBSECTOR_TRADING_LIMIT = "1000005139"  # trading-limit eligible

# OHLCV daily history endpoint — VERIFIED live (HTTP 200).
# date semantics: from=NEWER bound, to=OLDER bound (descending range), limit=0 = unlimited.
CHARTBIT_BASE_URL = f"https://{DEFAULT_API_HOST}/chartbit"

# Default history lookback (calendar days). ~365 cal days ≈ ~245 trading days —
# enough rows for a stable realised_vol stdev and a robust median daily turnover
# even on thinly-traded names. chartbit's limit=0 returns the full window in one
# call, so a wider window costs one extra response, not extra requests.
DEFAULT_LOOKBACK_DAYS = 365

logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext:
    """
    Build an SSL context for outbound API requests.

    Controlled by two env vars:
    - SB_INSECURE_TLS=1/true/yes  — disable certificate verification entirely
      (use ONLY behind a trusted corporate MITM proxy).
    - SB_CA_BUNDLE=<path>         — path to a CA bundle PEM for custom roots.
    """
    insecure = os.environ.get("SB_INSECURE_TLS", "").lower() in ("1", "true", "yes")
    ca = os.environ.get("SB_CA_BUNDLE")
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning(
            "SB_INSECURE_TLS set — TLS certificate verification DISABLED "
            "(use only behind a trusted proxy)"
        )
        return ctx
    if ca:
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


# ---------------------------------------------------------------------------
# DataSource Protocol
# ---------------------------------------------------------------------------

class DataSource(Protocol):
    """
    Pluggable data source for the screener.

    Implementations: FixtureDataSource (offline), LiveDataSource (HTTP).
    """

    def funding_universe(self) -> set[str]:
        """
        Return the intersection of day-trade ∩ trading-limit catalog memberships
        (the hard funding gate — "all funding options unlocked").
        """
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
        """Return the intersection of DBX ∩ MBX catalog memberships (the hard gate)."""
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
                    and row.get("mbx", "").strip().lower() == "true"
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

    Funding universe: emitten subsector endpoints (VERIFIED HTTP 200).
    OHLCV history: chartbit endpoint (VERIFIED HTTP 200).
    """

    def __init__(self, bearer_token: str | None = None) -> None:
        token = bearer_token or os.environ.get("SB_BEARER_TOKEN")
        if not token:
            raise EnvironmentError(
                "SB_BEARER_TOKEN env var must be set to use LiveDataSource"
            )
        self._token = token
        self._ssl_ctx = _build_ssl_context()

    def _get(self, url: str) -> dict:
        """Make an authenticated GET request; return parsed JSON body."""
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "okhttp/4.9.3",
                "X-Platform": "android",
                "X-AppVersion": "7.0.0",
                "X-DeviceType": "algo-prototype",
                "Accept-Language": "id",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15, context=self._ssl_ctx) as resp:
            return json.loads(resp.read().decode())

    def _fetch_subsector_symbols(self, label: str, subsector_id: str) -> set[str]:
        """
        GET one emitten subsector endpoint and return the uppercased symbol set.

        On exception, logs a warning and returns an empty set so a single
        dead endpoint does not silently corrupt the intersection.
        """
        url = f"{EMITTEN_BASE_URL}/{subsector_id}/company"
        try:
            body = self._get(url)
            rows = body.get("data") or []
            symbols = {row["symbol"].strip().upper() for row in rows if row.get("symbol")}
            logger.info(
                "emitten %s subsector (%s): %d symbols returned",
                label, subsector_id, len(symbols),
            )
            return symbols
        except Exception as exc:
            logger.warning(
                "emitten fetch failed for %s subsector (%s): %s",
                label, subsector_id, exc,
            )
            return set()

    def funding_universe(self) -> set[str]:
        """
        Return the intersection of day-trade ∩ trading-limit emitten subsectors
        as uppercase symbol strings (the hard funding gate —
        "all funding options unlocked").

        If either subsector returns an empty set (fetch failed), the intersection
        is naturally empty — that is the correct, visible behavior.
        """
        day_trade = self._fetch_subsector_symbols("day-trade", SUBSECTOR_DAY_TRADE)
        trading_limit = self._fetch_subsector_symbols("trading-limit", SUBSECTOR_TRADING_LIMIT)

        if not day_trade:
            logger.warning("day-trade subsector returned 0 symbols — intersection will be empty")
        if not trading_limit:
            logger.warning("trading-limit subsector returned 0 symbols — intersection will be empty")

        intersection = day_trade & trading_limit
        logger.info(
            "funding intersection: %d day-trade ∩ %d trading-limit = %d symbols",
            len(day_trade), len(trading_limit), len(intersection),
        )
        return intersection

    def daily_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV bars from the chartbit endpoint.

        Route: GET /chartbit/{SYMBOL}/price/daily?from={end}&to={start}&limit=0

        Date semantics (API quirk): ``from`` is the NEWER bound, ``to`` is the
        OLDER bound (rows arrive newest-first).  We pass ``end`` as ``from`` and
        ``start`` as ``to`` so that ``screener.start``/``end`` keep their
        conventional (older/newer) meaning.

        Rows are re-sorted ASCENDING by date before returning so that
        ``close.iloc[-1]`` is always the latest price, consistent with the
        FixtureDataSource contract.

        Response shape:
            {"data": {"chartbit": [<rows>], ...}}
        Row keys used: date, open, high, low, close, volume, value.
        """
        url = (
            f"{CHARTBIT_BASE_URL}/{symbol.upper()}/price/daily"
            f"?from={end}&to={start}&limit=0"
        )
        body = self._get(url)
        rows = body.get("data", {}).get("chartbit", [])
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "value"])
        df = pd.DataFrame(rows)
        df["date"] = df["date"].astype(str)
        df["close"] = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce")
        df["value"] = pd.to_numeric(df.get("value", pd.Series(dtype=float)), errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        # Re-sort ascending so iloc[-1] == latest row
        df = df.sort_values("date").reset_index(drop=True)
        return df


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
    fundable: bool               # in DBX ∩ MBX catalog


# ---------------------------------------------------------------------------
# Tag constants (strings, not an Enum, to stay JSON-friendly)
# ---------------------------------------------------------------------------

TAG_EDGE_CAPPED = "EDGE-CAPPED"
TAG_TOO_THIN = "TOO-THIN"


# ---------------------------------------------------------------------------
# Ranking dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RankedSymbol:
    """
    A funding-eligible symbol with its rank and structural friction annotations.

    min_edge_to_clear_bps: the floor a strategy's net per-trade edge must beat
      on this name (one-tick spread + 40 bps round-trip fee).
    is_edge_capped: True if min_spread_bps_val >= EDGE_CAP_THRESHOLD_BPS (40 bps).
    is_thin: True if median_daily_turnover_idr < TOO_THIN_TURNOVER_IDR.

    These flags are informational — no names are excluded for structural reasons.
    """
    rank: int
    symbol: str
    latest_price: float
    tick_sz: int
    min_spread_bps_val: float
    min_edge_to_clear_bps: float   # = min_spread_bps_val + ROUND_TRIP_BPS
    median_daily_turnover_idr: float
    realised_vol_bps_val: float
    is_edge_capped: bool           # min_spread_bps_val >= EDGE_CAP_THRESHOLD_BPS
    is_thin: bool                  # median_daily_turnover_idr < TOO_THIN_TURNOVER_IDR


@dataclass(frozen=True)
class DroppedSymbol:
    """A symbol excluded from the ranked list with its reason."""
    symbol: str
    reason: str
    detail: str


@dataclass(frozen=True)
class RankedUniverse:
    """
    Output of rank_universe — funding-eligible ranked list + safety drops.

    ranked: all funding-eligible names, sorted by liquidity, annotated with
      structural friction flags (is_edge_capped, is_thin).
    dropped: non-fundable safety exclusions only (reason = NOT-FUNDABLE).
    """
    ranked: list[RankedSymbol]
    dropped: list[DroppedSymbol]


# ---------------------------------------------------------------------------
# Pure ranking function
# ---------------------------------------------------------------------------

def rank_universe(records: list[SymbolMetrics]) -> RankedUniverse:
    """
    Characterize all funding-eligible symbols and sort by liquidity.

    Behavior (annotate-don't-drop):
    1. Non-fundable symbols are excluded with reason NOT-FUNDABLE (safety guard).
    2. All fundable symbols enter ``ranked``; none are dropped for structural
       reasons.  Edge-cap and thinness are computed and carried as boolean
       annotations on each RankedSymbol.
    3. Sort order: median_daily_turnover_idr descending, then
       realised_vol_bps_val descending, then symbol ascending.
    4. Contiguous ranks 1..N are assigned after sorting.

    This function is pure — it creates and returns new objects without
    mutating the input list.
    """
    fundable_records: list[SymbolMetrics] = []
    dropped: list[DroppedSymbol] = []

    for m in records:
        if not m.fundable:
            dropped.append(DroppedSymbol(
                symbol=m.symbol,
                reason="NOT-FUNDABLE",
                detail="not in DBX ∩ MBX catalog",
            ))
            continue
        fundable_records.append(m)

    # Sort: primary turnover desc, secondary vol desc, tertiary symbol asc
    sorted_fundable = sorted(
        fundable_records,
        key=lambda m: (-m.median_daily_turnover_idr, -m.realised_vol_bps_val, m.symbol),
    )

    ranked_list = [
        RankedSymbol(
            rank=i + 1,
            symbol=m.symbol,
            latest_price=m.latest_price,
            tick_sz=m.tick_sz,
            min_spread_bps_val=m.min_spread_bps_val,
            min_edge_to_clear_bps=m.min_spread_bps_val + ROUND_TRIP_BPS,
            median_daily_turnover_idr=m.median_daily_turnover_idr,
            realised_vol_bps_val=m.realised_vol_bps_val,
            is_edge_capped=m.min_spread_bps_val >= EDGE_CAP_THRESHOLD_BPS,
            is_thin=m.median_daily_turnover_idr < TOO_THIN_TURNOVER_IDR,
        )
        for i, m in enumerate(sorted_fundable)
    ]

    return RankedUniverse(ranked=ranked_list, dropped=dropped)


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


def _flags_cell(r: RankedSymbol) -> str:
    """Render the Flags table cell from boolean annotations."""
    parts = []
    if r.is_edge_capped:
        parts.append(TAG_EDGE_CAPPED.lower())
    if r.is_thin:
        parts.append(TAG_TOO_THIN.lower())
    return ", ".join(parts) if parts else "—"


def build_report(universe: RankedUniverse) -> str:
    """
    Render a markdown report: ranked annotated table + safety-drop section.

    The universe = all funding-eligible names (day-trade ∩ trading-limit,
    "all funding options unlocked"), ranked by liquidity.  Edge-cap and thin
    are informational flags, not exclusions.  No strategy assumption is
    encoded — strategy fit is decided downstream by backtest (plan 10).
    """
    lines: list[str] = [
        "# IDX Universe Screen",
        "",
        "Universe = **funding-eligible** (day-trade ∩ trading-limit — "
        "\"all funding options unlocked\"), ranked by liquidity.  "
        "Edge-capped and thin are **informational flags only** — no names are "
        "excluded for structural reasons.  "
        "No alpha signal or strategy assumption is encoded here — "
        "strategy fit is decided downstream by backtest (plan 10).",
        "",
        f"Edge-cap threshold: min_spread_bps ≥ **{EDGE_CAP_THRESHOLD_BPS:.0f} bps** "
        f"(= full round-trip fee: buy {BUY_BPS:.0f} bps + sell {SELL_BPS:.0f} bps).  ",
        f"Thin threshold: median daily turnover < **{TOO_THIN_TURNOVER_IDR/1e9:.1f} B IDR** "
        "(PLACEHOLDER — pending position-size derivation).",
        "",
        "---",
        "",
        "## Ranked Universe — funding-eligible, sorted by liquidity",
        "",
    ]

    if universe.ranked:
        lines += [
            "| Rank | Symbol | Price (IDR) | Tick | min_spread (bps) "
            "| min_edge_to_clear (bps) | Median Turnover | Vol (bps/day) | Flags |",
            "|------|--------|-------------|------|------------------"
            "|-------------------------|-----------------|---------------|-------|",
        ]
        for r in universe.ranked:
            lines.append(
                f"| {r.rank} | {r.symbol} | {r.latest_price:.0f} | {r.tick_sz} "
                f"| {r.min_spread_bps_val:.1f} "
                f"| {r.min_edge_to_clear_bps:.1f} "
                f"| {_fmt_idr(r.median_daily_turnover_idr)} "
                f"| {r.realised_vol_bps_val:.0f} "
                f"| {_flags_cell(r)} |"
            )
    else:
        lines.append("_No funding-eligible names found._")

    lines += [
        "",
        "---",
        "",
        "## Excluded (non-fundable safety drops)",
        "",
    ]
    if universe.dropped:
        lines += [
            "| Symbol | Reason | Detail |",
            "|--------|--------|--------|",
        ]
        for d in sorted(universe.dropped, key=lambda x: x.reason):
            lines.append(f"| {d.symbol} | {d.reason} | {d.detail} |")
    else:
        lines.append("_none_")

    lines += [
        "",
        "---",
        "",
        "## Notes",
        "",
        "- Fixture data is **synthetic** — prices and volumes are made-up for pipeline "
        "testing only. Replace with real captured data before using this list.",
        "- **min_edge_to_clear_bps** = one-tick spread + 40 bps round-trip fee.  "
        "This is the floor a strategy's net per-trade edge must beat on that name; "
        "it is informational, not a filter.",
        "- **edge-capped** and **thin** flags are informational annotations only — "
        "they do not exclude names from the universe.  Strategy fit is determined "
        "downstream by backtest (plan 10).",
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

    # Layer 1: funding gate (day-trade ∩ trading-limit intersection)
    print("Fetching funding universe (day-trade ∩ trading-limit)…", flush=True)
    fundable_set = ds.funding_universe()
    print(f"  {len(fundable_set)} symbols in intersection: {sorted(fundable_set)}", flush=True)

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
    print(f"\nRANKED UNIVERSE ({len(universe.ranked)}):")
    for r in universe.ranked:
        flags = _flags_cell(r)
        flag_str = f"  [{flags}]" if flags != "—" else ""
        print(f"  #{r.rank} {r.symbol}{flag_str}")
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
        description=(
            "IDX universe characterizer — defines the funding-eligible investable "
            "universe (day-trade ∩ trading-limit, 'all funding options unlocked') "
            "and annotates each name with structural friction metrics."
        )
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
        "--days", type=int, default=DEFAULT_LOOKBACK_DAYS, metavar="N",
        help=(
            "History lookback in calendar days, ending today "
            f"(default: {DEFAULT_LOOKBACK_DAYS}). Ignored if both --start and --end are given."
        ),
    )
    p.add_argument(
        "--start", default=None,
        help="Start date for daily history (YYYY-MM-DD). Overrides --days.",
    )
    p.add_argument(
        "--end", default=None,
        help="End date for daily history (YYYY-MM-DD). Defaults to today.",
    )
    args = p.parse_args()

    # Resolve the history window. Explicit --start/--end win; otherwise derive
    # a `--days` lookback ending today so the default window is always fresh.
    end_date = (
        datetime.date.fromisoformat(args.end) if args.end else datetime.date.today()
    )
    if args.start:
        start_date = datetime.date.fromisoformat(args.start)
    else:
        start_date = end_date - datetime.timedelta(days=args.days)
    args.start = start_date.isoformat()
    args.end = end_date.isoformat()
    return args


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
