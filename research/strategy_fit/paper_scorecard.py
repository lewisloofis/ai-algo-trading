"""
strategy_fit/paper_scorecard.py — scorecard of one strategy's PAPER forward test.

    make paper-scorecard STRATEGY=wifi_momo_h5
    python -m strategy_fit.paper_scorecard --strategy wifi_momo_h5 [--runs-dir runs] [--json]

Reads that strategy's paper stores (``runs/*-<id>-paper-aN.db``, every day, read
only) and the local daily bars. Per completed round trip:

    net P&L = (sell - buy notional) - fees - one tick x shares

fees = 0.15% of the buy notional + 0.25% of the sell notional (strategy/engine/
sink.go ScheduleFeeIDR; the sell includes the 0.1% sales tax), one tick = the IDX
tick at the average buy price. The tick is the research cost convention for the
spread; the paper sink already fills on the far touch, so this is conservative.

Verdict (docs/research/2026-10-15-first-candidate-shortlist.md section 6): at
least MIN_TRIPS completed trips AND all of
  1. mean net per trip > 0
  2. mean net minus the same-window always-long null > 0
  3. mean net after dropping the 2 best trips > 0
The null is the same symbol bought on every priced session in the window from
the first to the last paper entry, held the spec's hold, at the same cost
(40 bps + the tick at that day's close), from close-to-close bars.

A pass is necessary, not sufficient: it is a filter for gross failures, not
proof of an edge (at n=15 the standard error is ~340-375 bps). The emitter
plumbing check (shortlist section 6) is separate and not computed here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from screener.metrics import tick_size
from strategy_fit import paper_stores as ps
from strategy_fit.emitter_specs import SPECS, EmitterSpec

WIB = timezone(timedelta(hours=7))
FEE_BUY = 0.0015
FEE_SELL = 0.0025
FEE_RT_BPS = (FEE_BUY + FEE_SELL) * 1e4      # 40 bps
MIN_TRIPS = 15
DROP_BEST = 2
BANNER = "FILTER FOR GROSS FAILURES, NOT PROOF OF EDGE"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"


@dataclass(frozen=True)
class Trip:
    symbol: str
    entry_day: str
    exit_day: str
    shares: int
    avg_buy: float
    avg_sell: float
    gross_idr: float
    fee_idr: float
    tick_idr: float
    net_idr: float
    net_bps: float


@dataclass(frozen=True)
class Scorecard:
    strategy_id: str
    n_trips: int
    trips: tuple[Trip, ...]
    mean_net_idr: Optional[float]
    mean_net_bps: Optional[float]
    null_net_bps: Optional[float]
    edge_over_null_bps: Optional[float]
    mean_net_drop_best_bps: Optional[float]
    open_positions: dict
    verdict: str
    reasons: tuple[str, ...]


def _day(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, WIB).strftime("%Y-%m-%d")


def score_trip(t: ps.RoundTrip) -> Trip:
    fee = t.buy_notional_idr * FEE_BUY + t.sell_notional_idr * FEE_SELL
    tick = tick_size(t.avg_buy_idr) * t.shares
    gross = t.sell_notional_idr - t.buy_notional_idr
    net = gross - fee - tick
    return Trip(
        symbol=t.symbol, entry_day=_day(t.entry_ns), exit_day=_day(t.exit_ns), shares=t.shares,
        avg_buy=t.avg_buy_idr, avg_sell=t.sell_notional_idr / t.shares,
        gross_idr=gross, fee_idr=fee, tick_idr=float(tick), net_idr=net,
        net_bps=net / t.buy_notional_idr * 1e4,
    )


def load_priced_bars(symbol: str, data_dir: Path) -> Optional[pd.DataFrame]:
    """date (ISO str) + close for sessions that actually traded (volume > 0)."""
    path = Path(data_dir) / f"{symbol}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if "volume" in df.columns:
        df = df[pd.to_numeric(df["volume"], errors="coerce") > 0]
    return df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)[["date", "close"]]


def null_net_bps(bars: pd.DataFrame, first_entry: str, last_entry: str, hold: int) -> Optional[float]:
    """Mean net (gross - 40 bps - tick) of buying at every priced close in the window, holding `hold` sessions."""
    close = bars["close"].to_numpy(dtype=float)
    dates = bars["date"].to_numpy()
    vals = []
    for i in range(len(bars) - hold):
        if not (first_entry <= dates[i] <= last_entry):
            continue
        gross = (close[i + hold] / close[i] - 1.0) * 1e4
        vals.append(gross - FEE_RT_BPS - tick_size(close[i]) / close[i] * 1e4)
    return sum(vals) / len(vals) if vals else None


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def mean_drop_best(net_bps: Sequence[float], k: int = DROP_BEST) -> Optional[float]:
    if len(net_bps) <= k:
        return None
    return _mean(sorted(net_bps)[:-k])


def verdict_for(n: int, mean_net: Optional[float], edge: Optional[float],
                drop: Optional[float]) -> tuple[str, tuple[str, ...]]:
    checks = {
        "mean net per trip > 0": mean_net,
        "mean net minus always-long null > 0": edge,
        f"mean net after dropping the {DROP_BEST} best trips > 0": drop,
    }
    failed = tuple(name for name, v in checks.items() if v is not None and not v > 0)
    unknown = tuple(name for name, v in checks.items() if v is None)
    if n < MIN_TRIPS:
        extra = failed + tuple(f"{u} (not computable yet)" for u in unknown)
        return f"INCOMPLETE: {n} of {MIN_TRIPS} completed trips", extra
    if failed or unknown:
        return "STOP: fails the go-live screen", failed + tuple(f"{u} (not computable)" for u in unknown)
    return "PASSES the futility screen (necessary, not sufficient)", ()


def build_scorecard(
    spec: EmitterSpec, runs_dir: Path, data_dir: Path = DEFAULT_DATA_DIR,
) -> Scorecard:
    fills = ps.read_fills(ps.list_stores(runs_dir, spec.strategy_id))
    trips = tuple(score_trip(t) for t in ps.round_trips(fills))
    nets = [t.net_bps for t in trips]

    null_by_symbol: dict[str, Optional[float]] = {}
    for sym in sorted({t.symbol for t in trips}):
        entries = sorted(t.entry_day for t in trips if t.symbol == sym)
        bars = load_priced_bars(sym, data_dir)
        null_by_symbol[sym] = (
            null_net_bps(bars, entries[0], entries[-1], spec.hold) if bars is not None else None
        )
    nulls = [null_by_symbol[t.symbol] for t in trips]
    null = _mean(nulls) if nulls and all(v is not None for v in nulls) else None

    mean_bps = _mean(nets)
    edge = mean_bps - null if (mean_bps is not None and null is not None) else None
    drop = mean_drop_best(nets)
    verdict, reasons = verdict_for(len(trips), mean_bps, edge, drop)
    return Scorecard(
        strategy_id=spec.strategy_id, n_trips=len(trips), trips=trips,
        mean_net_idr=_mean([t.net_idr for t in trips]), mean_net_bps=mean_bps,
        null_net_bps=null, edge_over_null_bps=edge, mean_net_drop_best_bps=drop,
        open_positions=ps.net_lots(fills), verdict=verdict, reasons=reasons,
    )


def _f(x: Optional[float], spec: str = "+.1f") -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, spec)


def format_scorecard(sc: Scorecard) -> str:
    L = [f"paper scorecard: {sc.strategy_id}", f"*** {BANNER} ***", ""]
    if sc.trips:
        L.append(f"{'symbol':<7}{'entry':<12}{'exit':<12}{'shares':>7}{'buy':>9}{'sell':>9}"
                 f"{'gross':>10}{'fee':>8}{'tick':>8}{'net IDR':>10}{'net bps':>9}")
        for t in sc.trips:
            L.append(f"{t.symbol:<7}{t.entry_day:<12}{t.exit_day:<12}{t.shares:>7}{t.avg_buy:>9.0f}{t.avg_sell:>9.0f}"
                     f"{t.gross_idr:>10.0f}{t.fee_idr:>8.0f}{t.tick_idr:>8.0f}{t.net_idr:>10.0f}{t.net_bps:>9.1f}")
        L.append("")
    L += [
        f"completed round trips          {sc.n_trips}  (need >= {MIN_TRIPS})",
        f"mean net per trip              {_f(sc.mean_net_idr)} IDR  {_f(sc.mean_net_bps)} bps   (after 0.15%/0.25% fee and one tick)",
        f"always-long null, same window  {_f(sc.null_net_bps)} bps net of the same cost",
        f"mean minus null                {_f(sc.edge_over_null_bps)} bps",
        f"mean after dropping {DROP_BEST} best      {_f(sc.mean_net_drop_best_bps)} bps",
        f"open paper position            {', '.join(f'{s} {n} lot(s)' for s, n in sc.open_positions.items()) or 'flat'}",
        "",
        f"VERDICT: {sc.verdict}",
    ]
    L += [f"  - {r}" for r in sc.reasons]
    L += ["", f"{BANNER}: at n = 15 the standard error is ~340-375 bps; passing is necessary, not sufficient.",
          "The emitter plumbing check (shortlist section 6) is a separate requirement and is not computed here."]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None, registry: Optional[dict] = None) -> int:
    p = argparse.ArgumentParser(prog="strategy_fit.paper_scorecard", description=__doc__.split("\n")[1])
    p.add_argument("--strategy", required=True)
    p.add_argument("--runs-dir", default=str(Path(__file__).resolve().parents[2] / "runs"))
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    registry = SPECS if registry is None else registry
    if args.strategy not in registry:
        print(f"error: unknown strategy {args.strategy!r}; known: {sorted(registry)}", file=sys.stderr)
        return 2
    sc = build_scorecard(registry[args.strategy], Path(args.runs_dir), Path(args.data_dir))
    if args.json:
        print(json.dumps(asdict(sc), indent=2, default=str))
    else:
        print(format_scorecard(sc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
