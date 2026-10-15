"""
strategy_fit/families_idx_structural.py — IDX-structure alpha search (2026-10).

Families that earlier harness passes never touched, built on the full listing-date
daily history (research/data/daily) rather than the 2024+ window:

  * cross-sectional, low-turnover factor books (monthly / quarterly rebalance):
    low volatility, 12-1 momentum, 52-week-high proximity, accumulated foreign
    flow, 1-month reversal, trailing dividend yield, overnight-return persistence;
  * limit-class events (ARA-like close-at-high / ARB-like close-at-low);
  * dividend ex-date events (run-up, cum-date capture, post-ex rebound);
  * calendar windows (turn-of-month, pre-Lebaran) on a liquid equal-weight basket;
  * a descriptive overnight / intraday decomposition.

Every candidate goes through the SAME gates as the rest of strategy_fit; none is
weakened here:

  G1  live-feasible entry — signal on the close of day s, entry at close_{s+1}
      (entry_convention.next_close; next_open as sensitivity). Calendar-schedule
      events (dividend ex-dates, turn-of-month, Eid) use only information that is
      public long in advance and never a price after the decision day.
  G2  costs — one-tick spread + 40 bps round trip + point-in-time square-root
      impact (impact.py, calibrated coefficients, trailing 60-bar ADV / vol) +
      financing sensitivity (capital_model.LEVERAGED_SLEEVE; applies only to
      holds <= 2 trading days — longer holds are owned-cash only, ADR-0015).
      Factor books pay cost ONLY on names that enter or leave the book.
  G3  always-long null — event trades are scored against (a) the same name's
      unconditional mean forward return in the same calendar year and (b) the
      equal-weight eligible universe on the same entry day; factor books against
      the equal-weight eligible universe over identical windows. Both nets must be
      positive.
  G4  out-of-sample — chronological 60/40 (no parameter is fitted, so the split is
      a stability check, not a selection tool).
  G5  multiple testing — Bonferroni over every variant tried in this search.
  G6  capacity — net must survive a 10x position size.

Known limits (reported, not hidden): the local universe is survivor-biased;
prices are split-adjusted but NOT dividend-adjusted (dividends are added back
explicitly, net of 10% withholding); extreme |1-day| moves > 50% are treated as
bad bars and every forward window touching one is dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy import stats as _sps

from strategy_fit.capital_model import LEVERAGED_SLEEVE
from strategy_fit.entry_convention import ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN
from strategy_fit.financing import financing_bps
from strategy_fit.impact import get_impact_coeff
from strategy_fit.run_strategy_fit import ROUND_TRIP_BPS

ROUND_TRIP_FEE_BPS: float = float(ROUND_TRIP_BPS)
DEGENERATE_IMPACT_BPS: float = 10_000.0
DIVIDEND_WITHHOLDING_TAX: float = 0.10
BAD_BAR_ABS_RETURN: float = 0.50
ADV_WINDOW: int = 60
IS_FRAC: float = 0.60
MIN_CLUSTERS: int = 30
MIN_OOS_CLUSTERS: int = 30
BONFERRONI_ALPHA: float = 0.05

UP_EVENT_MIN_RET: float = 0.19      # ARA-class: >= +19% and close at the day's high
DOWN_EVENT_MAX_RET: float = -0.14   # ARB-class: <= -14% and close at the day's low
LOCKED_ENTRY_MIN_RET: float = 0.19  # whole next-day range above +19% => cannot buy

_ENTRIES = (ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN)

# Eid al-Fitr (1 Syawal), Gregorian. The window ends on the last trading day
# BEFORE the date, so a +/-1 day error is harmless; the runner also asserts the
# market really was closed around each date.
EID_AL_FITR: tuple[str, ...] = (
    "2001-12-16", "2002-12-06", "2003-11-25", "2004-11-14", "2005-11-03", "2006-10-24",
    "2007-10-13", "2008-10-01", "2009-09-20", "2010-09-10", "2011-08-31", "2012-08-19",
    "2013-08-08", "2014-07-28", "2015-07-17", "2016-07-06", "2017-06-25", "2018-06-15",
    "2019-06-05", "2020-05-24", "2021-05-13", "2022-05-02", "2023-04-22", "2024-04-10",
    "2025-03-31", "2026-03-20",
)

# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Panel:
    """Wide (dates x symbols) daily panel on the common trading calendar."""

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    value_idr: pd.DataFrame
    net_foreign: pd.DataFrame
    foreign_gross: pd.DataFrame
    dividend: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def build_panel(hist: dict[str, pd.DataFrame], min_names_trading: int = 10) -> Panel:
    """Align per-symbol daily frames on the union calendar.

    A date is kept only if at least ``min_names_trading`` names traded
    (volume > 0) — this drops market-wide holidays that some feeds pad with
    zero-volume rows.
    """
    if not hist:
        raise ValueError("build_panel needs at least one symbol")
    cols = sorted(hist)
    fields: dict[str, dict[str, pd.Series]] = {k: {} for k in (
        "open", "high", "low", "close", "volume", "value", "net_foreign", "foreign_gross", "dividend")}
    for sym in cols:
        df = hist[sym].copy()
        df.index = pd.DatetimeIndex(pd.to_datetime(df["date"].astype(str)))
        df = df[~df.index.duplicated(keep="last")].sort_index()
        for f in ("open", "high", "low", "close", "volume", "value", "dividend"):
            fields[f][sym] = _numeric(df, f)
        buy, sell = _numeric(df, "foreignbuy"), _numeric(df, "foreignsell")
        nf = _numeric(df, "net_foreign")
        fields["net_foreign"][sym] = nf if nf.notna().any() else (buy - sell)
        fields["foreign_gross"][sym] = buy + sell
    wide = {k: pd.concat(v, axis=1).sort_index()[cols] for k, v in fields.items()}
    n_trading = (wide["volume"].fillna(0) > 0).sum(axis=1)
    keep = n_trading >= min_names_trading
    wide = {k: v.loc[keep] for k, v in wide.items()}
    for k in ("open", "high", "low", "close"):      # 0 / negative prices are missing data, not prices
        wide[k] = wide[k].where(wide[k] > 0)
    close, volume = wide["close"], wide["volume"]
    value = wide["value"].where(wide["value"] > 0, close * volume)
    return Panel(
        open=wide["open"], high=wide["high"], low=wide["low"], close=close, volume=volume,
        value_idr=value, net_foreign=wide["net_foreign"], foreign_gross=wide["foreign_gross"],
        dividend=wide["dividend"],
    )


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


def tick_bps(price: np.ndarray) -> np.ndarray:
    """One IDX tick expressed in bps of price (fraksi harga ladder)."""
    p = np.asarray(price, dtype=float)
    tick = np.select([p < 200, p < 500, p < 2000, p < 5000], [1.0, 2.0, 5.0, 10.0], default=25.0)
    return tick / p * 1e4


def impact_inputs(panel: Panel) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trailing (point-in-time, ending at the signal day) ADV in IDR and daily vol in bps."""
    adv = panel.value_idr.rolling(ADV_WINDOW, min_periods=20).median()
    logret = np.log(panel.close).diff()
    vol = logret.rolling(ADV_WINDOW, min_periods=20).std() * 1e4
    return adv, vol


