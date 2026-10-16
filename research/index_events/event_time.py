"""
index_events/event_time.py — plan 21 §1a event-time breakdown (Phase 0b, descriptive).

HOLDOUT GUARD: only reviews announced <= 2021-12-31 are analysed. Non-freeze
reviews announced in 2022 or later are never passed to any statistic;
``guard_holdout`` raises if one reaches the analysis. Freeze reviews (MSCI >=
2026-02, FTSE >= 2026-03) are a separate descriptive block.

CLI: cd research && .venv/bin/python -m index_events.event_time
Writes out_index_events/*.png, legs_dev.csv, legs_freeze.csv and
docs/research/2026-10-16-index-rebalance-msci-1a.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from index_events import event_legs as el
from index_events.build_events import (DAILY_DIR, EVENTS_CSV, OUT_DIR, RESEARCH, coverage_tables, local_tickers,
                                       stock_files)

DEV_END = pd.Timestamp("2021-12-31")
HOLDOUT_START = pd.Timestamp("2022-01-01")
REPO = RESEARCH.parent
DOC_PATH = REPO / "docs" / "research" / "2026-10-16-index-rebalance-msci-1a.md"
BPS = 1e4


class HoldoutError(ValueError):
    """A non-freeze review announced in 2022+ reached the analysis."""


def review_start(label: str) -> pd.Timestamp:
    return pd.Timestamp(year=int(label[-7:-3]), month=int(label[-2:]), day=1)


def announce_ts(ev: pd.DataFrame) -> pd.Series:
    """Announce date; falls back to the first day of the review month when blank."""
    d = pd.to_datetime(ev["announce_ts_local"].replace("", np.nan), errors="coerce")
    fb = ev["review_label"].map(review_start)
    return d.fillna(fb)


def guard_holdout(ev: pd.DataFrame) -> None:
    bad = ev[(ev["review_type"] != "freeze") & (announce_ts(ev) >= HOLDOUT_START)]
    if len(bad):
        raise HoldoutError(f"{len(bad)} non-freeze event(s) announced >= 2022-01-01 "
                           f"(e.g. {bad['review_label'].iloc[0]}) must not be analysed")


def split_events(ev: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(development, freeze). Non-freeze 2022+ rows are dropped here, before anything is computed."""
    ts = announce_ts(ev)
    dev = ev[(ev["review_type"] != "freeze") & (ts <= DEV_END)].copy()
    frz = ev[ev["review_type"] == "freeze"].copy()
    guard_holdout(dev)
    return dev, frz


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def load_hist(daily_dir: Path = DAILY_DIR) -> dict[str, pd.DataFrame]:
    """Per-stock daily frames; index series (INDEX_SYMBOLS, e.g. IHSG) are excluded."""
    return {p.stem: pd.read_parquet(p) for p in stock_files(daily_dir)}


def load_panel():
    from strategy_fit.families_idx_structural import build_panel, detect_limit_events, locked_up_entry
    hist = load_hist()
    panel = build_panel(hist)
    lim_up = detect_limit_events(panel, "up").to_numpy()
    lock_next = locked_up_entry(panel).to_numpy()
    return panel, lim_up, lock_next


class Ctx:
    """Arrays and per-name bookkeeping shared by all events."""

    def __init__(self, panel, lim_up, lock_next):
        self.dates = panel.dates
        self.symbols = list(panel.symbols)
        self.col = {s: i for i, s in enumerate(self.symbols)}
        self.close = panel.close.to_numpy(float)
        self.open = panel.open.to_numpy(float)
        self.volume = panel.volume.to_numpy(float)
        self.mkt = el.build_market(self.close, self.open, self.volume)
        self.close_ff = panel.close.ffill().to_numpy(float)
        fin = np.isfinite(self.close)
        self.first_valid = np.where(fin.any(0), fin.argmax(0), -1)
        self.last_valid = np.where(fin.any(0), self.close.shape[0] - 1 - fin[::-1].argmax(0), -1)
        self.lim_up, self.lock_next = lim_up, lock_next

    def idx(self, s: str) -> int:
        i = self.dates.searchsorted(pd.Timestamp(s))
        if i >= len(self.dates) or self.dates[i] != pd.Timestamp(s):
            raise ValueError(f"session {s} not in panel calendar: re-run index_events.build_events")
        return int(i)


