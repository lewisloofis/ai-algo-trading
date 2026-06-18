# Microstructure Signals — Order-Book Imbalance & Micro-Price

This note documents the **academic thesis** behind the two first signals we plan
to study on captured BUMI data: **order-book imbalance (OBI)** and **micro-price**.
It records *why* these signals are expected to predict short-term price, the
*formulas* we will implement, and the *open question* of whether results derived on
developed-market equities transfer to a thin, 1-tick IDX name.

> **Status:** gate run — **PASS** (2026-06-18). The companion study (OBI/
> micro-price → forward-return) is built on the replay engine and run on the
> captured BUMI segments: **top-of-book imbalance predicts the forward mid
> direction** (0.75–0.80 hit-rate conditional on a move, out-of-sample, beating a
> block-bootstrap null). OFI (the event-flow version, §2.1) — ✅ **study complete, verdict `OFI_NO_GAIN`**.
> On a 1-tick book the static OBI snapshot is the sufficient statistic; OFI adds
> no reliable gain over OBI1. See [plan 05](../plans/05-ofi-trade-flow-study.md)
> and [thesis 02](./02-order-flow-and-price-impact.md).
> Harness, report, and figures: [`collector/study/`](../../collector/study/)
> (see [plan 04](../plans/04-microstructure-study.md)).
> See also [README](./README.md).

---

## 1. Why the printed price is a bad signal on BUMI

BUMI trades around 165–171 IDR with a **1-tick** spread. The last printed price is
therefore:

- **Coarse** — it can only sit on integer ticks, so it stays frozen for long
  stretches then jumps a full tick at once.
- **Laggy** — it only updates *after* a trade prints, i.e. after the move has
  already happened.
- **Contaminated by bid-ask bounce** — consecutive prints alternate between bid
  and ask even when fair value hasn't moved, injecting noise.

Both signals below exist to extract direction from the **resting order book**,
which updates continuously and *before* any trade prints.

---

## 2. Order-book imbalance (OBI)

### 2.1 Thesis

> Price moves are driven by **order-flow imbalance**, not by the passage of time.
> Net buying pressure mechanically lifts the offer and price ticks up; the book
> makes that pressure observable in advance.

Theoretical lineage:

- **Kyle (1985), "Continuous Auctions and Insider Trading"** (*Econometrica*).
  The origin. Price impact is **linear** in net order flow — "Kyle's lambda."
  Informed demand leaks into price through imbalance. This is why we expect the
  imbalance → price-change relationship to be roughly linear rather than random.
- **Glosten & Milgrom (1985), "Bid, Ask and Transaction Prices in a Specialist
  Market…"** (*Journal of Financial Economics*). Market makers skew quotes because
  order flow carries private information — imbalanced flow is informed flow, so
  price should move.
- **Lipton, Pesavento & Sotiropoulos (2013), "Trade arrival dynamics and quote
  imbalance in a limit order book"** ([arXiv:1312.0514](https://arxiv.org/abs/1312.0514)).
  The direct justification for **top-of-book imbalance**: they derive the
  **probability of an up-move as an explicit function of bid/ask queue
  imbalance**. This is essentially the relationship our study tests.
- **Cont, Kukanov & Stoikov (2014), "The price impact of order book events"**
  (*Journal of Financial Econometrics*). The modern empirical anchor. Defines
  **Order Flow Imbalance (OFI)** from order-book *events* (not just resting size)
  and shows price changes are linearly explained by it with high R². This is the
  **trade-tape** version we unlock after the next live capture.

OBI (resting size) is the *static* proxy; OFI (Cont–Kukanov–Stoikov) is the
*dynamic, event-flow* version. Same thesis, two measurements — we start with OBI
because it needs only the data we already have.

### 2.2 Formula

Top-of-book (level-1) imbalance:

```
OBI₁ = (V_bid − V_ask) / (V_bid + V_ask)         ∈ [−1, +1]
```

where `V_bid`, `V_ask` are the volumes resting at the best bid / best ask.

Depth-weighted to N levels (weights `wᵢ` decay with distance from touch, e.g.
`wᵢ = 1` for equal weight or `wᵢ = 1/(i+1)`):

```
OBI_N = (Σ wᵢ·V_bid,ᵢ − Σ wᵢ·V_ask,ᵢ) / (Σ wᵢ·V_bid,ᵢ + Σ wᵢ·V_ask,ᵢ)
```

Interpretation: `OBI → +1` = book stacked on the bid (upward pressure);
`OBI → −1` = stacked on the ask (downward pressure); `0` = balanced.

---

## 3. Micro-price

### 3.1 Thesis

The precise term and the rigorous construction come from one paper:

- **Stoikov (2018), "The micro-price: a high-frequency estimator of future
  prices"** (*Quantitative Finance*; [SSRN 2970694](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694)).

> The mid-price is a poor estimator of fair value: it ignores size and is
> contaminated by bid-ask bounce. The right estimator is the **expected future
> mid-price conditioned on current imbalance and spread**, constructed so it is a
> **martingale** — i.e. once you've used all current book information, there is no
> further predictable drift.

Two layers, kept separate because they are often conflated:

1. **Weighted mid-price** — the naive, intuitive size-weighting. Predates Stoikov
   and is *not* a martingale (it has predictable error).
2. **Stoikov's micro-price** — models imbalance and spread as a Markov chain,
   notes they **mean-revert**, and adds a correction term so the result is a true
   martingale estimate of future price. The correction is the whole contribution:
   it is why micro-price *leads* the printed price — it is literally
   `E[future price | book state now]`.

