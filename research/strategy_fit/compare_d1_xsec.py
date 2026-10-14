"""
strategy_fit/compare_d1_xsec.py — before/after table for the cross-sectional D1 re-score.

Offline (local parquet). For each entry convention (next_close, next_open,
signal_close_LOOKAHEAD) x hold, runs the cross-sectional family and prints the
long-only top-quintile DIAGNOSTIC leg: gross, net, equal-weight null (same
entry/hold/periods), paired edge over null, paired t-stat, eff_n, and the
concentration check (means excluding the 5 best periods). Cost is the same
stack the other daily cells use: best_feasible_hurdle_bps(median floor, impact,
hold), impact = median over names of the per-name daily impact at the standard
position notional. The full round trip is charged every period (an upper bound
for a book that only partly turns over).

    python -m strategy_fit.compare_d1_xsec --symbols-file S.txt --data-dir data/daily \
        --use-calibrated [--coeffs PATH] [--end 2026-10-14]
"""

from __future__ import annotations

import argparse

import numpy as np

import strategy_fit.impact as impact
from strategy_fit.entry_convention import (
    ENTRY_NEXT_CLOSE,
    ENTRY_NEXT_OPEN,
    ENTRY_SIGNAL_CLOSE_LOOKAHEAD,
)
from strategy_fit.families_cross_sectional import compute_period_returns, run_cross_sectional_family
from strategy_fit.financing import best_feasible_hurdle_bps
from strategy_fit.local_source import LocalParquetDataSource
from strategy_fit.run_strategy_fit import (
    HISTORY_START,
    ROUND_TRIP_BPS,
    SINGLE_NAME_HOLDS,
    _min_spread_bps,
)

ENTRIES = (ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN, ENTRY_SIGNAL_CLOSE_LOOKAHEAD)
T_CREDIBLE = 2.0


def use_coeffs_file(path: str | None) -> None:
    """Point the impact module at an alternative coefficients file (not copied, not written)."""
    if path is None:
        return
    impact._COEFFS_PATH = path
    impact._COEFFS_LOADED = False
    impact._CALIBRATED_COEFFS = None


def load_universe(symbols_file: str, data_dir: str, start: str, end: str):
    with open(symbols_file, encoding="utf-8") as fh:
        syms = [ln.split("#")[0].strip() for ln in fh]
    ds = LocalParquetDataSource([s for s in syms if s], data_dir)
    hist, floor = {}, {}
    for sym in sorted(ds.funding_universe()):
        df = ds.daily_history(sym, start, end)
        if len(df) < 30:
            continue
        hist[sym] = df
        floor[sym] = _min_spread_bps(float(df["close"].iloc[-1])) + ROUND_TRIP_BPS
    return hist, floor


def median_impact_bps(hist: dict, use_calibrated: bool) -> float:
    vals = [
        impact.compute_impact_bps_from_daily_df(df, use_calibrated=use_calibrated, symbol=s)
        for s, df in hist.items()
    ]
    return float(np.median(vals))


