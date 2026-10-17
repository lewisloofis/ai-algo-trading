"""
strategy_fit/compare_d1_rescore.py — before/after table for the D1/D2 re-score.

Reads the OLD run's strategy_portfolio.json (e.g. out_live, 2026-09-24) and one
or more NEW re-score dirs, lists every previously PASS-eligible cell (report.
_is_pass_eligible) with old vs new gross / net / edge-over-null / status, and
for every PASS-eligible cell in the new run adds a top-5-trade concentration
check (mean net excluding the 5 best trades).

    python -m strategy_fit.compare_d1_rescore --old out_live \
        --new results/d1/next_close --legacy results/d1/lookahead_legacy \
        --data-dir data/daily
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_fit.harness import deoverlap_sequential
from strategy_fit.long_only import long_only_mask
from strategy_fit.portfolio_json import cells_from_sidecar
from strategy_fit.report import _is_pass_eligible
from strategy_fit.run_strategy_fit import (
    HISTORY_START,
    SINGLE_NAME_SIGNAL_FNS,
)

SIDECAR = "strategy_portfolio.json"
TOP_N = 5
DAILY_SHAPES = {"single_name_time_series", "event_driven"}


def load_cells(d: str):
    with open(Path(d) / SIDECAR, encoding="utf-8") as fh:
        return cells_from_sidecar(json.load(fh))


def key(c):
    return (c.symbol, c.family_name, c.horizon_trading_days)


def fmt(x, nd=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def state(c) -> str:
    if c is None:
        return "absent"
    if c.status != "tested":
        return c.status
    if _is_pass_eligible(c):
        return "SURVIVES"
    if c.passes_cost_gate:
        return "cost-pass, gated"
    return "cost-fail"


def trades(symbol: str, family: str, hold: int, data_dir: str, end: str | None, entry: str | None = None) -> np.ndarray:
    df = pd.read_parquet(Path(data_dir) / f"{symbol}.parquet")
    df["date"] = df["date"].astype(str)
    df = df[(df["date"] >= HISTORY_START)]
    if end:
        df = df[df["date"] <= end]
    df = df.sort_values("date").reset_index(drop=True)
    kw = {} if entry is None else {"entry": entry}
    sig = SINGLE_NAME_SIGNAL_FNS[family](df, hold=hold, **kw)
    kept = deoverlap_sequential(
        sig, hold=hold, eligible=long_only_mask(sig["signal"].to_numpy(dtype=float))
    )
    fwd = kept["forward_return"].to_numpy(dtype=float)
    return fwd[np.isfinite(fwd) & (np.abs(fwd) > 0)] * 1e4


def concentration(c, data_dir: str, end: str | None, entry: str | None = None):
    if c.shape != "single_name_time_series":
        return None
    t = trades(c.symbol, c.family_name, c.horizon_trading_days, data_dir, end, entry)
    if len(t) <= TOP_N:
        return None
    hurdle = c.min_edge_hurdle_bps or 0.0
    trimmed = np.sort(t)[:-TOP_N]
    # t-stat of the edge over the always-long null, using the per-trade sigma
    # (the null's own sampling error is ignored, so this flatters the cell).
    over = c.edge_over_null_bps if c.edge_over_null_bps is not None else float("nan")
    t_null = over / (float(np.std(t, ddof=1)) / np.sqrt(len(t)))
    return float(np.mean(t) - hurdle), float(np.mean(trimmed) - hurdle), len(t), t_null


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--legacy", default=None, help="LOOKAHEAD re-run on the same local data")
    ap.add_argument("--data-dir", default="data/daily")
    ap.add_argument("--end", default=None, help="bars end date used by the re-score")
    ap.add_argument("--entry", default=None, help="entry convention the NEW run used (default next_close)")
    args = ap.parse_args(argv)

    old = {key(c): c for c in load_cells(args.old) if c.shape in DAILY_SHAPES}
    new = {key(c): c for c in load_cells(args.new) if c.shape in DAILY_SHAPES}
    leg = (
        {key(c): c for c in load_cells(args.legacy) if c.shape in DAILY_SHAPES}
        if args.legacy
        else {}
    )

    print("## Previously surviving cells (old PASS-eligible) -> new\n")
    print("| cell | old gross | old net | old edge/null | old eff_n | legacy-on-new-data gross | NEW gross | NEW net | NEW edge/null | NEW eff_n | NEW status |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, o in sorted(old.items(), key=lambda kv: kv[0]):
        if state(o) != "SURVIVES":
            continue
        n, lg = new.get(k), leg.get(k)
        print(
            f"| {k[0]} {k[1]} h{k[2]} | {fmt(o.gross_edge_bps)} | {fmt(o.net_edge_bps)} | "
            f"{fmt(o.edge_over_null_bps)} | {o.eff_n} | {fmt(lg.gross_edge_bps) if lg else '-'} | "
            f"{fmt(n.gross_edge_bps) if n else '-'} | {fmt(n.net_edge_bps) if n else '-'} | "
            f"{fmt(n.edge_over_null_bps) if n else '-'} | {n.eff_n if n else '-'} | {state(n)} |"
        )

    print("\n## Every PASS-eligible cell in the NEW run\n")
    survivors = [c for c in new.values() if state(c) == "SURVIVES"]
    if not survivors:
        print("none")
    for c in survivors:
        conc = concentration(c, args.data_dir, args.end, args.entry)
        print(
            f"- {c.symbol} {c.family_name} h{c.horizon_trading_days}: gross {fmt(c.gross_edge_bps)} "
            f"net {fmt(c.net_edge_bps)} eff_n {c.eff_n} null {fmt(c.null_edge_bps)} "
            f"edge/null {fmt(c.edge_over_null_bps)} oos_edge {fmt(c.oos_edge_bps)} "
            f"oos_eff_n {c.oos_eff_n} p {fmt(c.p_value, 4)} fdr {c.fdr_accepted}"
            + (f"; mean net {conc[1] + 0:.1f} excl top-{TOP_N} (all {conc[0]:.1f}, n={conc[2]}); t(edge/null)={conc[3]:.2f}" if conc else "")
        )

    def tally(cells):
        out: dict[str, int] = {}
        for c in cells.values():
            out[state(c)] = out.get(state(c), 0) + 1
        return dict(sorted(out.items()))

    print("\n## State tallies (single-name + event cells)\n")
    print("old:   ", tally(old))
    if leg:
        print("legacy:", tally(leg))
    print("new:   ", tally(new))


if __name__ == "__main__":
    main()
