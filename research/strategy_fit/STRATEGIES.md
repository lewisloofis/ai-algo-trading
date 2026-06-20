# Strategy families — the comparison items in the strategy-fit grid

This is the plain-language catalog of every strategy the plan-10 harness tests.
Each one is a **family** (a signal idea with a few parameters); the grid runs
`symbol × family × hold` cells and scores each against the ADR-0011 cost gate.
The implementations live in `families_*.py`; this doc is the index.

A strategy "passes" only if it clears all of:
1. **cost gate** — `gross_edge_bps > hurdle`, where
   `hurdle = min_edge_to_clear_bps (one-tick spread + 40 bps round-trip fee)
   + modelled_impact_bps + financing_bps`;
2. **statistical significance** — bootstrap `p < 0.05` on per-trade returns;
3. **multiple-testing survival** — Benjamini-Hochberg FDR at α=0.10 across the
   whole grid (so we don't keep the lucky tail of a big search);
4. **net edge > 0** after the full cost stack.

All families target the deployable band: hold ≤ 2 trading days (daily) or
same-day (intraday). `MIN_EFF_N = 30` independent observations is required on
each shape's natural unit.

---

## Single-name, daily bars — `families_single_name.py`

One stock, daily OHLCV, hold ≤ 2 days.

| Family | Idea | Trigger |
|---|---|---|
| **daily_momentum** | Yesterday's up-move continues today (trend-following). | 1-day close-to-close momentum; go in its direction. |
| **mean_reversion** | Price stretched from its average snaps back (opposite of momentum). | Deviation from the 5-day rolling mean, negated. |
| **volume_breakout** | A volume spike marks a real move worth following. | Volume z-score > 1.0, then trade the day's price direction. |

## Event-driven, pooled across the universe — `families_event.py`

Wait for a trigger, then pool every name's triggers together so thin names
still reach `MIN_EFF_N`. Often the only viable shape for very-wide-tick names.

| Family | Idea | Trigger |
|---|---|---|
| **gap_event** | Overnight gaps carry directional information. | Gap > 2%; trade in the gap's direction. |
| **volume_spike_event** | Abnormal volume precedes continuation. | Volume z-score > 1.5; trade the spike day's direction. |
| **foreign_flow_spike_event** | Foreign net flow pushes price. | Foreign net buy/sell z-score > 1.5; trade the flow's direction. |

(Foreign flow comes from chartbit's `foreignbuy`/`foreignsell`; the raw
`foreignflow` field is cumulative — daily flow is `buy − sell`.)

## Cross-sectional factor — `families_cross_sectional.py`

Rank **all** names against each other each day instead of betting on one name.
Factor = each name's trailing 1-day return rank; target = its forward 1-day
return. Test statistic = Spearman **Information Coefficient** (does the rank
predict the forward return?), summarised as IC t-stat and ICIR. This is a
long-the-winners / short-the-losers book. Observation unit = one trading day
(needs ≥ 5 names with valid data); `MIN_EFF_N` applies to the day count.

## Pairs / cointegration — `families_pairs.py`

Find two names that move together (Engle-Granger cointegration at 5%), then
trade the **spread** when it stretches, betting it mean-reverts. Market-neutral.
Spread half-life is estimated from an AR(1) fit; non-stationary pairs are
dropped. Candidate pairs are capped to the top names by volume (logged, not
silently truncated). Cost floor = sum of both legs' friction (two orders).
Observation unit = one non-overlapping residual-spread block per pair.

## Intraday, same-day — `families_intraday.py`

Enter and exit within one session → **0 financing** (this is where the first
genuinely positive survivors appear). 1-minute bars, WIB session 09:00–16:15,
lunch 12:00–13:30 excluded.

| Family | Idea | Mechanics |
|---|---|---|
| **ORB** (opening-range breakout) | The first break of the morning range sets the day's direction. | Mark high/low of 09:00–09:30; enter on the first bar that breaks it; exit at close. |
| **intraday_momentum** | The morning move predicts the rest of the day. | Direction of the move into ~11:00; hold to close. |
| **intraday_mean_reversion** | Overextension from VWAP reverts. | When price is stretched from VWAP by midday, fade it. |

---

## How to run

```bash
# offline, synthetic fixtures, no token
python -m strategy_fit.run_strategy_fit --source fixture --outdir out

# live, needs SB_BEARER_TOKEN in the environment
python -m strategy_fit.run_strategy_fit --source live --outdir out_live
```

Output: `strategy_portfolio.md` — full coverage table + a PASS / INCONCLUSIVE /
NO-GO verdict per cell. See `docs/adr/0011-capital-model-300m-leverage-sleeve.md`
for the cost gate this all feeds, and `docs/plans/10-strategy-fit-and-portfolio.md`
for the methodology.