def process_event(row, ctx: Ctx) -> dict:
    """-> {"status": reason or "ok", flags..., legs..., paths}"""
    res: dict = {"status": "ok"}
    if row.announce_session == "" or row.passive_trade_session == "":
        return {"status": "no_dates"}
    if not row.ticker or row.cross_check == "mismatch":
        return {"status": "ticker_unresolved"}
    if row.ticker not in ctx.col:
        return {"status": "no_bars_ticker_missing"}
    j = ctx.col[row.ticker]
    ia, ip = ctx.idx(row.announce_session), ctx.idx(row.passive_trade_session)
    lo = ia - el.PRE_DAYS
    if lo < 0:
        return {"status": "no_bars_before_calendar"}
    legs = el.event_legs(ctx.close, ctx.open, ctx.mkt, j, ia, ip)
    if legs is None:
        if ctx.first_valid[j] > lo:
            return {"status": "no_bars_not_yet_listed"}
        if ctx.last_valid[j] < ip:
            return {"status": "no_bars_ended_before_p"}
        return {"status": "no_bars_gap_in_window"}
    hi = ip + legs["l5_k"]
    if el.bad_bar_in_window(ctx.close[:, j], lo, hi):
        return {"status": "bad_bar_gt50pct"}
    # Plan §4.3: suspended between a and p -> exclude. Chartbit fills suspended and
    # delisted names with flat zero-volume bars, so a halt shows as a volume run, not a gap.
    if is_suspended(ctx.volume[ia + 1: ip + 1, j]):
        return {"status": "suspended_a_to_p"}
    res.update(legs)
    res["flag_locked_a1"] = bool(ctx.lim_up[ia + 1, j] or ctx.lock_next[ia, j])
    res["flag_no_trade_a1"] = bool(not ctx.volume[ia + 1, j] > 0)
    res["flag_partial_l5"] = legs["l5_k"] < el.POST_DAYS
    seg_v = ctx.volume[ia + 1: ip + 1, j]
    res["flag_halt_ge5_in_run"] = bool(_max_run(~(np.nan_to_num(seg_v) > 0)) >= 5)
    res["_paths"] = el.event_paths(ctx.close_ff, ctx.mkt, j, ia, ip, int(ctx.last_valid[j]))
    res["n_sessions_after_p"] = int(len(ctx.dates) - 1 - ip)
    return res


SUSPENSION_MIN_RUN = 5
"""Consecutive zero-volume sessions that count as a suspension (one quiet day is not)."""


def is_suspended(volume_a1_to_p: np.ndarray) -> bool:
    return _max_run(~(np.nan_to_num(volume_a1_to_p) > 0)) >= SUSPENSION_MIN_RUN


def _max_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def run_events(ev: pd.DataFrame, ctx: Ctx) -> tuple[pd.DataFrame, dict]:
    """Per-event results (one row per event) and path arrays keyed by event_id."""
    guard_holdout(ev)
    recs, paths = [], {}
    for row in ev.itertuples(index=False):
        r = process_event(row, ctx)
        p = r.pop("_paths", None)
        if p is not None:
            paths[row.event_id] = p
        recs.append({"event_id": row.event_id, **r})
    out = ev.merge(pd.DataFrame(recs), on="event_id", how="left")
    return out, paths


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

