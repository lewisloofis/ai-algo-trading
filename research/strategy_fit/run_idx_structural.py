"""
strategy_fit/run_idx_structural.py — offline runner for the 2026-10 alpha search.

Reads research/data/daily/*.parquet (market data only, no token, no network),
builds the panel, runs every pre-registered variant of families_idx_structural
through the common gates, splits by market regime, and writes
``<outdir>/results.json`` + ``<outdir>/results.md``.

    python -m strategy_fit.run_idx_structural --data-dir data/daily --outdir out_idx_structural

Reads only the daily parquet files; writes only under --outdir.
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

import strategy_fit.families_idx_structural as fx

HOLDS_FACTOR = (21, 63)
LIMIT_HOLDS = (1, 5)
BASE_NOTIONAL = 30e6          # 300M sleeve / 10 names
STRESS_NOTIONAL = 300e6       # 10x one name; factor books use sleeve 300M -> 3B
SLEEVE = 300e6
DIV_MIN_YIELD = 0.02

# Pre-registered variant count for Bonferroni: 7 factors x 2 holds + 4 limit-event
# + 3 dividend + 2 calendar + 1 regime filter.
M_TESTS = len(fx.FACTORS) * len(HOLDS_FACTOR) + 2 * len(LIMIT_HOLDS) + 3 + 2 + 1


def load_hist(data_dir: str) -> dict[str, pd.DataFrame]:
    hist = {}
    for f in sorted(glob.glob(os.path.join(data_dir, "*.parquet"))):
        sym = os.path.basename(f)[:-8]
        hist[sym] = pd.read_parquet(f)
    return hist


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    return o


def regime_report(panel, eligible, reg) -> dict:
    last = reg.iloc[-1]
    bear = reg["bear"]
    warm = reg["ma"].notna()
    # cross-check proxy: ADV-weighted top-30 (closer to a cap-weighted index like IHSG)
    adv, _ = fx.impact_inputs(panel)
    ret = panel.close.pct_change(fill_method=None)
    top = (adv.where(eligible).rank(axis=1, ascending=False) <= 30).shift(1, fill_value=False)
    w = adv.shift(1).where(top)
    r2 = (ret.where(ret.abs() <= fx.BAD_BAR_ABS_RETURN) * w).sum(axis=1) / w.sum(axis=1)
    lvl2 = (1 + r2.fillna(0)).cumprod() * 100
    ma2 = lvl2.rolling(200, min_periods=200).mean()
    dd2 = lvl2 / lvl2.rolling(252, min_periods=252).max() - 1
    since = None
    if bool(bear.iloc[-1]):
        idx = np.flatnonzero(~bear.to_numpy())
        since = str(panel.dates[idx[-1] + 1].date()) if len(idx) else str(panel.dates[0].date())
    return {
        "asof": str(panel.dates[-1].date()),
        "ew_level": float(last["level"]), "ew_ma200": float(last["ma"]), "ew_drawdown_from_52w_high": float(last["drawdown"]),
        "ew_below_ma200": bool(last["below_ma"]), "ew_deep_dd": bool(last["deep_dd"]), "bear_now": bool(last["bear"]),
        "bear_since": since,
        "share_days_bear": float(bear[warm].mean()),
        "n_days_bear": int(bear.sum()), "n_days_nonbear": int((~bear[warm]).sum()),
        "xcheck_adv_weighted_top30": {
            "level": float(lvl2.iloc[-1]), "ma200": float(ma2.iloc[-1]), "drawdown": float(dd2.iloc[-1]),
            "below_ma200": bool(lvl2.iloc[-1] < ma2.iloc[-1]), "deep_dd": bool(dd2.iloc[-1] < fx.REGIME_DD_THRESHOLD),
        },
        "ew_ret_1m": float(last["level"] / reg["level"].iloc[-22] - 1),
        "ew_ret_3m": float(last["level"] / reg["level"].iloc[-64] - 1),
        "ew_ret_6m": float(last["level"] / reg["level"].iloc[-127] - 1),
        "ew_ret_12m": float(last["level"] / reg["level"].iloc[-253] - 1),
    }


def bear_episodes(reg: pd.DataFrame) -> list[dict]:
    b = reg["bear"].to_numpy()
    out, i, n = [], 0, len(b)
    while i < n:
        if b[i]:
            j = i
            while j < n and b[j]:
                j += 1
            out.append({"start": str(reg.index[i].date()), "end": str(reg.index[j - 1].date()), "days": j - i,
                        "index_change": float(reg["level"].iloc[min(j, n - 1)] / reg["level"].iloc[max(i - 1, 0)] - 1)})
            i = j
        else:
            i += 1
    return out


def run_factors(panel, eligible, bear) -> list[dict]:
    res = []
    for name, fn in fx.FACTORS.items():
        fac = fn(panel)
        for hold in HOLDS_FACTOR:
            phases = [0, hold // 3, 2 * hold // 3]
            per = {ph: fx.run_factor_backtest(panel, fac, hold, eligible=eligible, phase=ph, notional_idr=SLEEVE)
                   for ph in phases}
            per10 = fx.run_factor_backtest(panel, fac, hold, eligible=eligible, phase=0, notional_idr=SLEEVE * 10)
            head = fx.summarize_periods(per[0], m_tests=M_TESTS, bear=bear, per_10x=per10)
            ph_net = [fx.summarize_periods(per[ph], m_tests=M_TESTS)["net_excess_name_bps"] for ph in phases]
            ph_t = [fx.summarize_periods(per[ph], m_tests=M_TESTS)["t_net_name"] for ph in phases]
            head.update({"family": "factor", "name": f"{name}_h{hold}", "hold": hold,
                         "phase_net_excess_bps": ph_net, "phase_t": ph_t,
                         "mean_turnover": float(per[0]["turnover"].mean()),
                         "mean_book_size": float(per[0]["n_top"].mean())})
            res.append(head)
    return res


def run_limit_events(panel, eligible, bear) -> list[dict]:
    elig_prev = eligible.shift(1, fill_value=False)
    res = []
    for kind in ("up", "down"):
        mask = fx.detect_limit_events(panel, kind)
        for hold in LIMIT_HOLDS:
            for entry in ("next_close", "next_open"):
                t = fx.build_event_trades(panel, mask, hold, entry, eligible=elig_prev,
                                          notional_idr=BASE_NOTIONAL, skip_locked=(kind == "up"))
                t10 = fx.recost(t, STRESS_NOTIONAL) if len(t) else None
                r = fx.summarize_event_trades(t, m_tests=M_TESTS, bear=bear, trades_10x=t10)
                r.update({"family": "limit_event", "name": f"{'ARA' if kind == 'up' else 'ARB'}_{hold}d_{entry}",
                          "hold": hold, "entry": entry, "counted_in_m": entry == "next_close",
                          "n_signals_all": int(mask.to_numpy().sum()),
                          "n_signals_eligible": int((mask & elig_prev).to_numpy().sum())})
                if len(t):
                    r["median_participation_pct"] = float(np.nanmedian(BASE_NOTIONAL / t["adv"]) * 100)
                res.append(r)
    return res


def run_dividends(panel, eligible, bear) -> list[dict]:
    res = []
    for variant in fx.DIVIDEND_VARIANTS:
        for min_yield in (DIV_MIN_YIELD, 0.0):
            mask, hold = fx.dividend_event_mask(panel, variant, min_yield=max(min_yield, 1e-9))
            t = fx.build_event_trades(panel, mask, hold, eligible=eligible, notional_idr=BASE_NOTIONAL)
            t10 = fx.recost(t, STRESS_NOTIONAL) if len(t) else None
            r = fx.summarize_event_trades(t, m_tests=M_TESTS, bear=bear, trades_10x=t10)
            r.update({"family": "dividend_event", "name": f"div_{variant}" + ("" if min_yield else "_allyield"),
                      "hold": hold, "counted_in_m": bool(min_yield)})
            if len(t) and hold <= 2:
                r["mean_financing_bps_if_leveraged"] = float(np.nanmean(t["fin_bps"]))
            res.append(r)
    return res


def run_calendar(panel, eligible, bear) -> list[dict]:
    res = []
    for kind in ("turn_of_month", "pre_lebaran"):
        w = fx.calendar_windows(panel, kind)
        per = fx.run_calendar_backtest(panel, w, eligible=eligible, notional_idr=SLEEVE)
        per10 = fx.run_calendar_backtest(panel, w, eligible=eligible, notional_idr=SLEEVE * 10)
        r = fx.summarize_periods(per, m_tests=M_TESTS, bear=bear, per_10x=per10)
        r.update({"family": "calendar", "name": kind, "hold": w[0][1] if w else None, "n_windows": len(per)})
        res.append(r)
    return res


def run_regime_filter(panel, eligible, reg, col: str = "bear") -> dict:
    flag = reg[col]
    eps, info = fx.regime_filter_episodes(panel, eligible, flag)
    eps10, _ = fx.regime_filter_episodes(panel, eligible, flag, notional_idr=SLEEVE * 10)
    r = fx.summarize_periods(eps, m_tests=M_TESTS, bear=None, per_10x=eps10, abs_applicable=False)
    # whole-sample descriptive: filter vs buy&hold of the EW eligible index (D1: 2-bar lag on the flag)
    lvl = reg["level"]
    ret = lvl.pct_change().fillna(0.0)
    expo = (~flag).astype(float).shift(2, fill_value=1.0)
    flips = expo.diff().abs().fillna(0.0)
    half_rt = float(eps["cost_bps"].mean() / 2 / 1e4) if len(eps) else 0.003
    strat = ret * expo - flips * half_rt
    warm = reg["ma"].notna()
    def stats(x):
        x = x[warm]
        cum = (1 + x).cumprod()
        yrs = len(x) / 252
        return {"cagr": float(cum.iloc[-1] ** (1 / yrs) - 1), "max_dd": float((cum / cum.cummax() - 1).min()),
                "total": float(cum.iloc[-1] - 1)}
    r.update({"family": "regime_filter", "name": "bear_to_cash" if col == "bear" else "bear_to_cash_raw_flicker_v1",
              "counted_in_m": col == "bear", "hold": None,
              "filter": stats(strat), "buy_hold": stats(ret), "n_flips": int(flips[warm].sum()),
              "open_episode": info, "episodes": int(len(eps))})
    return r


def overnight_report(panel, eligible, bear) -> dict:
    d = fx.overnight_intraday_decomposition(panel, eligible.shift(1, fill_value=False))
    d = d.dropna()
    b = bear.reindex(d.index).fillna(False)
    out = {}
    for lab, sel in (("all", slice(None)), ("bear", b.to_numpy()), ("nonbear", ~b.to_numpy())):
        sub = d[sel]
        out[lab] = {"n_days": int(len(sub)), "overnight_bps_per_day": float(sub["overnight_bps"].mean()),
                    "intraday_bps_per_day": float(sub["intraday_bps"].mean())}
    for lo, hi in (("2004", "2012"), ("2013", "2019"), ("2020", "2026")):
        sub = d.loc[lo:hi]
        out[f"{lo}-{hi}"] = {"n_days": int(len(sub)), "overnight_bps_per_day": float(sub["overnight_bps"].mean()),
                             "intraday_bps_per_day": float(sub["intraday_bps"].mean())}
    return out


def universe_report(hist, panel, eligible) -> dict:
    ends = {s: str(pd.to_datetime(df["date"].astype(str)).max().date()) for s, df in hist.items()}
    last = str(panel.dates[-1].date())
    ended = {s: e for s, e in ends.items() if e < "2026-10-01"}
    per_year = eligible.sum(axis=1).groupby(eligible.index.year).mean()
    return {
        "n_symbols": len(hist), "panel_days": len(panel.dates), "first": str(panel.dates[0].date()), "last": last,
        "n_ended_before_2026_10": len(ended),
        "eligible_names_by_year_mean": {int(k): float(v) for k, v in per_year.items()},
    }


def lebaran_check(panel) -> list[dict]:
    out = []
    for e in fx.EID_AL_FITR:
        before = np.flatnonzero(panel.dates < pd.Timestamp(e))
        if len(before) == 0 or before[-1] + 1 >= len(panel.dates):
            continue
        i = int(before[-1])
        out.append({"eid": e, "last_open": str(panel.dates[i].date()), "reopen": str(panel.dates[i + 1].date()),
                    "closure_cal_days": int((panel.dates[i + 1] - panel.dates[i]).days)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/daily")
    ap.add_argument("--outdir", default="out_idx_structural")
    a = ap.parse_args(argv)
    hist = load_hist(a.data_dir)
    last_date = max(pd.to_datetime(df["date"].astype(str)).max() for df in hist.values())
    panel = fx.build_panel(hist, min_names_trading=10)
    # the final bar can be an in-progress placeholder (zero volume everywhere) -> already dropped by the
    # calendar filter (needs >=10 names trading).
    eligible = fx.eligibility(panel)
    reg = fx.market_regime(panel, eligible)
    bear = reg["bear"]
    out = {"m_tests": M_TESTS, "universe": universe_report(hist, panel, eligible),
           "regime": regime_report(panel, eligible, reg), "bear_episodes": bear_episodes(reg),
           "lebaran_closure_check": lebaran_check(panel), "raw_last_date": str(last_date.date())}
    out["results"] = (run_factors(panel, eligible, bear) + run_limit_events(panel, eligible, bear)
                      + run_dividends(panel, eligible, bear) + run_calendar(panel, eligible, bear)
                      + [run_regime_filter(panel, eligible, reg), run_regime_filter(panel, eligible, reg, "bear_raw")])
    out["overnight_intraday"] = overnight_report(panel, eligible, bear)
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(_clean(out), fh, indent=1)
    with open(os.path.join(a.outdir, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(out))
    print(render_md(out))
    return 0


def _f(x, d=1):
    return "nan" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def render_md(out: dict) -> str:
    L = [f"# IDX structural search — results (m_tests={out['m_tests']})", "",
         "## Regime", "```", json.dumps(_clean(out["regime"]), indent=1), "```", "",
         "## Universe", "```", json.dumps(_clean({k: v for k, v in out["universe"].items()
                                                  if k != "eligible_names_by_year_mean"}), indent=1), "```", "",
         "## Results (bps; net_x = net excess over always-long name null; abs = absolute net)", "",
         "| family | variant | n | clusters | gross abs | cost | net_x name | net_x mkt | abs net | t | p_bonf | IS | OOS | ex-top5 | 10x | "
         "bear n / net_x / abs | nonbear n / net_x / abs | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["results"]:
        if r.get("family") == "regime_filter":
            continue
        rb, rn = r.get("regime", {}).get("bear", {}), r.get("regime", {}).get("nonbear", {})
        L.append("| {fam} | {nm} | {n} | {nc} | {g} | {c} | {nx} | {nm2} | {ab} | {t} | {pb} | {i} | {o} | {x5} | {t10} | {b} | {nb} | **{v}** |".format(
            fam=r["family"], nm=r["name"], n=r.get("n"), nc=r.get("n_clusters"), g=_f(r.get("gross_abs_bps")),
            c=_f(r.get("cost_bps")), nx=_f(r.get("net_excess_name_bps")), nm2=_f(r.get("net_excess_mkt_bps")),
            ab=_f(r.get("abs_net_bps")), t=_f(r.get("t_net_name"), 2), pb=_f(r.get("p_bonf"), 3),
            i=_f(r.get("is_net_excess_bps")), o=_f(r.get("oos_net_excess_bps")),
            x5=_f(r.get("ex_top5_net_excess_bps")), t10=_f(r.get("net_excess_name_10x_bps")),
            b=f"{rb.get('n_trades', '-')} / {_f(rb.get('net_excess_name_bps'))} / {_f(rb.get('abs_net_bps'))}",
            nb=f"{rn.get('n_trades', '-')} / {_f(rn.get('net_excess_name_bps'))} / {_f(rn.get('abs_net_bps'))}",
            v=r.get("verdict")))
    for r in out["results"]:
        if r.get("family") == "regime_filter":
            L += ["", f"## Regime filter: {r['name']}", "```", json.dumps(_clean(r), indent=1), "```"]
    L += ["", "## Overnight vs intraday (EW eligible, bps/day)", "```",
          json.dumps(_clean(out["overnight_intraday"]), indent=1), "```", "",
          "## Bear episodes", "```", json.dumps(_clean(out["bear_episodes"]), indent=1), "```", "",
          "## Lebaran closure check", "```", json.dumps(_clean(out["lebaran_closure_check"]), indent=1), "```"]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
