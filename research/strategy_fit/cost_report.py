"""
strategy_fit/cost_report.py — plan 18 B6: realised cost of every fill (live and paper) vs the cost the backtests assume.

    make cost-report
    python -m strategy_fit.cost_report [--runs-dir runs] [--data-dir research/data/daily] [--out-dir out_cost_report]

Writes ``cost_report.json`` and ``cost_report.md``. Read-only; local stores and daily bars only, no network.

What is measured, per fill (leg) and per round trip, in bps:

* fee        realised (broker fee from recon where listed, else the store's) vs the 15/25 bps schedule.
* slippage   against the decision price (the bot's own limit) always; against the arrival mid only when one was
             recorded — the stores record none today, so live slippage vs arrival is reported as NOT MEASURED.
* modelled   fee + half-tick spread + half the square-root impact at this size/name/date (cost_model.py).
* financing  modelled only (the broker's financing charge is in no local store); shown beside, never inside totals.

Two kinds of fill, never mixed:

* LIVE  — real orders at the broker. The only evidence about the market.
* PAPER — simulated fills. Their "realised" cost checks the simulator against the model, not the market.

Small n is stated, not hidden: below MIN_N_FILLS fills / MIN_N_TRIPS round trips a group is descriptive only.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from strategy_fit import cost_fills as cf
from strategy_fit import cost_model as cmod
from strategy_fit.capital_model import ACTIVE_CAPITAL_MODEL, CapitalModel
from strategy_fit.financing import financing_bps

REPORT_VERSION = 1
MIN_N_FILLS = 30
MIN_N_TRIPS = 15
FEE_RT_ASSUMPTION_BPS = 40.0
BASIS_ARRIVAL = "arrival_mid"
BASIS_SIMULATOR = "simulator"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_DATA_DIR = REPO_ROOT / "research" / "data" / "daily"
DEFAULT_OUT_DIR = REPO_ROOT / "research" / "out_cost_report"

LABELS = {
    cf.SOURCE_LIVE: "Live (real fills): real orders at the broker. The only evidence about the market.",
    cf.SOURCE_PAPER: ("Paper (simulator check, not market evidence): fills are simulated, so 'realised' cost here "
                      "only checks the simulator against the model."),
}
LEG_METRICS = ("fee_resid_bps", "slippage_decision_bps", "slippage_arrival_bps", "resid_bps")
TRIP_METRICS = ("realised_fee_bps", "realised_cost_bps", "modelled_cost_bps", "resid_bps")
_SIZE_EDGES = ((100_000, "<100k"), (1_000_000, "100k-1M"), (10_000_000, "1M-10M"), (50_000_000, "10M-50M"))


def size_bucket(notional_idr: float) -> str:
    for edge, label in _SIZE_EDGES:
        if notional_idr < edge:
            return label
    return ">=50M"


@dataclass(frozen=True)
class CostLeg:
    source: str
    origin: str
    strategy_id: str
    order_id: str
    symbol: str
    side: str
    day: str
    time_ns: int
    lots: int
    shares: int
    notional_idr: float
    size_bucket: str
    fill_price_idr: float
    decision_price_idr: Optional[float]
    arrival_mid_idr: Optional[float]
    fee_idr: float
    fee_source: str
    realised_fee_bps: float
    modelled_fee_bps: float
    fee_resid_bps: float
    slippage_decision_bps: Optional[float]
    slippage_arrival_bps: Optional[float]
    basis: Optional[str]
    modelled_spread_bps: float
    modelled_impact_bps: Optional[float]
    impact_priced: bool
    impact_note: str
    impact_as_of: Optional[str]
    impact_coeff: Optional[float]
    liq_bucket: Optional[str]
    modelled_cost_bps: float
    realised_cost_bps: Optional[float]
    resid_bps: Optional[float]


def _slip_bps(side: str, fill: float, ref: Optional[float]) -> Optional[float]:
    if not ref:
        return None
    sign = 1.0 if side == "buy" else -1.0
    return sign * (fill - ref) / ref * 1e4


def cost_leg(f: cf.CostFill, bars) -> CostLeg:
    """Realised vs modelled cost of one fill. ``bars`` are the symbol's daily bars, or None."""
    mdl = cmod.price_leg(f, bars)
    notional = f.notional_idr
    fee_bps = f.fee_idr / notional * 1e4
    slip_dec = _slip_bps(f.side, f.fill_price_idr, f.decision_price_idr)
    slip_arr = _slip_bps(f.side, f.fill_price_idr, f.arrival_mid_idr)
    if f.source == cf.SOURCE_PAPER and slip_dec is not None:
        basis, slip = BASIS_SIMULATOR, slip_dec
    elif f.source == cf.SOURCE_LIVE and slip_arr is not None:
        basis, slip = BASIS_ARRIVAL, slip_arr
    else:
        basis, slip = None, None
    realised = None if slip is None else fee_bps + slip
    return CostLeg(
        source=f.source, origin=f.origin, strategy_id=f.strategy_id, order_id=f.order_id, symbol=f.symbol,
        side=f.side, day=f.day, time_ns=f.time_ns, lots=f.lots, shares=f.shares, notional_idr=notional,
        size_bucket=size_bucket(notional), fill_price_idr=f.fill_price_idr, decision_price_idr=f.decision_price_idr,
        arrival_mid_idr=f.arrival_mid_idr, fee_idr=f.fee_idr, fee_source=f.fee_source, realised_fee_bps=fee_bps,
        modelled_fee_bps=mdl.modelled_fee_bps, fee_resid_bps=fee_bps - mdl.modelled_fee_bps,
        slippage_decision_bps=slip_dec, slippage_arrival_bps=slip_arr, basis=basis,
        modelled_spread_bps=mdl.modelled_spread_bps, modelled_impact_bps=mdl.modelled_impact_bps,
        impact_priced=mdl.impact_priced, impact_note=mdl.impact_note, impact_as_of=mdl.as_of,
        impact_coeff=mdl.coeff, liq_bucket=mdl.liq_bucket, modelled_cost_bps=mdl.modelled_cost_bps,
        realised_cost_bps=realised, resid_bps=None if realised is None else realised - mdl.modelled_cost_bps)


