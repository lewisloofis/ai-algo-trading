"""
plot_depth.py — render an order-book depth ladder for a single symbol at a
single instant in time, producing a two-panel PNG:

  LEFT  — discrete level ladder (horizontal bars, bid=green, offer=tomato)
  RIGHT — cumulative depth chart (classic exchange "depth" view)

Usage:
    python plot_depth.py <source> --at HH:MM[:SS] [--symbol S] [--out PATH]

<source> may be:
  • a .json file already containing the book JSON  (read directly; --at ignored)
  • any other path (.binpb / .binpb.zst) → calls the Go tool:
        go run ./cmd/sbcap book --symbol <S> --at <AT> --json <source>
    flags MUST precede the positional path (Go flag.Parse stops at first
    non-flag argument), so the order above is exact.

--symbol  required when sourcing from a segment; optional for a .json file
--at      required when sourcing from a segment; ignored for a .json file
--out     default: <viz-dir>/depth_<SYMBOL>_<AT_SANITISED>.png
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

# Use the non-interactive Agg backend so the script works headless.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROTO_DIR   = os.path.dirname(SCRIPT_DIR)      # prototype/viz/../  →  prototype/
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "depth_out.png")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Level = dict[str, Any]   # {"price": int, "volume": int}


# ---------------------------------------------------------------------------
# Data acquisition
# ---------------------------------------------------------------------------

def load_json_file(path: str) -> dict[str, Any]:
    """Read a pre-exported book JSON file directly."""
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def fetch_book_via_go(source: str, symbol: str, at: str) -> dict[str, Any]:
    """
    Shell out to `go run ./cmd/sbcap book --symbol S --at AT --json <source>`.

    Flags must come before the positional argument because Go's flag.Parse
    stops at the first non-flag token.
    """
    cmd = [
        "go", "run", "./cmd/sbcap",
        "book",
        "--symbol", symbol,
        "--at", at,
        "--json",
        source,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=PROTO_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(
            "ERROR: 'go' not found in PATH — cannot decode segment file.",
            file=sys.stderr,
        )
        sys.exit(1)

    if result.returncode != 0:
        print(f"ERROR: sbcap book exited {result.returncode}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    return json.loads(result.stdout)


def acquire_book(
    source: str,
    symbol: str | None,
    at: str | None,
) -> dict[str, Any]:
    """
    Return the parsed book JSON dict, sourcing it from a .json file or the
    Go tool depending on the file extension.
    """
    if source.endswith(".json"):
        return load_json_file(source)

    # Segment path — both --symbol and --at are required.
    if not symbol:
        print(
            "ERROR: --symbol is required when <source> is a segment file.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not at:
        print(
            "ERROR: --at is required when <source> is a segment file.",
            file=sys.stderr,
        )
        sys.exit(1)
    return fetch_book_via_go(source, symbol, at)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_out_path(symbol: str, at_str: str, requested: str | None) -> str:
    """Construct the output PNG path from symbol + time, or use the override."""
    if requested:
        return requested
    safe_at = at_str.replace(":", "")
    filename = f"depth_{symbol}_{safe_at}.png"
    return os.path.join(SCRIPT_DIR, filename)


def millions_formatter(x: float, _pos: Any) -> str:
    """Axis tick formatter: show volume in millions with 'M' suffix."""
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}M"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}K"
    return f"{x:.0f}"


# ---------------------------------------------------------------------------
# Plot — left panel (ladder)
# ---------------------------------------------------------------------------

def plot_ladder(
    ax: plt.Axes,
    bids: list[Level],
    offers: list[Level],
    mid_price: float | None,
) -> None:
    """
    Draw a horizontal-bar depth ladder.

    Bids are green; offers are tomato.  The Y axis shows price (ascending),
    with volumes on the X axis.  A dashed mid-price line separates the two
    sides.
    """
    bid_prices  = [lvl["price"]  for lvl in bids]
    bid_vols    = [lvl["volume"] for lvl in bids]
    offer_prices = [lvl["price"]  for lvl in offers]
    offer_vols   = [lvl["volume"] for lvl in offers]

    bar_height = 0.6

    if bids:
        bars = ax.barh(
            bid_prices,
            bid_vols,
            height=bar_height,
            color="#2ca02c",
            alpha=0.75,
            label="Bid",
        )
        # Highlight best bid (first element = highest price).
        bars[0].set_alpha(1.0)
        bars[0].set_edgecolor("darkgreen")
        bars[0].set_linewidth(1.2)

    if offers:
        bars = ax.barh(
            offer_prices,
            offer_vols,
            height=bar_height,
            color="tomato",
            alpha=0.75,
            label="Offer",
        )
        # Highlight best offer (first element = lowest price).
        bars[0].set_alpha(1.0)
        bars[0].set_edgecolor("darkred")
        bars[0].set_linewidth(1.2)

    if mid_price is not None:
        ax.axhline(mid_price, color="navy", linewidth=1.0, linestyle="--",
                   alpha=0.7, label=f"Mid {mid_price:.0f}")

    if bids:
        ax.annotate(
            f"Best bid\n{bid_prices[0]:,}",
            xy=(0, bid_prices[0]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=7,
            color="darkgreen",
        )
    if offers:
        ax.annotate(
            f"Best offer\n{offer_prices[0]:,}",
            xy=(0, offer_prices[0]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=7,
            color="darkred",
        )

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(millions_formatter))
    ax.set_xlabel("Volume")
    ax.set_ylabel("Price (IDR)")
    ax.set_title("Depth ladder")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, axis="x", linewidth=0.4, alpha=0.5)


# ---------------------------------------------------------------------------
# Plot — right panel (cumulative depth)
# ---------------------------------------------------------------------------

def plot_cumulative(
    ax: plt.Axes,
    bids: list[Level],
    offers: list[Level],
    mid_price: float | None,
) -> None:
    """
    Draw the classic exchange cumulative depth chart.

    Bids: green filled staircase from best bid leftward (descending price).
    Offers: red/tomato filled staircase from best offer rightward (ascending).
    """
    if bids:
        # bids are already sorted descending by price (best first).
        b_prices = [lvl["price"]  for lvl in bids]
        b_cumvol: list[float] = []
        running = 0.0
        for lvl in bids:
            running += lvl["volume"]
            b_cumvol.append(running)

        ax.step(b_prices, b_cumvol, where="post", color="#2ca02c", linewidth=1.2)
        ax.fill_between(
            b_prices, b_cumvol,
            step="post",
            color="#2ca02c",
            alpha=0.30,
            label="Bid (cumul.)",
        )

    if offers:
        # offers are sorted ascending by price (best first).
        o_prices = [lvl["price"]  for lvl in offers]
        o_cumvol: list[float] = []
        running = 0.0
        for lvl in offers:
            running += lvl["volume"]
            o_cumvol.append(running)

        ax.step(o_prices, o_cumvol, where="post", color="tomato", linewidth=1.2)
        ax.fill_between(
            o_prices, o_cumvol,
            step="post",
            color="tomato",
            alpha=0.30,
            label="Offer (cumul.)",
        )

    if mid_price is not None:
        ax.axvline(mid_price, color="navy", linewidth=1.0, linestyle="--",
                   alpha=0.7, label=f"Mid {mid_price:.0f}")

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(millions_formatter))
    ax.set_xlabel("Price (IDR)")
    ax.set_ylabel("Cumulative volume")
    ax.set_title("Cumulative depth")
    ax.legend(loc="upper center", fontsize=8)
    ax.grid(True, linewidth=0.4, alpha=0.5)


# ---------------------------------------------------------------------------
# High-level render
# ---------------------------------------------------------------------------

def render(book: dict[str, Any], out_path: str) -> None:
    """Validate book data, create the two-panel figure, and save to disk."""
    symbol  = book.get("symbol", "?")
    at_wib  = book.get("at_wib", book.get("at", "?"))
    bids: list[Level]   = book.get("bid",   [])
    offers: list[Level] = book.get("offer", [])

    if not bids and not offers:
        print(
            "ERROR: both bid and offer sides are empty — nothing to plot.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Compute mid-price only when both sides are present.
    mid_price: float | None = None
    if bids and offers:
        mid_price = (bids[0]["price"] + offers[0]["price"]) / 2.0

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(f"{symbol} order book @ {at_wib}", fontsize=13, fontweight="bold")

    plot_ladder(ax_left, bids, offers, mid_price)
    plot_cumulative(ax_right, bids, offers, mid_price)

    plt.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Render an order-book depth ladder (ladder + cumulative) as a PNG."
        )
    )
    p.add_argument(
        "source",
        help=(
            "Book JSON file (.json) OR segment file (.binpb / .binpb.zst) "
            "decoded via the Go sbcap tool."
        ),
    )
    p.add_argument(
        "--at",
        default=None,
        metavar="HH:MM[:SS]",
        help="Timestamp to query (required for segment files).",
    )
    p.add_argument(
        "--symbol",
        default=None,
        metavar="S",
        help=(
            "Symbol to plot (required for segment files; "
            "optional for .json — taken from the file if omitted)."
        ),
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Output PNG path (default: <viz-dir>/depth_<SYMBOL>_<AT>.png).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # 1. Acquire book data.
    book = acquire_book(args.source, args.symbol, args.at)

    # 2. Resolve symbol and time from JSON if not provided on CLI.
    symbol: str  = args.symbol or book.get("symbol", "UNKNOWN")
    at_str: str  = book.get("at", args.at or "unknown")
    at_wib: str  = book.get("at_wib", at_str)
    bids: list[Level]   = book.get("bid",   [])
    offers: list[Level] = book.get("offer", [])

    # 3. Build output path.
    out_path = build_out_path(symbol, at_str, args.out)

    # 4. Render.
    render(book, out_path)

    # 5. Summary to stdout.
    best_bid    = bids[0]["price"]    if bids   else None
    best_offer  = offers[0]["price"]  if offers else None
    spread      = (best_offer - best_bid) if (best_bid and best_offer) else None
    total_bid   = sum(lvl["volume"] for lvl in bids)
    total_offer = sum(lvl["volume"] for lvl in offers)

    print(f"Output:          {out_path}")
    print(f"Symbol:          {symbol}")
    print(f"At (WIB):        {at_wib}")
    print(f"Bid levels:      {len(bids)}")
    print(f"Offer levels:    {len(offers)}")
    print(f"Best bid:        {best_bid:,}" if best_bid is not None else "Best bid:        —")
    print(f"Best offer:      {best_offer:,}" if best_offer is not None else "Best offer:      —")
    print(f"Spread:          {spread:,}" if spread is not None else "Spread:          —")
    print(f"Total bid vol:   {total_bid:,}")
    print(f"Total offer vol: {total_offer:,}")


if __name__ == "__main__":
    main()
