# Fill Simulator & Backtester — Does OBI Survive Frictions?

> **Status:** ✅ Complete · **NO-GO** · **Owner:** @lewis · **Updated:** 2026-06-18
> **Related:** [04-microstructure-study.md](./04-microstructure-study.md) · [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) · [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md) · [Plans index](./README.md)

> ✅ **Result: NO-GO.** Built (`collector/study/backtest.py` + `test_backtest.py`)
> and run on the June 18 session across all 5 de-overlapped names. **Every
> (symbol, model, variant, threshold, horizon) cell loses after costs** — net
> PnL −51 to −99 bps/trade, **0 % win-rate**, bootstrap p = 1.000 everywhere.
> The decisive identity: `mean_net_bps − mean_gross_bps = −40 bps` in every
> cell (the all-in round-trip fee charged exactly once), and the *gross* leg is
> already negative (spread + adverse tick) before the fee even lands. The
> tick-scale OBI1 edge does not clear retail IDX frictions — confirming the
> expected base case. See [§7 Results](#7-results).

## 1. Why this is the next gate

Plans 04/05 established the signal facts and nothing more:

- `OBI1` is the entire edge. OFI, signed flow, Kyle's λ all add nothing.
- De-overlapped, 5/6 LQ45 names hold a **0.69–0.78** directional hit-rate on
  56–118 independent forward-mid moves. Real, but weak, and **coincident** — it
  predicts *direction*, not a *tradeable* move.
- The predicted move is **~1 tick against a ~1-tick spread**.

Against that, [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md)
fixes the cost of acting on it:

| | Buy | Sell | Round-trip |
|---|---|---|---|
| All-in fee | 0.15% | 0.25% | **0.40% (= 40 bps)** |

(All-in — the exchange fee is already inside the headline commission, not
additive; see reference 07 §1.) …**before** spread and slippage. So the only
question left is economic, not statistical: **after fees and realistic fills,
does acting on `OBI1` make or lose money?** A 70%-right signal whose average win
is one tick (~5–10 bps on these names) cannot pay a 40 bps round-trip plus a
crossed spread. This plan
builds the harness that proves or kills that, and it is the gate before any live
execution code.

> **A NO-GO here is the expected, useful outcome.** The honest base case is that
> a tick-scale coincident signal does not clear retail IDX frictions. The point
> of this plan is to *quantify* the gap (how negative, and which knob — fees,
> spread, hold time, passive vs aggressive — moves it most), so we know whether
> the strategy is dead or just needs a different execution style / fee tier /
> instrument set.

---

## 2. Locked decisions

1. **Python-first over the existing CSVs, not a new Go engine — yet.** The
   `sbcap features` and `sbcap trades` CSVs already carry everything a fill
   simulation needs (top-5 book, spread, trade tape with `side`). A Python
   backtester reuses the study harness, the de-overlap / effective-N discipline,
   and the bootstrap machinery already built and audited in
   `obi_forward_return.py` / `multisym_obi.py`. The production-grade,
   event-driven **Go** backtester that feeds the *same* `strategy.go` interface
   the live system uses (live/replay parity, the whole point of plan 02) is
   deferred until **after** this gate says the signal is tradeable — same staged
   discipline plans 04/05 followed (don't build the engine before the signal
   clears the gate).

2. **Long-only is the base case.** A Stockbit retail cash account cannot easily
   short on IDX (short-selling is restricted to a margin account and an
   exchange-approved short-able list). So the strategy may only act on the
   **bid-pressure** side (`OBI1` high → expect mid up → buy, later flatten). A
   long/short variant is run **only** as an upper-bound sensitivity, clearly
   labelled as not retail-tradeable.

3. **Taker (marketable) fills are the primary model; passive is the
   sensitivity.** The taker model needs the fewest assumptions and is the honest
   worst case: cross the spread, fill at the far touch, pay spread + fee, always
   fills up to top-of-book size. The passive (maker) model — rest at the near
   touch, fill only when the tape trades through the price and the queue ahead
   clears — is run second to bound the upside of saving the spread. If **even
   the passive model loses after fees, that is a clean NO-GO.**

4. **Fixed-horizon exit, to mirror the study.** Entry when `OBI1` crosses a
   threshold; exit a fixed `h` seconds later (the same 5 s / 30 s horizons the
   hit-rate study used). This makes the backtest a *direct economic translation*
   of the de-overlapped hit-rate — no new degrees of freedom, no curve-fit exit
   logic. Signal-reversal and stop/take-profit exits are explicitly out of scope
   for the gate (they add free parameters that invite overfitting on one day).

5. **Same statistical hygiene as 04/05.** Chronological OOS split (no random
   splits), non-overlapping trades only for the headline metric,
   `MIN_EFF_N = 30` independent-trade floor, block-bootstrap null on net PnL,
   per-symbol across the 5 names that held de-overlapped (BUMI, DEWA, INDY, ITMG,
   MDKA — ANTM excluded, it collapsed to 4 independent obs in the audit).

6. **Fees from the reference doc, as config, not constants.** The headline rate
   is **all-in** — the exchange fee is already inside it, not added on top
   (reference 07 §1). So `fee = {buy: 0.0015, sell: 0.0025}` is the only thing
   the simulator charges per leg. One source of truth, overridable for a
   different account tier.

---

## 3. Inputs (already produced by `sbcap`)

No new Go work is required for the gate. Both CSVs already exist:

**`sbcap features` → `*_features.csv`** (one row per book update):

```
recv_ns, iso_wib, bid_seq, offer_seq, last_price, iep,
delta_bid_vol, delta_ask_vol,
bid_px_0, bid_vol_0, ask_px_0, ask_vol_0, … (depth 5)
```

Supplies: `OBI1 = (bid_vol_0 − ask_vol_0)/(bid_vol_0 + ask_vol_0)`, the mid
`(bid_px_0 + ask_px_0)/2`, the spread, the far-touch fill prices (`ask_px_0` to
buy, `bid_px_0` to sell), and top-of-book size for the slippage cap.

**`sbcap trades` → `*_trades.csv`** (one row per print):

```
recv_ns, iso_wib, price, vol, side, trade_num
```

Supplies the passive-fill confirmation: a resting buy at price `P` fills once
cumulative traded volume at price `≤ P` after entry exceeds the queue ahead.

> If the gate passes and the Go engine is built later, that engine replays the
> raw segments through `datafeed.go`'s `decodeWrap` directly — but for this gate
> the CSVs are the contract.

---

## 4. The simulator

A new study module, `collector/study/backtest.py` (Python, delegated to a Sonnet
agent per the build convention). Core loop, per symbol:

```text
for each features row t where a trade is not already open:
    obi = OBI1(t)
    if obi >=  THRESH:   open LONG  at t   (bid-pressure)
    # long-only base case: obi <= -THRESH opens a SHORT only in the
    # long/short sensitivity variant, never in the retail base case

on an open trade, at t + h seconds:
    close the position
    record gross PnL (exit_px − entry_px) and net PnL (gross − fees − spread)
```

### 4.1 Taker fill model (primary)

```
entry (buy):  fill at ask_px_0(t_entry)              # cross the spread
exit  (sell): fill at bid_px_0(t_exit)
spread cost is implicit in using the far touch on both legs
size: min(target_lots, top-of-book lots); log truncation, never silently cap
fee:  entry_value × 0.15%  +  exit_value × 0.25%   # all-in, exchange fee included
```

### 4.2 Passive fill model (sensitivity)

```
entry (buy):  rest at bid_px_0(t_entry); queue_ahead = bid_vol_0(t_entry)
              fill when Σ trades at price ≤ bid_px_0 after t_entry > queue_ahead
              if unfilled within a max-wait window → trade is abandoned (logged)
exit: symmetric at the ask
fee:  same schedule (IDX has no maker rebate — reference doc §7)
```

Passive fills must also account for **adverse selection**: a passive buy tends
to fill precisely when the move goes against it. The model captures this for
free because fills come from the real tape — no separate adjustment needed, but
the resulting fill-rate and conditional PnL must be reported, not hidden.

### 4.3 Cost accounting

Single source of truth, mirroring [reference/07](../reference/07-fees-and-costs.md):

```python
FEE = {
    "buy":  0.0015,   # 0.15% all-in (exchange fee already included)
    "sell": 0.0025,   # 0.25% all-in (exchange fee + PPh already included)
}
```

Prices snap to the IDX tick (`roundTick`, ref 06 §5); quantities are whole lots
of 100; fees compute on the rounded price × rounded shares.

---

## 5. Outputs & verdict

`backtest.py` writes `out/backtest_report.md` plus an equity-curve PNG per
symbol. Reported metrics, OOS (last 40%) and per symbol:

| Metric | Why |
|--------|-----|
| **Gross vs net PnL** | how much of any edge fees eat (the headline) |
| Net PnL per trade (bps of notional) | is the average trade above the fee line |
| Win-rate after costs | hit-rate ≠ profitability once fees bite |
| Profit factor, avg win / avg loss | shape of the return distribution |
| Fill rate (passive only) | how many signals actually execute |
| Independent (non-overlapping) trade count | the effective-N floor (≥ 30) |
| Block-bootstrap p-value on net PnL | is net PnL distinguishable from zero |

**Three-state verdict** (same logic as the hardened 04/05 scripts):

- **PASS** — net-positive PnL after fees on OOS, on ≥ `MIN_EFF_N` independent
  trades, clearing the bootstrap null, for at least one (symbol, horizon,
  threshold) cell. → green-light the Go event-driven engine + paper trading.
- **NO-GO** — net PnL ≤ 0 after fees across all cells even under the passive
  (best-case) model. → the tick-scale signal does not clear retail frictions;
  pivot (different fee tier, longer-horizon signal, or shelve).
- **INCONCLUSIVE** — net-positive but on < `MIN_EFF_N` independent trades or
  failing the null. → needs the multi-day capture (No. 1) to accumulate sample,
  then re-run.

---

## 6. Implementation steps

- [x] **6.1** `backtest.py` skeleton: loads features + trades CSVs, computes
  `OBI1`, mid, spread; reuses `load_csv`/`clean`/`filter_continuous_session`/
  `add_obi1`/`_crosses_lunch`/`MIN_EFF_N` from `multisym_obi.py`.
- [x] **6.2** Taker fill model + cost accounting (§4.1, §4.3) — `leg_cost`,
  `_long_pnl`/`_short_pnl`, `FEE = {buy: 0.0015, sell: 0.0025}`.
- [x] **6.3** Entry/exit loop with fixed-horizon exit and one-position-at-a-time
  (§4); long-only base case (`run_taker`).
- [x] **6.4** Per-trade blotter (`Trade` dataclass) + per-symbol equity curve
  PNG; gross-vs-net and fee-drag metrics (§5).
- [x] **6.5** Passive fill model from the trade tape (§4.2) as the sensitivity —
  `run_passive`, run across all 5 names (per-symbol trade tapes exported from the
  June 18 segment via `sbcap trades --symbol …`).
- [x] **6.6** Threshold {0.3, 0.5, 0.7} / horizon {5 s, 30 s} sweep across the 5
  de-overlapped names; block-bootstrap null on per-trade net bps; `MIN_EFF_N=30`
  floor in the verdict.
- [x] **6.7** `out/backtest_report.md` with the three-state verdict; long/short
  upper-bound variant clearly labelled non-retail.
- [x] **6.8** Unit tests (`test_backtest.py`, 4/4 pass) for the fill + fee math —
  a hand-checked trade and the worked example in reference 07 §5 match to the
  rupiah (1,056,500 / 633,900 / 1,690,400).

---

## 7. Results

**Verdict: NO-GO** (long/short upper bound also NO-GO). Run on the June 18
continuous session, OOS = last 40 % of each name's intraday rows. Full output in
`collector/study/out/backtest_report.md` + `backtest_equity_{SYM}.png`.

### Headline — taker long-only (retail base case), threshold 0.5, h = 5 s

| Symbol | n_trades | mean gross bps | mean net bps | win-rate | bootstrap p |
|--------|---------:|---------------:|-------------:|---------:|------------:|
| BUMI | 503 | −57.71 | **−97.56** | 0.000 | 1.000 |
| DEWA | 339 | — | **−86.56** | 0.000 | 1.000 |
| INDY |  86 | — | **−87.07** | 0.000 | 1.000 |
| ITMG | 138 | −15.32 | **−55.29** | 0.000 | 1.000 |
| MDKA | 318 | — | **−73.47** | 0.000 | 1.000 |

Every cell across the full {threshold × horizon × symbol × model × variant}
sweep is net-negative (−51 to −99 bps/trade), with a **0 % after-cost win-rate**
and bootstrap p = 1.000 — net PnL is indistinguishable from "always loses."

### Why — the cost identity

In **every** cell `mean_net_bps − mean_gross_bps = −40 bps` to the basis point:
the 0.40 % all-in round-trip fee, charged exactly once (validating the
inclusive-not-additive fee model from [reference 07](../reference/07-fees-and-costs.md)).
And the *gross* leg is **already negative** before the fee — the taker pays the
spread twice and the predicted ~1-tick move doesn't even cover that:

- **BUMI** (1-tick book, 168/169): gross ≈ −58 bps ≈ the ~59 bps spread alone.
- **ITMG** (high price → tick is a small bps fraction): gross only −15 bps, the
  least-bad name, but the flat 40 bps fee still buries it to −55 bps net.

### Passive (sensitivity, all 5 names)

Per-symbol trade tapes were exported from the June 18 segment
(`sbcap trades --symbol {BUMI,DEWA,INDY,ITMG,MDKA}`), so the passive model runs
across the full set. It barely executes: fill-rate ≈ 0.1–0.8 % (only a handful
of the thousands of signals fill within the horizon, because the L1 queue ahead
is hundreds of thousands to millions of shares), and every filled trade still
nets ≈ −46 to −83 bps, usually with a forced taker flatten on the exit. **Even
the best-case fill model loses on every name** — the clean NO-GO condition from
§5.

### What this means

The tick-scale, coincident OBI1 edge (plans 04/05) does **not** clear retail IDX
frictions on this data — the honest base case. The Go event-driven engine
(locked decision 1) stays deferred. Re-opening this gate requires a *different*
lever, not more of the same: a longer-horizon / larger-amplitude signal, a
cheaper fee tier, or an instrument where the predicted move ≫ spread + 40 bps.
Accumulating multi-day data (capture track No. 1) would tighten the estimate but
will not change a −55-to-−99 bps gap that is structural, not sampling noise.

---

## 8. Out of scope (deliberately)

- The Go event-driven engine and live/replay strategy parity — deferred until
  this gate passes (locked decision 1).
- Signal-reversal / stop / take-profit exits — free parameters that overfit one
  day (locked decision 4).
- Multi-day / portfolio-level sizing, compounding, and risk limits — meaningless
  on a single session; revisit after No. 1 (multi-day capture) accumulates data.
- Market-impact beyond the top-of-book size cap — the captured depth is 5
  levels; deeper sweeps are not modelled, only flagged when size exceeds L1.

---

Prev: [05-ofi-trade-flow-study.md ←](./05-ofi-trade-flow-study.md) ·
Index: [README](./README.md)
