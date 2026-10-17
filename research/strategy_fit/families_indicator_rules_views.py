"""Reported-only views for plan 24 (strategy_fit/families_indicator_rules.py): the C-rand control and the
portfolio view. Neither is gated (plan 24 section 4.4)."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from strategy_fit.families_index_rebalance import BPS, Env

DEV_START = pd.Timestamp("2015-01-01")
TRADING_DAYS_PER_YEAR = 252.0


# ---------------------------------------------------------------------------
# C-rand: same names, same years, same hold lengths, random eligible entry days
# ---------------------------------------------------------------------------

def _candidate_gross(book, j: int, year: int, h: int) -> np.ndarray:
    """Gross total returns of every valid open_e -> open_{e+h} trade of name j with entry in ``year``."""
    start = int(book.dates.searchsorted(DEV_START))
    e = np.flatnonzero((book.dates.year == year) & (np.arange(book.n) >= max(start, 1)))
    e = e[e + h <= book.last]
    t = e - 1
    x = e + h
    ok = (book.elig[t, j] & ~book.locked[t, j] & book.tradable[e, j] & book.tradable[x, j]
          & ((book.bad_cum[x, j] - book.bad_cum[e - 1, j]) == 0))
    e, x = e[ok], x[ok]
    return (book.open[x, j] + book.div_cum[x, j] - book.div_cum[e, j]) / book.open[e, j] - 1.0


def c_rand(book, T: pd.DataFrame, n_draws: int = 200, seed: int = 24) -> dict:
    """Mean gross of ``n_draws`` random twins; the rule's own mean gross is placed in that distribution.

    Each trade gets ``n_draws`` draws (with replacement) from the valid entry days of the same name and calendar
    year, at the trade's own hold length. Trades with no valid alternative day are left out of both sides."""
    rng = np.random.default_rng(seed)
    cache: dict[tuple, np.ndarray] = {}
    draws, own = [], []
    for j, y, h, g in zip(T["j"], T["entry_date"].dt.year, T["hold"], T["gross"]):
        key = (int(j), int(y), int(h))
        if key not in cache:
            cache[key] = _candidate_gross(book, *key)
        cand = cache[key]
        if len(cand) == 0:
            continue
        draws.append(cand[rng.integers(0, len(cand), n_draws)])
        own.append(g)
    if not own:
        return {"n_trades": 0, "n_used": 0, "rule_mean_gross_bps": float("nan"), "percentile": float("nan")}
    means = np.mean(draws, axis=0) * BPS
    rule = float(np.mean(own) * BPS)
    return {"n_trades": int(len(T)), "n_used": len(own), "n_draws": n_draws, "seed": seed,
            "rule_mean_gross_bps": rule, "rand_mean_bps": float(means.mean()),
            "rand_p05_bps": float(np.percentile(means, 5)), "rand_p95_bps": float(np.percentile(means, 95)),
            "percentile": float((means < rule).mean() * 100.0 + 0.5 * (means == rule).mean() * 100.0)}


# ---------------------------------------------------------------------------
# Portfolio view
# ---------------------------------------------------------------------------

def _ff_open_returns(book) -> np.ndarray:
    """open_d -> open_{d+1} total return on forward-filled opens (a suspended day earns 0); last row 0."""
    op = pd.DataFrame(book.open).ffill().to_numpy(float)
    r = np.zeros(op.shape)
    with np.errstate(invalid="ignore", divide="ignore"):
        r[:-1] = (op[1:] + book.net_div[1:]) / op[:-1] - 1.0
    return np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)


def _series_metrics(r: pd.Series) -> dict:
    eq = (1.0 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-9)
    by_year = (1.0 + r).groupby(r.index.year).prod() - 1.0
    return {"cagr": float(eq.iloc[-1] ** (1.0 / years) - 1.0), "max_drawdown": float((eq / eq.cummax() - 1.0).min()),
            "worst_year": float(by_year.min()), "worst_year_label": int(by_year.idxmin()),
            "total_return": float(eq.iloc[-1] - 1.0)}


def portfolio_series(env: Env, book, T: pd.DataFrame, ihsg_open: np.ndarray) -> pd.DataFrame:
    """Daily returns over the development window: rule book (equal weight across open positions, net of the
    current-coeff round-trip cost charged on the entry day, cash earns 0), equal-weight eligible universe
    (daily rebalanced, names eligible on the prior close) and IHSG."""
    r = _ff_open_returns(book)
    n = book.n
    start = int(book.dates.searchsorted(DEV_START))
    tot, cnt = np.zeros(n), np.zeros(n)
    for j, e, x, c in zip(T["j"], T["e"], T["x"], T["cost_cur"].to_numpy(float) / BPS):
        tot[e:x] += r[e:x, j]
        tot[e] -= c
        cnt[e:x] += 1
    rule = np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)
    prior = np.vstack([np.zeros((1, book.elig.shape[1]), bool), book.elig[:-1]])
    uni = np.where(prior, r, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)         # all-NaN rows (no eligible name) -> 0
        uni_ret = np.nan_to_num(np.nanmean(uni, axis=1), nan=0.0)
    ih = pd.Series(ihsg_open).ffill().to_numpy(float)
    ih_ret = np.zeros(n)
    ih_ret[:-1] = np.nan_to_num(ih[1:] / ih[:-1] - 1.0)
    sl = slice(start, book.last)                      # day d earns open_d -> open_{d+1}, so the last day is excluded
    return pd.DataFrame({"rule": rule[sl], "universe_ew": uni_ret[sl], "ihsg": ih_ret[sl], "n_open": cnt[sl]},
                        index=book.dates[sl])


def portfolio_view(env: Env, book, T: pd.DataFrame, ihsg_open: np.ndarray) -> dict:
    s = portfolio_series(env, book, T, ihsg_open)
    return {"rule": _series_metrics(s["rule"]), "universe_ew": _series_metrics(s["universe_ew"]),
            "ihsg": _series_metrics(s["ihsg"]),
            "time_in_market": float((s["n_open"] > 0).mean()), "mean_open_positions": float(s["n_open"].mean()),
            "mean_open_positions_when_invested": float(s["n_open"][s["n_open"] > 0].mean())
            if (s["n_open"] > 0).any() else float("nan")}