def cost_components(
    price: np.ndarray, adv: np.ndarray, vol_bps: np.ndarray, notional_idr: float, symbols: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(floor_bps, impact_bps, coeff) for a ROUND TRIP at ``notional_idr``."""
    price = np.asarray(price, dtype=float)
    adv = np.asarray(adv, dtype=float)
    vol = np.asarray(vol_bps, dtype=float)
    floor = tick_bps(price) + ROUND_TRIP_FEE_BPS
    coeff = np.array(
        [get_impact_coeff(s, adv_notional_idr=a if np.isfinite(a) and a > 0 else None)
         for s, a in zip(symbols, adv)], dtype=float)
    bad = ~np.isfinite(adv) | (adv <= 0) | ~np.isfinite(vol)
    part = np.clip(notional_idr / np.where(bad, 1.0, adv), 0.0, 1.0)
    impact = 2.0 * coeff * np.where(bad, 0.0, vol) * np.sqrt(part)
    impact = np.where(bad, DEGENERATE_IMPACT_BPS, impact)
    return floor, impact, coeff


def trade_cost_bps(price, adv, vol_bps, notional_idr: float, symbols: Sequence[str]) -> np.ndarray:
    floor, impact, _ = cost_components(price, adv, vol_bps, notional_idr, symbols)
    return floor + impact


# ---------------------------------------------------------------------------
# Eligibility and forward returns
# ---------------------------------------------------------------------------


def eligibility(
    panel: Panel,
    *,
    min_adv_idr: float = 2e9,
    min_price: float = 100.0,
    window: int = ADV_WINDOW,
    max_zero_vol_days: int = 3,
    min_history: int = 252,
) -> pd.DataFrame:
    """Point-in-time tradeable mask on signal day s (uses bars <= s only)."""
    adv = panel.value_idr.rolling(window, min_periods=window).median()
    zeros = (panel.volume.fillna(0) == 0).astype(float).rolling(window, min_periods=window).sum()
    history = panel.close.notna().cumsum()
    ok = (adv >= min_adv_idr) & (panel.close >= min_price) & (zeros <= max_zero_vol_days) & (history >= min_history)
    return ok.fillna(False).astype(bool)


def fwd_total_return(
    panel: Panel,
    hold: int,
    entry: str = ENTRY_NEXT_CLOSE,
    *,
    div_tax: float = DIVIDEND_WITHHOLDING_TAX,
    bad_move: float = BAD_BAR_ABS_RETURN,
) -> pd.DataFrame:
    """Total return of buying at session s+1 and selling ``hold`` sessions later.

    Row s is the SIGNAL day. The signal-day price is never used as an entry
    (D1). Dividends with an ex-date in (s+1, s+1+hold] are added back net of
    withholding tax. NaN where the entry day did not trade, a price is missing,
    or a bar with |1-day return| > ``bad_move`` (suspected unadjusted split)
    falls inside the window.
    """
    if entry not in _ENTRIES:
        raise ValueError(f"entry must be one of {_ENTRIES}, got {entry!r}")
    px = panel.close if entry == ENTRY_NEXT_CLOSE else panel.open
    p_in = px.shift(-1)
    p_out = px.shift(-(1 + hold))
    cd = (panel.dividend.fillna(0.0) * (1.0 - div_tax)).cumsum()
    dsum = cd.shift(-(1 + hold)) - cd.shift(-1)
    tr = (p_out + dsum) / p_in - 1.0
    bad = (panel.close.pct_change(fill_method=None).abs() > bad_move).astype(float)
    cb = bad.cumsum()
    window_bad = (cb.shift(-(1 + hold)) - cb) > 0
    entry_traded = panel.volume.shift(-1) > 0
    valid = entry_traded & ~window_bad & (p_in > 0) & (p_out > 0)
    return tr.where(valid)


# ---------------------------------------------------------------------------
# Factors (all causal: value at row t uses bars <= t only; higher = better)
# ---------------------------------------------------------------------------


def factor_low_vol(panel: Panel, window: int = 60) -> pd.DataFrame:
    ret = panel.close.pct_change(fill_method=None)
    return -ret.rolling(window, min_periods=window).std()


def factor_mom_12_1(panel: Panel) -> pd.DataFrame:
    return panel.close.shift(21) / panel.close.shift(252) - 1.0


def factor_high52w(panel: Panel) -> pd.DataFrame:
    return panel.close / panel.close.rolling(252, min_periods=252).max()


def factor_foreign_accum(panel: Panel, window: int = 60) -> pd.DataFrame:
    nf = panel.net_foreign.rolling(window, min_periods=int(window * 2 / 3)).sum()
    gv = panel.value_idr.rolling(window, min_periods=int(window * 2 / 3)).sum()
    gross = panel.foreign_gross.rolling(window, min_periods=int(window * 2 / 3)).sum()
    return (nf / gv).where(gross > 0)


def factor_rev_1m(panel: Panel) -> pd.DataFrame:
    return -(panel.close / panel.close.shift(21) - 1.0)


def factor_div_yield(panel: Panel) -> pd.DataFrame:
    trailing = panel.dividend.fillna(0.0).rolling(252, min_periods=1).sum()
    return trailing / panel.close


def factor_overnight_persist(panel: Panel, window: int = 60) -> pd.DataFrame:
    overnight = panel.open / panel.close.shift(1) - 1.0
    return overnight.rolling(window, min_periods=window).mean()


FACTORS: dict[str, Callable[[Panel], pd.DataFrame]] = {
    "low_vol": factor_low_vol,
    "mom_12_1": factor_mom_12_1,
    "high52w": factor_high52w,
    "foreign_accum": factor_foreign_accum,
    "rev_1m": factor_rev_1m,
    "div_yield": factor_div_yield,
    "overnight_persist": factor_overnight_persist,
}


# ---------------------------------------------------------------------------
# Cross-sectional factor book
# ---------------------------------------------------------------------------

PERIOD_COLUMNS = ["date", "pos", "n_elig", "n_top", "gross", "null", "cost_bps", "turnover"]


def run_factor_backtest(
    panel: Panel,
    factor: pd.DataFrame,
    hold: int,
    *,
    eligible: pd.DataFrame,
    entry: str = ENTRY_NEXT_CLOSE,
    phase: int = 0,
    top_frac: float = 0.2,
    min_top: int = 5,
    min_universe: int = 25,
    notional_idr: float = 300e6,
) -> pd.DataFrame:
    """Equal-weight top-``top_frac`` long-only book, rebalanced every ``hold`` sessions.

    Period k signals on close s_k, enters close_{s_k+1} and exits close_{s_k+1+hold}
    = the next period's entry, so windows are contiguous and non-overlapping.
    ``null`` is the equal-weight mean over every eligible name with a valid
    factor value and forward return (the always-long benchmark for this book).
    Cost is charged only for names that enter or leave the book (half a round
    trip per leg); names that stay cost nothing.
    """
    fwd = fwd_total_return(panel, hold, entry)
    adv, vol = impact_inputs(panel)
    elig_v = eligible.reindex_like(fwd).fillna(False).to_numpy(dtype=bool)
    fac_v = factor.reindex_like(fwd).to_numpy(dtype=float)
    fwd_v = fwd.to_numpy(dtype=float)
    close_v = panel.close.to_numpy(dtype=float)
    adv_v = adv.to_numpy(dtype=float)
    vol_v = vol.to_numpy(dtype=float)
    syms = np.array(panel.symbols)
    n = len(panel.dates)

    ok_all = elig_v & np.isfinite(fac_v) & np.isfinite(fwd_v)
    counts = ok_all.sum(axis=1)
    starts = np.flatnonzero(counts >= min_universe)
    if len(starts) == 0:
        return pd.DataFrame(columns=PERIOD_COLUMNS)
    rows = []
    prev: set[int] = set()
    s = int(starts[0]) + int(phase)
    while s + 1 + hold < n:
        ok = ok_all[s]
        idx = np.flatnonzero(ok)
        if len(idx) < min_universe:
            s += hold
            continue
        n_top = max(min_top, int(round(top_frac * len(idx))))
        order = idx[np.argsort(-fac_v[s, idx], kind="stable")]
        sel = set(int(i) for i in order[:n_top])
        entering = sorted(sel - prev)
        exiting = sorted(prev - sel)
        per_name = notional_idr / n_top
        touched = np.array(entering + exiting, dtype=int)
        cost = 0.0
        if len(touched):
            floor, impact, _ = cost_components(
                close_v[s + 1, touched], adv_v[s, touched], vol_v[s, touched], per_name, syms[touched])
            c = floor + impact
            if not np.isfinite(c).all():
                fill = np.nanmedian(c[np.isfinite(c)]) if np.isfinite(c).any() else DEGENERATE_IMPACT_BPS
                c = np.where(np.isfinite(c), c, fill)
            cost = float(c.sum() / 2.0 / n_top)
        gross = float(np.nanmean(fwd_v[s, sorted(sel)]))
        null = float(np.nanmean(fwd_v[s, idx]))
        rows.append((panel.dates[s], s, len(idx), n_top, gross, null, cost, len(entering) / n_top))
        prev = sel
        s += hold
    return pd.DataFrame(rows, columns=PERIOD_COLUMNS)


# ---------------------------------------------------------------------------
# Limit-class events
# ---------------------------------------------------------------------------


def detect_limit_events(panel: Panel, kind: str) -> pd.DataFrame:
    """Signal-day mask for ARA-like ('up') or ARB-like ('down') closes.

    Proxy by realised bar shape (tier-agnostic, causal): 'up' = day return >=
    +19% and close at the day's high; 'down' = <= -14% and close at the day's
    low. Bars with |return| > 50% are treated as bad bars and never flagged.
    """
    if kind not in ("up", "down"):
        raise ValueError("kind must be 'up' or 'down'")
    ret = panel.close / panel.close.shift(1) - 1.0
    traded = panel.volume.fillna(0) > 0
    sane = ret.abs() <= BAD_BAR_ABS_RETURN
    if kind == "up":
        m = (ret >= UP_EVENT_MIN_RET) & (panel.close >= panel.high - 1e-9)
    else:
        m = (ret <= DOWN_EVENT_MAX_RET) & (panel.close <= panel.low + 1e-9)
    return (m & traded & sane).fillna(False).astype(bool)


def locked_up_entry(panel: Panel) -> pd.DataFrame:
    """True at s when day s+1 traded entirely above +19% of close_s (nothing to buy)."""
    nxt_low = panel.low.shift(-1)
    return ((nxt_low / panel.close - 1.0) >= LOCKED_ENTRY_MIN_RET).fillna(False).astype(bool)


# ---------------------------------------------------------------------------
# Event trades
# ---------------------------------------------------------------------------

TRADE_COLUMNS = [
    "symbol", "pos", "date", "gross", "null_name", "null_mkt", "floor_bps", "impact_bps", "cost_bps",
    "coeff", "adv", "vol_bps", "fin_bps",
]


def build_event_trades(
    panel: Panel,
    mask: pd.DataFrame,
    hold: int,
    entry: str = ENTRY_NEXT_CLOSE,
    *,
    eligible: pd.DataFrame,
    notional_idr: float = 30e6,
    sequential: bool = True,
    skip_locked: bool = False,
) -> pd.DataFrame:
    """One row per executable event trade (sequential, no overlap within a name)."""
    fwd = fwd_total_return(panel, hold, entry)
    adv, vol = impact_inputs(panel)
    elig = eligible.reindex_like(fwd).fillna(False).astype(bool)
    cand = mask.reindex_like(fwd).fillna(False).astype(bool) & elig & fwd.notna()
    if skip_locked:
        cand = cand & ~locked_up_entry(panel).reindex_like(fwd)
    null_name = fwd.groupby(fwd.index.year).transform("mean")
    null_mkt = fwd.where(elig).mean(axis=1)
    cand_v = cand.to_numpy(dtype=bool)
    syms = panel.symbols
    rec_sym: list[str] = []
    rec_pos: list[int] = []
    for j, sym in enumerate(syms):
        last_ok = -1
        for s in np.flatnonzero(cand_v[:, j]):
            if sequential and s < last_ok:
                continue
            rec_sym.append(sym)
            rec_pos.append(int(s))
            last_ok = int(s) + hold
    if not rec_pos:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    pos = np.array(rec_pos)
    jj = np.array([syms.index(x) for x in rec_sym])
    price = panel.close.to_numpy(dtype=float)[pos + 1, jj]
    adv_v = adv.to_numpy(dtype=float)[pos, jj]
    vol_v = vol.to_numpy(dtype=float)[pos, jj]
    floor, impact, coeff = cost_components(price, adv_v, vol_v, notional_idr, rec_sym)
    dows = panel.dates[pos + 1].dayofweek.to_numpy()
    fin = np.array([
        financing_bps(int(d), hold, capital_model=LEVERAGED_SLEEVE) if hold <= 2 else np.nan for d in dows])
    out = pd.DataFrame({
        "symbol": rec_sym,
        "pos": pos,
        "date": panel.dates[pos],
        "gross": fwd.to_numpy(dtype=float)[pos, jj],
        "null_name": null_name.to_numpy(dtype=float)[pos, jj],
        "null_mkt": null_mkt.to_numpy(dtype=float)[pos],
        "floor_bps": floor,
        "impact_bps": impact,
        "cost_bps": floor + impact,
        "coeff": coeff,
        "adv": adv_v,
        "vol_bps": vol_v,
        "fin_bps": fin,
    })
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)


def recost(trades: pd.DataFrame, notional_idr: float) -> pd.DataFrame:
    """Same trades re-priced at another position size (floor unchanged)."""
    out = trades.copy()
    adv = out["adv"].to_numpy(dtype=float)
    vol = out["vol_bps"].to_numpy(dtype=float)
    bad = ~np.isfinite(adv) | (adv <= 0) | ~np.isfinite(vol)
    part = np.clip(notional_idr / np.where(bad, 1.0, adv), 0.0, 1.0)
    impact = 2.0 * out["coeff"].to_numpy(dtype=float) * np.where(bad, 0.0, vol) * np.sqrt(part)
    out["impact_bps"] = np.where(bad, DEGENERATE_IMPACT_BPS, impact)
    out["cost_bps"] = out["floor_bps"] + out["impact_bps"]
    return out


# ---------------------------------------------------------------------------
# Dividend + calendar schedules
# ---------------------------------------------------------------------------

# variant -> (entry offset relative to the ex-date bar x, hold in sessions).
# Entry bar is x + offset; the DECISION day is entry - 1 so the harness's
# next_close convention lands on the intended bar.
DIVIDEND_VARIANTS: dict[str, tuple[int, int]] = {
    "runup": (-6, 5),         # buy close(x-6), sell close(x-1): pre-ex run-up, no dividend
    "cum_capture": (-1, 2),   # buy cum-day close(x-1), hold through ex to close(x+1)
    "post_ex": (0, 5),        # buy ex-day close(x), hold 5: rebound after the ex drop
}


def dividend_event_mask(panel: Panel, variant: str, min_yield: float = 0.02) -> tuple[pd.DataFrame, int]:
    """(decision-day mask, hold) for ex-date events with dividend/price >= ``min_yield``.

    The yield filter uses the close of the DECISION day only; the ex-date and
    amount are corporate-calendar facts announced weeks ahead (assumption flagged
    in the report — the local feed carries no announcement date).
    """
    if variant not in DIVIDEND_VARIANTS:
        raise ValueError(f"variant must be one of {sorted(DIVIDEND_VARIANTS)}")
    offset, hold = DIVIDEND_VARIANTS[variant]
    div = panel.dividend.fillna(0.0).to_numpy(dtype=float)
    close = panel.close.to_numpy(dtype=float)
    mask = np.zeros(div.shape, dtype=bool)
    xs, js = np.nonzero(div > 0)
    for x, j in zip(xs, js):
        s = int(x) + offset - 1
        if s < 0 or s + 1 + hold >= div.shape[0]:
            continue
        px = close[s, j]
        if np.isfinite(px) and px > 0 and div[x, j] / px >= min_yield:
            mask[s, j] = True
    return pd.DataFrame(mask, index=panel.dates, columns=panel.symbols), hold


def calendar_windows(
    panel: Panel, kind: str, eid_dates: Sequence[str] | None = None
) -> list[tuple[int, int]]:
    """[(entry_bar_pos, hold)] for schedule-only windows (no price information used)."""
    dates = panel.dates
    n = len(dates)
    if kind == "turn_of_month":
        hold = 3
        return [(p, hold) for p in range(n - 1)
                if dates[p].month != dates[p + 1].month and p + hold < n]
    if kind == "pre_lebaran":
        hold = 10
        out = []
        for e in (EID_AL_FITR if eid_dates is None else eid_dates):
            before = np.flatnonzero(dates < pd.Timestamp(e))
            if len(before) == 0:
                continue
            last = int(before[-1])
            if last + 1 >= n or last - hold < 0:
                continue
            out.append((last - hold, hold))
        return out
    raise ValueError("kind must be 'turn_of_month' or 'pre_lebaran'")


def run_calendar_backtest(
    panel: Panel,
    windows: Sequence[tuple[int, int]],
    *,
    eligible: pd.DataFrame,
    top_n: int = 30,
    notional_idr: float = 300e6,
) -> pd.DataFrame:
    """Equal-weight basket of the ``top_n`` most liquid eligible names, per window.

    ``null`` is the same basket's mean forward return over EVERY session at the
    same hold (always-long the basket), so excess isolates the calendar effect.
    The full round trip is charged to every window.
    """
    cols = list(PERIOD_COLUMNS)
    if not windows:
        return pd.DataFrame(columns=cols)
    adv, vol = impact_inputs(panel)
    adv_rank = adv.where(eligible.reindex_like(adv).fillna(False))
    top = adv_rank.rank(axis=1, ascending=False) <= top_n
    syms = np.array(panel.symbols)
    close_v = panel.close.to_numpy(dtype=float)
    adv_v, vol_v = adv.to_numpy(dtype=float), vol.to_numpy(dtype=float)
    rows = []
    null_by_hold: dict[int, float] = {}
    for entry_pos, hold in windows:
        s = entry_pos - 1
        if s < 0:
            continue
        fwd = fwd_total_return(panel, hold)
        if hold not in null_by_hold:
            null_by_hold[hold] = float(fwd.where(top).mean(axis=1).mean())
        members = np.flatnonzero(top.to_numpy()[s] & np.isfinite(fwd.to_numpy(dtype=float)[s]))
        if len(members) < 5:
            continue
        gross = float(np.nanmean(fwd.to_numpy(dtype=float)[s, members]))
        c = trade_cost_bps(close_v[entry_pos, members], adv_v[s, members], vol_v[s, members],
                           notional_idr / len(members), syms[members])
        rows.append((panel.dates[entry_pos], entry_pos, len(members), len(members), gross,
                     null_by_hold[hold], float(np.nanmean(c)), 1.0))
    return pd.DataFrame(rows, columns=cols)


def overnight_intraday_decomposition(panel: Panel, eligible: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight daily overnight (close->open) vs intraday (open->close), bps."""
    traded = panel.volume.fillna(0) > 0
    mask = eligible.reindex_like(panel.close).fillna(False).astype(bool) & traded
    overnight = (panel.open / panel.close.shift(1) - 1.0).where(mask)
    intraday = (panel.close / panel.open - 1.0).where(mask)
    return pd.DataFrame({
        "overnight_bps": overnight.mean(axis=1) * 1e4,
        "intraday_bps": intraday.mean(axis=1) * 1e4,
    })


# ---------------------------------------------------------------------------
# Statistics + verdict
# ---------------------------------------------------------------------------


def t_stat(x: Iterable[float]) -> float:
    a = np.asarray(list(x), dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return float("nan")
    sd = a.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return float("nan")
    return float(a.mean() / (sd / math.sqrt(len(a))))


def p_two_sided(t: float, n: int) -> float:
    if t is None or not np.isfinite(t):
        return 1.0
    return float(2.0 * _sps.t.sf(abs(t), df=max(int(n) - 1, 1)))


def bonferroni(p: float, m: int) -> float:
    return float(min(1.0, p * m))


def chrono_split(s: pd.Series, frac: float = IS_FRAC) -> tuple[pd.Series, pd.Series]:
    k = int(round(len(s) * frac))
    return s.iloc[:k], s.iloc[k:]


def verdict(
    *,
    n: int,
    n_clusters: int,
    net_excess_name_bps: float,
    net_excess_mkt_bps: float,
    t_net_name: float,
    p_bonf: float,
    oos_net_excess_bps: float,
    oos_n_clusters: int,
    ex_top5_net_excess_bps: float,
    net_excess_name_10x_bps: float,
    gross_excess_name_bps: float,
    cost_bps: float,
    p_raw: float,
    abs_net_bps: float | None = None,
    bear_abs_net_bps: float | None = None,
) -> tuple[str, list[str]]:
    """PASS only if EVERY gate clears (relative AND absolute); 'promising' = net-positive and raw-significant
    but blocked by a gate (deflation / OOS / concentration / capacity); else FAIL."""

    def pos(x: float) -> bool:
        return bool(np.isfinite(x) and x > 0)

    gates = {
        "net_excess_vs_name_null>0": pos(net_excess_name_bps),
        "net_excess_vs_market>0": pos(net_excess_mkt_bps),
        f"bonferroni_p<{BONFERRONI_ALPHA}": bool(np.isfinite(p_bonf) and p_bonf < BONFERRONI_ALPHA),
        "oos_net_excess>0": pos(oos_net_excess_bps),
        f"oos_clusters>={MIN_OOS_CLUSTERS}": oos_n_clusters >= MIN_OOS_CLUSTERS,
        "excess_ex_top5>0": pos(ex_top5_net_excess_bps),
        "capacity_10x>0": pos(net_excess_name_10x_bps),
        f"clusters>={MIN_CLUSTERS}": n_clusters >= MIN_CLUSTERS,
    }
    # Absolute-return gates (None = not applicable, e.g. a to-cash regime filter).
    # Beating always-long while still losing money is NOT a pass, and in a bear
    # regime that is the common failure mode.
    if abs_net_bps is not None:
        gates["absolute_net>0"] = pos(abs_net_bps)
    if bear_abs_net_bps is not None:
        gates["absolute_net_in_bear>0"] = pos(bear_abs_net_bps)
    failed = [k for k, v in gates.items() if not v]
    if not failed:
        return "PASS", []
    if pos(net_excess_name_bps) and np.isfinite(p_raw) and p_raw < 0.05:
        return "promising", failed
    return "FAIL", failed


# ---------------------------------------------------------------------------
# Market regime (point-in-time) — bear / non-bear
# ---------------------------------------------------------------------------

REGIME_MA_WINDOW: int = 200
REGIME_DD_WINDOW: int = 252
REGIME_DD_THRESHOLD: float = -0.15
REGIME_EXIT_DD: float = -0.075   # bear ends when drawdown recovers to within 7.5% of the 52w high
MIN_REGIME_CLUSTERS: int = 10


def bear_state(below: pd.Series, deep: pd.Series, drawdown: pd.Series, *, exit_dd: float = REGIME_EXIT_DD) -> pd.Series:
    """Hysteresis state machine: ON when (below MA and deep drawdown); stays ON until the
    drawdown recovers above ``exit_dd`` (a plain AND of the two conditions flickered:
    50 episodes on this universe, many one day long)."""
    on = False
    out = []
    for b, d, dd in zip(below.to_numpy(), deep.to_numpy(), drawdown.to_numpy()):
        if not on and bool(b) and bool(d):
            on = True
        elif on and np.isfinite(dd) and dd > exit_dd:
            on = False
        out.append(on)
    return pd.Series(out, index=below.index, dtype=bool)


def market_regime(
    panel: Panel,
    eligible: pd.DataFrame,
    *,
    ma_window: int = REGIME_MA_WINDOW,
    dd_window: int = REGIME_DD_WINDOW,
    dd_threshold: float = REGIME_DD_THRESHOLD,
) -> pd.DataFrame:
    """Point-in-time regime from an equal-weight index of the eligible universe.

    No IHSG series is stored locally, so the proxy is the equal-weight daily
    return of names eligible on the PREVIOUS close (membership never uses the
    day being measured). ``bear_raw`` on day t = index below its 200-day MA AND
    drawdown from its 252-day high worse than 15%; ``bear`` adds hysteresis (stays on
    until drawdown recovers past -7.5%). Both use bars <= t only.
    Warm-up rows are not-bear (unknown is not scored as bear).
    """
    elig_prev = eligible.reindex_like(panel.close).fillna(False).astype(bool).shift(1, fill_value=False)
    ret = panel.close.pct_change(fill_method=None)
    ok = elig_prev & (panel.volume.fillna(0) > 0) & (ret.abs() <= BAD_BAR_ABS_RETURN)
    r = ret.where(ok).mean(axis=1).fillna(0.0)
    level = (1.0 + r).cumprod() * 100.0
    ma = level.rolling(ma_window, min_periods=ma_window).mean()
    hi = level.rolling(dd_window, min_periods=dd_window).max()
    dd = level / hi - 1.0
    below = (level < ma).fillna(False)
    deep = (dd < dd_threshold).fillna(False)
    return pd.DataFrame({
        "level": level, "ma": ma, "drawdown": dd,
        "below_ma": below.astype(bool), "deep_dd": deep.astype(bool),
        "bear_raw": (below & deep).astype(bool),
        "bear": bear_state(below, deep, dd),
    })


# ---------------------------------------------------------------------------
# Summaries (cluster-level statistics, regime split, capacity)
# ---------------------------------------------------------------------------


def _regime_block(cl: pd.DataFrame, bear: pd.Series | None) -> dict:
    out: dict = {}
    if bear is None:
        return out
    flag = bear.reindex(cl.index).fillna(False).astype(bool)
    for name, sel in (("bear", flag), ("nonbear", ~flag)):
        sub = cl[sel]
        out[name] = {
            "n_clusters": int(len(sub)),
            "n_trades": int(sub["n"].sum()) if len(sub) else 0,
            "net_excess_name_bps": float(sub["net_excess_name"].mean()) if len(sub) else float("nan"),
            "abs_net_bps": float(sub["abs_net"].mean()) if len(sub) else float("nan"),
            "gross_bps": float(sub["gross"].mean()) if len(sub) else float("nan"),
            "t_net_excess": t_stat(sub["net_excess_name"]),
        }
    return out


def _summarize_rows(
    df: pd.DataFrame, *, m_tests: int, bear: pd.Series | None, net10x_bps: float, abs_applicable: bool = True
) -> dict:
    cl = df.groupby("date").agg(
        n=("n", "sum"), gross=("gross_abs", "mean"), net_excess_name=("net_excess_name", "mean"),
        net_excess_mkt=("net_excess_mkt", "mean"), abs_net=("abs_net", "mean"),
        cost=("cost_bps", "mean"), gross_excess=("gross_excess_name", "mean"),
    ).sort_index()
    is_, oos = chrono_split(cl["net_excess_name"])
    top5 = cl["net_excess_name"].sort_values(ascending=False).iloc[5:]
    t = t_stat(cl["net_excess_name"])
    p = p_two_sided(t, len(cl))
    regime = _regime_block(cl, bear)
    bear_abs = None
    if regime.get("bear", {}).get("n_clusters", 0) >= MIN_REGIME_CLUSTERS:
        bear_abs = regime["bear"]["abs_net_bps"]
    res = {
        "n": int(df["n"].sum()), "n_clusters": int(len(cl)),
        "gross_abs_bps": float(cl["gross"].mean()), "gross_excess_name_bps": float(cl["gross_excess"].mean()),
        "cost_bps": float(cl["cost"].mean()),
        "net_excess_name_bps": float(cl["net_excess_name"].mean()),
        "net_excess_mkt_bps": float(cl["net_excess_mkt"].mean()),
        "abs_net_bps": float(cl["abs_net"].mean()),
        "t_net_name": t, "p_raw": p, "p_bonf": bonferroni(p, m_tests),
        "is_net_excess_bps": float(is_.mean()) if len(is_) else float("nan"),
        "oos_net_excess_bps": float(oos.mean()) if len(oos) else float("nan"),
        "oos_n_clusters": int(len(oos)),
        "ex_top5_net_excess_bps": float(top5.mean()) if len(top5) else float("nan"),
        "net_excess_name_10x_bps": float(net10x_bps),
        "median_net_excess_name_bps": float(cl["net_excess_name"].median()),
        "regime": regime, "bear_abs_net_bps": bear_abs,
    }
    res["verdict"], res["failed_gates"] = verdict(
        **{k: res[k] for k in (
            "n", "n_clusters", "net_excess_name_bps", "net_excess_mkt_bps", "t_net_name", "p_bonf",
            "oos_net_excess_bps", "oos_n_clusters", "ex_top5_net_excess_bps", "net_excess_name_10x_bps",
            "gross_excess_name_bps", "cost_bps", "p_raw")},
        abs_net_bps=res["abs_net_bps"] if abs_applicable else None,
        bear_abs_net_bps=bear_abs if abs_applicable else None,
    )
    return res


def _trade_rows(trades: pd.DataFrame) -> pd.DataFrame:
    c = trades["cost_bps"].to_numpy(dtype=float)
    g = trades["gross"].to_numpy(dtype=float) * 1e4
    nn = trades["null_name"].to_numpy(dtype=float) * 1e4
    nm = trades["null_mkt"].to_numpy(dtype=float) * 1e4
    return pd.DataFrame({
        "date": trades["date"].to_numpy(), "n": 1, "gross_abs": g, "cost_bps": c,
        "gross_excess_name": g - nn, "net_excess_name": g - nn - c, "net_excess_mkt": g - nm - c,
        "abs_net": g - c,
    })


def summarize_event_trades(
    trades: pd.DataFrame, *, m_tests: int, bear: pd.Series | None = None,
    trades_10x: pd.DataFrame | None = None,
) -> dict:
    if trades.empty:
        return {"n": 0, "verdict": "FAIL", "failed_gates": ["no_trades"], "regime": {}}
    rows = _trade_rows(trades)
    net10 = float("nan")
    if trades_10x is not None and len(trades_10x):
        net10 = float(_trade_rows(trades_10x).groupby("date")["net_excess_name"].mean().mean())
    return _summarize_rows(rows, m_tests=m_tests, bear=bear, net10x_bps=net10)


def _period_rows(per: pd.DataFrame) -> pd.DataFrame:
    g = per["gross"].to_numpy(dtype=float) * 1e4
    nl = per["null"].to_numpy(dtype=float) * 1e4
    c = per["cost_bps"].to_numpy(dtype=float)
    return pd.DataFrame({
        "date": per["date"].to_numpy(), "n": 1, "gross_abs": g, "cost_bps": c,
        "gross_excess_name": g - nl, "net_excess_name": g - nl - c, "net_excess_mkt": g - nl - c,
        "abs_net": g - c,
    })


def summarize_periods(
    per: pd.DataFrame, *, m_tests: int, bear: pd.Series | None = None,
    per_10x: pd.DataFrame | None = None, abs_applicable: bool = True,
) -> dict:
    if per.empty:
        return {"n": 0, "verdict": "FAIL", "failed_gates": ["no_periods"], "regime": {}}
    net10 = float("nan")
    if per_10x is not None and len(per_10x):
        net10 = float(_period_rows(per_10x)["net_excess_name"].mean())
    return _summarize_rows(_period_rows(per), m_tests=m_tests, bear=bear, net10x_bps=net10,
                           abs_applicable=abs_applicable)


# ---------------------------------------------------------------------------
# Regime filter that goes to cash (the bear-regime play for a long-only account)
# ---------------------------------------------------------------------------


def regime_filter_episodes(
    panel: Panel, eligible: pd.DataFrame, bear: pd.Series, *, top_n: int = 30, notional_idr: float = 300e6,
) -> tuple[pd.DataFrame, dict]:
    """Episodes where a long-only liquid basket is sold to cash when ``bear`` turns on.

    D1 timing: bear flips on at close s -> sell the basket at close_{s+1}; it
    flips off at close e -> re-buy at close_{e+1}. Each CLOSED episode is one
    cluster: ``gross`` = 0 (cash; interest ignored, conservative for the filter),
    ``null`` = the basket's own return over the off-period (always-long),
    ``cost_bps`` = one full round trip. Excess = avoided loss - cost. A still-open
    episode is reported in the info dict and never scored. Shorting is not
    modelled: IDX retail cannot short, so no short leg is ever counted.
    """
    adv, vol = impact_inputs(panel)
    adv_rank = adv.where(eligible.reindex_like(adv).fillna(False))
    top = (adv_rank.rank(axis=1, ascending=False) <= top_n).to_numpy()
    close_v = panel.close.to_numpy(dtype=float)
    adv_v, vol_v = adv.to_numpy(dtype=float), vol.to_numpy(dtype=float)
    syms = np.array(panel.symbols)
    flag = bear.reindex(panel.dates).fillna(False).to_numpy(dtype=bool)
    n = len(flag)
    rows: list[tuple] = []
    info: dict = {"open_episode_since": None, "open_episode_basket_return": float("nan")}
    s = 1
    while s < n - 1:
        if flag[s] and not flag[s - 1]:
            e = s
            while e < n and flag[e]:
                e += 1
            members = np.flatnonzero(top[s] & np.isfinite(close_v[s + 1]))
            if len(members) >= 5:
                if e >= n - 1:
                    info["open_episode_since"] = panel.dates[s]
                    info["open_episode_basket_return"] = float(
                        np.nanmean(close_v[n - 1, members] / close_v[s + 1, members] - 1.0))
                else:
                    basket = float(np.nanmean(close_v[e + 1, members] / close_v[s + 1, members] - 1.0))
                    c = trade_cost_bps(close_v[s + 1, members], adv_v[s, members], vol_v[s, members],
                                       notional_idr / len(members), syms[members])
                    rows.append((panel.dates[s], s, len(members), len(members), 0.0, basket,
                                 float(np.nanmean(c)), 1.0))
            s = e
        else:
            s += 1
    return pd.DataFrame(rows, columns=PERIOD_COLUMNS), info
