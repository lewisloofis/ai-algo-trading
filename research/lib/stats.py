"""
lib/stats.py — Statistical helpers for the OBI/OFI research studies.

Functions
---------
_circular_block_resample  — circular block resampling for bootstrap
bootstrap_null            — block-bootstrap 95th pct reference for corr and hit-rate
conditional_hit_rate      — P(sign(signal)==sign(target) | |target|>0)
nonoverlap_hit_rate       — de-overlapped (independent-observation) hit-rate
decile_means              — bin signal into deciles; return (centers, means, sems)
is_monotone               — crude monotonicity check on an array of means
ols_hac                   — OLS with Newey-West HAC SE; returns (slope, t_stat, r2)

required_effective_n      — power-derived minimum-N to resolve an edge at z-sigma

Constants
---------
MIN_EFF_N   — minimum independent observations required to trust an OOS hit-rate
REQUIRED_N_UNRESOLVABLE — sentinel "required N" for a non-positive target edge
"""

from __future__ import annotations

import warnings

import numpy as np
import scipy.stats as sps
import statsmodels.api as sm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_EFF_N = 30          # minimum de-overlapped OOS conditional observations
N_BOOTSTRAP = 500       # default number of bootstrap draws
BOOTSTRAP_BLOCK = 30    # default block length for circular block bootstrap

REQUIRED_N_UNRESOLVABLE: int = 10 ** 9
"""
Sentinel "required N" returned by required_effective_n() when the target edge
is non-positive (or NaN).  A non-positive edge is never confirmed a genuine
win no matter how much data is collected, so "required sample size" is
reported as effectively unreachable rather than some finite, misleadingly
small, number.
"""


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def _circular_block_resample(
    arr: np.ndarray, block: int, n_draws: int, rng: np.random.Generator
) -> np.ndarray:
    """
    Return an array of shape *(n_draws, len(arr))*.

    Each row is a circular block-resampled permutation of *arr*, preserving
    short-range autocorrelation structure.
    """
    n = len(arr)
    n_blocks = int(np.ceil(n / block))
    out = np.empty((n_draws, n), dtype=float)
    for d in range(n_draws):
        starts = rng.integers(0, n, size=n_blocks)
        pieces = [arr[np.arange(s, s + block) % n] for s in starts]
        out[d] = np.concatenate(pieces)[:n]
    return out


def bootstrap_null(
    signal: np.ndarray,
    target: np.ndarray,
    block: int = BOOTSTRAP_BLOCK,
    n_draws: int = N_BOOTSTRAP,
    seed: int = 42,
) -> tuple[float, float]:
    """
    Compute the 95th percentile of |Pearson corr| and hit-rate under the
    circular block-bootstrap null (signal shuffled, target fixed).

    Returns ``(corr_95, hr_95)``.  Returns ``(nan, nan)`` when there are fewer
    than 50 valid observations.
    """
    rng = np.random.default_rng(seed)
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 50:
        return np.nan, np.nan

    resampled = _circular_block_resample(s, block, n_draws, rng)
    nonzero_t = np.abs(t) > 0

    hrs = np.empty(n_draws)
    for d in range(n_draws):
        perm = resampled[d]
        nt = t[nonzero_t]
        np_ = perm[nonzero_t]
        hrs[d] = float(np.mean(np.sign(np_) == np.sign(nt))) if len(nt) > 0 else np.nan

    corrs = np.array([abs(sps.pearsonr(resampled[d], t)[0]) for d in range(n_draws)])
    return float(np.nanpercentile(corrs, 95)), float(np.nanpercentile(hrs, 95))


# ---------------------------------------------------------------------------
# Hit-rate helpers
# ---------------------------------------------------------------------------

def conditional_hit_rate(
    signal: np.ndarray, target: np.ndarray
) -> tuple[float, int]:
    """
    P(sign(signal) == sign(target)) conditional on ``|target| > 0``.

    Returns ``(hit_rate, n_conditional)``.  Returns ``(nan, 0)`` when no
    non-zero target rows exist after filtering NaNs.

    .. note::
       ``obi_forward_return.py`` uses a 3-tuple variant ``(hit_rate, base_rate,
       n)`` internally; callers of that version handle the extra element.
       This shared version returns a 2-tuple and is used by ``multisym_obi``,
       ``ofi_obi_compare``, and ``backtest``.
    """
    valid = np.isfinite(signal) & np.isfinite(target)
    s, t = signal[valid], target[valid]
    nonzero = np.abs(t) > 0
    s_nz, t_nz = s[nonzero], t[nonzero]
    if len(s_nz) == 0:
        return np.nan, 0
    return float(np.mean(np.sign(s_nz) == np.sign(t_nz))), int(len(s_nz))


