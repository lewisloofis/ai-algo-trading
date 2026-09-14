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
from lib.session import filter_continuous_session

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

# Capture-watchlist slice (feeds the collector's CAPTURE_WATCHLIST_FILE / SSM watchlist, ADR-0006).
DEFAULT_WATCHLIST_TOP_N = 30
# Friction bands for the broad/diverse slice, by one-tick min_spread_bps:
FRICTION_BAND_TIGHT_BPS = 20.0       # < 20  : fine grid, intraday-plausible
# 20 <= min_spread < EDGE_CAP_THRESHOLD_BPS (40) : moderate
# min_spread >= EDGE_CAP_THRESHOLD_BPS : wide / edge-capped (swing/event)

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
#
# Defined in screener.emitten, which is stdlib-only so the eligibility snapshot
# can run on the collector box without pandas. Imported rather than repeated:
# two copies of an endpoint id drift, and a stale one here would silently screen
# the wrong universe.
from screener.emitten import (  # noqa: E402
    EMITTEN_BASE_URL,
    SUBSECTOR_DAY_TRADE,
    SUBSECTOR_TRADING_LIMIT,
)

# OHLCV daily history endpoint — VERIFIED live (HTTP 200).
# date semantics: from=NEWER bound, to=OLDER bound (descending range), limit=0 = unlimited.
CHARTBIT_BASE_URL = f"https://{DEFAULT_API_HOST}/chartbit"

# Default history lookback (calendar days). ~365 cal days ≈ ~245 trading days —
# enough rows for a stable realised_vol stdev and a robust median daily turnover
# even on thinly-traded names. chartbit's limit=0 returns the full window in one
# call, so a wider window costs one extra response, not extra requests.
DEFAULT_LOOKBACK_DAYS = 365

# Intraday (1-min) chartbit endpoint window limits — VERIFIED live.
#   from=NEWER bound, to=OLDER bound (rows arrive newest-first).
#   A 120-day request returns HTTP 400, so intraday history must be PAGED in
#   <= 90-day chunks walking backwards. ~90 cal days ≈ ~55 trading days.
INTRADAY_MAX_WINDOW_DAYS = 90
INTRADAY_DEFAULT_LOOKBACK_DAYS = 90
_SECONDS_PER_DAY = 86_400

logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext:
    """
    Build an SSL context for outbound API requests.

    Controlled by two env vars:
    - SB_INSECURE_TLS=1/true/yes  — disable certificate verification entirely
      (use ONLY behind a trusted corporate MITM proxy).
    - SB_CA_BUNDLE=<path>         — path to a CA bundle PEM for custom roots.

    When neither is set and the interpreter has no usable root store, fall back
    to the certifi bundle.  A bare venv on macOS loads zero roots, which makes
    OpenSSL reject the real DigiCert chain with the misleading error
    "self-signed certificate in certificate chain" — it means "root not
    trusted", not "MITM proxy".  Verification stays ON in every branch but
    SB_INSECURE_TLS.
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
    ctx = ssl.create_default_context()
    if ctx.get_ca_certs():
        return ctx
    try:
        import certifi
    except ImportError:
        logger.warning(
            "TLS trust store is empty and certifi is not installed — HTTPS "
            "requests will fail. Install certifi or set SB_CA_BUNDLE."
        )
        return ctx
    logger.info("TLS trust store empty; falling back to certifi bundle")
    return ssl.create_default_context(cafile=certifi.where())


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
        Optional:
          - value (pre-computed daily IDR turnover — used by median_daily_turnover).
          - foreignbuy, foreignsell (daily foreign buy/sell volume in shares).
          - net_foreign (foreignbuy - foreignsell; daily net, NOT cumulative).
            Filled to 0 when both foreignbuy and foreignsell are absent.
            Used by the foreign_flow_spike_event detector in families_event.py.
        """
        ...

    def intraday_history(
        self,
        symbol: str,
        lookback_days: int = INTRADAY_DEFAULT_LOOKBACK_DAYS,
    ) -> pd.DataFrame:
        """
        Return session-filtered 1-min intraday OHLCV bars (ADR-0011 same-day band).

        Columns: datetime (WIB pd.Timestamp), date (WIB calendar date),
        open, high, low, close, volume, value, net_foreign.

        Coverage longer than ~90 days is paged in <=90-day chunks (the chartbit
        intraday window cap).  Implementations must session-filter (keep
        09:00–16:15 WIB, drop pre-open and the 12:00–13:30 lunch gap), dedupe by
        unix_timestamp, and sort ascending.  ``net_foreign`` falls back to 0
        when both foreign_buy and foreign_sell are absent.
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
        Row keys used: date, open, high, low, close, volume, value,
        foreignbuy, foreignsell, foreignflow (cumulative — NOT used as daily),
        frequency, shareoutstanding.

        Foreign-flow columns extracted:
        - ``foreignbuy``:  daily foreign buy volume (shares).
        - ``foreignsell``: daily foreign sell volume (shares).
        - ``net_foreign``: foreignbuy - foreignsell (daily net; NaN-safe).
          ``foreignflow`` from the API is a CUMULATIVE running total and is
          intentionally NOT used here; ``net_foreign`` is always the daily net.
        Rows where ``foreignbuy`` and ``foreignsell`` are both absent are kept
        (net_foreign filled to 0); rows missing close/volume are still dropped.
        """
        url = (
            f"{CHARTBIT_BASE_URL}/{symbol.upper()}/price/daily"
            f"?from={end}&to={start}&limit=0"
        )
        body = self._get(url)
        rows = body.get("data", {}).get("chartbit", [])
        if not rows:
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "volume", "value",
                         "foreignbuy", "foreignsell", "net_foreign"]
            )
        df = pd.DataFrame(rows)
        df["date"] = df["date"].astype(str)
        df["close"] = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce")
        df["value"] = pd.to_numeric(df.get("value", pd.Series(dtype=float)), errors="coerce")
        # Foreign-flow extraction.  NaN when the row doesn't carry the column.
        df["foreignbuy"] = pd.to_numeric(
            df["foreignbuy"] if "foreignbuy" in df.columns else pd.Series(dtype=float),
            errors="coerce",
        )
        df["foreignsell"] = pd.to_numeric(
            df["foreignsell"] if "foreignsell" in df.columns else pd.Series(dtype=float),
            errors="coerce",
        )
        # net_foreign = foreignbuy - foreignsell (daily net).
        # Fill 0 only when BOTH legs are absent (both NaN); preserve individual
        # NaNs so callers can distinguish "missing" from "zero-flow".
        both_absent = df["foreignbuy"].isna() & df["foreignsell"].isna()
        df["net_foreign"] = df["foreignbuy"].sub(df["foreignsell"])
        df.loc[both_absent, "net_foreign"] = 0.0
        # Drop rows where the core price/volume data is unusable (original behaviour).
        df = df.dropna(subset=["close", "volume"])
        # Re-sort ascending so iloc[-1] == latest row
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def _fetch_intraday_chunk(self, symbol: str, newer_unix: int, older_unix: int) -> list[dict]:
        """
        GET one intraday chartbit chunk for the unix window [older, newer].

        Route: GET /chartbit/{SYMBOL}/price/intraday?from={newer}&to={older}&limit=0
        Date semantics (API quirk): ``from`` is the NEWER bound, ``to`` is the
        OLDER bound; rows arrive newest-first.

        Returns the raw row list (possibly empty).  Response shape is
        ``{"data": {"chartbit": [<rows>]}}``.
        """
        url = (
            f"{CHARTBIT_BASE_URL}/{symbol.upper()}/price/intraday"
            f"?from={newer_unix}&to={older_unix}&limit=0"
        )
        body = self._get(url)
        return body.get("data", {}).get("chartbit", []) or []

    def intraday_history(
        self,
        symbol: str,
        lookback_days: int = INTRADAY_DEFAULT_LOOKBACK_DAYS,
    ) -> pd.DataFrame:
        """
        Fetch session-filtered 1-min intraday OHLCV bars from chartbit.

        The chartbit intraday endpoint rejects windows wider than ~90 days
        (a 120-day request returns HTTP 400), so coverage longer than
        ``INTRADAY_MAX_WINDOW_DAYS`` is assembled by PAGING in <=90-day chunks
        walking backwards from "now":

            chunk 0:  from = now,                  to = now - 90d
            chunk 1:  from = chunk0_oldest - 1s,   to = (that) - 90d
            ...
        Paging stops when the requested ``lookback_days`` bound is reached or a
        chunk returns no rows.  Chunks are concatenated, de-duplicated by
        ``unix_timestamp``, sorted ascending, and session-filtered (keep
        09:00–16:15 WIB; drop pre-open and the 12:00–13:30 lunch gap) via
        ``lib.session.filter_continuous_session``.

        Intraday schema (NOTE: differs from the daily endpoint):
            close, datetime ("YYYY-MM-DD HH:MM:SS", already WIB/UTC+7), open,
            high, low, volume, value, foreign_buy, foreign_sell, frequency,
            symbol, unix_timestamp.
        Several numerics are STRING-typed ("volume":"800", "unix_timestamp":…,
        "frequency":"1") and are coerced with ``pd.to_numeric``.  Foreign columns
        use underscores (foreign_buy / foreign_sell), unlike the daily endpoint
        (foreignbuy / foreignsell).

        ``net_foreign = foreign_buy - foreign_sell`` (filled to 0 when BOTH
        legs are absent).

        Parameters
        ----------
        symbol : str
            Ticker symbol (case-insensitive).
        lookback_days : int
            Calendar-day lookback ending now.  Paged in <=90-day chunks.

        Returns
        -------
        pd.DataFrame
            Ascending, session-filtered, with columns: datetime (WIB
            pd.Timestamp), date (WIB calendar date), open, high, low, close,
            volume, value, net_foreign.  Empty (typed) frame when no data.
        """
        now_unix = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        lookback_floor = now_unix - int(lookback_days) * _SECONDS_PER_DAY
        chunk_seconds = INTRADAY_MAX_WINDOW_DAYS * _SECONDS_PER_DAY

        chunks: list[pd.DataFrame] = []
        newer = now_unix
        # Bound the loop defensively: at most ceil(lookback / 90) + 1 chunks.
        max_chunks = lookback_days // INTRADAY_MAX_WINDOW_DAYS + 2
        for _ in range(max_chunks):
            older = max(newer - chunk_seconds, lookback_floor)
            rows = self._fetch_intraday_chunk(symbol, newer, older)
            if not rows:
                break
            chunks.append(pd.DataFrame(rows))
            oldest_in_chunk = min(int(r["unix_timestamp"]) for r in rows)
            if oldest_in_chunk <= lookback_floor:
                break
            # Walk the next chunk strictly below this chunk's oldest bar.
            newer = oldest_in_chunk - 1
            if newer <= lookback_floor:
                break

        if not chunks:
            return self._empty_intraday_frame()

        df = pd.concat(chunks, ignore_index=True)
        return self._normalise_intraday(df)

    @staticmethod
    def _empty_intraday_frame() -> pd.DataFrame:
        """Return a correctly-typed empty intraday DataFrame."""
        return pd.DataFrame(
            columns=[
                "datetime", "date", "open", "high", "low",
                "close", "volume", "value", "net_foreign",
            ]
        )

    @staticmethod
    def _normalise_intraday(df: pd.DataFrame) -> pd.DataFrame:
        """
        Coerce types, dedupe, sort, session-filter, and select output columns.

        Pure transform: builds a new DataFrame; the input is not mutated.
        """
        out = df.copy()
        out["unix_timestamp"] = pd.to_numeric(
            out.get("unix_timestamp", pd.Series(dtype="int64")), errors="coerce"
        )
        for col in ("open", "high", "low", "close", "volume", "value"):
            out[col] = pd.to_numeric(
                out.get(col, pd.Series(dtype=float)), errors="coerce"
            )
        # Foreign flow (underscore schema). NaN when the row lacks the column.
        # Build per-row Series aligned to ``out.index`` so an entirely-absent
        # column yields an all-NaN column (not a misaligned empty Series).
        if "foreign_buy" in out.columns:
            fb = pd.to_numeric(out["foreign_buy"], errors="coerce")
        else:
            fb = pd.Series(float("nan"), index=out.index)
        if "foreign_sell" in out.columns:
            fs = pd.to_numeric(out["foreign_sell"], errors="coerce")
        else:
            fs = pd.Series(float("nan"), index=out.index)
        both_absent = fb.isna() & fs.isna()
        net_foreign = fb.sub(fs)
        net_foreign[both_absent] = 0.0
        out["net_foreign"] = net_foreign

        # datetime string is already WIB; parse to a naive WIB Timestamp.
        out["_wib_dt"] = pd.to_datetime(out["datetime"], errors="coerce")

        # Drop rows with unusable core data, then dedupe by unix_timestamp.
        out = out.dropna(subset=["close", "_wib_dt", "unix_timestamp"])
        out = out.drop_duplicates(subset="unix_timestamp", keep="first")
        out = out.sort_values("unix_timestamp").reset_index(drop=True)

        # Session filter (keep 09:00–16:15 WIB; drop pre-open + lunch).
        out = filter_continuous_session(out)

        out["datetime"] = out["_wib_dt"]
        out["date"] = out["_wib_dt"].dt.date
        cols = [
            "datetime", "date", "open", "high", "low",
            "close", "volume", "value", "net_foreign",
        ]
        return out[cols].reset_index(drop=True)


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
# Capture-watchlist selection
# ---------------------------------------------------------------------------