def cell_defs() -> list[tuple[str, str, callable]]:
    """(cell, side, mask_fn). side 'add' / 'del'."""
    def idx(name):
        return lambda d: d["index_name"] == name

    def adds(d):
        return d["change"].isin(["add", "migrate_in"])

    def dels(d):
        return d["change"].isin(["del", "migrate_out"])
    lm = ["Large", "Mid"]
    return [
        ("MSCI_ST", "add", lambda d: idx("MSCI_ST")(d) & adds(d)),
        ("MSCI_ST", "del", lambda d: idx("MSCI_ST")(d) & dels(d)),
        ("MSCI_SC", "add", lambda d: idx("MSCI_SC")(d) & adds(d)),
        ("MSCI_SC", "del", lambda d: idx("MSCI_SC")(d) & dels(d)),
        ("FTSE_ALL", "add", lambda d: (d["provider"] == "FTSE") & adds(d)),
        ("FTSE_ALL", "del", lambda d: (d["provider"] == "FTSE") & dels(d)),
        ("FTSE_AW(L+M)", "add", lambda d: (d["provider"] == "FTSE") & d["to_segment"].isin(lm) & ~d["from_segment"].isin(lm)),
        ("FTSE_AW(L+M)", "del", lambda d: (d["provider"] == "FTSE") & d["from_segment"].isin(lm) & ~d["to_segment"].isin(lm)),
    ]


def included(res: pd.DataFrame) -> pd.DataFrame:
    return res[res["status"] == "ok"]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fb(x, nd=0) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * BPS:.{nd}f}"


def ft(x) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.2f}"


def fs(x) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:.0f}%"


def legs_md(tab: dict) -> str:
    rows = ["| leg | N | reviews | mean bps | median bps | share of L1-L4 | t (review-clustered) |",
            "|---|---|---|---|---|---|---|"]
    names = {"L1": "L1 pre-announce", "L2": "L2 gap", "L3": "L3 first session", "L4": "L4 run-in",
             "T14": "**L1-L4 total**", "L5": "L5 post-passive", "T15": "full L1-L5"}
    for k in ("L1", "L2", "L3", "L4", "T14", "L5", "T15"):
        s = tab[k]
        share = fs(s.get("share")) if k in el.LEGS[:4] else ("100%" if k == "T14" else "")
        rows.append(f"| {names[k]} | {s['N']} | {s['reviews']} | {fb(s['mean'])} | {fb(s['median'])} | {share} | {ft(s['t'])} |")
    return "\n".join(rows)


def md_table(df: pd.DataFrame, index=True) -> str:
    d = df.reset_index() if index else df
    cols = [str(c) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in d.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def path_stats(sub: pd.DataFrame, paths: dict, key: str) -> pd.DataFrame:
    arr = np.vstack([paths[e][key] for e in sub["event_id"]])
    df = pd.DataFrame(arr)
    rev = sub["review_label"].to_numpy()
    rm = df.groupby(rev).mean()
    n = rm.notna().sum(axis=0)
    se = rm.std(ddof=1) / np.sqrt(n.where(n >= 2))
    return pd.DataFrame({"mean": df.mean(), "median": df.median(), "se": se, "n_events": df.notna().sum(axis=0)})


def make_plots(res: pd.DataFrame, paths: dict, out_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ok = included(res)
    files = []
    groups = [("MSCI_ST", ok["index_name"] == "MSCI_ST"), ("MSCI_SC", ok["index_name"] == "MSCI_SC"),
              ("FTSE_ALL", ok["provider"] == "FTSE")]
    colors = {"add": "#1b7f5c", "del": "#b3382c"}
    labels = {"add": "additions", "del": "deletions"}
    for name, gm in groups:
        g = ok[gm]
        if g.empty:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), sharey=False)
        for ax, (key, rng, title) in zip(axes, (("a", el.PATH_A, "anchored at a (announcement session)"),
                                                ("p", el.PATH_P, "anchored at p (passive-trade close)"))):
            x = np.arange(rng[0], rng[1] + 1)
            for side in ("add", "del"):
                sub = g[g["change"].isin(["add", "migrate_in"] if side == "add" else ["del", "migrate_out"])]
                if sub.empty:
                    continue
                st = path_stats(sub, paths, key)
                n_rev = sub["review_label"].nunique()
                ax.plot(x, st["mean"] * BPS, color=colors[side], lw=2,
                        label=f"{labels[side]} mean (N={len(sub)}, reviews={n_rev})")
                ax.plot(x, st["median"] * BPS, color=colors[side], lw=1.2, ls="--", label=f"{labels[side]} median")
                ax.fill_between(x, (st["mean"] - 2 * st["se"]) * BPS, (st["mean"] + 2 * st["se"]) * BPS,
                                color=colors[side], alpha=0.15, lw=0)
            ax.axhline(0, color="#888", lw=0.8)
            ax.axvline(0, color="#444", lw=0.8, ls=":")
            ax.set_title(title, fontsize=10)
            ax.set_xlabel("sessions relative to anchor")
            ax.set_ylabel("market-adjusted cumulative log return (bps)")
            ax.legend(fontsize=7, frameon=False)
        fig.suptitle(f"{name}: development window (announced <= 2021-12-31), band = mean +/- 2 SE from review means",
                     fontsize=10)
        fig.tight_layout()
        f = out_dir / f"event_time_{name.lower()}.png"
        fig.savefig(f, dpi=130)
        plt.close(fig)
        files.append(f)
    return files


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def exclusion_table(res: pd.DataFrame) -> pd.DataFrame:
    res = res.assign(group=np.where(res["provider"] == "FTSE", "FTSE", res["index_name"]))
    t = res.groupby(["group", "status"]).size().unstack("status", fill_value=0)
    t["total"] = t.sum(axis=1)
    return t


