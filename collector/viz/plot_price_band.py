"""
plot_price_band.py — visualise a market-data segment as a price + bid/offer band.

Usage:
    python plot_price_band.py <source> [--symbol S] [--out PATH]
                              [--from HH:MM] [--to HH:MM]

<source> may be:
  • a .csv or .ndjson file produced by sbcap (read directly)
  • any other path (.binpb / .binpb.zst) → decoded via
    `go run ./cmd/sbcap export --format csv <source>` from the prototype dir
"""

import argparse
import csv
import datetime
import io
import os
import subprocess
import sys
from collections import defaultdict

# Use the non-interactive Agg backend so the script works headless.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import zoneinfo

WIB = zoneinfo.ZoneInfo("Asia/Jakarta")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROTO_DIR   = os.path.dirname(SCRIPT_DIR)          # prototype/viz/../ → prototype/
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "price_band.png")


# ---------------------------------------------------------------------------
# Data acquisition
# ---------------------------------------------------------------------------

def fetch_csv_text(source: str) -> str:
    """Return CSV text: read file directly for .csv/.ndjson, else run sbcap."""
    ext = os.path.splitext(source)[1].lower()
    if ext in (".csv",):
        with open(source, "r", encoding="utf-8") as f:
            return f.read()

    # For a segment file, invoke sbcap.
    cmd = ["go", "run", "./cmd/sbcap", "export", "--format", "csv", source]
    try:
        result = subprocess.run(
            cmd,
            cwd=PROTO_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("ERROR: 'go' not found in PATH — cannot decode segment file.", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"ERROR: sbcap exited {result.returncode}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    return result.stdout


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_rows(csv_text: str):
    """Parse CSV into a list of dicts; skip rows with parse errors."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        try:
            row["_time"] = datetime.datetime.fromisoformat(row["iso_time"])
            row["_last_price"]  = float(row["last_price"]  or 0)
            row["_top_price"]   = float(row["top_price"]   or 0)
            row["_top_volume"]  = float(row["top_volume"]  or 0)
        except (ValueError, KeyError):
            continue
        rows.append(row)
    return rows


def select_symbol(rows, requested: str | None) -> str:
    """Return the symbol to plot; auto-select the most-active if not specified."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        sym = r.get("symbol", "")
        if sym:
            counts[sym] += 1

    if not counts:
        print("ERROR: no symbol data found in source.", file=sys.stderr)
        sys.exit(1)

    if requested:
        if requested not in counts:
            print(f"ERROR: symbol '{requested}' not found. Available: {sorted(counts)}", file=sys.stderr)
            sys.exit(1)
        return requested

    best = max(counts, key=lambda s: counts[s])
    if len(counts) > 1:
        print(
            f"NOTE: multiple symbols found {sorted(counts)}; "
            f"auto-selected '{best}' (most events). Use --symbol to override.",
            file=sys.stderr,
        )
    return best


def clip_time(rows, frm: str | None, to: str | None):
    """Filter rows to the [from, to] HH:MM window (WIB)."""
    if not frm and not to:
        return rows

    def hhmm(s: str) -> datetime.time:
        h, m = s.split(":")
        return datetime.time(int(h), int(m))

    t_from = hhmm(frm) if frm else None
    t_to   = hhmm(to)  if to  else None

    out = []
    for r in rows:
        t = r["_time"].time()
        if t_from and t < t_from:
            continue
        if t_to and t > t_to:
            continue
        out.append(r)
    return out


def build_series(rows, symbol: str):
    """
    Returns:
      lp_times, lp_prices   — liveprice events
      band_times, bids, offers — merged, forward-filled bid/offer for fill_between
    """
    lp_times:  list[datetime.datetime] = []
    lp_prices: list[float]             = []

    # Forward-fill state
    last_bid:   float | None = None
    last_offer: float | None = None

    band_times: list[datetime.datetime] = []
    bids:       list[float]             = []
    offers:     list[float]             = []

    sym_rows = [r for r in rows if r.get("symbol") == symbol]
    # Already sorted by recv_ns from sbcap, but sort defensively.
    sym_rows.sort(key=lambda r: r["_time"])

    for r in sym_rows:
        kind = r.get("kind", "")
        t    = r["_time"]

        if kind == "liveprice":
            price = r["_last_price"]
            if price > 0:
                lp_times.append(t)
                lp_prices.append(price)

        elif kind == "orderbook":
            side  = r.get("side", "").upper()
            price = r["_top_price"]
            if price <= 0:
                continue
            if side == "BID":
                last_bid = price
            elif side == "OFFER":
                last_offer = price
            # Emit a band sample once both sides are known.
            if last_bid is not None and last_offer is not None:
                band_times.append(t)
                bids.append(last_bid)
                offers.append(last_offer)

    return lp_times, lp_prices, band_times, bids, offers


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def make_plot(
    symbol:     str,
    lp_times:   list,
    lp_prices:  list,
    band_times: list,
    bids:       list,
    offers:     list,
    out_path:   str,
):
    fig, ax = plt.subplots(figsize=(13, 5))

    # Shaded spread band.
    if band_times:
        ax.fill_between(
            band_times, bids, offers,
            step="post", alpha=0.25, color="steelblue",
            label="bid–offer spread",
        )
        ax.step(band_times, bids,   where="post", color="steelblue",
                linewidth=0.8, alpha=0.7, label="best bid")
        ax.step(band_times, offers, where="post", color="tomato",
                linewidth=0.8, alpha=0.7, label="best offer")

    # Last-traded price on top.
    if lp_times:
        ax.plot(lp_times, lp_prices, color="black", linewidth=1.4,
                marker=".", markersize=3, label="last price")

    # Infer date from data (first available timestamp).
    all_times = lp_times + band_times
    date_str  = all_times[0].strftime("%Y-%m-%d") if all_times else "?"

    ax.set_title(f"{symbol} — price & bid/offer band ({date_str} WIB)", fontsize=13)
    ax.set_xlabel("Time (WIB)")
    ax.set_ylabel("Price (IDR)")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=WIB))
    fig.autofmt_xdate(rotation=30)

    ax.legend(loc="best", fontsize=9)
    ax.grid(True, linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Plot price + bid/offer band from a market-data segment or exported CSV."
    )
    p.add_argument("source",      help="Segment file (.binpb/.binpb.zst) or exported .csv/.ndjson")
    p.add_argument("--symbol",    default=None, metavar="S", help="Symbol to plot (auto-select if omitted)")
    p.add_argument("--out",       default=DEFAULT_OUT, metavar="PATH", help="Output PNG path")
    p.add_argument("--from",      dest="from_time", default=None, metavar="HH:MM", help="Start time (WIB)")
    p.add_argument("--to",        dest="to_time",   default=None, metavar="HH:MM", help="End time (WIB)")
    return p.parse_args()


