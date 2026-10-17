"""
foreign_flow/checks.py - plan 22 section 2.3 data checks 1-5 plus the 5% missing-name-day rule.

Pure functions take frames; the CLI loads data/foreign_flow/*.parquet, data/daily and
universe_monthly.csv and writes data/foreign_flow/checks.md.

Membership for a session ``s`` is the universe of the latest month-end strictly before ``s``
(same no-look-ahead rule as universe.py). Sessions before the first month-end are not scored.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from foreign_flow import DAILY_DIR, DATA_DIR, UNIVERSE_CSV
from foreign_flow.universe import load_daily, read_universe_csv

NET_TOL_RP = 1_000_000
CLOSE_MATCH_MIN = 0.99
CLOSE_TOL = 0.5
MISSING_BLOCK_SHARE = 0.05
VALUE_BOUND_FACTOR = 2.0


def check_net_identity(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """(1) rows where |net_foreign - (foreign_buy - foreign_sell)| > Rp 1M."""
    bad = []
    for sym, f in frames.items():
        diff = (f["net_foreign"] - (f["foreign_buy"] - f["foreign_sell"])).abs()
        for i in f.index[diff > NET_TOL_RP]:
            bad.append((sym, f.at[i, "date"], float(diff[i])))
    return pd.DataFrame(bad, columns=["symbol", "date", "abs_diff"])


def check_sessions(frames: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame],
                   calendar: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(2) (a) foreign dates not on the market calendar; (b) own-bar sessions inside the
    symbol's foreign span with no foreign row."""
    not_session, missing = [], []
    cal = set(calendar)
    for sym, f in frames.items():
        dates = set(f["date"])
        not_session += [(sym, d) for d in sorted(dates - cal)]
        if f.empty or sym not in daily:
            continue
        lo, hi = f["date"].min(), f["date"].max()
        own = daily[sym]["date"]
        inside = own[(own >= lo) & (own <= hi)]
        missing += [(sym, d) for d in inside if d not in dates]
    return (pd.DataFrame(not_session, columns=["symbol", "date"]),
            pd.DataFrame(missing, columns=["symbol", "date"]))


