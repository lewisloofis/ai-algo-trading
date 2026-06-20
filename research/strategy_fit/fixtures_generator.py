"""
strategy_fit/fixtures_generator.py — Deterministic synthetic daily OHLCV generator.

Generates ≥250 trading-day price series for the 5 universe names used in plan-10
strategy-fit testing.  All data is SYNTHETIC — it does NOT represent real market
data.  The generator uses a fixed random seed for reproducibility.

Injected edge
-------------
ITMG receives a deterministic mean-reversion edge: after two consecutive days of
price decline exceeding 1 daily-sigma, the next day's return is biased upward by
ITMG_EDGE_BPS / 10_000.  This acts as the test oracle: at least one synthetic
series should produce a survivor in strategy_portfolio.md.

The remaining four names (BUMI, MDKA, ANTM, DEWA) are pure random walks at their
respective price levels and tick sizes — they serve as noise controls.

Output
------
One CSV per symbol in the specified output directory:
    {output_dir}/{SYMBOL}.csv
Header: date,open,high,low,close,volume
Row 1 is a comment line: "# SYNTHETIC — NOT real market data …"

The DataSource Protocol
-----------------------
StrategyFitFixtureDataSource implements the screener DataSource protocol for
offline pipeline testing.
"""

from __future__ import annotations

import csv
import os
from typing import NamedTuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNIVERSE_SYMBOLS: tuple[str, ...] = ("ITMG", "BUMI", "MDKA", "ANTM", "DEWA")

# Number of synthetic trading-day rows to generate per symbol.
N_TRADING_DAYS: int = 260  # ~1 year of trading days

# Generator seed — must never change so results are deterministic.
FIXTURE_SEED: int = 20260620

# Injected edge: ITMG has a synthetic alternating mean-reversion pattern.
# Pattern: odd days = up by UP_BPS, even days = down by DOWN_BPS.
# mean_reversion_signal: on down-days, price below 5-day rolling mean → buy → next day up.
# On up-days, price above 5-day rolling mean → sell → next day down.
# Signed-return edge per trade: mean(sign(signal) * fwd_return) >> round-trip fees.
# Mean signed return ≈ (UP_BPS + DOWN_BPS) / 2 = 300 bps >> 89 bps ITMG hurdle.
ITMG_UP_BPS: float = 400.0            # return on odd (up) days
ITMG_DOWN_BPS: float = 200.0          # magnitude of return on even (down) days

# Reference prices (IDR) chosen to match the screener fixture price levels.
REFERENCE_PRICES: dict[str, float] = {
    "ITMG": 27_500.0,   # low tick → low friction
    "BUMI": 168.0,      # cheap → high friction
    "MDKA": 2_250.0,
    "ANTM": 1_800.0,
    "DEWA": 53.0,       # very cheap → very high friction
}

# Daily volatility (annualised ~25% → daily ~1.6%) per symbol.
DAILY_VOL: dict[str, float] = {
    "ITMG": 0.016,
    "BUMI": 0.022,
    "MDKA": 0.018,
    "ANTM": 0.017,
    "DEWA": 0.025,
}

# Approximate median daily volume (shares) for turnover computation.
DAILY_VOLUME: dict[str, float] = {
    "ITMG": 5_000_000,
    "BUMI": 900_000_000,
    "MDKA": 10_000_000,
    "ANTM": 50_000_000,
    "DEWA": 200_000_000,
}

# Approximate median daily net foreign flow (shares) per symbol.
# Synthetic net_foreign = foreignbuy - foreignsell (daily; not cumulative).
# ITMG receives occasional injected spikes to exercise the event detector.
DAILY_FOREIGN_FLOW_SCALE: dict[str, float] = {
    "ITMG": 1_000_000,
    "BUMI": 5_000_000,
    "MDKA": 500_000,
    "ANTM": 2_000_000,
    "DEWA": 3_000_000,
}

# Start date for the synthetic history (recent enough to be plausible).
HISTORY_START: str = "2025-06-01"


# ---------------------------------------------------------------------------
# Internal tick-size helper (mirrors screener.metrics)
# ---------------------------------------------------------------------------