def main():
    args = parse_args()

    # 1. Fetch raw CSV text.
    csv_text = fetch_csv_text(args.source)

    # 2. Parse all rows.
    rows = parse_rows(csv_text)
    if not rows:
        print("ERROR: no rows parsed from source.", file=sys.stderr)
        sys.exit(1)

    # 3. Choose symbol.
    symbol = select_symbol(rows, args.symbol)

    # 4. Clip to time window.
    rows = clip_time(rows, args.from_time, args.to_time)

    # 5. Build time series.
    lp_times, lp_prices, band_times, bids, offers = build_series(rows, symbol)

    if not lp_times and not band_times:
        print(f"ERROR: no data to plot for symbol '{symbol}'.", file=sys.stderr)
        sys.exit(1)

    # 6. Plot and save.
    make_plot(symbol, lp_times, lp_prices, band_times, bids, offers, args.out)

    # 7. Summary to stdout.
    all_times = sorted(lp_times + band_times)
    span_start = all_times[0].strftime("%H:%M:%S") if all_times else "?"
    span_end   = all_times[-1].strftime("%H:%M:%S") if all_times else "?"
    print(f"Output:          {args.out}")
    print(f"Symbol:          {symbol}")
    print(f"Liveprice pts:   {len(lp_times)}")
    print(f"Orderbook smpls: {len(band_times)}")
    print(f"Time span (WIB): {span_start} – {span_end}")


if __name__ == "__main__":
    main()