@dataclass(frozen=True)
class RoundTrip:
    source: str
    symbol: str
    entry_day: str
    exit_day: str
    hold_trading_days: int
    shares: int
    n_legs: int
    buy_notional_idr: float
    sell_notional_idr: float
    size_bucket: str
    realised_fee_bps: float
    modelled_fee_bps: float
    slippage_decision_bps: Optional[float]
    basis: Optional[str]
    realised_cost_bps: Optional[float]
    modelled_cost_bps: float
    resid_bps: Optional[float]
    impact_priced_all: bool
    capital_model: str
    modelled_financing_bps: float
    modelled_incl_financing_bps: float
    realised_financing_bps: Optional[float]
    price_move_bps: float


def _weighted(legs: Sequence[CostLeg], attr: str, denom: float) -> Optional[float]:
    vals = [getattr(x, attr) for x in legs]
    if any(v is None for v in vals):
        return None
    return sum(x.notional_idr * v for x, v in zip(legs, vals)) / denom


def _close_trip(legs: list, bought: float, buy_n: float, sell_n: float, model: CapitalModel) -> RoundTrip:
    entry, exit_ = legs[0].day, legs[-1].day
    hold = cmod.hold_days(entry, exit_)
    fin = financing_bps(date.fromisoformat(entry).weekday(), hold, entry_date=date.fromisoformat(entry),
                        capital_model=model)
    modelled = _weighted(legs, "modelled_cost_bps", buy_n)
    realised = _weighted(legs, "realised_cost_bps", buy_n)
    bases = {x.basis for x in legs}
    basis = bases.pop() if len(bases) == 1 else None
    return RoundTrip(
        source=legs[0].source, symbol=legs[0].symbol, entry_day=entry, exit_day=exit_, hold_trading_days=hold,
        shares=int(bought), n_legs=len(legs), buy_notional_idr=buy_n, sell_notional_idr=sell_n,
        size_bucket=size_bucket(buy_n), realised_fee_bps=sum(x.fee_idr for x in legs) / buy_n * 1e4,
        modelled_fee_bps=_weighted(legs, "modelled_fee_bps", buy_n),
        slippage_decision_bps=_weighted(legs, "slippage_decision_bps", buy_n), basis=basis,
        realised_cost_bps=realised, modelled_cost_bps=modelled,
        resid_bps=None if realised is None else realised - modelled,
        impact_priced_all=all(x.impact_priced for x in legs), capital_model=model.name,
        modelled_financing_bps=fin, modelled_incl_financing_bps=modelled + fin, realised_financing_bps=None,
        price_move_bps=(sell_n - buy_n) / buy_n * 1e4)