def f(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def phase_stats(hist, hold: int, entry: str) -> dict | None:
    """Rebalance-phase sensitivity: the live rule starts at the first eligible day, but
    any of the hold+1 phases is an equally valid schedule. Returns the phase-average
    gross/edge and the min/max edge and max paired t across phases."""
    if entry == ENTRY_SIGNAL_CLOSE_LOOKAHEAD:
        return None
    p = compute_period_returns(hist, hold, entry)
    if p.empty:
        return None
    gross, edge, tt = [], [], []
    for off in range(hold + 1):
        q = p[(p["pos"] - p["pos"].min()) % (hold + 1) == off]
        if len(q) < 30:
            continue
        d = (q["long_ret"] - q["null_ret"]).to_numpy() * 1e4
        sd = float(np.std(d, ddof=1))
        gross.append(float(q["long_ret"].mean() * 1e4))
        edge.append(float(d.mean()))
        tt.append(float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else 0.0)
    if not gross:
        return None
    return {"gross": float(np.mean(gross)), "edge_min": min(edge), "edge_max": max(edge), "t_max": max(tt)}


def rows(hist, floor, use_calibrated: bool):
    median_floor = float(np.median(list(floor.values())))
    imp = median_impact_bps(hist, use_calibrated)
    out = []
    for entry in ENTRIES:
        for hold in SINGLE_NAME_HOLDS:
            res = run_cross_sectional_family(hist, hold, floor, entry=entry)
            d = res.get("diagnostic")
            if d is None:
                out.append({"entry": entry, "hold": hold, "status": res["status"], "eff_n": res.get("eff_n")})
                continue
            hurdle, _ = best_feasible_hurdle_bps(
                min_edge_to_clear_bps=median_floor, modelled_impact_bps=imp, hold_trading_days=hold
            )
            net = d["long_gross_bps"] - hurdle
            ph = phase_stats(hist, hold, entry)
            ex5 = d["long_gross_ex_top5_bps"]
            out.append({
                "entry": entry, "hold": hold, "status": res["status"], "eff_n": d["n_periods"],
                "gross": d["long_gross_bps"], "hurdle": hurdle, "net": net, "null": d["null_bps"],
                "edge": d["edge_over_null_bps"], "t": d["t_paired"],
                "net_ex5": None if ex5 is None else ex5 - hurdle,
                "edge_ex5": d["edge_ex_top5_bps"],
                "phase": ph, "phase_net": None if ph is None else ph["gross"] - hurdle,
                "mechanical_pass": net > 0 and d["edge_over_null_bps"] > 0,
                "credible": net > 0 and d["edge_over_null_bps"] > 0 and d["t_paired"] > T_CREDIBLE
                and ex5 is not None and (ex5 - hurdle) > 0 and (d["edge_ex_top5_bps"] or 0) > 0,
            })
    return imp, median_floor, out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--data-dir", default="data/daily")
    ap.add_argument("--start", default=HISTORY_START)
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--use-calibrated", action="store_true")
    ap.add_argument("--coeffs", default=None, help="alternative impact coeffs JSON (read only)")
    ap.add_argument("--label", default="production")
    args = ap.parse_args(argv)

    use_coeffs_file(args.coeffs)
    hist, floor = load_universe(args.symbols_file, args.data_dir, args.start, args.end)
    imp, median_floor, table = rows(hist, floor, args.use_calibrated)

    print(f"### cost stack: {args.label}; {len(hist)} names; median floor {median_floor:.1f} bps; "
          f"median per-name impact {imp:.1f} bps (calibrated={args.use_calibrated})\n")
    print("| entry | hold | eff_n | gross (top-quintile long) | hurdle | net | null (EW universe) | edge over null | t(paired) | net ex top-5 | edge ex top-5 | mechanical pass | credible | phase-avg net | edge range over phases | max t over phases |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in table:
        if "gross" not in r:
            print(f"| {r['entry']} | {r['hold']} | {r['eff_n']} | {r['status']} | | | | | | | | | | | | |")
            continue
        print(
            f"| {r['entry']} | {r['hold']} | {r['eff_n']} | {f(r['gross'])} | {f(r['hurdle'])} | {f(r['net'])} | "
            f"{f(r['null'])} | {f(r['edge'])} | {f(r['t'], 2)} | {f(r['net_ex5'])} | {f(r['edge_ex5'])} | "
            f"{'YES' if r['mechanical_pass'] else 'no'} | {'YES' if r['credible'] else 'no'} | "
            + (
                f"{f(r['phase_net'])} | {f(r['phase']['edge_min'])} .. {f(r['phase']['edge_max'])} | {f(r['phase']['t_max'], 2)} |"
                if r["phase"]
                else "- | - | - |"
            )
        )


if __name__ == "__main__":
    main()
