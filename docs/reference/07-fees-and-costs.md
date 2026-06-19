# Transaction Fees & Costs — What a Round-Trip Actually Costs

Every backtest and fill simulation has to net out the cost of trading, or it
will report alpha that does not survive contact with the broker. This document
pins down the **exact fee schedule** a Stockbit order pays on IDX, separates the
**buy** and **sell** legs (they are *not* symmetric), and states the all-in
hurdle a strategy must clear before it makes money.

> Source: Stockbit's own order-preview UI (the "Exchange Fee" breakdown shown in
> the trading app, captured June 2026) plus the broker's published commission.
> Numbers are current as of capture; brokers can revise commission tiers, and
> the regulatory levies (BEI/KPEI/KSEI, PPh, PPN) are set by the exchange and
> tax authority and change rarely but do change. Re-confirm against a live order
> preview before trusting these in production.

---

## Table of Contents

1. [The headline rate is all-in](#1-the-headline-rate-is-all-in)
2. [Stockbit commission (all-in)](#2-stockbit-commission-all-in)
3. [Exchange fee breakdown (the component inside the commission)](#3-exchange-fee-breakdown-the-component-inside-the-commission)
4. [All-in cost — buy vs. sell](#4-all-in-cost--buy-vs-sell)
5. [Worked example (from the screenshot)](#5-worked-example-from-the-screenshot)
6. [What this means for the strategy](#6-what-this-means-for-the-strategy)
7. [Cost model for the fill simulator](#7-cost-model-for-the-fill-simulator)

---

## 1. The headline rate is all-in

An IDX trade through Stockbit shows two line items in the order preview — the
broker commission and an "Exchange Fee" breakdown — but they are **not
additive**. The headline rate you pay is:

- **Buy:** `0.15%` of order value, **all-in**.
- **Sell:** `0.25%` of order value, **all-in**.

The "Exchange Fee" popover (`0.0433%` buy / `0.1433%` sell) is a *breakdown of
what is already inside* that headline rate — the regulatory/clearing levies from
BEI (the exchange), KPEI (clearing), KSEI (custody), plus VAT (PPN) and, on
sells only, the final income tax (PPh Final). It is **not** charged on top.

> **Why we know it's inclusive, not additive.** Subtract the exchange fee from
> the headline rate on each leg and the broker's own net margin falls out:
> `0.15% − 0.0433% = 0.1067%` on the buy, `0.25% − 0.1433% = 0.1067%` on the
> sell. The broker's cut is **symmetric** (~0.107% both sides) and the *entire*
> buy/sell asymmetry is the exchange fee plus the sell-only `0.1%` PPh Final.
> That is exactly the shape you'd expect if `0.15%`/`0.25%` is the all-in
> figure. (An earlier draft of this doc treated the two layers as additive — it
> was wrong; the commission already includes the exchange fee.)

---

## 2. Stockbit commission (all-in)

| Side | All-in rate | of which exchange fee | broker net |
|------|-------------|-----------------------|-----------|
| Buy  | **0.15%** | 0.0433% | ~0.1067% |
| Sell | **0.25%** | 0.1433% (incl. 0.1% PPh) | ~0.1067% |

The `0.10%` gap between the buy and sell headline rate is almost entirely the
`0.1%` PPh Final, a sell-only tax. The broker's underlying margin is effectively
symmetric; the government's cut is bundled into the higher sell rate.

> Commission can vary by account tier / promo. `0.15%` buy / `0.25%` sell is the
> figure the user confirmed for this account. Make it a config knob, not a
> constant baked across the codebase.

---

## 3. Exchange fee breakdown (the component inside the commission)

These are the line items from the order preview's **Exchange Fee** popover.
Percentages are of **order value** (price × shares). They sum to the
`0.0433%`/`0.1433%` that is *already inside* the headline commission — listed
here so the cost is auditable, not because they are an extra charge.

| Component | Rate | Applies to | What it is |
|-----------|------|-----------|------------|
| Biaya Transaksi BEI       | 0.018%  | Buy & Sell | IDX (Bursa Efek Indonesia) transaction levy |
| Biaya Kliring KPEI        | 0.009%  | Buy & Sell | KPEI clearing fee |
| Biaya Penyelesaian KSEI   | 0.003%  | Buy & Sell | KSEI settlement fee |
| Dana Jaminan KPEI         | 0.010%  | Buy & Sell | KPEI guarantee fund contribution |
| PPN (VAT 11%)             | 0.0033% | Buy & Sell | 11% VAT on the BEI+KPEI+KSEI service fees (11% × 0.030%) |
| **PPh Final**             | **0.1%**| **Sell only** | Final income tax on sale proceeds |
| **Total (sell)**          | **0.1433%** | — | Sum of all rows above |
| **Total (buy)**           | **0.0433%** | — | Same, minus the sell-only PPh Final |

The screenshot's `0.1433%` total is from a **Sell** Order Preview, so it
includes the `0.1%` PPh. The buy-side exchange fee is the same stack **without**
PPh = `0.0433%`.

Sanity check on the PPN line: `0.018 + 0.009 + 0.003 = 0.030%` of value is the
BEI/KPEI/KSEI service portion, and `11% × 0.030% = 0.0033%`, which matches the
PPN row exactly. (Dana Jaminan is a fund contribution, not a taxable service, so
it sits outside the PPN base.)

---

## 4. All-in cost — buy vs. sell

| Leg | **All-in** | (of which exchange fee) |
|-----|-----------|-------------------------|
| **Buy**  | **0.15%** | 0.0433% |
| **Sell** | **0.25%** | 0.1433% |
| **Round-trip** | **0.40%** | 0.1866% |

**A flat round-trip costs 0.40% of notional = 40 basis points**, before any
spread or slippage. That is the floor: a position must move at least `+0.40%` in
your favour *just to break even* on fees alone, and more once you account for
crossing the spread.

---

## 5. Worked example (from the screenshot)

The screenshot shows a **sell** of BIRD (Blue Bird): `2,113` lots at price
`2,000`.

```
Shares       = 2,113 lots × 100 shares/lot      = 211,300 shares
Order value  = 211,300 × 2,000                  = 422,600,000 IDR

Sell cost (all-in)  0.25%   × 422,600,000        =   1,056,500 IDR
  of which exchange fee 0.1433% × 422,600,000    =     605,586 IDR  (inside the 0.25%)
  of which broker net   0.1067% × 422,600,000    =     450,914 IDR
                                                 ------------------
Net proceeds        422,600,000 − 1,056,500      = 421,543,500 IDR
```

A symmetric buy of the same notional would instead pay
`0.15% × 422,600,000 = 633,900 IDR`, leaving a round-trip cost on this notional
of `1,690,400 IDR` (`0.40%`).

---

## 6. What this means for the strategy

This is the number that makes the [microstructure study](../plans/04-microstructure-study.md)
go/no-go gate real:

- The OBI signal in plans 04/05 predicts **forward mid-price moves of a few to a
  few tens of basis points** over 1–30 second horizons on LQ45 names.
- The fee hurdle alone is **40 bps round-trip**, and that is *before* the
  half-spread paid on entry and exit.
- So a signal that is "directionally correct ~70% of the time" is **not
  automatically tradeable** — the average winning move must exceed fees + spread
  + slippage, not just be positive.

This is exactly why a **fill simulator + backtester** is the next gate rather
than more alpha mining: the open question is not "does OBI predict direction"
(it does, weakly) but "does the predicted move clear ~40 bps of fees plus the
spread after realistic fills." Document the answer there; this doc just supplies
the cost inputs.

---

## 7. Cost model for the fill simulator

The simulator should charge, per leg, the **all-in headline rate** — do not add
the exchange fee on top, it is already inside:

```
leg_cost(value, side) = value × fee[side]

fee = { buy: 0.0015, sell: 0.0025 }   # all-in, exchange fee already included
```

Plus the **microstructure costs** the fee table does not include:

- **Spread cost.** A marketable (taker) order crosses the spread — model entry
  at the far touch (buy at ask, sell at bid), not at mid. On a tick-wide spread
  this can be several bps on its own for these price levels.
- **No maker rebate.** IDX has **no maker/taker fee distinction** — the
  commission and levies are the same whether you provide or take liquidity
  (confirmed: the order preview shows one Exchange Fee regardless of order type,
  and the commission is a flat per-side rate). Resting a passive limit order
  saves the *spread*, not the *fee*. There is no rebate for providing liquidity.
- **Queue position & partial fills.** A passive limit order only fills if the
  market trades through your price *and* the queue ahead of you clears — see the
  fill-simulator plan for the queue model. Assume no fill until proven,
  not the optimistic opposite.
- **Slippage / market impact.** Sweeping more than the top-of-book size walks up
  (or down) the book; size each fill against the captured depth.

Rounding notes:
- Prices must be snapped to the IDX tick (`roundTick`, see
  [06-pre-trade-rules.md §5](./06-pre-trade-rules.md#5-tick-size-fraksi-harga)).
- Quantities are whole lots of 100 shares
  ([§6](./06-pre-trade-rules.md#6-lot-size)).
- Fees are computed on the rounded price × rounded shares — model them on the
  same integers the exchange uses, not on the un-rounded mid.

---

Prev: [Pre-Trade Rules ←](./06-pre-trade-rules.md) ·
Index: [README](./README.md)