def build_round_trips(legs: Sequence[CostLeg], capital_model: CapitalModel = ACTIVE_CAPITAL_MODEL) -> tuple:
    """Flat-to-flat round trips per (source, symbol). Returns (trips, orphan_sell_count). Open positions are omitted."""
    state: dict = {}
    trips: list = []
    orphans = 0
    for leg in sorted(legs, key=lambda x: x.time_ns):
        key = (leg.source, leg.symbol)
        st = state.get(key)
        if leg.side == "buy":
            st = st or state.setdefault(key, dict(legs=[], open=0, bought=0, buy=0.0, sell=0.0))
            st["legs"].append(leg)
            st["open"] += leg.shares
            st["bought"] += leg.shares
            st["buy"] += leg.notional_idr
        elif st is None:
            orphans += 1
        else:
            st["legs"].append(leg)
            st["open"] -= leg.shares
            st["sell"] += leg.notional_idr
            if st["open"] <= 0:
                trips.append(_close_trip(st["legs"], st["bought"], st["buy"], st["sell"], capital_model))
                del state[key]
    return trips, orphans


# ---- statistics --------------------------------------------------------------------------------------

def _t_crit(df: int) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.975, df))


def summarise(values: Sequence[Optional[float]], min_n: int) -> dict:
    xs = [float(v) for v in values if v is not None and math.isfinite(v)]
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "sd": None, "ci95": None, "enough": False, "note": "no data"}
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs) if n > 1 else None
    ci = None
    if n > 1:
        half = _t_crit(n - 1) * sd / math.sqrt(n)
        ci = [mean - half, mean + half]
    enough = n >= min_n
    note = "ok" if enough else f"n too small to conclude (n={n} < {min_n}); descriptive only"
    return {"n": n, "mean": mean, "median": statistics.median(xs), "sd": sd, "ci95": ci, "enough": enough,
            "note": note}


def _group(rows: Sequence, keyfn) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(keyfn(r), []).append(r)
    return dict(sorted(out.items()))


def _metrics(rows: Sequence, metrics: Sequence[str], min_n: int) -> dict:
    return {m: summarise([getattr(r, m) for r in rows], min_n) for m in metrics}


def _aggregate(rows: Sequence, metrics: Sequence[str], min_n: int, dims: dict) -> dict:
    out = {"overall": _metrics(rows, metrics, min_n)}
    for name, keyfn in dims.items():
        out[name] = {k: _metrics(g, metrics, min_n) for k, g in _group(rows, keyfn).items()}
    return out


# ---- report ------------------------------------------------------------------------------------------

def _flags(legs: Sequence[CostLeg], trips: Sequence[RoundTrip], cov: dict, orphans: int) -> list:
    flags = []
    for src in cf.SOURCES:
        n_f = sum(1 for x in legs if x.source == src)
        n_t = sum(1 for x in trips if x.source == src)
        name = src.capitalize()
        if n_f < MIN_N_FILLS:
            flags.append(f"{name}: {n_f} fill(s), too small to conclude (need >= {MIN_N_FILLS}); descriptive only.")
        if n_t < MIN_N_TRIPS:
            flags.append(f"{name}: {n_t} round trip(s), too small to conclude (need >= {MIN_N_TRIPS}); descriptive only.")
    live = [x for x in legs if x.source == cf.SOURCE_LIVE]
    if live:
        n_arr = sum(1 for x in live if x.slippage_arrival_bps is not None)
        flags.append(f"Live slippage vs arrival mid: {n_arr} of {len(live)} live fills have an arrival mid. "
                     "The stores record only the decision price, so market slippage and spread are NOT MEASURED "
                     "(B6 needs the book at submit time).")
        n_store = sum(1 for x in live if x.fee_source == "store")
        if n_store:
            flags.append(f"{n_store} live fill(s) use the store's fee because the broker history has not listed them yet.")
    n_un = sum(1 for x in legs if not x.impact_priced)
    if n_un:
        flags.append(f"{n_un} fill(s) have no modelled impact (no daily bars or too little history); their modelled "
                     "cost excludes impact.")
    if orphans:
        flags.append(f"{orphans} sell(s) had no earlier buy in the stores and are not in any round trip.")
    flags.append("Financing is modelled only; the broker's financing charge is not in any local store.")
    return flags