def nonoverlap_hit_rate(
    df, signal_col: str, target_col: str, h_s: int
) -> tuple[float, int]:
    """
    De-overlapped (independent-observation) conditional hit-rate.

    Walks forward through *df* keeping only rows whose ``recv_ns`` is at least
    ``last_kept_recv_ns + h_s * 1e9`` ahead — ensuring no two kept observations
    share an overlapping forward window of length *h_s* seconds.

    Returns ``(hit_rate, eff_n)`` where *eff_n* is the number of kept rows with
    ``|target| > 0``.  Returns ``(nan, 0)`` when there are not enough valid rows.
    """
    if target_col not in df.columns:
        return float("nan"), 0

    recv_ns = df["recv_ns"].to_numpy(dtype=np.int64)
    sig = df[signal_col].to_numpy(dtype=float)
    tgt = df[target_col].to_numpy(dtype=float)
    h_ns = int(h_s * 1e9)

    kept_sig: list[float] = []
    kept_tgt: list[float] = []
    last_ns: int = -1

    for i in range(len(recv_ns)):
        if not (np.isfinite(sig[i]) and np.isfinite(tgt[i])):
            continue
        if last_ns >= 0 and recv_ns[i] < last_ns + h_ns:
            continue
        kept_sig.append(sig[i])
        kept_tgt.append(tgt[i])
        last_ns = recv_ns[i]

    if not kept_sig:
        return float("nan"), 0

    ks = np.array(kept_sig)
    kt = np.array(kept_tgt)
    nonzero = np.abs(kt) > 0
    eff_n = int(nonzero.sum())
    if eff_n == 0:
        return float("nan"), 0

    return float(np.mean(np.sign(ks[nonzero]) == np.sign(kt[nonzero]))), eff_n


# ---------------------------------------------------------------------------
# Decile analysis
# ---------------------------------------------------------------------------