def friction_band(min_spread_bps: float) -> str:
    """
    Classify a min_spread_bps value into a friction band.

    Returns:
      "tight"    — min_spread < FRICTION_BAND_TIGHT_BPS (< 20 bps): fine grid, intraday-plausible.
      "moderate" — FRICTION_BAND_TIGHT_BPS <= min_spread < EDGE_CAP_THRESHOLD_BPS (20–39.9 bps).
      "wide"     — min_spread >= EDGE_CAP_THRESHOLD_BPS (>= 40 bps): edge-capped, swing/event.
    """
    if min_spread_bps < FRICTION_BAND_TIGHT_BPS:
        return "tight"
    if min_spread_bps < EDGE_CAP_THRESHOLD_BPS:
        return "moderate"
    return "wide"


def select_capture_watchlist(
    ranked: list[RankedSymbol],
    top_n: int = DEFAULT_WATCHLIST_TOP_N,
) -> list[RankedSymbol]:
    """
    Select a broad/diverse capture watchlist from the ranked universe.

    Classifies each symbol into a friction band (tight/moderate/wide) by
    min_spread_bps_val, preserving the incoming liquidity order within each
    band.  Picks names via round-robin across bands [tight, moderate, wide],
    taking the next-most-liquid unused name from each non-empty band each
    round, until top_n names are selected or all bands are exhausted.

    Special cases:
    - top_n <= 0 or top_n >= len(ranked): return all of ranked in rank order.

    Pure — does not mutate inputs.  Returns a new list in selection order.
    """
    if top_n <= 0 or top_n >= len(ranked):
        return list(ranked)

    # Partition into bands, preserving liquidity order within each.
    bands: dict[str, list[RankedSymbol]] = {"tight": [], "moderate": [], "wide": []}
    for sym in ranked:
        bands[friction_band(sym.min_spread_bps_val)].append(sym)

    # Round-robin across [tight, moderate, wide]; track next index per band.
    band_order = ["tight", "moderate", "wide"]
    indices: dict[str, int] = {"tight": 0, "moderate": 0, "wide": 0}
    selected: list[RankedSymbol] = []

    while len(selected) < top_n:
        progress = False
        for band in band_order:
            if len(selected) >= top_n:
                break
            idx = indices[band]
            if idx < len(bands[band]):
                selected.append(bands[band][idx])
                indices[band] = idx + 1
                progress = True
        if not progress:
            # All bands exhausted.
            break

    return selected