def _tick_size(price: float) -> int:
    """IDX fraksi harga step function (mirrors screener.metrics.tick_size)."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2_000:
        return 5
    if price < 5_000:
        return 10
    return 25


def _snap_to_tick(price: float) -> float:
    """Round *price* to the nearest valid IDX tick."""
    tick = _tick_size(price)
    return round(price / tick) * tick


# ---------------------------------------------------------------------------
# Synthetic series generation
# ---------------------------------------------------------------------------

def _generate_series(
    symbol: str,
    n: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Generate a synthetic OHLCV daily series for *symbol*.

    ITMG receives the injected mean-reversion edge; all others are noise.
    Returns a DataFrame with columns: date, open, high, low, close, volume.
    """
    vol = DAILY_VOL[symbol]
    ref_price = REFERENCE_PRICES[symbol]
    avg_volume = DAILY_VOLUME[symbol]

    # Pure log-return random walk.
    log_returns = rng.normal(0.0, vol, size=n)

    # Inject a synthetic mean-reversion pattern for ITMG.
    # The price alternates between HIGH and LOW levels around a fixed baseline.
    # HIGH level: ref_price * (1 + UP_FRAC). LOW level: ref_price * (1 - DOWN_FRAC).
    # The 5-day rolling mean stays near ref_price.
    # On HIGH days: price above rolling mean → signal negative → SELL → next day low → edge
    # On LOW days: price below rolling mean → signal positive → BUY → next day high → edge
    # Mean signed return ≈ mean(fwd_return_on_low_day, fwd_return_on_high_day) >> hurdle.
    # NOTE: purely synthetic for offline pipeline validation only.
    if symbol == "ITMG":
        up_frac = ITMG_UP_BPS / 1e4         # e.g. 0.04 = 4% above ref on high days
        down_frac = ITMG_DOWN_BPS / 1e4     # e.g. 0.02 = 2% below ref on low days
        # Rebuild log_returns so price alternates HIGH/LOW around ref_price.
        # p[i] = ref_price * (1 + up_frac) on odd i; ref_price * (1 - down_frac) on even i.
        # log_return[0] = log(p[0]/ref_price)
        # log_return[i] = log(p[i]/p[i-1])
        log_returns[0] = np.log(1.0 - down_frac)  # start on a low day
        for i in range(1, n):
            prev_level = 1.0 + up_frac if (i - 1) % 2 == 1 else 1.0 - down_frac
            curr_level = 1.0 + up_frac if i % 2 == 1 else 1.0 - down_frac
            log_returns[i] = np.log(curr_level / prev_level) + rng.normal(0.0, 0.001)

    # Reconstruct price series from log-returns.
    log_prices = np.cumsum(log_returns)
    closes_raw = ref_price * np.exp(log_prices)

    # Snap to tick grid.
    closes = np.array([_snap_to_tick(p) for p in closes_raw])

    # Generate OHLC from close using intraday vol (simplified: uniform range).
    intraday_vol = vol * 0.6
    daily_range = closes * intraday_vol * rng.uniform(0.5, 1.5, size=n)
    opens = np.array([_snap_to_tick(c + daily_range[i] * rng.uniform(-0.5, 0.5)) for i, c in enumerate(closes)])
    highs = np.array([_snap_to_tick(max(c, o) + daily_range[i] * 0.5) for i, (c, o) in enumerate(zip(closes, opens))])
    lows = np.array([_snap_to_tick(min(c, o) - daily_range[i] * 0.5) for i, (c, o) in enumerate(zip(closes, opens))])

    # Clip negatives (shouldn't happen with ref_price >> 0 and vol=2%).
    closes = np.clip(closes, 1, None)
    opens = np.clip(opens, 1, None)
    highs = np.maximum(highs, closes)
    lows = np.maximum(1, np.minimum(lows, closes))

    # Volume: log-normal around avg_volume with 40% sigma.
    volumes = (avg_volume * rng.lognormal(0.0, 0.4, size=n)).astype(int)

    # Net foreign flow (foreignbuy - foreignsell, daily — NOT cumulative).
    # Normal noise around zero.  ITMG receives periodic injected spikes so the
    # foreign_flow_spike_event detector has signal to pick up in offline tests.
    ff_scale = DAILY_FOREIGN_FLOW_SCALE[symbol]
    net_foreign = (ff_scale * rng.normal(0.0, 1.0, size=n)).astype(int)
    if symbol == "ITMG":
        # Inject clear spikes every ~25 rows so the z-score threshold is crossed.
        spike_indices = list(range(10, n, 25))
        for idx in spike_indices:
            net_foreign[idx] = int(ff_scale * 8.0)  # 8-sigma spike

    # Generate business-day dates starting from HISTORY_START.
    dates = pd.bdate_range(start=HISTORY_START, periods=n)

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens.astype(int),
            "high": highs.astype(int),
            "low": lows.astype(int),
            "close": closes.astype(int),
            "volume": volumes,
            "net_foreign": net_foreign,
        }
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_fixtures(output_dir: str, n: int = N_TRADING_DAYS) -> None:
    """
    Write synthetic OHLCV CSV fixtures for all universe symbols.

    Each file is prefixed with a SYNTHETIC comment so consumers know this
    is not real market data.

    Parameters
    ----------
    output_dir : str
        Directory in which to write ``{SYMBOL}.csv`` files.  Created if missing.
    n : int
        Number of trading-day rows to generate per symbol.
    """
    os.makedirs(output_dir, exist_ok=True)
    rng = np.random.default_rng(FIXTURE_SEED)

    for symbol in UNIVERSE_SYMBOLS:
        df = _generate_series(symbol, n, rng)
        path = os.path.join(output_dir, f"{symbol}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            ref = REFERENCE_PRICES[symbol]
            f.write(
                f"# SYNTHETIC — NOT real market data. "
                f"Generated offline for plan-10 strategy-fit testing only. "
                f"Symbol={symbol}, ref_price={ref:.0f} IDR, seed={FIXTURE_SEED}.\n"
            )
            df.to_csv(f, index=False)


def get_or_generate_fixtures(output_dir: str, n: int = N_TRADING_DAYS) -> None:
    """
    Generate fixtures if they do not already exist.

    If all expected CSV files are present in *output_dir*, this is a no-op.
    Otherwise calls ``generate_fixtures`` to create them.
    """
    all_present = all(
        os.path.isfile(os.path.join(output_dir, f"{s}.csv"))
        for s in UNIVERSE_SYMBOLS
    )
    if not all_present:
        generate_fixtures(output_dir, n=n)


# ---------------------------------------------------------------------------
# StrategyFitFixtureDataSource — DataSource Protocol implementation
# ---------------------------------------------------------------------------

class StrategyFitFixtureDataSource:
    """
    Offline DataSource that reads from the strategy_fit synthetic fixture CSVs.

    Implements the same protocol as screener.screen_universe.DataSource:
        funding_universe() -> set[str]
        daily_history(symbol, start, end) -> pd.DataFrame

    Calls get_or_generate_fixtures on construction so tests never need to
    pre-generate data.
    """

    def __init__(self, fixtures_dir: str | None = None) -> None:
        if fixtures_dir is None:
            fixtures_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "fixtures"
            )
        self._dir = fixtures_dir
        get_or_generate_fixtures(self._dir)

    def funding_universe(self) -> set[str]:
        """Return all 5 universe symbols (all funding options unlocked in fixtures)."""
        return set(UNIVERSE_SYMBOLS)

    def intraday_history(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        """
        Generate synthetic 1-min intraday bars for the given symbol.

        Session: 09:00–11:59 (180 bars) + 13:30–16:15 (165 bars) = 345 bars/day.
        ITMG receives injected ORB edge.
        Other symbols: random walk, no systematic pattern.

        Returns DataFrame with columns: datetime (pd.Timestamp), date,
        open, high, low, close, volume, value, net_foreign.
        """
        import pytz
        wib = pytz.timezone("Asia/Jakarta")

        seed = FIXTURE_SEED + abs(hash(symbol)) % 1000
        rng = np.random.default_rng(seed)
        ref_price = REFERENCE_PRICES.get(symbol, 1000.0)

        # Generate 60 business days (≈ 84 calendar days, within lookback_days=90)
        n_trading_days = 60
        anchor = pd.Timestamp("2025-09-01")
        trading_dates = pd.bdate_range(start=anchor, periods=n_trading_days)

        rows = []
        for day_dt in trading_dates:
            date_obj = day_dt.date()

            # Build list of (hour, minute) for the two sessions
            bar_times: list[tuple[int, int]] = []
            for h in range(9, 12):  # 09:00–11:59
                for m in range(60):
                    bar_times.append((h, m))
            for h_m in range(13 * 60 + 30, 16 * 60 + 16):  # 13:30–16:15
                bar_times.append((h_m // 60, h_m % 60))

            n_bars = len(bar_times)

            if symbol == "ITMG":
                # Injected ORB edge: deterministic on ALL days
                # OR window 09:00–09:29: flat at ref_price
                # Bar 09:30 (trigger): close = ref_price * 1.010
                # Last bar: close = ref_price * 1.020 (return ≈ 99 bps from entry)
                closes = []
                for h, m in bar_times:
                    if h == 9 and m < 30:
                        closes.append(ref_price)
                    elif h == 9 and m == 30:
                        closes.append(ref_price * 1.010)
                    elif h == 16 and m == 15:
                        closes.append(ref_price * 1.020)
                    else:
                        closes.append(ref_price * 1.010)
                closes = np.array(closes)
                highs = np.maximum(closes, ref_price)
                lows = np.full(n_bars, ref_price * 0.995)
                opens = np.full(n_bars, ref_price)
            else:
                # Random walk
                log_returns = rng.normal(0.0, 0.0002, size=n_bars)
                log_prices = np.cumsum(log_returns)
                closes = ref_price * np.exp(log_prices)
                highs = closes * (1.0 + np.abs(rng.normal(0.0, 0.0001, size=n_bars)))
                lows = closes * (1.0 - np.abs(rng.normal(0.0, 0.0001, size=n_bars)))
                opens = np.roll(closes, 1)
                opens[0] = ref_price

            volumes = rng.integers(500, 2000, size=n_bars)
            values = closes * volumes

            for i, (h, m) in enumerate(bar_times):
                # Build naive datetime then localize to WIB
                naive_dt = pd.Timestamp(
                    year=day_dt.year, month=day_dt.month, day=day_dt.day,
                    hour=h, minute=m,
                )
                wib_dt = wib.localize(naive_dt.to_pydatetime())
                rows.append({
                    "datetime": wib_dt,
                    "date": date_obj,
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    "volume": int(volumes[i]),
                    "value": float(values[i]),
                    "net_foreign": 0,
                })

        df = pd.DataFrame(rows)

        # Session filter: keep only AM [09:00, 12:00) and PM [13:30, 16:15]
        def _in_session(dt: object) -> bool:
            ts = pd.Timestamp(dt)
            t_min = ts.hour * 60 + ts.minute
            in_am = 9 * 60 <= t_min < 12 * 60
            in_pm = 13 * 60 + 30 <= t_min <= 16 * 60 + 15
            return in_am or in_pm

        mask = df["datetime"].apply(_in_session)
        df = df[mask].sort_values("datetime").reset_index(drop=True)
        return df

    def daily_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Return daily OHLCV for *symbol* within [start, end] (inclusive).

        Columns: date (str), open (float), high (float), low (float),
                 close (float), volume (float).

        Raises FileNotFoundError if the fixture CSV is missing.
        """
        path = os.path.join(self._dir, f"{symbol}.csv")
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Fixture CSV not found: {path}. "
                f"Call generate_fixtures('{self._dir}') first."
            )
        dtype_map: dict[str, type] = {
            "date": str,
            "open": float,
            "high": float,
            "low": float,
            "close": float,
            "volume": float,
        }
        df = pd.read_csv(path, comment="#", dtype=dtype_map)
        df["date"] = df["date"].astype(str)
        # Ensure net_foreign column is present; coerce if the CSV was generated
        # before this column was added.
        if "net_foreign" in df.columns:
            df["net_foreign"] = pd.to_numeric(df["net_foreign"], errors="coerce").fillna(0.0)
        else:
            df["net_foreign"] = 0.0
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df.reset_index(drop=True)
