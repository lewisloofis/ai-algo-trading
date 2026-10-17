"""
strategy_fit/compare_costwin.py — before/after table for the point-in-time
impact re-score (trailing 60-bar ADV and vol vs full-history).

    python -m strategy_fit.compare_costwin \
        --old results/costwin/legacy_full_history \
        --new results/costwin/trailing_point_in_time \
        --data-dir data/daily --use-calibrated

Reads both runs' strategy_portfolio.json sidecars (single-name cells only; event
cells charge no impact and pooled/xsec/pairs cells are never tested). Prints:
per-cell table (verdict changes + top 15 by new net), whole-grid impact
distribution, named cells, and for every NEW passer a concentration check (net
excluding its top-5 trades) and a Welch t-stat of the trade mean vs the
always-long null that includes the null's own variance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from strategy_fit.families_single_name import run_single_name_family
from strategy_fit.local_source import LocalParquetDataSource
from strategy_fit.portfolio_json import cells_from_sidecar
from strategy_fit.report import _is_pass_eligible
from strategy_fit.run_strategy_fit import (
    HISTORY_END,
    HISTORY_START,
    SINGLE_NAME_SIGNAL_FNS,
    build_impact_model,
)

SIDECAR = "strategy_portfolio.json"
TOP_N = 5
NAMED = (
    ("BNBR", "daily_momentum", 2),
    ("HRTA", "volume_breakout", 5),
    ("INCO", "mean_reversion", 2),
)
T_CREDIBLE = 2.0
MAX_CREDIBLE_HOLD = 2  # ~2-day horizon ceiling of the capital model


def load_cells(d: str):
    with open(Path(d) / SIDECAR, encoding="utf-8") as fh:
        return [c for c in cells_from_sidecar(json.load(fh)) if c.shape == "single_name_time_series"]


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
        return "PASS"
    return "cost-pass, gated" if c.passes_cost_gate else "cost-fail"


def welch_t_vs_null(trades_bps: np.ndarray, null_bps: np.ndarray, hold: int) -> float:
    """t of mean(trades) - mean(null) with BOTH variances.  The null is every
    priced day's forward return (overlapping windows), so its effective n is
    len(null) / (hold + 1): conservative, and still not the flattering
    'null has zero variance' version."""
    n_t, n_n = len(trades_bps), max(len(null_bps) / (hold + 1), 2.0)
    se = np.sqrt(np.var(trades_bps, ddof=1) / n_t + np.var(null_bps, ddof=1) / n_n)
    return float((np.mean(trades_bps) - np.mean(null_bps)) / se) if se > 0 else 0.0


def concentration(c, ds, use_calibrated: bool, entry: str):
    """Per-trade net (trailing basis) for a cell: (mean net, mean net ex top-5, n, welch t, null mean)."""
    df = ds.daily_history(c.symbol, HISTORY_START, HISTORY_END)
    model = build_impact_model(ds, c.symbol, df, HISTORY_START, HISTORY_END, use_calibrated)
    fn = SINGLE_NAME_SIGNAL_FNS[c.family_name]
    hold = c.horizon_trading_days
    res = run_single_name_family(
        df, fn, c.symbol, 0.0, c.family_name, hold, entry=entry, impact_model=model
    )
    if res["status"] != "tested":
        return None
    ret = np.asarray(res["trade_returns_bps"], dtype=float)
    imp = np.asarray(res["per_trade_impact_bps"], dtype=float)
    # hurdle_i = (floor + financing) + impact_i; floor+financing from the cell itself.
    base = (c.min_edge_hurdle_bps or 0.0) - (c.modelled_impact_bps or 0.0)
    net = ret - (base + imp)
    if len(net) <= TOP_N:
        return None
    null = fn(df, hold=hold, entry=entry)["forward_return"].to_numpy(dtype=float)
    null = null[np.isfinite(null)] * 1e4
    return (
        float(net.mean()),
        float(np.sort(net)[:-TOP_N].mean()),
        len(net),
        welch_t_vs_null(ret, null, hold),
        float(null.mean()),
    )


def quantiles(vals) -> str:
    a = np.asarray([v for v in vals if v is not None and np.isfinite(v)], dtype=float)
    q = np.percentile(a, [0, 10, 25, 50, 75, 90, 100])
    return " | ".join(fmt(x, 1) for x in q) + f" | {fmt(a.mean(), 1)} | {len(a)}"


def row(k, o, n) -> str:
    return (
        f"| {k[0]} {k[1]} h{k[2]} | {fmt(o.modelled_impact_bps) if o else '-'} | "
        f"{fmt(n.modelled_impact_bps) if n else '-'} | {fmt(n.impact_median_bps) if n else '-'} | "
        f"{fmt(o.net_edge_bps) if o else '-'} | {fmt(n.net_edge_bps) if n else '-'} | "
        f"{state(o)} | {state(n)} |"
    )


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="legacy_full_history run dir")
    ap.add_argument("--new", required=True, help="trailing_point_in_time run dir")
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--data-dir", default="data/daily")
    ap.add_argument("--entry", default="next_close")
    ap.add_argument("--use-calibrated", action="store_true")
    args = ap.parse_args(argv)

    with open(args.symbols_file, encoding="utf-8") as fh:
        syms = [ln.split("#")[0].strip() for ln in fh if ln.strip()]
    ds = LocalParquetDataSource(syms, args.data_dir)

    old = {key(c): c for c in load_cells(args.old)}
    new = {key(c): c for c in load_cells(args.new)}
    header = "| cell | old impact | new impact (mean) | new impact (median) | old net | new net | old verdict | new verdict |\n|---|---|---|---|---|---|---|---|"

    print("## Cells whose verdict changed\n")
    print(header)
    changed = [k for k in sorted(new) if state(old.get(k)) != state(new[k])]
    for k in changed:
        print(row(k, old.get(k), new[k]))
    if not changed:
        print("| none | | | | | | | |")

    print("\n## Top 15 cells by new net (tested)\n")
    print(header)
    tested = [k for k in new if new[k].status == "tested" and new[k].net_edge_bps is not None]
    for k in sorted(tested, key=lambda k: -new[k].net_edge_bps)[:15]:
        print(row(k, old.get(k), new[k]))

    print("\n## Named cells\n")
    print(header)
    for sym, fam, h in NAMED:
        print(row((sym, fam, h), old.get((sym, fam, h)), new.get((sym, fam, h))))

    print("\n## Whole grid: impact charged per single-name cell (bps)\n")
    print("| basis | min | p10 | p25 | median | p75 | p90 | max | mean | cells |\n|---|---|---|---|---|---|---|---|---|---|")
    print("| old (full-history) | " + quantiles(c.modelled_impact_bps for c in old.values() if c.impact_priced) + " |")
    print("| new (trailing PIT, mean/trade) | " + quantiles(c.modelled_impact_bps for c in new.values() if c.impact_priced) + " |")
    both = [k for k in new if k in old and new[k].impact_priced and old[k].impact_priced
            and old[k].modelled_impact_bps and new[k].modelled_impact_bps == new[k].modelled_impact_bps]
    ratios = [new[k].modelled_impact_bps / old[k].modelled_impact_bps for k in both if old[k].modelled_impact_bps > 0]
    print(f"\nnew/old impact ratio over {len(ratios)} cells: " + quantiles(ratios))
    excl = sum(c.impact_excluded_n or 0 for c in new.values())
    print(f"trades excluded for < 20 bars of history (summed over cells): {excl}")

    tally = lambda cells: {s: sum(1 for c in cells.values() if state(c) == s) for s in sorted({state(c) for c in cells.values()})}
    print("\n## Tallies\n")
    print("old:", tally(old))
    print("new:", tally(new))

    print("\n## New passers (PASS in new, not in old)\n")
    newly = [k for k in sorted(new) if state(new[k]) == "PASS" and state(old.get(k)) != "PASS"]
    if not newly:
        print("none")
    for k in newly:
        c = new[k]
        conc = concentration(c, ds, args.use_calibrated, args.entry)
        if conc is None:
            print(f"- {k}: concentration unavailable")
            continue
        mean_net, ex5, n, t, null_mean = conc
        beta_frac = (c.null_edge_bps or 0.0) / c.gross_edge_bps if c.gross_edge_bps else float("nan")
        credible = ex5 > 0 and t >= T_CREDIBLE and k[2] <= MAX_CREDIBLE_HOLD and beta_frac < 0.5
        print(
            f"- {k[0]} {k[1]} h{k[2]}: gross {fmt(c.gross_edge_bps)} net {fmt(c.net_edge_bps)} (impact {fmt(c.modelled_impact_bps)}) "
            f"eff_n {c.eff_n} null {fmt(c.null_edge_bps)} (null/gross {fmt(beta_frac, 2)}) edge/null {fmt(c.edge_over_null_bps)} "
            f"oos_edge {fmt(c.oos_edge_bps)} p {fmt(c.p_value, 4)}; net ex top-{TOP_N} {fmt(ex5)} (all {fmt(mean_net)}, n={n}); "
            f"Welch t(edge vs null, both variances)={t:.2f}; credible={'YES' if credible else 'no'}"
        )


if __name__ == "__main__":
    main()