def check_value_bound(frames: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """(3) buy + sell > 2 x chartbit value traded on the same day."""
    bad = []
    for sym, f in frames.items():
        if sym not in daily or f.empty:
            continue
        m = f.merge(daily[sym][["date", "value"]], on="date", how="inner")
        over = m[(m["foreign_buy"] + m["foreign_sell"]) > VALUE_BOUND_FACTOR * m["value"]]
        bad += [(sym, r.date, r.foreign_buy + r.foreign_sell, r.value) for r in over.itertuples()]
    return pd.DataFrame(bad, columns=["symbol", "date", "buy_plus_sell", "value"])


def check_close_match(frames: dict[str, pd.DataFrame],
                      daily: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, float]:
    """(4) sb_close vs chartbit close. Returns (mismatches, overall match rate over comparable rows)."""
    bad, total, ok = [], 0, 0
    for sym, f in frames.items():
        if sym not in daily or f.empty:
            continue
        m = f.merge(daily[sym][["date", "close"]], on="date", how="inner").dropna(subset=["sb_close", "close"])
        match = (m["sb_close"] - m["close"]).abs() <= CLOSE_TOL
        total += len(m)
        ok += int(match.sum())
        bad += [(sym, r.date, r.sb_close, r.close) for r in m[~match].itertuples()]
    rate = ok / total if total else float("nan")
    return pd.DataFrame(bad, columns=["symbol", "date", "sb_close", "chartbit_close"]), rate


def coverage_table(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """(5) rows per symbol per year plus first_date."""
    rows = []
    for sym, f in sorted(frames.items()):
        if f.empty:
            continue
        counts = f.groupby(f["date"].dt.year).size()
        rec = {"symbol": sym, "first_date": f["date"].min().strftime("%Y-%m-%d")}
        rec.update({int(y): int(n) for y, n in counts.items()})
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    years = sorted(c for c in df.columns if isinstance(c, int))
    return df[["symbol", "first_date", *years]].fillna(0)


def missing_name_days(frames: dict[str, pd.DataFrame], universe: pd.DataFrame,
                      calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """Per year: universe name-days expected vs missing a foreign row; share > 5% => data_blocked."""
    uni = universe.assign(month_end=pd.to_datetime(universe["month_end"]))
    ends = sorted(uni["month_end"].unique())
    members = {e: sorted(g["symbol"]) for e, g in uni.groupby("month_end")}
    have = {s: set(f["date"]) for s, f in frames.items()}
    expected: dict[int, int] = {}
    missing: dict[int, int] = {}
    for i, e in enumerate(ends):
        nxt = ends[i + 1] if i + 1 < len(ends) else calendar.max()
        for s in calendar[(calendar > e) & (calendar <= nxt)]:
            for sym in members[e]:
                expected[s.year] = expected.get(s.year, 0) + 1
                if s not in have.get(sym, ()):
                    missing[s.year] = missing.get(s.year, 0) + 1
    rows = []
    for y in sorted(expected):
        share = missing.get(y, 0) / expected[y]
        rows.append((y, expected[y], missing.get(y, 0), share,
                     "data_blocked" if share > MISSING_BLOCK_SHARE else "ok"))
    return pd.DataFrame(rows, columns=["year", "expected", "missing", "missing_share", "status"])


def load_foreign(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(Path(data_dir).glob("*.parquet")):
        f = pd.read_parquet(p)
        f["date"] = pd.to_datetime(f["date"])
        out[p.stem] = f.sort_values("date").reset_index(drop=True)
    return out


def market_calendar(daily_dir: Path, daily: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """IHSG bars when present (clean index calendar), else the union of stock bar dates."""
    ihsg = Path(daily_dir) / "IHSG.parquet"
    if ihsg.exists():
        dates = pd.to_datetime(pd.read_parquet(ihsg, columns=["date"])["date"])
        return pd.DatetimeIndex(dates).unique().sort_values()
    return pd.DatetimeIndex(sorted({d for f in daily.values() for d in f["date"]}))


def _table(df: pd.DataFrame, limit: int = 15) -> str:
    if df.empty:
        return "none\n"
    shown = df.head(limit).to_string(index=False)
    more = f"\n... and {len(df) - limit} more" if len(df) > limit else ""
    return f"```\n{shown}{more}\n```\n"


def build_report(frames, daily, calendar, universe) -> tuple[str, dict]:
    c1 = check_net_identity(frames)
    c2a, c2b = check_sessions(frames, daily, calendar)
    c3 = check_value_bound(frames, daily)
    c4, rate = check_close_match(frames, daily)
    cov = coverage_table(frames)
    mnd = missing_name_days(frames, universe, calendar)
    blocked = [int(y) for y in mnd.loc[mnd["status"] == "data_blocked", "year"]]
    stats = {"c1": len(c1), "c2a": len(c2a), "c2b": len(c2b), "c3": len(c3), "c4_rate": rate,
             "symbols": len(frames), "rows": sum(len(f) for f in frames.values()), "blocked": blocked}
    close_flag = "OK" if rate >= CLOSE_MATCH_MIN else "FAIL (<99%)"
    md = [
        "# Foreign-flow data checks (plan 22 s2.3)", "",
        f"symbols: {stats['symbols']}  rows: {stats['rows']}", "",
        f"1. net = buy - sell (tol Rp 1M): {len(c1)} violations", _table(c1),
        f"2a. foreign dates not on the market calendar: {len(c2a)}", _table(c2a),
        f"2b. own-bar sessions inside the foreign span with no foreign row: {len(c2b)}", _table(c2b),
        f"3. buy + sell > 2 x chartbit value: {len(c3)} violations", _table(c3),
        f"4. sb_close == chartbit close: match rate {rate:.4f} -> {close_flag}; "
        f"{len(c4)} mismatches", _table(c4),
        "5. coverage (rows per symbol per year, first date)", _table(cov, limit=len(cov) or 1),
        "Missing universe name-days per year (>5% => data_blocked)", _table(mnd, limit=len(mnd) or 1),
        f"data_blocked years: {blocked or 'none'}", "",
    ]
    return "\n".join(md), stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run plan 22 s2.3 data checks 1-5.")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--daily-dir", type=Path, default=DAILY_DIR)
    ap.add_argument("--universe", type=Path, default=UNIVERSE_CSV)
    args = ap.parse_args(argv)
    frames = load_foreign(args.data_dir)
    if not frames:
        print("no foreign-flow parquet files found: run python -m foreign_flow.fetch first")
        return 2
    daily = load_daily(args.daily_dir)
    report, s = build_report(frames, daily, market_calendar(args.daily_dir, daily),
                             read_universe_csv(args.universe))
    out = args.data_dir / "checks.md"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(report)
    os.replace(tmp, out)
    print(f"symbols {s['symbols']}, rows {s['rows']}; c1 {s['c1']}, c2a {s['c2a']}, c2b {s['c2b']}, "
          f"c3 {s['c3']}, close-match {s['c4_rate']:.4f}; data_blocked years: {s['blocked'] or 'none'}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