def flag_table(ok: pd.DataFrame) -> pd.DataFrame:
    ok = ok.assign(group=np.where(ok["provider"] == "FTSE", "FTSE", ok["index_name"]))
    cols = ["flag_locked_a1", "flag_no_trade_a1", "flag_partial_l5", "flag_halt_ge5_in_run"]
    t = ok.groupby("group")[cols].sum().astype(int)
    t.insert(0, "included", ok.groupby("group").size())
    return t


def build_report(ev: pd.DataFrame, dev_res: pd.DataFrame, frz_res: pd.DataFrame, plots: list[Path],
                 ctx: Ctx) -> tuple[str, dict]:
    have = local_tickers()
    cov = coverage_tables(ev, have)
    ok = included(dev_res)
    parts: list[str] = []
    P = parts.append
    summary: dict = {"readings": {}}
    P("# Index rebalance, Phase 0b: MSCI go/no-go, section 1a event-time breakdown\n")
    P("Plan: [21-index-rebalance-event-study](../plans/21-index-rebalance-event-study.md) (section 1a, section 4.3). "
      "Descriptive only: no gates, no G1-G8, no strategy backtest. Generated by "
      "`research/index_events/event_time.py` (re-runnable; every number below is that script's output).\n")
    P("**Development window only**: reviews announced <= 2021-12-31. Non-freeze reviews announced in 2022 or later "
      "are the untouched holdout: no statistic was computed on them (the code raises if one reaches the analysis). "
      "Freeze reviews (MSCI >= 2026-02, FTSE >= 2026-03) are a separate descriptive block.\n")
    P("**Market null**: equal-weight mean daily log return of all local-panel names that traded that day "
      "(volume > 0, |daily return| <= 50%), excluding the event name. IHSG (index series) is excluded from the panel. "
      f"Panel: {len(ctx.symbols)} local names, {len(ctx.dates)} sessions "
      f"({ctx.dates[0].date()} .. {ctx.dates[-1].date()}). "
      "L2/L3 use the market's overnight (close to open) and intraday (open to close) means on the same name set, "
      "so the five market legs sum exactly to the full-window market return. All returns in bps of log return.\n")

    # ---- coverage
    P("## 1. Data coverage\n")
    P("### 1.1 Events and reviews (all years; counts only)\n")
    P(md_table(cov["coverage"]) + "\n")
    P(md_table(cov["reviews"]) + "\n")
    P("FTSE counts exclude Micro-only moves; Mid<->Large moves are `other` (kept in the table, not in any cell). "
      "FTSE-2015-03 and FTSE-2015-09 have no FTSE calendar row and the date rule could not be confirmed, so their "
      "events carry no session dates and are excluded from the analysis (`no_dates`).\n")
    miss = cov["missing_bars"]
    P(f"### 1.2 Events whose ticker has no local bars (all years)\n")
    P(f"{int(miss['events'].sum()) if len(miss) else 0} events, {len(miss)} tickers "
      "(a fetch is pending; re-run `build_events` and `event_time` after it).\n")
    P(md_table(miss.groupby("provider")["ticker"].apply(lambda s: ", ".join(sorted(s))).to_frame("tickers")) + "\n")
    P(f"Unresolved / low-confidence mapping: {len(cov['unresolved'])} events "
      f"({', '.join(sorted(cov['unresolved']['provider_name'].unique()))}); kept in `events.csv` with "
      "`cross_check=mismatch` and excluded here as `ticker_unresolved`.\n")
    P("### 1.3 Development-window events and exclusions (plan section 4.3)\n")
    dev_n = dev_res.groupby(["provider", "index_name"]).agg(events=("event_id", "size"),
                                                           reviews=("review_label", "nunique"))
    P(md_table(dev_n) + "\n")
    P("Exclusion reason per event (first reason that applies). `no_bars_*` = required endpoint missing "
      "(close a-20, a, p, open/close a+1): `ticker_missing` has no local file; `not_yet_listed` first bar after a-20; "
      "`ended_before_p` last bar before p (delisted / ended); `gap_in_window` file spans the window but an "
      "endpoint has no bar (suspension). `bad_bar_gt50pct` = a one-day |return| > 50% in a-20..p+k.\n")
    P(md_table(exclusion_table(dev_res)) + "\n")
    P("Flags on **included** events (kept in all statistics): `locked_a1` = ARA-style up-limit close at a+1 or a+1 "
      "range entirely above +19% of close_a (existing `detect_limit_events` / `locked_up_entry`); `no_trade_a1` = "
      "zero volume at a+1; `partial_l5` = fewer than 20 sessions after p were available (L5 uses the longest "
      "available horizon; none are excluded); `halt_ge5_in_run` = 5+ consecutive zero-volume/no-bar sessions "
      "between a+1 and p.\n")
    P(md_table(flag_table(ok)) + "\n")

    # ---- legs
    P("## 2. Legs per cell (development window)\n")
    P(f"Cells with fewer than {el.FLOOR_REVIEWS} reviews are **below floor** (descriptive only). Additions = add + "
      "migrate_in into the index; deletions = del + migrate_out. FTSE_AW(L+M) = FTSE moves into / out of "
      "Large+Mid (All-World).\n")
    for cell, side, fn in cell_defs():
        sub = ok[fn(ok)]
        tab = el.cell_table(sub)
        reads = el.decision_readings(tab, side)
        floor = tab["reviews"] < el.FLOOR_REVIEWS
        summary["readings"][(cell, side)] = (tab, reads, floor)
        title = f"{cell} {'additions' if side == 'add' else 'deletions'}"
        P(f"### {title}: {tab['events']} events, {tab['reviews']} reviews" + (" (below floor)" if floor else "") + "\n")
        P(legs_md(tab) + "\n")
        P("Reading: " + "; ".join(reads) + (" [below floor: descriptive only]" if floor else "") + "\n")

    P("## 3. Event-time paths\n")
    P("Mean (solid) and median (dashed) market-adjusted cumulative log return; band = event mean +/- 2 SE, "
      "SE from review means (events averaged within a review first).\n")
    for f in plots:
        P(f"![{f.stem}](../../research/out_index_events/{f.name})\n")

    # ---- freeze
    P("## 4. Freeze block (descriptive)\n")
    P("MSCI reviews from 2026-02 and FTSE-2026-09. Different regime (deletion-only, HSC-driven, stressed market); "
      "no clustered t is reported and nothing here is a gate input.\n")
    P(md_table(exclusion_table(frz_res)) + "\n")
    fok = included(frz_res)
    if len(fok):
        rows = []
        for (rev, idxn, ch), g in fok.groupby(["review_label", "index_name", "change"]):
            rows.append({"review": rev, "index": idxn, "change": ch, "N": len(g),
                         **{L: fb(g[L].mean()) for L in el.LEGS}, "L1-L4": fb(g["T14"].mean()),
                         "L5 sessions": f"{int(g['l5_k'].min())}-{int(g['l5_k'].max())}",
                         "sessions after p": int(g["n_sessions_after_p"].iloc[0])})
        P("Mean market-adjusted legs in bps per review x index x change:\n")
        P(md_table(pd.DataFrame(rows), index=False) + "\n")
        P("Per event:\n")
        pe = fok[["review_label", "index_name", "change", "ticker", "L1", "L2", "L3", "L4", "L5", "l5_k",
                  "flag_locked_a1"]].copy()
        for L in el.LEGS:
            pe[L] = pe[L].map(fb)
        P(md_table(pe, index=False) + "\n")
    else:
        P("No freeze events with bars.\n")

    # ---- implications
    P("## 5. What this means for plan 21 Phase 1\n")
    P("Facts from the tables above, read through the plan section 1a decision table (thresholds used: share >= 50% "
      "and |t| >= 2 for anticipation; L2 share >= 50% for 'L2 dominates'; L4 mean > 0 with t >= 2 for 'L4 "
      "positive and material'; deletions need L1-L4 mean < 0 and L5 mean > 0, supported if L5 t >= 2).\n")
    for (cell, side), (tab, reads, floor) in summary["readings"].items():
        P(f"- {cell} {'additions' if side == 'add' else 'deletions'} ({tab['events']} events, {tab['reviews']} "
          f"reviews{', below floor' if floor else ''}): " + "; ".join(reads))
    P("")
    P("L3-L5 means (bps) and review-clustered t per cell, development window:\n")
    rows = []
    for (cell, side), (tab, reads, floor) in summary["readings"].items():
        rows.append({"cell": f"{cell} {'add' if side == 'add' else 'del'}", "events": tab["events"],
                     "reviews": tab["reviews"], "floor": "below" if floor else "ok",
                     **{f"{k} mean (t)": f"{fb(tab[k]['mean'])} ({ft(tab[k]['t'])})" for k in ("L3", "L4", "L5")}})
    P(md_table(pd.DataFrame(rows), index=False) + "\n")
    P("Phase 0b only runs this descriptive step. The plan's rule for closing the family (no material L3-L5 "
      "move at MSCI-sized flows) and for promoting H4 applies to the cells above that are at or above the "
      f"{el.FLOOR_REVIEWS}-review floor; cells below it are descriptive and cannot trigger either. "
      "Bars for the tickers listed in section 1.2 are still missing; the excluded counts in section 1.3 will change "
      "after the fetch.\n")
    return "\n".join(parts), summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev = pd.read_csv(EVENTS_CSV, dtype=str, keep_default_na=False)
    dev, frz = split_events(ev)
    print(f"development events: {len(dev)} ({dev['review_label'].nunique()} reviews); freeze events: {len(frz)} "
          f"({frz['review_label'].nunique()} reviews)")
    panel, lim_up, lock_next = load_panel()
    ctx = Ctx(panel, lim_up, lock_next)
    dev_res, dev_paths = run_events(dev, ctx)
    frz_res, frz_paths = run_events(frz, ctx)
    keep = [c for c in dev_res.columns if not c.startswith("_")]
    dev_res[keep].to_csv(OUT_DIR / "legs_dev.csv", index=False)
    frz_res[keep].to_csv(OUT_DIR / "legs_freeze.csv", index=False)
    plots = make_plots(dev_res, dev_paths, OUT_DIR)
    text, summary = build_report(ev, dev_res, frz_res, plots, ctx)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(text)
    print(text)
    print(f"\nwrote {DOC_PATH}")


if __name__ == "__main__":
    sys.exit(main())