def build_report(runs_dir, data_dir, capital_model: CapitalModel = ACTIVE_CAPITAL_MODEL) -> dict:
    fills, cov = cf.load_store_fills(runs_dir)
    fills, bcov = cf.attach_broker_data(runs_dir, fills)
    bars_cache: dict = {}

    def bars(sym: str):
        if sym not in bars_cache:
            bars_cache[sym] = cmod.load_daily(data_dir, sym)
        return bars_cache[sym]

    legs = [cost_leg(f, bars(f.symbol)) for f in fills]
    trips, orphans = build_round_trips(legs, capital_model)
    leg_dims = {"by_symbol": lambda r: r.symbol, "by_side": lambda r: r.side, "by_size": lambda r: r.size_bucket}
    trip_dims = {"by_symbol": lambda r: r.symbol, "by_size": lambda r: r.size_bucket}
    for src in cf.SOURCES:
        cov[src]["fills"] = sum(1 for x in legs if x.source == src)
    return {
        "version": REPORT_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labels": LABELS,
        "basis": {"fee_round_trip_assumption_bps": FEE_RT_ASSUMPTION_BPS, "capital_model": capital_model.name,
                  "min_n_fills": MIN_N_FILLS, "min_n_round_trips": MIN_N_TRIPS,
                  "model": "fee 15/25 bps + half-tick spread + half sqrt-impact (calibrated, trailing 60 bars < fill day)"},
        "coverage": {**cov, **bcov, "unpaired_sells": orphans},
        "flags": _flags(legs, trips, cov, orphans),
        "aggregates": {s: _aggregate([x for x in legs if x.source == s], LEG_METRICS, MIN_N_FILLS, leg_dims)
                       for s in cf.SOURCES},
        "round_trip_aggregates": {s: _aggregate([x for x in trips if x.source == s], TRIP_METRICS, MIN_N_TRIPS,
                                                trip_dims) for s in cf.SOURCES},
        "fills": [asdict(x) for x in legs],
        "round_trips": [asdict(x) for x in trips],
    }


# ---- markdown ----------------------------------------------------------------------------------------

def _f(x: Optional[float], spec: str = "+.1f") -> str:
    return "n/a" if x is None else format(round(x, 6) + 0.0, spec)


def _cell(s: dict) -> str:
    if s["n"] == 0:
        return "not measured"
    ci = "" if s["ci95"] is None else f" [{_f(s['ci95'][0])}, {_f(s['ci95'][1])}]"
    return f"n={s['n']}: {_f(s['mean'])} (med {_f(s['median'])}){ci}"


def _rows_for(agg: dict, metrics: Sequence[str], min_n: int) -> list:
    rows = [("all", agg["overall"])]
    for dim in ("by_side", "by_symbol", "by_size"):
        for key, ms in agg.get(dim, {}).items():
            rows.append((f"{dim[3:]} {key}", ms))
    out = []
    for name, ms in rows:
        small = "yes" if any(ms[m]["n"] and not ms[m]["enough"] for m in metrics) else "no"
        out.append("| " + " | ".join([name] + [_cell(ms[m]) for m in metrics] + [small]) + " |")
    return out


def _table(agg: dict, metrics: Sequence[str], titles: Sequence[str], min_n: int) -> list:
    head = "| group | " + " | ".join(titles) + " | n too small |"
    return [head, "|" + "---|" * (len(titles) + 2)] + _rows_for(agg, metrics, min_n)


