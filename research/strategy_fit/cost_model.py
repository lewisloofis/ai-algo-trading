"""
strategy_fit/cost_model.py — the cost the backtests assume, evaluated for one fill (one leg).

Per leg, in bps of that leg's notional (the same components strategy_fit prices a round trip with, split by leg):

* fee     — the schedule, 15 bps buy / 25 bps sell (paper_scorecard.FEE_BUY / FEE_SELL; sums to the ~40 bps
            round trip every cell is charged).
* spread  — half an IDX tick: the minimum half-spread when the book is one tick wide (the research convention
            charges one tick per round trip, paper_scorecard.score_trip).
* impact  — half of impact.TrailingImpactModel's round-trip square-root impact at this fill's notional, with the
            calibrated coefficient (impact_coeffs.json), vol/ADV from the 60 bars strictly BEFORE the fill day
            (no lookahead). None, never 0, when it cannot be priced (no bars / too little history).

Financing is a round-trip quantity and is added in cost_report, beside the leg totals, never inside them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Optional

import pandas as pd

from screener.metrics import tick_size
from strategy_fit import impact as imp
from strategy_fit.paper_scorecard import FEE_BUY, FEE_SELL

_BAR_COLUMNS = ("date", "close", "volume", "value")


def modelled_fee_bps(side: str) -> float:
    return (FEE_BUY if side == "buy" else FEE_SELL) * 1e4


def modelled_spread_bps(price_idr: float) -> float:
    return 0.5 * tick_size(price_idr) / price_idr * 1e4


def load_daily(data_dir, symbol: str) -> Optional[pd.DataFrame]:
    """Daily bars (date as ISO string) or None when the symbol has no local file."""
    path = Path(data_dir) / f"{symbol}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    cols = [c for c in _BAR_COLUMNS if c in df.columns]
    df = df[cols].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values("date").reset_index(drop=True)


@dataclass(frozen=True)
class LegModel:
    modelled_fee_bps: float
    modelled_spread_bps: float
    modelled_impact_bps: Optional[float]
    impact_priced: bool
    impact_note: str
    as_of: Optional[str]
    coeff: Optional[float]
    liq_bucket: Optional[str]

    @property
    def modelled_cost_bps(self) -> float:
        return self.modelled_fee_bps + self.modelled_spread_bps + (self.modelled_impact_bps or 0.0)


def _unpriced(fee: float, spread: float, note: str, as_of: Optional[str] = None) -> LegModel:
    return LegModel(fee, spread, None, False, note, as_of, None, None)


def price_leg(fill, bars: Optional[pd.DataFrame]) -> LegModel:
    """Model one fill. ``fill`` needs side, symbol, day, fill_price_idr, notional_idr."""
    fee = modelled_fee_bps(fill.side)
    spread = modelled_spread_bps(fill.fill_price_idr)
    if bars is None or len(bars) == 0:
        return _unpriced(fee, spread, "impact not priced: no daily bars for this symbol")
    prior = bars[bars["date"] < fill.day]
    if prior.empty:
        return _unpriced(fee, spread, "impact not priced: no daily bars before the fill day")
    as_of = str(prior["date"].iloc[-1])
    model = imp.TrailingImpactModel(bars, symbol=fill.symbol, use_calibrated=True,
                                    position_notional_idr=float(fill.notional_idr))
    try:
        _vol, adv = model.inputs(as_of)
        rt = model.impact_bps(as_of)
        coeff = model.coeff(as_of)
    except imp.InsufficientImpactHistory as exc:
        return _unpriced(fee, spread, f"impact not priced: insufficient history ({exc})", as_of)
    return LegModel(fee, spread, 0.5 * rt, True, "", as_of, coeff, imp.liquidity_bucket(adv))


def hold_days(entry_day: str, exit_day: str) -> int:
    from lib.market_calendar import CALENDAR

    return CALENDAR.trading_days_between(_date.fromisoformat(entry_day), _date.fromisoformat(exit_day))