### 3.2 Formulas

**Weighted mid-price** (start here — trivial, no model fitting):

```
P_wmid = (P_bid·V_ask + P_ask·V_bid) / (V_bid + V_ask)
```

Note the **cross-weighting**: fair value leans toward the side with *less* size,
because that side will clear first. With 5M bid / 0.5M ask, `P_wmid` sits near the
ask — price is about to go there. Equivalently `P_wmid = mid + (spread/2)·OBI₁`,
which makes explicit that the weighted mid is just OBI expressed in price units.

**Stoikov micro-price** (only if the weighted mid shows signal):

```
P_micro = P_mid + g(I, S)
```

where `I` is imbalance, `S` is the (discretized) spread, and `g(I, S)` is the
adjustment estimated from the data via the Markov-chain construction in Stoikov
(2018) — the limit of expected future mid-price changes conditioned on `(I, S)`.
Fitting `g` requires estimating transition matrices from the capture, so it is a
second-stage effort, not the first cut.

---

## 4. How the two relate

They are two views of the same imbalance:

- **OBI** — a dimensionless ratio in `[−1, +1]`.
- **Micro-price** — the same information expressed **in price units**, directly
  comparable to your actual entry/exit levels ("micro-price is 0.7 ticks above
  last" is tradeable; a raw ratio is not).

The study computes both and picks whichever gives the stronger forward-return
signal at the horizon that matters.

---

## 5. The open question for IDX / BUMI

Every result above was derived and validated on **US and other developed-market
equities**. There is **no guarantee they transfer** to a thin, low-priced,
1-tick IDX name. Reasons they might weaken or invert here:

- **Tick-size constraint** — a 1-tick spread truncates the price granularity the
  micro-price adjustment relies on.
- **Lower informed-trader fraction** — the Kyle/Glosten-Milgrom mechanism is
  weaker if less flow is information-driven.
- **Different participant mix** — retail-dominated flow, ARA/ARB band dynamics,
  and auction effects can break the linear-impact assumption.

This is precisely **why the study is a go/no-go gate**: if OBI/micro-price have no
forward-return power on BUMI, book-only strategies won't work and we must wait for
the trade tape (OFI) before investing in the backtest harness. Cheap to find out
on data we already have.

---

## 6. Implementation plan (summary)

Built as the go/no-go gate in [plan 04](../plans/04-microstructure-study.md); the
harness lives in [`collector/study/`](../../collector/study/) (Python stats over a
Go `sbcap features` CSV). Status per step below.

1. **Weighted mid-price + top-of-book OBI** (this doc, §2.2 & §3.2 layer 1) —
   ✅ **done, PASS.** Computed on every book update over the replay engine and
   regressed against forward **mid** returns at 1s/5s/10s/30s/60s. Top-of-book
   imbalance (`OBI1`, equivalently the weighted-mid skew) hit **0.75–0.80**
   conditional on a move, out-of-sample, clearing a block-bootstrap null.
   *(Target is forward mid, not last-price — last price is the contaminated signal
   §1 rejects.)*
2. **Depth-weighted OBI** (§2.2, N>1) — ✅ **done, weaker.** `1/(i+1)`-weighted
   depth was the only signal also passing the strict monotone-decile test, but its
   conditional hit-rate (0.65–0.68) trails top-of-book; equal-weight depth was
   near coin-flip on the pre-lunch holdout. Depth beyond the touch did **not** add
   robust power over level-1.
3. **Stoikov micro-price** (§3.2 layer 2) — ⏭️ **unlocked, deferred.** Steps 1–2
   passed, so this is now justified; needs Markov transition-matrix fitting. Next
   after the backtester, not before.
4. **OFI** (Cont–Kukanov–Stoikov) — ✅ **done, verdict `OFI_NO_GAIN`.** OFI
   incremental R² over OBI1 did not reach 5 pp on the held-out OOS split. On a
   1-tick book, the static snapshot (OBI1) is the sufficient statistic — the
   within-snapshot flow dynamics add no reliable predictive power. Kyle's lambda
   is statistically nonzero (t ≈ 3.4) but economically negligible (~0 IDR/100
   lots). OBI generalises strongly across all 6 symbols (6/6 pass, OOS hit-rates
   0.64–0.99 at 5 s). See [thesis 02](./02-order-flow-and-price-impact.md) and
   [plan 05](../plans/05-ofi-trade-flow-study.md).

---

## References

- Kyle, A. S. (1985). *Continuous Auctions and Insider Trading.* Econometrica
  53(6), 1315–1335.
- Glosten, L. R., & Milgrom, P. R. (1985). *Bid, Ask and Transaction Prices in a
  Specialist Market with Heterogeneously Informed Traders.* Journal of Financial
  Economics 14(1), 71–100.
- Lipton, A., Pesavento, U., & Sotiropoulos, M. G. (2013). *Trade arrival dynamics
  and quote imbalance in a limit order book.* arXiv:1312.0514.
- Cont, R., Kukanov, A., & Stoikov, S. (2014). *The price impact of order book
  events.* Journal of Financial Econometrics 12(1), 47–88.
- Stoikov, S. (2018). *The micro-price: a high-frequency estimator of future
  prices.* Quantitative Finance 18(12), 1959–1966. SSRN 2970694.
- Cartea, Á., Jaimungal, S., & Penalva, J. (2015). *Algorithmic and
  High-Frequency Trading.* Cambridge University Press. (Textbook synthesis of the
  above.)