# ---------------------------------------------------------------------------
# Watchlist file builder
# ---------------------------------------------------------------------------

def build_watchlist_file(
    selected: list[RankedSymbol],
    universe_size: int,
    top_n: int,
) -> str:
    """
    Render the capture-watchlist file content parseable by the collector.

    Collector parsing rules (CAPTURE_WATCHLIST_FILE / ADR-0006):
      - Blank lines are ignored.
      - Lines whose first non-whitespace character is '#' are ignored.
      - Every other line is a bare symbol (uppercased).

    So ALL metadata goes on '#' comment lines; each selected symbol appears
    ALONE on its own line with no trailing inline comment.
    """
    symbol_list = [s.symbol for s in selected]
    comma_joined = ",".join(symbol_list)

    # Comment table header — columns: rank, symbol, band, min_spread, min_edge_to_clear, turnover.
    # Align widths to the widest expected value.
    col_rank  = "rank"
    col_sym   = "symbol"
    col_band  = "band"
    col_ms    = "min_spread"
    col_me    = "min_edge_to_clear"
    col_tv    = "turnover"

    def _row(rank: str, sym: str, band: str, ms: str, me: str, tv: str) -> str:
        return (
            f"# {rank:<6}{sym:<10}{band:<10}{ms:<12}{me:<20}{tv}"
        )

    header_row = _row(col_rank, col_sym, col_band, col_ms, col_me, col_tv)

    lines: list[str] = [
        "# IDX capture watchlist — generated by screen_universe.py",
        f"# Universe: funding intersection (day-trade ∩ trading-limit), "
        f"{universe_size} names; slice: broad/diverse across friction bands, "
        f"top {len(selected)} of requested {top_n}.",
        "# Delivery (ADR-0006): paste the line below into terraform.tfvars, then `tofu apply`.",
        f'#   watchlist = "{comma_joined}"',
        "#",
        header_row,
        "#",
    ]

    for s in selected:
        band = friction_band(s.min_spread_bps_val)
        detail_row = _row(
            str(s.rank),
            s.symbol,
            band,
            f"{s.min_spread_bps_val:.1f}bps",
            f"{s.min_edge_to_clear_bps:.1f}bps",
            _fmt_idr(s.median_daily_turnover_idr),
        )
        lines.append(detail_row)

    # Blank separator between comment table and bare-symbol list.
    lines.append("#")

    for s in selected:
        lines.append(s.symbol)

    lines.append("")  # trailing newline
    return "\n".join(lines)


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
    top_n: int = DEFAULT_WATCHLIST_TOP_N,
    watchlist_out: str | None = None,
) -> RankedUniverse:
    """
    Fan-out per symbol → barrier → rank → write report → optionally write watchlist.

    Sequential loop (comment marks where a workflow would parallelize).
    If watchlist_out is not None, select_capture_watchlist is called on the
    ranked universe and the result is written to watchlist_out (ADR-0006).
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

    # Write capture watchlist if requested (ADR-0006).
    if watchlist_out is not None:
        selected = select_capture_watchlist(universe.ranked, top_n)
        wl_text = build_watchlist_file(selected, len(universe.ranked), top_n)
        with open(watchlist_out, "w", encoding="utf-8") as f:
            f.write(wl_text)

        # Band breakdown summary.
        band_counts: dict[str, int] = {"tight": 0, "moderate": 0, "wide": 0}
        for s in selected:
            band_counts[friction_band(s.min_spread_bps_val)] += 1
        band_str = " ".join(f"{b}={band_counts[b]}" for b in ["tight", "moderate", "wide"])
        comma_joined = ",".join(s.symbol for s in selected)

        print(f"\nWatchlist: {len(selected)} selected ({band_str})", flush=True)
        print(f"  symbols: {comma_joined}", flush=True)
        print(f"  written → {watchlist_out}", flush=True)

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
    p.add_argument(
        "--top", type=int, default=DEFAULT_WATCHLIST_TOP_N, metavar="N",
        help=(
            f"Watchlist size cap (default: {DEFAULT_WATCHLIST_TOP_N}). "
            "0 = include all ranked names."
        ),
    )
    p.add_argument(
        "--watchlist-out", default=None, metavar="FILE",
        help=(
            "Path for the capture-watchlist output file (ADR-0006). "
            "Defaults to <outdir>/watchlist.txt unless --no-watchlist is given."
        ),
    )
    p.add_argument(
        "--no-watchlist", action="store_true",
        help="Skip emitting the capture-watchlist file.",
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

    # Resolve watchlist output path: explicit --watchlist-out wins; otherwise
    # default to <outdir>/watchlist.txt unless --no-watchlist suppresses it.
    if args.no_watchlist:
        watchlist_out: str | None = None
    elif args.watchlist_out is not None:
        watchlist_out = args.watchlist_out
    else:
        watchlist_out = os.path.join(args.outdir, "watchlist.txt")

    run_screen(
        ds,
        start=args.start,
        end=args.end,
        outdir=args.outdir,
        top_n=args.top,
        watchlist_out=watchlist_out,
    )


if __name__ == "__main__":
    main()