def decile_means(
    signal: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bin *signal* into deciles; return *(bin_centers, means, sems)* for *target*.

    Rows where either *signal* or *target* is NaN are dropped.  Returns arrays
    of ``nan`` when there are fewer than 20 valid rows or too few unique edges.
    """
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 20:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)
    edges = np.unique(np.percentile(s, np.linspace(0, 100, 11)))
    if len(edges) < 3:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)
    bin_idx = np.digitize(s, edges[1:-1])
    n_bins = len(edges) - 1
    centers = np.array([np.mean(s[bin_idx == b]) if np.any(bin_idx == b) else np.nan for b in range(n_bins)])
    means = np.array([np.mean(t[bin_idx == b]) if np.any(bin_idx == b) else np.nan for b in range(n_bins)])
    sems = np.array([
        (np.std(t[bin_idx == b], ddof=1) / np.sqrt(np.sum(bin_idx == b)))
        if np.sum(bin_idx == b) > 1 else np.nan
        for b in range(n_bins)
    ])
    return centers, means, sems


def is_monotone(means: np.ndarray, min_frac: float = 0.80) -> bool:
    """
    Crude monotonicity check: at least *min_frac* of consecutive
    *(mean[i], mean[i+1])* pairs must be non-decreasing (or all
    non-increasing).  Returns ``True`` for both monotone directions
    (the signal is considered directional either way).
    """
    valid = means[np.isfinite(means)]
    if len(valid) < 3:
        return False
    diffs = np.diff(valid)
    n_inc = np.sum(diffs >= 0)
    n_dec = np.sum(diffs <= 0)
    return max(n_inc, n_dec) / len(diffs) >= min_frac


# ---------------------------------------------------------------------------
# OLS with Newey-West HAC
# ---------------------------------------------------------------------------

def ols_hac(
    signal: np.ndarray, target: np.ndarray, maxlags: int
) -> tuple[float, float, float]:
    """
    Fit OLS ``target ~ signal`` with Newey-West HAC standard errors.

    Returns ``(slope, t_stat, r_squared)``.  Returns ``(nan, nan, nan)`` when
    there are fewer than 30 valid observations or the fit fails.
    """
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 30:
        return np.nan, np.nan, np.nan
    X = sm.add_constant(s, prepend=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = sm.OLS(t, X).fit(
                cov_type="HAC",
                cov_kwds={"maxlags": maxlags, "use_correction": True},
            )
        return float(fit.params[1]), float(fit.tvalues[1]), float(fit.rsquared)
    except Exception:
        return np.nan, np.nan, np.nan


# ---------------------------------------------------------------------------
# Power / required sample-size calculation (plan-10 W2.1)
# ---------------------------------------------------------------------------

def required_effective_n(
    target_edge_bps: float,
    per_trade_sigma_bps: float,
    z: float = 2.0,
) -> int:
    """
    Minimum independent (de-overlapped) observations needed to resolve
    ``target_edge_bps`` at ``z`` standard errors, given the observed
    per-trade dispersion ``per_trade_sigma_bps``.

    The standard error of a mean estimated from N i.i.d. per-trade returns
    with per-trade standard deviation ``per_trade_sigma_bps`` is
    ``per_trade_sigma_bps / sqrt(N)``.  Requiring the edge to clear ``z``
    standard errors means::

        target_edge_bps >= z * per_trade_sigma_bps / sqrt(N)
        N                >= (z * per_trade_sigma_bps / target_edge_bps) ** 2

    Worked example (plan-10 roadmap §6 W2.1): a 20 bps edge at z=2 against a
    ~150 bps per-trade spread needs N >= (2 * 150 / 20) ** 2 = 225 round-trips.

    This is a *power* calculation, distinct from any not-zero floor (e.g.
    ``MIN_OOS_EFF_N`` in ``strategy_fit/intraday_walk_forward.py``).  A cell
    can clear a not-zero floor and still be badly underpowered relative to
    this number — see that module's ``oos_underpowered`` flag, which is
    exactly this: "clears the hard floor, misses the power-derived N".  An
    UNDERPOWERED verdict is expected to be common and honest, not a pipeline
    failure.

    Parameters
    ----------
    target_edge_bps : float
        The edge (in bps) the sample is claiming to have detected.  Must be
        > 0 to be resolvable at all — a non-positive or NaN edge returns
        ``REQUIRED_N_UNRESOLVABLE`` (no amount of data confirms a
        non-positive edge as a genuine win).
    per_trade_sigma_bps : float
        Observed standard deviation of per-trade returns, in bps.  Must be
        >= 0; a degenerate 0 (no dispersion at all) returns the theoretical
        minimum, 1.
    z : float
        Number of standard errors required (default 2.0, ~97.7% one-sided /
        ~95.4% two-sided under a normal approximation — NOT the common
        "95% one-sided" shorthand, which is z=1.645; 95% two-sided is z=1.96).

    Returns
    -------
    int
        The required effective-N, rounded up to the nearest integer, with a
        floor of 1.  Returns ``REQUIRED_N_UNRESOLVABLE`` when
        ``target_edge_bps`` is non-positive or non-finite.
    """
    if target_edge_bps is None or per_trade_sigma_bps is None:
        return REQUIRED_N_UNRESOLVABLE
    if not np.isfinite(target_edge_bps) or target_edge_bps <= 0:
        return REQUIRED_N_UNRESOLVABLE
    if not np.isfinite(per_trade_sigma_bps) or per_trade_sigma_bps <= 0:
        return 1
    n = (z * per_trade_sigma_bps / target_edge_bps) ** 2
    return max(1, int(np.ceil(n)))


# ---------------------------------------------------------------------------
# Significance of a strategy's per-trade edge over the always-long null
# ---------------------------------------------------------------------------

MIN_T_STAT = 2.0
"""|t| a per-trade edge over the always-long null must clear before it is called more than luck."""


def welch_t(sample, null, null_n_eff: float | None = None) -> float:
    """
    Welch's t for mean(sample) - mean(null), allowing unequal variances.

    ``sample`` is the strategy's per-trade returns; ``null`` is the per-trade return of
    buying on every priced day for the same hold (the harness's always-long null). Both
    must be in the same units; a common cost cancels, so gross or net is fine.

    Consecutive always-long trades overlap by the hold, so they are not independent
    draws. Pass ``null_n_eff`` (e.g. ``len(null) / hold``) to use that many effective
    observations for the null's standard error; default is ``len(null)``.

    Returns NaN when undefined (fewer than 2 sample trades, an empty null, or zero
    variance on both sides).
    """
    a = np.asarray(sample, dtype=float)
    b = np.asarray(null, dtype=float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    nb = float(null_n_eff) if null_n_eff is not None else float(len(b))
    if nb <= 0:
        return float("nan")
    se2 = float(np.var(a, ddof=1)) / len(a) + float(np.var(b, ddof=1)) / nb
    if se2 <= 0:
        return float("nan")
    return float((np.mean(a) - np.mean(b)) / np.sqrt(se2))