def _fill_rows(legs: Sequence[dict], limit: int = 40) -> list:
    lines = ["| day | symbol | side | lots | fill | decision | fee bps (model) | slip vs decision | cost resid |",
             "|---|---|---|---|---|---|---|---|---|"]
    for x in legs[:limit]:
        lines.append(f"| {x['day']} | {x['symbol']} | {x['side']} | {x['lots']} | {x['fill_price_idr']:.0f} | "
                     f"{_f(x['decision_price_idr'], '.0f')} | {x['realised_fee_bps']:.1f} ({x['modelled_fee_bps']:.1f}) | "
                     f"{_f(x['slippage_decision_bps'])} | {_f(x['resid_bps'])} |")
    return lines


def _trip_rows(trips: Sequence[dict], limit: int = 40) -> list:
    lines = ["| symbol | entry | exit | hold | buy notional | realised fee bps | realised cost | modelled cost | "
             "modelled financing |", "|---|---|---|---|---|---|---|---|---|"]
    for t in trips[:limit]:
        lines.append(f"| {t['symbol']} | {t['entry_day']} | {t['exit_day']} | {t['hold_trading_days']} | "
                     f"{t['buy_notional_idr']:,.0f} | {t['realised_fee_bps']:.1f} | {_f(t['realised_cost_bps'])} | "
                     f"{t['modelled_cost_bps']:.1f} | {t['modelled_financing_bps']:.1f} |")
    return lines


def render_markdown(doc: dict) -> str:
    L = ["# Realised cost vs model (plan 18 B6)", "",
         f"Generated {doc['generated']}. Model: {doc['basis']['model']}. Capital model: {doc['basis']['capital_model']}. "
         f"The backtests charge about {doc['basis']['fee_round_trip_assumption_bps']:.0f} bps round trip in fees "
         "plus a tick and impact.", "", "## Read this first", ""]
    L += [f"- {f}" for f in doc["flags"]]
    sections = ((cf.SOURCE_LIVE, "Live (real fills)"), (cf.SOURCE_PAPER, "Paper (simulator check, not market evidence)"))
    for src, title in sections:
        cov = doc["coverage"][src]
        L += ["", f"## {title}", "", doc["labels"][src], "",
              f"Intents {cov['intents']}, fills {cov['fills']}, rejects {cov['rejects']}.", "",
              "Per fill, bps of leg notional, positive = costlier than modelled. Cells: n: mean (median) [95% CI].", ""]
        L += _table(doc["aggregates"][src], ("fee_resid_bps", "slippage_decision_bps", "slippage_arrival_bps", "resid_bps"),
                    ("fee: realised - modelled", "slippage vs decision price", "slippage vs arrival mid",
                     "total cost: realised - modelled"), MIN_N_FILLS)
        L += ["", "Per round trip, bps of buy notional (financing is modelled, shown in the trip list, never in totals).", ""]
        L += _table(doc["round_trip_aggregates"][src], ("realised_fee_bps", "realised_cost_bps", "modelled_cost_bps",
                                                        "resid_bps"),
                    ("realised fee", "realised total cost", "modelled total cost", "realised - modelled"), MIN_N_TRIPS)
        fills = [x for x in doc["fills"] if x["source"] == src]
        trips = [x for x in doc["round_trips"] if x["source"] == src]
        if fills:
            L += ["", "Fills:", ""] + _fill_rows(fills)
        if trips:
            L += ["", "Round trips:", ""] + _trip_rows(trips)
    return "\n".join(L) + "\n"


def write_report(out_dir, doc: dict) -> tuple:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / "cost_report.json", out / "cost_report.md"
    jp.write_text(json.dumps(doc, indent=2, default=str) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(doc), encoding="utf-8")
    return jp, mp


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="strategy_fit.cost_report", description="Realised vs modelled trading cost (B6).")
    p.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = p.parse_args(argv)
    doc = build_report(Path(args.runs_dir), Path(args.data_dir))
    jp, mp = write_report(args.out_dir, doc)
    print(f"wrote {jp}\nwrote {mp}")
    for f in doc["flags"]:
        print(f"- {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
